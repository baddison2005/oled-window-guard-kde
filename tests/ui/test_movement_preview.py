import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy
import unittest
from PySide6.QtWidgets import QApplication
from oled_window_guard.movement_preview import MovementPreview
from oled_window_guard.preview_data import drawing_preview


class MovementPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def drawing(self):
        a=dict(x=20,y=30,width=300,height=200)
        return dict(outputId='A',geometry=dict(x=0,y=0,width=1000,height=600),windows=[a,dict(a,x=400)],totalWindows=2,
            destinations=[{'from':a,'to':dict(a,y=300)}],windowInfo=[
                dict(appId='org.example.Editor',eligibility='eligible',reason='',moveNumber=1),
                dict(appId='steam',eligibility='excluded',reason='excluded_app',moveNumber=0)])

    def test_named_eligibility_and_striped_exclusion_are_accessible_and_render(self):
        widget=MovementPreview();widget.resize(620,240)
        widget.set_preview(self.drawing(),'A',{'org.example.editor':'Editor','steam':'Steam'})
        self.assertEqual(widget.window_label(0),('Editor','Eligible','Eligible by window settings; layout and safety checks determine the move',1))
        self.assertEqual(widget.window_label(1)[:2],('Steam','Excluded'))
        self.assertIn('Steam: Excluded',widget.accessibleDescription())
        image=widget.grab().toImage()
        self.assertFalse(image.isNull())
        self.assertEqual(len(widget._hit_regions),2)
        self.assertIn('Excluded from movement',widget._hit_regions[1][1])

    def test_malformed_metadata_cannot_become_a_display_hint(self):
        for patch in [dict(moveNumber=True),dict(moveNumber=2),dict(eligibility='movable'),dict(appId='x'*129),dict(reason='x'*65)]:
            drawing=self.drawing();drawing['windowInfo'][0].update(patch)
            with self.assertRaises(ValueError):drawing_preview(drawing,['A'],1)
        drawing=self.drawing();drawing['windowInfo']=[]
        with self.assertRaises(ValueError):drawing_preview(drawing,['A'],1)
        drawing=self.drawing();drawing['windowInfo'][0].update(identity='secret',title='private')
        self.assertNotIn('identity',drawing_preview(drawing,['A'],1)['windowInfo'][0])
        self.assertNotIn('title',drawing_preview(drawing,['A'],1)['windowInfo'][0])

    def test_legacy_rectangles_do_not_claim_to_be_movable(self):
        drawing=self.drawing();del drawing['windowInfo']
        widget=MovementPreview();widget.set_preview(drawing,'A')
        self.assertEqual(widget.window_label(0)[:2],('Application','Unavailable'))
