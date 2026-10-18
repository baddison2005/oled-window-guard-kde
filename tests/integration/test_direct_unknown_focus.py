"""Refuse partial, focused, synthetic or wrongly attributed keyboard evidence."""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_unknown_focus as unknown
from test_direct_output_scope import matrix


def sample():
    before, after, origin_before, origin_after, _, _ = matrix()
    before.update(focusKnown=False, focusGeneration=2, unknownFocusKeyboard=0)
    after.update(focusKnown=False, focusGeneration=2, unknownFocusKeyboard=4,
                 keyboard=4, sequence=4)
    origin_after['libinput']['keyboard'] = 4
    for row in after['outputs']:
        row['events'] = next(old['events'] for old in before['outputs'] if old['id'] == row['id']) + 4
    return before, after, origin_before, origin_after, 0


class UnknownFocusTests(unittest.TestCase):
    def test_complete_physical_keyboard_marks_both_outputs_without_client_delivery(self):
        result = unknown.keyboard_result(*sample())
        self.assertEqual(result['outputEventDeltas'], {'DP-1': 4, 'HDMI-A-1': 4})
        self.assertEqual(result['unknownFocusKeyboard'], 4)
        self.assertFalse(result['clientDeliveryRequired'])
        self.assertEqual(result['clientKeyboardNotifications'], 0)

    def test_requires_two_press_release_pairs(self):
        args = list(sample())
        args[1].update(keyboard=2, sequence=2, unknownFocusKeyboard=2)
        args[3]['libinput']['keyboard'] = 2
        for row in args[1]['outputs']:
            row['events'] -= 2
        self.assertIsNone(unknown.keyboard_result(*args))

    def test_missing_or_known_focus_never_passes(self):
        for name in ('focusKnown', 'focusGeneration', 'unknownFocusKeyboard'):
            args = list(sample()); del args[1][name]
            with self.assertRaises(RuntimeError): unknown.keyboard_result(*args)
        for value in (True, None, 'false', 0):
            args = list(sample()); args[1]['focusKnown'] = value
            with self.assertRaises(RuntimeError): unknown.keyboard_result(*args)

    def test_transient_focus_change_is_refused_even_if_focus_is_unknown_again(self):
        args = list(sample()); args[1]['focusGeneration'] += 2
        with self.assertRaisesRegex(RuntimeError, 'focus changed'):
            unknown.keyboard_result(*args)

    def test_every_keyboard_callback_must_have_unknown_focus(self):
        for count in (0, 3, 5):
            args = list(sample()); args[1]['unknownFocusKeyboard'] = count
            with self.assertRaisesRegex(RuntimeError, 'entirely observed'):
                unknown.keyboard_result(*args)

    def test_unknown_focus_counter_regression_or_invalid_type_refused(self):
        args = list(sample()); args[0]['unknownFocusKeyboard'] = 5
        with self.assertRaisesRegex(RuntimeError, 'regressed'): unknown.keyboard_result(*args)
        for value in (True, -1, '2'):
            args = list(sample()); args[1]['unknownFocusKeyboard'] = value
            with self.assertRaises(RuntimeError): unknown.keyboard_result(*args)

    def test_instruction_client_must_receive_no_keys(self):
        for value in (1, True, -1, '0'):
            args = list(sample()); args[4] = value
            with self.assertRaisesRegex(RuntimeError, 'instruction client'):
                unknown.keyboard_result(*args)

    def test_mouse_input_requires_a_new_baseline(self):
        for field in ('pointer', 'button', 'axis'):
            args = list(sample()); args[1][field] += 1
            with self.assertRaises(unknown.scope.NonKeyboardInput):
                unknown.keyboard_result(*args)

    def test_synthetic_or_mixed_origin_cannot_pass(self):
        args = list(sample()); args[3]['libinput']['keyboard'] = 0
        self.assertIsNone(unknown.keyboard_result(*args))
        args[3]['libinput']['keyboard'] = 2
        with self.assertRaises(RuntimeError): unknown.keyboard_result(*args)

    def test_each_output_must_receive_every_keyboard_callback(self):
        args = list(sample()); args[1]['outputs'][1]['events'] -= 1
        with self.assertRaisesRegex(RuntimeError, 'attribution'):
            unknown.keyboard_result(*args)

    def test_topology_session_epoch_and_counter_changes_fail(self):
        for field, value in (('topologyGeneration', 1), ('sessionGeneration', 1),
                             ('epoch', 'new'), ('lockState', 'locked'), ('keyboard', -1)):
            args = list(sample()); args[1][field] = value
            with self.assertRaises(RuntimeError): unknown.keyboard_result(*args)

    def test_initial_focus_failure_retains_actual_snapshot_before_refusal(self):
        before, _, origin, _, _ = sample()
        before['focusKnown'] = True
        rows = [{'id':row['id']} for row in before['outputs']]
        process = Mock(); process.poll.return_value = None
        with TemporaryDirectory() as folder, patch.object(unknown.scope, 'coherent_state',
                                                          return_value=(before, origin, rows)):
            directory = Path(folder)
            with self.assertRaisesRegex(RuntimeError, 'not established'):
                unknown.observe(directory, {}, process, process, 45, 3)
            progress = json.loads((directory/'unknown-focus-progress.json').read_text())
            self.assertEqual(progress['validation'], 'failed')
            self.assertEqual(progress['stage'], 'initialization')
            self.assertIs(progress['baseline']['focusKnown'], True)


if __name__ == '__main__':
    unittest.main()
