"""Manual-change acceptance must await explicit operator confirmation."""
import unittest
from types import SimpleNamespace
from tools.desktop_stale_restore import request_stale_restore


class StaleRestoreTests(unittest.TestCase):
    def test_resize_checkpoint_names_resize_before_restore(self):
        rows=[]
        actions=[]
        request_stale_restore(lambda:dict(transactionActive=False,restoreAvailable=True),
            actions.append,lambda k,v:rows.append((k,v)),
            SimpleNamespace(exists=lambda:True),sleep=lambda seconds:None,change="resize")
        self.assertIn("Resize ONE target",rows[0][1])
        self.assertIn("I resized one window",rows[0][1])
        self.assertEqual(actions,["Restore"])

    def exercise(self,confirmed_after=2,available=True,busy=False):
        now=[0]
        self.actions=[]
        self.rows=[]
        def sleep(seconds):now[0]+=seconds
        request_stale_restore(lambda:dict(transactionActive=busy,restoreAvailable=available),
            lambda action:self.actions.append((action,now[0])),lambda k,v:self.rows.append(k),
            SimpleNamespace(exists=lambda:now[0]>=confirmed_after),lambda:now[0],sleep)

    def test_confirmation_and_quiet_wait_precede_restore(self):
        self.exercise()
        self.assertEqual(self.actions[0][0],'Restore')
        self.assertGreaterEqual(self.actions[0][1],8)
        self.assertEqual(self.rows,['operator-move-window','operator-frame-change-confirmed','before-stale-restore'])

    def test_missing_confirmation_never_requests_restore(self):
        with self.assertRaisesRegex(RuntimeError,'confirmation timed out'):
            self.exercise(confirmed_after=999)
        self.assertEqual(self.actions,[])

    def test_unavailable_inverse_never_counts_as_stale_record_refusal(self):
        with self.assertRaisesRegex(RuntimeError,'Restore unavailable'):
            self.exercise(available=False)
        self.assertEqual(self.actions,[])

    def test_busy_runtime_never_requests_restore(self):
        with self.assertRaisesRegex(RuntimeError,'Restore unavailable'):
            self.exercise(busy=True)
        self.assertEqual(self.actions,[])


if __name__=='__main__':unittest.main()
