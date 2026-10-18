"""Safety controls in the supervised physical-test driver; no desktop writes."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusMessage
from oled_window_guard import preferences
from tools import desktop_cancellation_round as driver


class DesktopRoundTests(unittest.TestCase):
    def test_unverified_reconfigure_case_never_arms(self):
        with patch.object(driver,'require_fixed_installation',side_effect=RuntimeError('unverified installation')):
            actions,rows=self.exercise(case='reconfigure-warning')
        self.assertEqual(actions,['Pause'])
        self.assertTrue(any(r['kind']=='error' and 'unverified installation' in r['value'] for r in rows))

    @classmethod
    def setUpClass(cls):
        cls.app=QCoreApplication.instance() or QCoreApplication([])

    def exercise(self, movement_result="committed", inverse=True, case="move-restore", two_outputs=False, preflight="ready", preview_moves=1,preview_reason=None,libraries=None,restore_result="committed",restore_warning=True,startup_gap=False):
        actions=[]
        state={"paused":True,"transactionActive":False,"resumeReady":True,
               "movementReady":False,"displays":[{"id":"output-1","name":"DP-1"}],
               "skipAvailable":False,"warningRemainingSeconds":0,"lastResult":"none",
               "restoreAvailable":False}
        if two_outputs:state['displays'].append({'id':'output-2','name':'HDMI-A-1'})
        self.intent_payloads=[]
        phase=None
        polls=0
        clock=0
        library={'status':'available','executionReady':True,'revision':'revision-1',
                 'groups':[{'id':'minis-id','name':'Minis','layoutIds':['left','right']}],
                 'layouts':[{'id':name,'groupId':'minis-id','x':x,'y':0,'width':1/3,'height':1/3}
                            for name,x in (('left',0),('right',2/3))]}
        libraries=list(libraries) if libraries is not None else [library,library]
        def read_library():
            return libraries.pop(0)

        def status():
            nonlocal polls,phase
            if startup_gap and clock<.3:
                return dict(state,displays=[])
            if case=="scheduled-restore" and phase is None and state["movementReady"] and clock>=60:
                phase="move"
            if phase:
                polls+=1
                limit=10 if case=="scheduled-restore" and phase=="move" else 3
                state["transactionActive"]=polls<limit
                state["skipAvailable"]=polls<limit and (phase!='restore' or restore_warning)
                state["warningRemainingSeconds"]=20 if polls<limit else 0
                if polls>=limit:
                    state["lastResult"]=movement_result if phase=="move" else restore_result
                    state["restoreAvailable"]=inverse if phase=="move" else False
            return dict(state)

        class Interface:
            def setTimeout(self,value):pass
            def call(self,method):
                return SimpleNamespace(type=lambda:QDBusMessage.MessageType.ReplyMessage,
                                       arguments=lambda:[json.dumps(status())])

        def intent(action,payload=None):
            nonlocal phase,polls
            actions.append(action)
            self.intent_payloads.append((action,payload))
            if action=="PlanPreview":
                state['planningResult']={'id':'preview-1','status':preflight,'moves':preview_moves if preflight=='ready' else 0,
                                         'reason':preview_reason if preflight=='ready' else 'no-eligible-windows: other_desktop=2'}
                return {'ok':True,'requestId':'preview-1','status':dict(state)}
            if action=="Resume":state.update(paused=False,movementReady=True)
            if action in ("MoveNow","Restore"):
                phase="move" if action=="MoveNow" else "restore"
                polls=0;state["transactionActive"]=True
            if action=="Pause":state.update(paused=True,transactionActive=False)
            return {"ok":True,"status":dict(state)}

        def sleep(seconds):
            nonlocal clock
            clock+=seconds

        with tempfile.TemporaryDirectory() as folder:
            settings=Path(folder)/"settings.json"
            receipt=Path(folder)/"receipt.jsonl"
            preferences.save_settings(settings,preferences.defaults())
            def change(backup,record):
                applied=[False]
                def apply():
                    applied[0]=True;record('library-changed',{})
                def restore():
                    if applied[0]:record('library-restored',{});applied[0]=False
                return SimpleNamespace(apply=apply,restore=restore)
            with patch.object(driver,"QDBusInterface",return_value=Interface()), \
                 patch.object(driver,"PaddingChange",side_effect=change), \
                 patch.object(driver,"LayoutProvider",return_value=SimpleNamespace(read_snapshot=read_library)), \
                 patch.object(driver,"request_stale_restore",side_effect=lambda s,i,r,p,**kw:i('Restore')), \
                 patch.object(driver,"check_fixture_scope"), \
                 patch.object(driver,"call_host",side_effect=intent), \
                 patch.object(driver.preferences,"default_settings_path",return_value=settings), \
                 patch.object(driver.time,"monotonic",side_effect=lambda:clock), \
                 patch.object(driver.time,"sleep",side_effect=sleep), \
                 patch.object(driver.sys,"argv",["round","--receipt",str(receipt),"--case",case,"--delay","0"]):
                driver.main()
            self.saved_settings=preferences.load_settings(settings).values
            rows=[json.loads(line) for line in receipt.read_text().splitlines()]
        return actions,rows

    def test_scheduled_round_waits_for_timer_without_move_now(self):
        actions,rows=self.exercise(case='scheduled-restore')
        self.assertNotIn('MoveNow',actions)
        self.assertIn('Restore',actions)
        self.assertEqual(self.saved_settings['intervalMinutes'],1)
        self.assertEqual(self.saved_settings['warningSeconds'],10)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertGreaterEqual(next(r['elapsed'] for r in rows if r['kind']=='scheduled-transaction-observed'),60)

    def test_batched_pause_reload_waits_for_display_status_before_configuration(self):
        actions,rows=self.exercise(startup_gap=True)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        kinds=[r['kind'] for r in rows]
        self.assertLess(kinds.index('waiting-for-display-status'),kinds.index('SettingsChanged'))
        self.assertIn('Restore',actions)

    def test_scheduled_preflight_refusal_never_arms(self):
        actions,rows=self.exercise(case='scheduled-restore',preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertNotIn('Restore',actions)

    def test_single_move_restore_preflight_failure_never_arms(self):
        actions,rows=self.exercise(case='move-restore',preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)

    def test_imported_changed_library_restore_refuses_without_warning(self):
        actions,rows=self.exercise(case='imported-change-restore',preview_moves=2,
                                  restore_result='rejected: library-changed-or-unavailable',restore_warning=False)
        self.assertIn('Restore',actions)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertIn('library-restored',[r['kind'] for r in rows])

    def test_imported_change_preflight_failure_never_edits_library(self):
        actions,rows=self.exercise(case='imported-change-warning',preview_moves=2,preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('library-changed',[r['kind'] for r in rows])

    def test_unexpected_imported_restore_warning_skips_and_restores_library(self):
        actions,rows=self.exercise(case='imported-change-restore',preview_moves=2)
        self.assertIn('Skip',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))
        self.assertIn('library-restored',[r['kind'] for r in rows])

    def test_restore_only_follows_a_committed_movement_and_consumes_inverse(self):
        actions,rows=self.exercise()
        self.assertLess(actions.index("MoveNow"),actions.index("Restore"))
        self.assertEqual(actions[-1],"Pause")
        self.assertTrue(next(r["value"] for r in rows if r["kind"]=="restore-result")["inverseConsumed"])

    def test_cancelled_movement_never_requests_restore_and_always_pauses(self):
        actions,rows=self.exercise(movement_result="cancelled: native-input")
        self.assertNotIn("Restore",actions)
        self.assertEqual(actions[-1],"Pause")
        self.assertTrue(any(r["kind"]=="error" for r in rows))

    def test_missing_inverse_never_requests_restore_and_always_pauses(self):
        actions,rows=self.exercise(inverse=False)
        self.assertNotIn("Restore",actions)
        self.assertEqual(actions[-1],"Pause")
        self.assertTrue(any(r["kind"]=="error" for r in rows))

    def test_cross_monitor_selects_both_but_moves_only_target_and_requires_native_cancel(self):
        actions,rows=self.exercise(movement_result='cancelled: native-input',inverse=False,
                                   case='cross-monitor',two_outputs=True)
        self.assertEqual(self.saved_settings['movementDisplays'],['output-1','output-2'])
        self.assertIn(('MoveNow',{'outputId':'output-1'}),self.intent_payloads)
        scope=next(r['value'] for r in rows if r['kind']=='cross-monitor-scope')
        self.assertEqual(scope['inputOutput'],'HDMI-A-1')
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertTrue(any(r['kind']=='round-result' for r in rows))
        self.assertNotIn('Restore',actions)
        self.assertEqual(actions[-1],'Pause')

    def test_cross_monitor_refuses_missing_second_output_before_resume(self):
        actions,rows=self.exercise(case='cross-monitor')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))
        self.assertEqual(actions[-1],'Pause')

    def test_cross_monitor_preflight_no_plan_never_arms_and_records_reason(self):
        actions,rows=self.exercise(case='cross-monitor',two_outputs=True,preflight='no-plan')
        self.assertIn('PlanPreview',actions)
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertIn('other_desktop=2',next(r['value'] for r in rows if r['kind']=='error'))
        self.assertEqual(actions[-1],'Pause')

    def test_cross_monitor_does_not_accept_skip_or_committed_move_as_cancellation(self):
        for result in ('cancelled: user-skip','committed'):
            actions,rows=self.exercise(movement_result=result,inverse=False,
                                       case='cross-monitor',two_outputs=True)
            self.assertTrue(any(r['kind']=='error' for r in rows))
            self.assertFalse(any(r['kind']=='round-result' for r in rows))
            self.assertEqual(actions[-1],'Pause')

    def test_quit_warning_cannot_pass_if_input_cancels_before_quit(self):
        actions,rows=self.exercise(case='quit-warning',movement_result='cancelled: native-input',
                                   inverse=False)
        self.assertIn('PlanPreview',actions)
        self.assertNotIn('Quit',actions)
        self.assertFalse(any(r['kind']=='round-result' for r in rows))
        self.assertIn('not verified',next(r['value'] for r in rows if r['kind']=='error'))
        self.assertEqual(actions[-1],'Pause')

    def test_controller_loss_cannot_pass_early_input_cancellation(self):
        actions,rows=self.exercise(case='controller-loss',movement_result='cancelled: native-input',inverse=False)
        self.assertFalse(any(r['kind']=='round-result' for r in rows))
        self.assertIn('not verified',next(r['value'] for r in rows if r['kind']=='error'))
        self.assertEqual(actions[-1],'Pause')

    def test_controller_loss_no_plan_never_arms(self):
        actions,rows=self.exercise(case='controller-loss',preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)

    def test_target_loss_no_plan_never_arms(self):
        actions,rows=self.exercise(case='target-loss',preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)

    def test_target_loss_cannot_pass_early_input_cancellation(self):
        actions,rows=self.exercise(case='target-loss',movement_result='cancelled: native-input',inverse=False)
        self.assertFalse(any(r['kind']=='round-result' for r in rows))
        self.assertIn('not produce verified',next(r['value'] for r in rows if r['kind']=='error'))

    def test_quit_warning_preflight_no_plan_never_arms(self):
        actions,rows=self.exercise(case='quit-warning',preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertNotIn('Quit',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))

    def test_lock_warning_input_cancellation_before_lock_cannot_pass(self):
        actions,rows=self.exercise(case='lock-warning',movement_result='cancelled: native-input',inverse=False)
        self.assertIn('PlanPreview',actions)
        self.assertFalse(any(r['kind']=='round-result' for r in rows))
        self.assertIn('not verified',next(r['value'] for r in rows if r['kind']=='error'))
        self.assertEqual(actions[-1],'Pause')

    def test_lock_warning_no_plan_does_not_arm(self):
        actions,rows=self.exercise(case='lock-warning',preflight='no-plan')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))

    def test_group_restore_requires_two_move_preview_and_consumes_inverse(self):
        actions,rows=self.exercise(case='group-restore',preview_moves=2)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertLess(actions.index('PlanPreview'),actions.index('Resume'))
        self.assertLess(actions.index('MoveNow'),actions.index('Restore'))
        self.assertEqual(self.saved_settings['mode'],'group')
        self.assertEqual(self.saved_settings['groupId'],'builtin.horizontal-halves')
        self.assertEqual(self.saved_settings['maximumWindows'],2)
        self.assertFalse(self.saved_settings['rotateGroup'])
        self.assertTrue(next(r['value'] for r in rows if r['kind']=='restore-result')['inverseConsumed'])
        self.assertEqual(actions[-1],'Pause')

    def test_group_restore_single_move_preview_never_arms(self):
        actions,rows=self.exercise(case='group-restore',preview_moves=1)
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertIn('exactly two moves',next(r['value'] for r in rows if r['kind']=='error'))

    def test_group_cancelled_forward_never_requests_restore(self):
        actions,rows=self.exercise(case='group-restore',preview_moves=2,
                                   movement_result='cancelled: native-input',inverse=False)
        self.assertNotIn('Restore',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))
        self.assertEqual(actions[-1],'Pause')

    def test_group_rotation_commits_and_restores(self):
        actions,rows=self.exercise(case='group-rotation-restore',preview_moves=2,preview_reason='group-rotation')
        self.assertTrue(self.saved_settings['rotateGroup'])
        self.assertEqual(self.saved_settings['maximumWindows'],2)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertIn('Restore',actions)
        self.assertTrue(next(r['value'] for r in rows if r['kind']=='restore-result')['inverseConsumed'])

    def test_group_rotation_refuses_drift_preview(self):
        actions,rows=self.exercise(case='group-rotation-restore',preview_moves=2,preview_reason='group-drift')
        self.assertNotIn('Resume',actions)
        self.assertIn('rotation preview required',next(r['value'] for r in rows if r['kind']=='error'))

    def test_group_rotation_refuses_single_window_preview(self):
        actions,rows=self.exercise(case='group-rotation-restore',preview_moves=1,preview_reason='group-rotation')
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))

    def test_imported_group_commits_restore_and_retains_source_revision(self):
        actions,rows=self.exercise(case='imported-group-restore',preview_moves=2,preview_reason='group-drift')
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertEqual(self.saved_settings['groupId'],'kde:minis-id')
        self.assertFalse(self.saved_settings['rotateGroup'])
        self.assertIn('Restore',actions)
        before=next(r['value'] for r in rows if r['kind']=='imported-library')
        after=next(r['value'] for r in rows if r['kind']=='imported-library-after')
        self.assertEqual(before['revision'],after['revision'])

    def test_imported_missing_library_never_arms(self):
        actions,rows=self.exercise(case='imported-group-restore',libraries=[{'status':'missing'}])
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)
        self.assertIn('not execution-ready',next(r['value'] for r in rows if r['kind']=='error'))

    def test_imported_changed_corner_zones_never_arm(self):
        actions,rows=self.exercise(case='imported-group-restore',libraries=[
            {'status':'available','executionReady':True,'revision':'r1',
             'groups':[{'id':'minis','name':'Minis','layoutIds':[]}],'layouts':[]}])
        self.assertNotIn('Resume',actions)
        self.assertIn('zones changed',next(r['value'] for r in rows if r['kind']=='error'))

    def test_imported_missing_two_move_preview_never_arms(self):
        actions,rows=self.exercise(case='imported-group-restore',preview_moves=1)
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)

    def test_imported_rotation_commits_restores_and_checks_revision(self):
        actions,rows=self.exercise(case='imported-rotation-restore',preview_moves=2,preview_reason='group-rotation')
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertEqual(self.saved_settings['groupId'],'kde:minis-id')
        self.assertTrue(self.saved_settings['rotateGroup'])
        self.assertIn('Restore',actions)
        self.assertTrue(any(r['kind']=='imported-library-after' for r in rows))

    def test_imported_rotation_drift_preview_never_arms(self):
        actions,rows=self.exercise(case='imported-rotation-restore',preview_moves=2,preview_reason='group-drift')
        self.assertNotIn('Resume',actions)
        self.assertIn('rotation preview required',next(r['value'] for r in rows if r['kind']=='error'))

    def test_imported_rotation_unavailable_source_never_arms(self):
        actions,rows=self.exercise(case='imported-rotation-restore',libraries=[{'status':'unavailable'}])
        self.assertNotIn('Resume',actions)
        self.assertNotIn('MoveNow',actions)

    def test_resize_restore_refuses_without_warning(self):
        actions,rows=self.exercise(case='stale-resize',preview_moves=2,
                                  restore_result='restore-skipped: desktop-changed',restore_warning=False)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertIn('Restore',actions)
        self.assertTrue(next(r['value'] for r in rows if r['kind']=='stale-restore-result')['inverseConsumed'])

    def test_stale_restore_requires_desktop_changed_without_warning(self):
        actions,rows=self.exercise(case='stale-restore',preview_moves=2,
                                   restore_result='restore-skipped: desktop-changed',restore_warning=False)
        self.assertFalse(any(r['kind']=='error' for r in rows))
        self.assertIn('Restore',actions)
        self.assertTrue(next(r['value'] for r in rows if r['kind']=='stale-restore-result')['inverseConsumed'])
        self.assertEqual(actions[-1],'Pause')

    def test_unexpected_stale_restore_warning_is_skipped_before_execution(self):
        actions,rows=self.exercise(case='stale-restore',preview_moves=2)
        self.assertIn('Skip',actions)
        self.assertFalse(any(r['kind']=='round-result' for r in rows))
        self.assertIn('Unexpected stale Restore warning',next(r['value'] for r in rows if r['kind']=='error'))

    def test_stale_restore_forward_cancellation_never_requests_inverse(self):
        actions,rows=self.exercise(case='stale-restore',preview_moves=2,
                                   movement_result='cancelled: native-input',inverse=False)
        self.assertNotIn('Restore',actions)
        self.assertTrue(any(r['kind']=='error' for r in rows))


if __name__=="__main__":unittest.main()
