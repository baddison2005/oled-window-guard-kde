"""Physical test apps must be separately managed and retain journal diagnostics."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from tools import desktop_acceptance_launch as driver


class LaunchTests(unittest.TestCase):
    def test_gtk_role_uses_separate_wayland_only_script(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(driver.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr='')):
            result=driver.launch('gtk-windows',receipt=Path(directory)/'gtk.jsonl')
            self.assertTrue(result['command'][1].endswith('desktop_gtk_windows.py'))
            self.assertNotIn('--backend',result['command'])
            with self.assertRaisesRegex(ValueError,'Wayland'):driver.launch('gtk-windows',receipt=Path(directory)/'bad.jsonl',backend='xwayland')
    def test_gtk4_role_selects_version_and_rejects_other_backend(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(driver.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr='')):
            result=driver.launch('gtk4-windows',receipt=Path(folder)/'fixture.jsonl')
            self.assertEqual(result['command'][-2:],['--gtk-version','4'])
            with self.assertRaisesRegex(ValueError,'Wayland'):driver.launch('gtk4-windows',receipt=Path(folder)/'bad.jsonl',backend='xwayland')

    def test_xwayland_backend_is_explicit_in_fixture_command(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(driver.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr='')):
            result=driver.launch('windows',receipt=Path(directory)/'fixture.jsonl',backend='xwayland')
        self.assertEqual(result['command'][-2:],['--backend','xwayland'])

    def test_constraint_profile_and_wayland_backend_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(driver.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr='')):
            result=driver.launch('windows',receipt=Path(directory)/'fixture.jsonl',backend='wayland',profile='fixed-bounded')
        self.assertEqual(result['command'][-4:],['--backend','wayland','--profile','fixed-bounded'])

    def test_roles_use_distinct_transient_units_without_restart_or_autostart(self):
        with patch.object(driver.subprocess,"run",return_value=SimpleNamespace(returncode=0,stderr="")) as run:
            dashboard=driver.launch("dashboard")
            control=driver.launch("control",output="HDMI-A-1",test="quit-warning")
        self.assertNotEqual(dashboard['unit'],control['unit'])
        for call in run.call_args_list:
            args=call.args[0]
            self.assertEqual(args[:2],['systemd-run','--user'])
            self.assertIn('--property=Restart=no',args)
            self.assertNotIn('--scope',args)
            self.assertNotIn('--collect',args)
            self.assertFalse(call.kwargs.get('shell',False))
        self.assertEqual(dashboard['command'][-1],'open')
        self.assertEqual(control['command'][-4:],['--output','HDMI-A-1','--test','quit-warning'])

    def test_environment_is_limited_to_desktop_connection_fields(self):
        with patch.dict(driver.os.environ,{'WAYLAND_DISPLAY':'wayland-test','SOME_API_KEY':'private-test-value'},clear=True), \
             patch.object(driver.subprocess,'run',return_value=SimpleNamespace(returncode=0,stderr="")) as run:
            driver.launch('dashboard')
        args=run.call_args.args[0]
        self.assertIn('--setenv=WAYLAND_DISPLAY=wayland-test',args)
        self.assertFalse(any('SOME_API_KEY' in value or 'private-test-value' in value for value in args))

    def test_failed_manager_launch_is_reported(self):
        with patch.object(driver.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='bus unavailable')):
            with self.assertRaisesRegex(RuntimeError,'bus unavailable'):
                driver.launch('dashboard')

    def test_existing_fixture_receipt_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt=Path(directory)/'receipt.jsonl'
            receipt.write_text('existing evidence')
            with patch.object(driver.subprocess,'run') as run, self.assertRaisesRegex(ValueError,'already exists'):
                driver.launch('windows',receipt=receipt)
            run.assert_not_called()
            self.assertEqual(receipt.read_text(),'existing evidence')

    def test_fixture_requires_receipt_before_launch(self):
        with patch.object(driver.subprocess,'run') as run, self.assertRaisesRegex(ValueError,'unique receipt'):
            driver.launch('windows')
        run.assert_not_called()


if __name__=='__main__':unittest.main()
