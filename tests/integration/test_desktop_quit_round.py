"""Warning teardown acceptance must prove unload before reopening paused."""
import unittest
from tools.desktop_quit_round import quit_during_warning


class QuitRoundTests(unittest.TestCase):
    def exercise(self, warning=None, teardown=True, fresh=None, before=True, quit_error=False):
        self.rows=[]
        self.actions=[]
        now=[0]
        reopened=[False]
        quitting=[False]
        def inspect():
            present = reopened[0] or (before and (not quitting[0] or not teardown))
            return {'controllerOwned':present,'adapterLoaded':present}
        def intent(action):
            self.actions.append(action)
            quitting[0]=True
            if quit_error:raise RuntimeError('Quit refused')
        def reopen():
            self.actions.append('Open')
            reopened[0]=True
        def sleep(seconds):now[0]+=seconds
        current=dict(skipAvailable=True,transactionActive=True,warningRemainingSeconds=17)
        current.update(warning or {})
        startup=dict(paused=True,movementReady=False,transactionActive=False,restoreAvailable=False)
        startup.update(fresh or {})
        return quit_during_warning(current,intent,lambda:startup,
                                   lambda kind,value:self.rows.append((kind,value)),
                                   inspect,reopen,lambda:now[0],sleep)

    def test_quit_unloads_then_reopens_paused(self):
        fresh=self.exercise()
        self.assertEqual(self.actions,['Quit','Open'])
        self.assertTrue(fresh['paused'])
        kinds=[kind for kind,value in self.rows]
        self.assertLess(kinds.index('quit-verified'),kinds.index('reopening'))
        self.assertIn('reopened-paused',kinds)

    def test_missing_or_late_warning_never_quits(self):
        for warning in ({'skipAvailable':False},{'transactionActive':False},{'warningRemainingSeconds':4}):
            with self.subTest(warning=warning),self.assertRaisesRegex(RuntimeError,'active warning'):
                self.exercise(warning=warning)
            self.assertEqual(self.actions,[])

    def test_missing_ownership_never_quits(self):
        with self.assertRaisesRegex(RuntimeError,'Expected owned'):
            self.exercise(before=False)
        self.assertEqual(self.actions,[])

    def test_failed_unload_is_rejected_but_open_recovery_attempted(self):
        with self.assertRaisesRegex(RuntimeError,'did not unload'):
            self.exercise(teardown=False)
        self.assertEqual(self.actions,['Quit','Open'])
        self.assertNotIn('quit-verified',[kind for kind,value in self.rows])

    def test_quit_refusal_is_not_passed_and_recovery_is_attempted(self):
        with self.assertRaisesRegex(RuntimeError,'Quit refused'):
            self.exercise(quit_error=True)
        self.assertEqual(self.actions,['Quit','Open'])

    def test_unsafe_reopened_state_is_rejected(self):
        for field in ('paused','movementReady','transactionActive','restoreAvailable'):
            with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'did not remain paused'):
                self.exercise(fresh={field:field!='paused'})


if __name__=='__main__':unittest.main()
