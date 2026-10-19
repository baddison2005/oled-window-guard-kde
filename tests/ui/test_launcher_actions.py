"""Desktop quicklist actions dispatch to the existing controller, never a second host."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import configparser
import contextlib
import io
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

from oled_window_guard import __main__ as cli
from oled_window_guard.dashboard import create_application

ROOT = Path(__file__).resolve().parents[2]


class LauncherActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def entry(self):
        entry = configparser.ConfigParser(interpolation=None)
        entry.read(ROOT / 'packages/desktop/oled-window-guard.desktop')
        return entry

    def test_all_dock_actions_have_requested_names_order_and_controller_routes(self):
        expected = [
            ('Open', 'Open OLED Window Guard'),
            ('PauseMovement', 'Pause movement guarding'),
            ('PauseBrightness', 'Pause brightness guarding'),
            ('Pause', 'Pause all guarding'),
            ('MoveNow', 'Move Now'),
            ('Skip', 'Skip'),
            ('Restore', 'Restore previous position'),
            ('RestoreBrightness', 'Restore Brightness'),
            ('Quit', 'Quit'),
        ]
        entry = self.entry()
        self.assertEqual(entry['Desktop Entry']['Actions'].strip(';').split(';'), [x[0] for x in expected])
        for action, label in expected:
            with self.subTest(action=action):
                group = entry['Desktop Action ' + action]
                self.assertEqual(group['Name'], label)
                executable, command = shlex.split(group['Exec'])
                self.assertEqual(executable, 'oled-window-guard')
                with patch.object(cli, 'call_host', return_value={'ok': True}) as call, \
                     patch.object(cli, 'SessionHost') as host, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main([command]), 0)
                    call.assert_called_once_with(action)
                    host.assert_not_called()

    def test_missing_controller_and_refused_actions_do_not_start_or_resume_guarding(self):
        for response in (None, {'ok': False, 'reason': 'capability-unavailable'}):
            for command in ('pause-movement', 'pause-brightness', 'pause', 'move-now', 'skip', 'restore', 'restore-brightness', 'quit'):
                with self.subTest(response=response, command=command), \
                     patch.object(cli, 'call_host', return_value=response) as call, \
                     patch.object(cli, 'SessionHost') as host, \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main([command]), 2)
                    call.assert_called_once_with(cli.COMMANDS[command])
                    host.assert_not_called()
