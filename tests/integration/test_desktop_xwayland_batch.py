import unittest
from tools.desktop_xwayland_batch import verify_round


class XWaylandBatchTests(unittest.TestCase):
    def rows(self):
        return [dict(kind='movement-result',value=dict(result='committed',warningSeen=True)),
                dict(kind='restore-result',value=dict(result='committed',inverseConsumed=True,warningSeen=True)),
                dict(kind='final-pause',value=dict(ok=True))]

    def test_forward_and_restore_commits_are_required(self):
        verify_round(self.rows())
        rows=self.rows();rows[0]['value']['result']='cancelled: native-input'
        with self.assertRaisesRegex(RuntimeError,'Forward movement'):
            verify_round(rows)

    def test_unconsumed_inverse_stops_batch(self):
        rows=self.rows();rows[1]['value']['inverseConsumed']=False
        with self.assertRaisesRegex(RuntimeError,'not verified'):
            verify_round(rows)

