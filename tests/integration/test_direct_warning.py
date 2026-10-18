"""Reject incomplete, out-of-order and synthetic warning evidence."""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_warning as warning
import run_input_direct as direct


class WarningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.log = self.directory / 'kwin.log'
        self.log.write_text(warning.SHOWN + '\n')
        self.check = warning.WarningCheck(self.directory, {})
        self.counts = {kind: 1 for kind in warning.KINDS}
        self.current = {'buttonKnown': True, 'buttonsDown': False}
        self.before = {'libinput': {kind: 0 for kind in warning.KINDS}}
        self.origin = {'libinput': {kind: 2 for kind in warning.KINDS}}

    def advance(self, **kwargs):
        args = dict(client_counts=self.counts, held=True, released=True,
                    current=self.current, origin=self.origin, origin_before=self.before)
        args.update(kwargs)
        self.check.advance(**args)

    def test_outside_input_arms_visible_instruction(self):
        self.advance()
        self.assertTrue(self.check.armed)
        self.assertIn('NOW CLICK SKIP', (self.directory/'instructions.txt').read_text())
        self.assertFalse(self.check.finished)

    def test_requires_each_client_and_libinput_kind(self):
        for kind in warning.KINDS:
            self.advance(client_counts=dict(self.counts, **{kind: 0}))
            self.assertFalse(self.check.armed)
            self.advance(origin={'libinput': dict(self.origin['libinput'], **{kind: 0})})
            self.assertFalse(self.check.armed)

    def test_requires_held_then_released_and_known_release(self):
        for args in ({'held': False}, {'released': False},
                     {'current': dict(self.current, buttonsDown=True)},
                     {'current': dict(self.current, buttonKnown=False)}):
            self.advance(**args)
            self.assertFalse(self.check.armed)

    def test_premature_skip_fails(self):
        self.log.write_text(warning.SHOWN+'\n'+warning.SKIP+'\n'+warning.HIDDEN)
        with self.assertRaisesRegex(RuntimeError, 'before outside-input'):
            self.advance()

    def test_outside_dismissal_focus_loss_expiry_fail(self):
        for reason in ('outside-input-dismissed-warning', 'focus-changed', 'expired', 'surface-reappeared'):
            self.log.write_text(warning.SHOWN+'\n'+warning.PREFIX+'FAIL '+reason)
            with self.assertRaises(RuntimeError):
                self.advance()

    def test_duplicate_and_reordered_callbacks_fail(self):
        for log in (warning.SHOWN+'\n'+warning.SHOWN, warning.SKIP+'\n'+warning.SHOWN,
                    warning.SHOWN+'\n'+warning.HIDDEN, warning.HIDDEN+'\n'+warning.SKIP):
            with self.assertRaises(RuntimeError):
                warning.check_log(log)

    def test_skip_needs_separate_physical_press_release(self):
        self.advance()
        self.log.write_text(warning.SHOWN+'\n'+warning.SKIP+'\n'+warning.HIDDEN)
        self.advance()
        self.assertFalse(self.check.finished)
        self.origin['libinput']['button'] += 1
        self.advance()
        self.assertFalse(self.check.finished)
        self.origin['libinput']['button'] += 1
        self.advance(current=dict(self.current, buttonsDown=True))
        self.assertFalse(self.check.finished)
        self.advance()
        self.assertTrue(self.check.finished)

    def test_incomplete_check_cannot_finish(self):
        with self.assertRaises(RuntimeError):
            self.check.finish()

    def test_unload_refusal_and_still_loaded_fail(self):
        log='\n'.join((warning.SHOWN,warning.SKIP,warning.HIDDEN))
        for replies in ([False], [True, True]):
            with patch.object(warning,'scripting', side_effect=replies), self.assertRaises(RuntimeError):
                warning.unload_fixture({}, log)

    def test_complete_check_unloads_one_surface(self):
        self.advance()
        self.log.write_text('\n'.join((warning.SHOWN,warning.SKIP,warning.HIDDEN)))
        self.origin['libinput']['button'] += 2
        self.advance()
        with patch.object(warning,'scripting', side_effect=[True,False]), patch.object(warning.time,'sleep'):
            self.assertEqual(self.check.finish()['warningCount'],1)

    def test_gdbus_boolean_and_script_id_replies(self):
        for reply, expected in (('(true,)\n', True), ('(false,)\n', False), ('(3,)\n', 3)):
            with patch.object(warning.subprocess, 'run', return_value=SimpleNamespace(stdout=reply)):
                self.assertEqual(warning.scripting({}, 'test'), expected)

    def test_private_environment_strips_host_and_injection_variables(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(direct.os.environ, {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'host',
                            'OWG_INPUT_TEST_TRIGGER':'unsafe','KWIN_DRM_DEVICES':'host',
                            'XAUTHORITY':'host','SESSION_MANAGER':'host'}):
                env=direct.private_environment(Path(temporary),Path('/tmp/probe'),True)
            for key in ('DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','SESSION_MANAGER',
                        'OWG_INPUT_TEST_TRIGGER','KWIN_DRM_DEVICES'):
                self.assertNotIn(key,env)
            self.assertEqual(env['OWG_INPUT_PROBE_DIRECT'],'1')


if __name__ == '__main__':
    unittest.main()
