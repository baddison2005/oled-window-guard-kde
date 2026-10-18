import unittest
from tools.desktop_topology_warning import disconnect_during_warning, topology_evidence
from tools.desktop_topology_warning_batch import verify_round, output_order


class TopologyWarningTests(unittest.TestCase):
    def exercise(self,early=False,unsafe=False,input_cancel=False,missed=False,wrong=False,coupled=False):
        now=[0.0];self.events=[];self.intents=[]
        displays=[dict(name=n,id=n) for n in ('DP-1','HDMI-A-1')]
        def status():
            t=now[0]; gone=1<=t<(4 if early else 14) and not missed
            names=[r for r in displays if not (gone and r['name']==('HDMI-A-1' if wrong else 'DP-1'))]
            started=t<1 or missed
            return dict(displays=names,paused=not started and not (unsafe and t>=14),
                        transactionActive=started,movementReady=started or (unsafe and t>=14),
                        restoreAvailable=False,skipAvailable=started,warningRemainingSeconds=max(0,10-t),
                        lastResult='none' if started else 'cancelled: '+('native-input' if input_cancel else 'native-topology'),
                        resumeReady=t>=14,nativeObservation={'available':True},
                        cancellationTrace=[] if started or (input_cancel and not coupled) else
                        ([dict(reason='native-input',commandId='move-1',armed=True,epoch='e',generation=1,topologyGeneration=0,at=1000),
                          dict(reason='native-topology',commandId=None,armed=False,epoch='e',generation=2,topologyGeneration=1,at=1004)] if coupled else
                         [dict(reason='native-topology',commandId='move-1',armed=True,epoch='e',generation=1,topologyGeneration=1,at=1000)]))
        def sleep(seconds):now[0]+=seconds
        return disconnect_during_warning(status(),'DP-1',status,
            lambda action:self.intents.append(action),lambda k,v:self.events.append((k,v)),
            lambda:now[0],sleep)

    def test_disconnect_cancel_dwell_and_ready_paused_recovery_without_resume(self):
        final=self.exercise()
        self.assertTrue(final['resumeReady']);self.assertTrue(final['paused'])
        self.assertEqual(self.intents,[])
        self.assertEqual(self.events[-1][0],'topology-warning-recovered')

    def test_input_cancellation_does_not_count_as_topology_cancellation(self):
        with self.assertRaisesRegex(RuntimeError,'Topology cancellation not verified'):
            self.exercise(input_cancel=True)
        self.assertEqual(self.events[-1][0], 'topology-cancellation-diagnostics')
        self.assertEqual(self.events[-1][1]['result'], 'cancelled: native-input')

    def test_input_first_requires_fresh_coupled_topology_and_completes_dwell_recovery(self):
        self.exercise(input_cancel=True,coupled=True)
        cancellation=next(v for k,v in self.events if k=='topology-warning-cancelled')
        self.assertEqual(cancellation['result'],'cancelled: native-input')
        self.assertEqual(cancellation['evidence']['mode'],'input-before-topology')
        self.assertEqual(self.events[-1][0],'topology-warning-recovered')
        self.assertEqual(self.intents,[])

    def test_stale_distant_or_replaced_observer_events_cannot_supply_topology_evidence(self):
        first=dict(reason='native-input',commandId='move-1',armed=True,epoch='e',generation=1,topologyGeneration=0,at=1000)
        topology=dict(reason='native-topology',commandId=None,armed=False,epoch='e',generation=2,topologyGeneration=1,at=1004)
        baseline=dict(cancellationTrace=[])
        for changed in (dict(topology,at=1300),dict(topology,epoch='replacement'),
                        dict(topology,generation=1),dict(topology,topologyGeneration=0)):
            self.assertIsNone(topology_evidence(baseline,dict(cancellationTrace=[first,changed])))
        self.assertIsNone(topology_evidence(dict(cancellationTrace=[first,topology]),dict(cancellationTrace=[first,topology])))
        self.assertIsNone(topology_evidence(baseline,dict(cancellationTrace=[dict(first,commandId=None),topology])))

    def test_selected_output_is_first_in_batch(self):
        self.assertEqual(output_order('DP-1'),('DP-1','HDMI-A-1'))
        self.assertEqual(output_order('HDMI-A-1'),('HDMI-A-1','DP-1'))

    def test_reconnect_before_original_warning_expiry_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'dwell'):
            self.exercise(early=True)

    def test_automatic_rearming_after_reconnect_fails(self):
        with self.assertRaisesRegex(RuntimeError,'remain paused'):
            self.exercise(unsafe=True)

    def test_missed_disconnect_skips_before_movement(self):
        with self.assertRaisesRegex(RuntimeError,'skipped before movement'):
            self.exercise(missed=True)
        self.assertEqual(self.intents,['Skip'])

    def test_wrong_monitor_disconnect_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'before target disconnect'):
            self.exercise(wrong=True)

    def test_batch_requires_recovery_and_final_pause(self):
        rows=[dict(kind='topology-warning-cancelled',value=dict(evidence=dict(mode='topology-first'))),
              dict(kind='topology-warning-recovered',value=dict(oldWarningExpired=True,noResumeAfterDisconnect=True)),
              dict(kind='final-pause',value={'ok':True})]
        verify_round(rows)
        for bad in (rows[:-1],rows[1:],[rows[0],rows[-1]],rows+[dict(kind='error',value='failed')]):
            with self.assertRaises(RuntimeError):verify_round(bad)


if __name__=='__main__':unittest.main()
