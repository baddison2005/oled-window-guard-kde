import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import desktop_stability as runner
from tests.integration import test_desktop_gtk as gtk_tests


class StabilityTests(unittest.TestCase):
    def exercise(self,cancel=False,rearm=False,early=False,unready=False,initial_reset=False,stuck_reset=False):
        now=[0.0];paused=[False];last=['none'];frames=gtk_tests.GtkTests().snapshot();baseline=copy.deepcopy(frames)
        events=[(60,63,0),(65,68,2),(123,126,0),(133,136,2),(186,189,0),(201,204,2)]
        if early:events[0]=(0,10,0)
        applied=set();self.intents=[];self.rows=[];pause_at=[None]
        def status():
            t=now[0]
            for i,(_,end,index) in enumerate(events):
                if t>=end and i not in applied and not paused[0]:
                    applied.add(i);frames['windows'][index]['rect']['x']+=20
                    last[0]='cancelled: native-input' if cancel and i==0 else 'committed'
            active=next(((start,end) for start,end,_ in events if start<=t<end),None) if not paused[0] else None
            is_paused=paused[0] and not (rearm and t>=pause_at[0]+5)
            result=dict(paused=is_paused,transactionActive=bool(active),skipAvailable=bool(active),
                        resumeReady=not (unready and paused[0] and t>=pause_at[0]+5),
                        warningRemainingSeconds=active[1]-t if active else 0,lastResult=last[0],
                        restoreAvailable=not paused[0] and last[0]=='committed' and not active,movementReady=not is_paused)
            if paused[0] and (stuck_reset or initial_reset and t-pause_at[0]<.25):
                result.update(resumeReady=False,displays=[],planningReady=False,nativeObservation={'available':False})
            return result
        def intent(action,payload=None):
            self.intents.append(action);self.assertEqual(action,'Pause');paused[0]=True;pause_at[0]=now[0]
        return runner.run_cycles(status,intent,lambda:copy.deepcopy(frames),
            lambda k,v:self.rows.append((k,copy.deepcopy(v))),baseline,
            clock=lambda:now[0],sleep=lambda seconds:now.__setitem__(0,now[0]+seconds))

    def test_three_continuous_moves_per_output_and_pause_crosses_next_deadlines(self):
        self.assertEqual(self.exercise(),{'DP-1':3,'HDMI-A-1':3})
        self.assertEqual(self.intents,['Pause'])
        self.assertEqual(len([r for r in self.rows if r[0]=='stability-cycle-verified']),6)
        self.assertEqual(self.rows[-1][0],'stability-paused-verified')
        self.assertEqual(self.rows[-1][1]['seconds'],70)

    def test_warning_cancellation_cannot_count_as_scheduled_commit(self):
        with self.assertRaisesRegex(RuntimeError,'did not commit'):self.exercise(cancel=True)

    def test_early_movement_cannot_count_as_normal_timer(self):
        with self.assertRaisesRegex(RuntimeError,'before normal timer'):self.exercise(early=True)

    def test_rearming_during_paused_dwell_fails(self):
        with self.assertRaisesRegex(RuntimeError,'rearmed'):self.exercise(rearm=True)

    def test_loss_of_awake_readiness_during_dwell_does_not_pass(self):
        with self.assertRaisesRegex(RuntimeError,'uninterrupted awake'):self.exercise(unready=True)

    def test_pause_status_reset_settles_before_strict_dwell(self):
        self.assertEqual(self.exercise(initial_reset=True),runner.COUNTS)
        self.assertTrue(any(k=='paused-readiness-settling' for k,v in self.rows))
        self.assertEqual(self.rows[-1][0],'stability-paused-verified')
        with self.assertRaisesRegex(RuntimeError,'did not recover'):self.exercise(stuck_reset=True)

    def test_later_readiness_loss_remains_failure_after_initial_reset(self):
        with self.assertRaisesRegex(RuntimeError,'uninterrupted awake'):self.exercise(initial_reset=True,unready=True)

    def test_shorter_schedule_retains_three_cycles_and_deadline_coverage(self):
        self.assertEqual(runner.COUNTS,{'DP-1':3,'HDMI-A-1':3})
        self.assertEqual(runner.WARNING_SECONDS,3)
        self.assertGreaterEqual(runner.PAUSED_DWELL,max(runner.INTERVALS.values())+runner.WARNING_SECONDS+2)
        self.assertLessEqual(3*max(runner.INTERVALS.values())+3*runner.WARNING_SECONDS+runner.PAUSED_DWELL+10,runner.MAX_RUN_SECONDS)
        self.assertLess(runner.MAX_RUN_SECONDS,300)

    def exercise_main(self,timeout=False,paused_only=False):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);receipt=folder/'round.jsonl';fixture=folder/'fixture.jsonl'
            sample=gtk_tests.GtkTests().fixture();sample['monotonic']=0
            for i,w in enumerate(sample['windows']):w['title']=str(i)
            fixture.write_text(json.dumps(sample)+'\n')
            settings=folder/'settings.json';original=runner.preferences.defaults()
            runner.preferences.save_settings(settings,original)
            state=dict(paused=True,transactionActive=False,resumeReady=True,movementReady=False)
            actions=[]
            def host(action,payload=None):
                actions.append(action)
                if action=='PlanPreview':state['planningResult']=dict(id='preview',status='ready',moves=1)
                if action=='Resume':
                    state.update(paused=False,movementReady=True)
                    loaded=runner.preferences.load_settings(settings)
                    runner.preferences.save_settings(settings,dict(loaded.values,paused=False))
                if action=='Pause':
                    state.update(paused=True,movementReady=False)
                    loaded=runner.preferences.load_settings(settings)
                    runner.preferences.save_settings(settings,dict(loaded.values,paused=True))
                return dict(ok=True,requestId='preview')
            def cycles(status,intent,observe,record,baseline):
                with self.assertRaisesRegex(RuntimeError,'Forbidden'):intent('MoveNow')
                with self.assertRaisesRegex(RuntimeError,'Forbidden'):intent('Restore')
                if timeout:
                    handler=next(call.args[1] for call in handlers.call_args_list if call.args[0]==runner.signal.SIGALRM)
                    handler(runner.signal.SIGALRM,None)
                intent('Pause');return dict(runner.COUNTS)
            with patch.object(runner,'FIXTURE',fixture),patch.object(runner,'capture',return_value=gtk_tests.GtkTests().snapshot()), \
                 patch.object(runner,'read_status',side_effect=lambda:copy.deepcopy(state)),patch.object(runner,'call_host',side_effect=host), \
                 patch.object(runner,'run_cycles',side_effect=cycles),patch.object(runner.preferences,'default_settings_path',return_value=settings), \
                 patch.object(runner,'paused_observation') as paused_check, \
                 patch.object(runner.QCoreApplication,'instance',return_value=True),patch.object(runner.time,'sleep'), \
                 patch.object(runner.signal,'signal') as handlers,patch.object(runner.signal,'setitimer') as alarm, \
                 patch.object(runner.sys,'argv',['test','--receipt',str(receipt)]+(['--paused-only'] if paused_only else [])):runner.main()
            self.assertEqual(alarm.call_args_list[0].args,(runner.signal.ITIMER_REAL,90 if paused_only else 285))
            self.assertEqual(alarm.call_args_list[-1].args,(runner.signal.ITIMER_REAL,0))
            rows=[json.loads(line) for line in receipt.read_text().splitlines()]
            errors=[r for r in rows if r['kind']=='error']
            if timeout:
                self.assertEqual(len(errors),1)
                self.assertIn('285-second physical test limit',errors[0]['value'])
                self.assertFalse(any(r['kind']=='round-result' for r in rows))
            else:self.assertFalse(errors)
            self.assertEqual(actions.count('Resume'),0 if paused_only else 1)
            self.assertNotIn('MoveNow',actions);self.assertNotIn('Restore',actions)
            self.assertEqual(actions.count('PlanPreview'),0 if paused_only else 2)
            if paused_only:
                paused_check.assert_called_once()
                self.assertEqual(next(r['value'] for r in rows if r['kind']=='round-result')['case'],'stability-paused')
            self.assertEqual(runner.preferences.load_settings(settings).values,original)
            self.assertTrue(next(r['value'] for r in rows if r['kind']=='final-pause')['ok'])

    def test_main_resumes_once_and_restores_original_settings(self):
        self.exercise_main()

    def test_time_limit_disarms_timer_pauses_and_restores_settings(self):
        self.exercise_main(timeout=True)

    def test_paused_only_check_never_arms_or_repeats_movements(self):
        self.exercise_main(paused_only=True)


if __name__=='__main__':unittest.main()
