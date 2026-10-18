"""Crash acceptance must preserve the full warning dwell before recovery."""
import unittest
from tools.desktop_controller_loss import controller_loss_during_warning


class ControllerLossTests(unittest.TestCase):
    def exercise(self,warning=None,crash_error=False,restart=False,unsafe=False):
        now=[0]
        state=dict(controllerOwned=True,adapterLoaded=True)
        self.events=[]
        crashed=[False]
        def inspect():
            if restart and crashed[0] and now[0]>1:
                state['controllerOwned']=True
            return dict(state)
        def crash():
            self.events.append(('crash',now[0]))
            if crash_error:raise RuntimeError('Invalid owner')
            crashed[0]=True
            state['controllerOwned']=False
            return dict(pid=123)
        def cleanup():
            self.events.append(('cleanup',now[0]))
            state['adapterLoaded']=False
        def reopen():
            self.events.append(('open',now[0]))
            state.update(controllerOwned=True,adapterLoaded=True)
        def sleep(seconds):now[0]+=seconds
        current=dict(skipAvailable=True,transactionActive=True,warningRemainingSeconds=17)
        current.update(warning or {})
        fresh=dict(paused=not unsafe,movementReady=False,transactionActive=False,restoreAvailable=False)
        return controller_loss_during_warning(current,lambda:fresh,
            lambda k,v:self.events.append((k,now[0])),inspect,crash,cleanup,reopen,lambda:now[0],sleep)

    def test_dwell_exceeds_warning_expiry_before_cleanup_and_paused_reopen(self):
        fresh=self.exercise()
        self.assertTrue(fresh['paused'])
        cleanup=next(t for k,t in self.events if k=='cleanup')
        self.assertGreaterEqual(cleanup,19)
        kinds=[k for k,t in self.events]
        self.assertLess(kinds.index('controller-loss-dwell-complete'),kinds.index('cleanup'))
        self.assertLess(kinds.index('cleanup'),kinds.index('open'))

    def test_inactive_or_late_warning_never_crashes(self):
        for warning in ({'skipAvailable':False},{'transactionActive':False},{'warningRemainingSeconds':4}):
            with self.subTest(warning=warning),self.assertRaisesRegex(RuntimeError,'active warning'):
                self.exercise(warning=warning)
            self.assertEqual(self.events,[])

    def test_owner_validation_failure_never_cleans_up_or_reopens(self):
        with self.assertRaisesRegex(RuntimeError,'Invalid owner'):
            self.exercise(crash_error=True)
        self.assertEqual(self.events,[('crash',0)])

    def test_unexpected_restart_cannot_pass_loss_dwell(self):
        with self.assertRaisesRegex(RuntimeError,'Unexpected controller restart'):
            self.exercise(restart=True)
        self.assertNotIn('controller-loss-dwell-complete',[k for k,t in self.events])

    def test_unpaused_reopen_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'not paused'):
            self.exercise(unsafe=True)


if __name__=='__main__':unittest.main()
