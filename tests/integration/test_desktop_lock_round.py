"""Paused physical lifecycle driver must distinguish native lock from input."""
import unittest
from tools.desktop_lock_round import observe


class LockRoundTests(unittest.TestCase):
    def exercise(self, states):
        rows=[]
        now=[0]
        index=[0]
        def status():
            state=states[min(index[0],len(states)-1)]
            index[0]+=1
            return dict(dict(paused=True,transactionActive=False,movementReady=False,
                             resumeReady=False,suspendedReasons=[]),**state)
        def sleep(seconds):now[0]+=1
        observe(status,lambda kind,value: rows.append((kind,value)),lambda:now[0],sleep)
        return rows

    def test_requires_native_locked_then_unlock_and_fresh_readiness(self):
        rows=self.exercise([
            {'suspendedReasons':['input-state-unknown-or-held']},
            {'suspendedReasons':['session-not-authoritatively-ready/locked/awake/active']},
            {'suspendedReasons':['input-state-unknown-or-held']},
            {'resumeReady':True}])
        self.assertEqual([kind for kind,value in rows if kind in ('native-locked','unlock-observed','round-result')],
                         ['native-locked','unlock-observed','round-result'])

    def test_input_cancellation_cannot_count_as_lock(self):
        with self.assertRaisesRegex(RuntimeError,'waiting for lock'):
            self.exercise([{'lastResult':'cancelled: native-input'}])

    def test_locked_without_recovery_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'waiting for unlock'):
            self.exercise([{'suspendedReasons':['session-not-authoritatively-ready/locked/awake/active']}])

    def test_unlocked_without_fresh_readiness_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'waiting for recover'):
            self.exercise([{'suspendedReasons':['session-not-authoritatively-ready/locked/awake/active']},{}])

    def test_armed_or_busy_or_unpaused_is_rejected(self):
        for field in ('movementReady','transactionActive','paused'):
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError,'paused, idle, unarmed'):
                self.exercise([{field:field!='paused'}])

    def test_resume_unavailable_while_locked(self):
        with self.assertRaisesRegex(RuntimeError,'Resume unexpectedly available'):
            self.exercise([{'suspendedReasons':['session-not-authoritatively-ready/locked/awake/active'],
                            'resumeReady':True}])


if __name__=='__main__':unittest.main()
