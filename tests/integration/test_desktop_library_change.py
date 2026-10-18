from pathlib import Path
import tempfile
import unittest
from oled_window_guard.layout_provider import LayoutProvider
from tools.desktop_library_change import PaddingChange
from tools.desktop_library_batch import verify_round


class LibraryChangeTests(unittest.TestCase):
    def setUp(self):
        self.raw=dict(CustomLayouts='[]',CustomGroups='[]',GroupOrder='[]',LayoutPadding='0')
        self.writes=[]
        self.rows=[]
        self.provider=LayoutProvider(runner=lambda argv,timeout:self.raw[argv[argv.index('--key')+1]],debounce_seconds=0)
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        def write(value):
            self.writes.append(value);self.raw['LayoutPadding']=value
        self.change=PaddingChange(Path(self.folder.name)/'backup.json',lambda k,v:self.rows.append((k,v)),self.provider,write)

    def test_changes_one_key_then_recovers_exact_original_revision(self):
        original=dict(self.raw)
        self.change.apply()
        self.assertEqual(self.raw['LayoutPadding'],'1')
        self.change.restore()
        self.assertEqual(self.raw,original)
        self.assertEqual(self.writes,['1','0'])
        self.change.restore()
        self.assertEqual(self.writes,['1','0'])

    def test_external_change_before_edit_prevents_write(self):
        self.raw['GroupOrder']='["new"]'
        with self.assertRaisesRegex(RuntimeError,'before temporary'):
            self.change.apply()
        self.assertEqual(self.writes,[])

    def test_external_padding_edit_is_preserved(self):
        self.change.apply();self.raw['LayoutPadding']='7'
        with self.assertRaisesRegex(RuntimeError,'refusing to overwrite'):
            self.change.restore()
        self.assertEqual(self.raw['LayoutPadding'],'7')

    def test_other_library_edits_survive_cleanup_but_fail_revision_check(self):
        self.change.apply();self.raw['GroupOrder']='["new"]'
        with self.assertRaisesRegex(RuntimeError,'other library data changed'):
            self.change.restore()
        self.assertEqual(self.raw['GroupOrder'],'["new"]')
        self.assertEqual(self.raw['LayoutPadding'],'0')

    def test_batch_rejects_missing_restoration_or_child_error(self):
        rows=[dict(kind=k,value={}) for k in ('library-changed','library-warning-result','round-result','final-pause')]
        with self.assertRaisesRegex(RuntimeError,'not verified'):
            verify_round(rows,'imported-change-warning')
        with self.assertRaisesRegex(RuntimeError,'cleanup failed'):
            verify_round([dict(kind='error',value='cleanup failed')],'imported-change-restore')
