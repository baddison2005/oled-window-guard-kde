import unittest
from tools.desktop_dimming_check import verify_hold


class DimmingHoldTests(unittest.TestCase):
    def test_short_input_clear_does_not_count_as_brightness_hold(self):
        self.assertEqual(verify_hold(1,0,6,6),'waiting')
        self.assertEqual(verify_hold(2,1,6,6),'failed')
        self.assertEqual(verify_hold(5,0,7,6),'restart')
        self.assertEqual(verify_hold(4.2,0,6,6),'verified')


if __name__=='__main__':unittest.main()
