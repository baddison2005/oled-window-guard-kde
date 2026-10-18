import copy
import unittest
from tools.desktop_constraints_batch import verify_fixture


class ConstraintsTests(unittest.TestCase):
    def snapshot(self):
        return dict(profile='fixed-bounded',platform='wayland',windows=[
            dict(title=output+' [fixed]',clientSize=[420,180],minimumSize=[420,180],maximumSize=[420,180])
            if kind=='fixed' else dict(title=output+' [bounded]',clientSize=[420,180],minimumSize=[320,140],maximumSize=[560,260])
            for output in ('DP-1','HDMI-A-1') for kind in ('fixed','bounded')])

    def test_correct_wayland_constraints_are_accepted(self):
        verify_fixture(self.snapshot())

    def test_changed_fixed_size_is_rejected(self):
        snapshot=self.snapshot();snapshot['windows'][0]['clientSize']=[421,180]
        with self.assertRaisesRegex(RuntimeError,'Fixed-size'):
            verify_fixture(snapshot)

    def test_out_of_range_bounded_size_is_rejected(self):
        snapshot=self.snapshot();snapshot['windows'][1]['clientSize']=[600,180]
        with self.assertRaisesRegex(RuntimeError,'outside constraints'):
            verify_fixture(snapshot)

    def test_wrong_backend_cannot_count_as_wayland_constraint_coverage(self):
        snapshot=self.snapshot();snapshot['platform']='xcb'
        with self.assertRaisesRegex(RuntimeError,'Wayland'):
            verify_fixture(snapshot)
