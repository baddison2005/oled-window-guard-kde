"""Warning lock acceptance requires native-lock cancellation and disarmed recovery."""
import unittest
from tools.desktop_warning_lock import lock_during_warning


class WarningLockTests(unittest.TestCase):
    def exercise(self,states,warning=None):
        clock=[0]
        index=[0]
        self.rows=[]
        self.actions=[]
        def status():
            state=states[min(index[0],len(states)-1)]
            index[0]+=1
            return dict(dict(paused=True,movementReady=False,transactionActive=False,
                             skipAvailable=False,restoreAvailable=False,resumeReady=False,
                             lastResult='cancelled: native-lock',suspendedReasons=[]),**state)
        def sleep(seconds):clock[0]+=1
        current=dict(skipAvailable=True,transactionActive=True,warningRemainingSeconds=17)
        current.update(warning or {})
        return lock_during_warning(current,status,lambda k,v:self.rows.append((k,v)),
                                   lambda:self.actions.append('Lock'),lambda:clock[0],sleep)

    locked={'suspendedReasons':['session-not-authoritatively-ready/locked/awake/active']}

    def test_native_lock_and_fresh_recovery_stay_disarmed(self):
        state=self.exercise([self.locked,{'suspendedReasons':['input-state-unknown-or-held']},
                             {'resumeReady':True}])
        self.assertEqual(self.actions,['Lock'])
        self.assertFalse(state['movementReady'])
        self.assertEqual([k for k,v in self.rows if k in ('warning-lock-cancelled','warning-unlocked','lock-recovery-verified')],
                         ['warning-lock-cancelled','warning-unlocked','lock-recovery-verified'])

    def test_input_cancellation_cannot_count_as_native_lock(self):
        with self.assertRaisesRegex(RuntimeError,'Expected native-lock'):
            self.exercise([dict(self.locked,lastResult='cancelled: native-input')])

    def test_missing_native_lock_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'waiting for warning lock lock'):
            self.exercise([{}])

    def test_missing_recovery_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'waiting for warning lock recover'):
            self.exercise([self.locked,{}])

    def test_activation_busy_warning_or_inverse_after_lock_is_rejected(self):
        for field in ('paused','movementReady','transactionActive','skipAvailable','restoreAvailable'):
            with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'disarmed, idle'):
                self.exercise([dict(self.locked,**{field:field!='paused'})])

    def test_late_or_missing_warning_never_locks(self):
        for warning in ({'warningRemainingSeconds':4},{'skipAvailable':False},{'transactionActive':False}):
            with self.subTest(warning=warning),self.assertRaisesRegex(RuntimeError,'active warning'):
                self.exercise([self.locked],warning)
            self.assertEqual(self.actions,[])

    def test_locked_resume_ready_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'Resume unexpectedly available'):
            self.exercise([dict(self.locked,resumeReady=True)])


if __name__=='__main__':unittest.main()
