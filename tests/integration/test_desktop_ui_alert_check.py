import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from oled_window_guard import preferences
from tools import desktop_ui_alert_check as check
from tools import desktop_acceptance_launch as launch


class UiAlertCheckTests(unittest.TestCase):
    def test_restore_preserves_original_bytes_and_acknowledges_revision(self):
        from oled_window_guard.session import revision
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            original=preferences.defaults();raw=b'{"schemaVersion":1,"paused":true}\n'
            preferences.save_settings(path,dict(original,soundEnabled=True))
            with patch('oled_window_guard.session.call_host',return_value={'ok':True}) as call:
                check.restore_settings(path,original,raw)
            self.assertEqual(path.read_bytes(),raw)
            call.assert_called_once_with('SettingsChanged',{'revision':revision(original)})

    def test_restore_removes_test_file_if_original_was_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            with patch('oled_window_guard.session.call_host',return_value={'ok':True}):
                check.restore_settings(path,preferences.defaults(),None)
            self.assertFalse(path.exists())

    def test_unacknowledged_restore_is_failure(self):
        with tempfile.TemporaryDirectory() as folder,patch('oled_window_guard.session.call_host',return_value={'ok':False}):
            with self.assertRaisesRegex(RuntimeError,'not acknowledged'):
                check.restore_settings(Path(folder)/'settings.json',preferences.defaults(),None)

    def test_failure_retains_specific_reason_and_worker_stderr(self):
        with tempfile.TemporaryDirectory() as folder:
            receipt=Path(folder)/'run.jsonl'
            receipt.write_text('{"kind":"error","value":"Forward movement not committed after warning"}\nnot-json\n')
            detail=check.failure_details(receipt,'worker traceback')
            self.assertIn('Forward movement not committed',detail)
            self.assertIn('worker traceback',detail)

    def test_missing_receipt_still_reports_startup_error(self):
        self.assertEqual(check.failure_details(None,'ImportError: unavailable module'),'ImportError: unavailable module')

    def test_receipts_survive_tmp_cleanup(self):
        self.assertTrue(check.RECEIPT_DIR.is_relative_to(check.ROOT / 'docs/evidence'))

    def test_launcher_prepares_panel_without_starting_worker(self):
        with patch.object(launch.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr='')):
            result=launch.launch('control',test='ui-alert')
        self.assertTrue(result['command'][1].endswith('desktop_ui_alert_check.py'))
        self.assertNotIn('--run',result['command'])
        self.assertNotIn('--receipt',result['command'])


if __name__=='__main__':unittest.main()
