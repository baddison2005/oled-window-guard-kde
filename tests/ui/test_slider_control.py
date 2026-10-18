import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from oled_window_guard.slider_control import SliderControl


class SliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_numeric_edit_and_drag_update_same_canonical_percentage(self):
        control=SliderControl(dict(minimum=0,maximum=.25),.10,multiplier=100,suffix=' %',step=.01)
        changed=[];control.valueChanged.connect(changed.append)
        self.assertEqual(control.number.value(),10)
        control.number.setValue(12.5)
        self.assertEqual(control.value(),.125)
        self.assertEqual(changed,[.125])
        control.slider.setValue(20)
        self.assertEqual(control.value(),.20)
        self.assertEqual(control.number.value(),20)
        self.assertEqual(changed,[.125,.20])
        self.assertFalse(control.slider.hasTracking())

    def test_signal_blocking_does_not_save_and_fade_snaps_to_half_seconds(self):
        control=SliderControl(dict(minimum=0,maximum=3),0,suffix=' s',step=.5)
        changed=[];control.valueChanged.connect(changed.append)
        control.blockSignals(True);control.setValue(1.5);control.blockSignals(False)
        self.assertEqual(changed,[])
        self.assertEqual(control.slider.value(),3)
        control.number.setValue(.7)
        self.assertEqual(control.value(),.5)
        self.assertEqual(changed,[.5])
