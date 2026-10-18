import unittest
from tools.desktop_scheduled_batch import verify_round


class ScheduledBatchTests(unittest.TestCase):
    def rows(self):
        return [dict(kind='scheduled-transaction-observed',value={}),
                dict(kind='restore-result',value=dict(result='committed',inverseConsumed=True,warningSeen=True)),
                dict(kind='final-pause',value=dict(ok=True))]

    def test_verified_scheduled_restore_is_accepted(self):
        verify_round(self.rows())

    def test_manual_move_cannot_count_as_scheduled(self):
        with self.assertRaisesRegex(RuntimeError,'MoveNow'):
            verify_round(self.rows()+[dict(kind='MoveNow',value={})])

    def test_failed_child_or_unconfirmed_pause_stops_batch(self):
        with self.assertRaisesRegex(RuntimeError,'child failed'):
            verify_round(self.rows()+[dict(kind='error',value='child failed')])
        rows=self.rows();rows[-1]['value']['ok']=False
        with self.assertRaisesRegex(RuntimeError,'not verified'):
            verify_round(rows)

