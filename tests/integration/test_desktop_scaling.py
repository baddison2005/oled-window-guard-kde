import unittest
from tools.desktop_scaling import require_scales,wait_baseline


class ScalingTests(unittest.TestCase):
    def test_both_fractional_scales_required(self):
        require_scales({'DP-1':dict(scale=1.25),'HDMI-A-1':dict(scale=1.5)})
        with self.assertRaisesRegex(RuntimeError,'125%'):
            require_scales({'DP-1':dict(scale=1),'HDMI-A-1':dict(scale=1.5)})

    def test_missing_output_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'Before Start'):
            require_scales({'DP-1':dict(scale=1.25)})

    def test_waits_until_saved_layout_returns(self):
        baseline={'DP-1':dict(scale=1,pos=dict(x=0,y=0))}
        now=[0];rows=[]
        wait_baseline(baseline,lambda k,v:rows.append(k),
                      lambda:baseline if now[0]>=2 else {},lambda:now[0],lambda seconds:now.__setitem__(0,now[0]+seconds))
        self.assertEqual(rows,['operator-restore-scales','scale-baseline-restored'])
        self.assertGreaterEqual(now[0],2)

    def test_wrong_layout_times_out_without_claiming_recovery(self):
        now=[0];rows=[]
        with self.assertRaisesRegex(RuntimeError,'not yet restored'):
            wait_baseline({'DP-1':dict(scale=1)},lambda k,v:rows.append(k),
                          lambda:{'DP-1':dict(scale=1.25)},lambda:now[0],lambda seconds:now.__setitem__(0,now[0]+seconds))
        self.assertNotIn('scale-baseline-restored',rows)
