import configparser
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from oled_window_guard.autostart import LoginStartup, ENTRY


class LoginStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.startup = LoginStartup(self.home)

    def test_read_is_side_effect_free_and_enable_disable_round_trip(self):
        self.assertFalse(self.startup.read().enabled)
        self.assertFalse(self.startup.path.parent.exists())
        self.assertTrue(self.startup.set_enabled(True).enabled)
        config = configparser.ConfigParser(); config.read(self.startup.path)
        self.assertEqual(config['Desktop Entry']['Exec'], '/usr/bin/oled-window-guard open')
        self.assertEqual(config['Desktop Entry']['TryExec'], '/usr/bin/oled-window-guard')
        self.assertEqual(config['Desktop Entry']['OnlyShowIn'], 'KDE;')
        self.assertNotIn('start', config['Desktop Entry']['Exec'])
        self.assertFalse(self.startup.set_enabled(False).enabled)
        self.assertFalse(self.startup.path.exists())
        self.assertFalse(list(self.startup.path.parent.glob('.*.desktop')))

    def test_foreign_entry_and_symlink_are_never_modified(self):
        self.startup.path.parent.mkdir()
        for content in ('[Desktop Entry]\nType=Application\nExec=some-other-app\n', 'broken'):
            self.startup.path.write_text(content)
            self.assertFalse(self.startup.read().editable)
            for enabled in (True,False):
                with self.assertRaises(OSError): self.startup.set_enabled(enabled)
                self.assertEqual(self.startup.path.read_text(),content)
        self.startup.path.unlink()
        target = self.home/'other.desktop'; target.write_text(ENTRY)
        self.startup.path.symlink_to(target)
        with self.assertRaises(OSError): self.startup.set_enabled(False)
        self.assertEqual(target.read_text(),ENTRY)

    def test_external_disable_is_reflected_and_atomic_failure_preserves_entry(self):
        self.startup.set_enabled(True)
        self.startup.path.write_text(ENTRY.replace('Hidden=false', 'Hidden=true'))
        self.assertFalse(self.startup.read().enabled)
        before = self.startup.path.read_bytes()
        with patch('oled_window_guard.autostart.os.replace',side_effect=OSError('write refused')):
            with self.assertRaises(OSError):self.startup.set_enabled(True)
        self.assertEqual(self.startup.path.read_bytes(),before)
        self.assertEqual(len(list(self.startup.path.parent.iterdir())),1)

    def test_default_path_respects_xdg_config_home(self):
        with patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.home)}):
            self.assertEqual(LoginStartup().path,self.startup.path)
