"""Reject incomplete hotplug boundaries and require fresh two-output recovery."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_hotplug as hotplug
from run_input_direct import KINDS
from test_input_direct import backend, evidence


def geometry():
    return [
        {'id': 'HDMI-A-1', 'x': 0, 'y': 0, 'width': 1920, 'height': 1080, 'scale': 1},
        {'id': 'DP-1', 'x': 1920, 'y': 0, 'width': 3413, 'height': 1440, 'scale': 1.5},
    ]


def state():
    baseline = evidence()[0]
    baseline.update(sequence=0, topologyGeneration=0)
    baseline['outputs'] = [{'id': row['id'], 'events': 0} for row in geometry()]
    held = deepcopy(baseline)
    held.update(sequence=4, buttonKnown=True, buttonsDown=True,
                **{kind: 1 for kind in KINDS})
    held['outputs'] = [{'id': 'HDMI-A-1', 'events': 0}, {'id': 'DP-1', 'events': 4}]
    final = deepcopy(held)
    final.update(sequence=8, buttonsDown=False, **{kind: 2 for kind in KINDS})
    final['outputs'] = [{'id': 'HDMI-A-1', 'events': 0}, {'id': 'DP-1', 'events': 8}]
    origin0, origin1, origin2 = backend(), backend(), backend()
    origin1['libinput'] = {kind: 1 for kind in KINDS}
    origin2['libinput'] = {kind: 2 for kind in KINDS}
    return baseline, held, final, origin0, origin1, origin2


def reset(final, generation, rows, monotonic):
    result = deepcopy(final)
    result.update(topologyGeneration=generation, buttonKnown=False, buttonsDown=False,
                  ageMs=-1, monotonicMs=monotonic, outputGeometries=deepcopy(rows))
    result['outputs'] = [{'id': row['id'], 'events': 0} for row in rows]
    return result


class HotplugTests(unittest.TestCase):
    def test_missing_placement_readiness_fails_before_full_run_deadline(self):
        process = Mock()
        process.poll.return_value = None
        cycle = Mock(initial_geometry=geometry())
        with TemporaryDirectory() as temporary:
            with patch.object(hotplug, 'event'), patch.object(hotplug, 'publish_instruction'), \
                    patch.object(hotplug.placement, 'load_stage'), \
                    patch.object(hotplug.time, 'monotonic', side_effect=[0, 11]):
                with self.assertRaisesRegex(RuntimeError, 'readiness missing for DP-1'):
                    hotplug.recovery_stage(Path(temporary), {}, process, process, cycle,
                                           {'name':'DP-1'}, 0, 120)

    def setUp(self):
        (self.baseline, self.held, self.final, self.origin0,
         self.origin1, self.origin2) = state()
        self.clients0 = {kind: 0 for kind in KINDS}
        self.clients1 = {kind: 1 for kind in KINDS}
        self.clients2 = {kind: 2 for kind in KINDS}
        self.check = hotplug.HotplugCycle(
            self.baseline, self.origin0, self.clients0, geometry(), 3, 'HDMI-A-1')
        self.disconnected_geometry = [
            {'id': 'DP-1', 'x': 0, 'y': 0, 'width': 3413, 'height': 1440, 'scale': 1.5}]
        self.removed = reset(self.final, 1, self.disconnected_geometry, 1000)
        self.restored = reset(self.final, 2, geometry(), 3000)

    def trace(self, rows=()):
        return {'schema': 1, 'epoch': self.baseline['epoch'], 'transitions': [],
                'topologyTransitions': list(rows), 'outputGeometries': geometry()}

    def seed(self):
        self.check.advance(self.held, self.origin1, self.trace(), self.clients1,
                           geometry(), 0)
        self.assertEqual(self.check.stage, 'initial-input')
        self.check.advance(self.final, self.origin2, self.trace(), self.clients2,
                           geometry(), .1)
        self.assertEqual(self.check.stage, 'disconnect')
        self.assertIn('NOW DISCONNECT', self.check.message)

    def disconnect(self, record=None):
        record = record or self.removed
        trace = self.trace([record])
        self.check.advance(record, self.origin2, trace, self.clients2,
                           self.disconnected_geometry, 1)
        self.check.advance(record, self.origin2, trace, self.clients2,
                           self.disconnected_geometry, 2)

    def reconnect(self, record=None, returned_geometry=None):
        record = record or self.restored
        returned_geometry = returned_geometry or geometry()
        trace = self.trace([self.removed, record])
        self.check.advance(record, self.origin2, trace, self.clients2,
                           returned_geometry, 3)
        self.check.advance(record, self.origin2, trace, self.clients2,
                           returned_geometry, 4)

    def test_complete_remove_restore_sequence_passes(self):
        self.seed()
        self.disconnect()
        self.assertEqual(self.check.stage, 'reconnect')
        self.assertIn('NOW RECONNECT', self.check.message)
        self.reconnect()
        self.assertEqual(self.check.stage, 'recovered')
        self.assertEqual(self.check.result['targetConnector'], 'HDMI-A-1')
        self.assertEqual(self.check.result['survivingConnector'], 'DP-1')
        self.assertEqual(self.check.result['disconnectResetCount'], 1)
        self.assertEqual(self.check.result['reconnectResetCount'], 1)
        self.assertEqual(self.check.result['returnedOutputGeometries'], geometry())

    def test_displayport_removal_restoration_and_wrong_cable_refusal(self):
        # Activity now belongs to the HDMI survivor, not the removed DP output.
        for row in (self.held, self.final):
            row['outputs'][0]['events'], row['outputs'][1]['events'] = (
                row['outputs'][1]['events'], row['outputs'][0]['events'])
        self.check = hotplug.HotplugCycle(
            self.baseline, self.origin0, self.clients0, geometry(), 3, 'DP-1')
        self.disconnected_geometry = [geometry()[0]]
        self.removed = reset(self.final, 1, self.disconnected_geometry, 1000)
        self.restored = reset(self.final, 2, geometry(), 3000)
        self.seed()
        self.assertIn('NOW DISCONNECT DP-1 only', self.check.message)
        self.assertIn('Leave HDMI-A-1 connected', self.check.message)
        wrong = reset(self.final, 1, [geometry()[1]], 1000)
        with self.assertRaisesRegex(RuntimeError, 'HDMI-A-1 was disconnected instead of DP-1'):
            self.check.advance(wrong, self.origin2, self.trace([wrong]), self.clients2,
                               [geometry()[1]], 1)
        self.disconnect()
        self.assertIn('NOW RECONNECT the cable for DP-1', self.check.message)
        self.reconnect()
        self.assertEqual(self.check.result['targetConnector'], 'DP-1')
        self.assertEqual(self.check.result['survivingConnector'], 'HDMI-A-1')
        self.assertTrue(hotplug.same_geometry(
            self.check.result['returnedOutputGeometries'], geometry()))

    def test_connector_selection_refuses_missing_or_ambiguous_targets(self):
        self.assertEqual(hotplug.select_target(geometry(), 'DP'), 'DP-1')
        self.assertEqual(hotplug.select_target(geometry(), 'HDMI'), 'HDMI-A-1')
        for rows, connector in [([], 'DP'), ([geometry()[0]], 'DP'),
                                ([geometry()[1], dict(geometry()[1], id='DP-2')], 'DP'),
                                (geometry(), 'invalid')]:
            with self.subTest(rows=rows, connector=connector), self.assertRaises(RuntimeError):
                hotplug.select_target(rows, connector)

    def test_restoration_accepts_changed_enumeration_order(self):
        self.seed(); self.disconnect()
        reversed_geometry = list(reversed(geometry()))
        restored = reset(self.final, 2, reversed_geometry, 3000)
        self.reconnect(restored, reversed_geometry)
        self.assertEqual(self.check.stage, 'recovered')
        self.assertTrue(hotplug.same_geometry(
            self.check.result['returnedOutputGeometries'], geometry()))

    def test_ten_reset_bursts_for_removal_and_reconnect_pass(self):
        self.seed()
        removal = [reset(self.final, gen, self.disconnected_geometry, 1000 + gen)
                   for gen in range(1, 11)]
        for now in (1, 2):
            self.check.advance(removal[-1], self.origin2, self.trace(removal), self.clients2,
                               self.disconnected_geometry, now)
        self.assertEqual(self.check.stage, 'reconnect')
        restoration = [reset(self.final, gen, geometry(), 3000 + gen)
                       for gen in range(11, 21)]
        for now in (3, 4):
            self.check.advance(restoration[-1], self.origin2, self.trace(removal + restoration),
                               self.clients2, geometry(), now)
        self.assertEqual(self.check.stage, 'recovered')
        self.assertEqual(self.check.result['disconnectResetCount'], 10)
        self.assertEqual(self.check.result['reconnectResetCount'], 10)

    def test_trace_limit_and_evicted_generation_still_fail(self):
        self.seed()
        records = [reset(self.final, gen, self.disconnected_geometry, 1000 + gen)
                   for gen in range(1, hotplug.TOPOLOGY_TRACE_LIMIT + 2)]
        with self.assertRaisesRegex(RuntimeError, 'bounded topology'):
            self.check.advance(records[-1], self.origin2, self.trace(records), self.clients2,
                               self.disconnected_geometry, 1)
        with self.assertRaisesRegex(RuntimeError, 'missing or truncated'):
            self.check.advance(records[-1], self.origin2, self.trace(records[-hotplug.TOPOLOGY_TRACE_LIMIT:]),
                               self.clients2, self.disconnected_geometry, 1)

    def test_reset_burst_with_internal_generation_gap_fails(self):
        self.seed()
        records = [reset(self.final, gen, self.disconnected_geometry, 1000 + gen)
                   for gen in range(1, 11)]
        records[4] = deepcopy(records[5])
        with self.assertRaisesRegex(RuntimeError, 'boundary'):
            self.check.advance(records[-1], self.origin2, self.trace(records), self.clients2,
                               self.disconnected_geometry, 1)

    def test_captured_physical_overflow_remains_incomplete_with_larger_limit(self):
        path = Path(__file__).resolve().parents[2] / 'docs/evidence/2026-10-01-direct-hotplug-attempt3.json'
        progress = json.loads(path.read_text())['progress']
        with self.assertRaisesRegex(RuntimeError, 'expected=10, recorded=\\[5, 6, 7, 8, 9, 10, 11, 12\\]'):
            hotplug.validate_current(progress['baseline'], progress['current'],
                                     progress['environment'], progress['transitions'],
                                     progress['outputGeometries'], 3)

    def test_initial_input_requires_each_observer_origin_client_kind_and_release(self):
        for kind in KINDS:
            check = hotplug.HotplugCycle(
                self.baseline, self.origin0, self.clients0, geometry(), 3, 'HDMI-A-1')
            current = dict(self.final, **{kind: 0})
            check.advance(current, self.origin2, self.trace(), self.clients2, geometry(), 0)
            self.assertEqual(check.stage, 'initial-input')
            origin = deepcopy(self.origin2); origin['libinput'][kind] = 0
            check.advance(self.final, origin, self.trace(), self.clients2, geometry(), 0)
            self.assertEqual(check.stage, 'initial-input')
            clients = dict(self.clients2, **{kind: 0})
            check.advance(self.final, self.origin2, self.trace(), clients, geometry(), 0)
            self.assertEqual(check.stage, 'initial-input')
        self.check.advance(self.final, self.origin2, self.trace(), self.clients2, geometry(), 0)
        self.assertEqual(self.check.stage, 'initial-input')

    def test_topology_change_before_complete_initial_input_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'before initial'):
            self.check.advance(self.removed, self.origin2, self.trace([self.removed]),
                               self.clients2, self.disconnected_geometry, 1)

    def test_wrong_connector_disconnect_fails(self):
        self.seed()
        wrong_geometry = [geometry()[0]]
        wrong = reset(self.final, 1, wrong_geometry, 1000)
        with self.assertRaisesRegex(RuntimeError, 'DP-1 was disconnected instead of HDMI-A-1'):
            self.check.advance(wrong, self.origin2, self.trace([wrong]),
                               self.clients2, wrong_geometry, 1)

    def test_missing_or_truncated_topology_record_fails(self):
        self.seed()
        with self.assertRaisesRegex(RuntimeError, 'missing or truncated'):
            self.check.advance(self.removed, self.origin2, self.trace(), self.clients2,
                               self.disconnected_geometry, 1)
        current = dict(self.removed, topologyGeneration=9)
        with self.assertRaisesRegex(RuntimeError, 'missing or truncated'):
            self.check.advance(current, self.origin2, self.trace([self.removed]), self.clients2,
                               self.disconnected_geometry, 1)

    def test_failure_retains_rejected_sample_and_does_not_accept_it(self):
        self.seed()
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            trace = self.trace()
            with self.assertRaisesRegex(RuntimeError, 'baseline=0, current=1, expected=1, recorded=\\[\\]'):
                hotplug.advance_with_evidence(
                    directory, self.check, self.removed, self.origin2, trace,
                    self.clients2, self.disconnected_geometry, 1)
            retained = json.loads((directory / 'hotplug-progress.json').read_text())
            self.assertEqual(retained['validation'], 'failed')
            self.assertEqual(retained['stageBeforeValidation'], 'disconnect')
            self.assertEqual(retained['current'], self.removed)
            self.assertEqual(retained['transitions'], trace)
            self.assertEqual(retained['outputGeometries'], self.disconnected_geometry)
            self.assertIn('recorded=[]', retained['error'])
            self.assertIsNone(retained['result'])
            self.assertIsNone(self.check.result)
            self.assertFalse((directory / 'hotplug-progress.tmp').exists())

    def test_every_reset_must_clear_button_age_and_output_events(self):
        self.seed()
        for change in ({'buttonKnown': True}, {'buttonsDown': True}, {'ageMs': 0}):
            bad = dict(self.removed, **change)
            with self.assertRaisesRegex(RuntimeError, 'clear activity'):
                self.check.advance(bad, self.origin2, self.trace([bad]), self.clients2,
                                   self.disconnected_geometry, 1)
        bad = deepcopy(self.removed); bad['outputs'][0]['events'] = 1
        with self.assertRaisesRegex(RuntimeError, 'clear activity'):
            self.check.advance(bad, self.origin2, self.trace([bad]), self.clients2,
                               self.disconnected_geometry, 1)

    def test_reset_output_identity_and_geometry_must_agree(self):
        self.seed()
        bad = deepcopy(self.removed); bad['outputs'][0]['id'] = 'HDMI-A-1'
        with self.assertRaisesRegex(RuntimeError, 'identities disagree'):
            self.check.advance(bad, self.origin2, self.trace([bad]), self.clients2,
                               self.disconnected_geometry, 1)

    def test_session_epoch_activity_lock_and_sleep_changes_fail(self):
        self.seed()
        changes = ({'epoch': 'other'}, {'sessionGeneration': 1},
                   {'sessionActivity': 'inactive'}, {'lockState': 'locked'},
                   {'sleepState': 'sleeping'})
        for change in changes:
            bad = dict(self.removed, **change)
            with self.assertRaises(RuntimeError):
                self.check.advance(bad, self.origin2, self.trace([self.removed]),
                                   self.clients2, self.disconnected_geometry, 1)

    def test_counter_regression_and_unordered_boundaries_fail(self):
        self.seed()
        bad = dict(self.removed, pointer=1)
        with self.assertRaisesRegex(RuntimeError, 'regressed'):
            self.check.advance(bad, self.origin2, self.trace([bad]), self.clients2,
                               self.disconnected_geometry, 1)
        self.disconnect()
        bad = dict(self.restored, monotonicMs=500)
        with self.assertRaisesRegex(RuntimeError, 'unordered'):
            self.check.advance(bad, self.origin2, self.trace([self.removed, bad]),
                               self.clients2, geometry(), 3)

    def test_disconnect_needs_a_record_of_survivor_only(self):
        self.seed()
        record = reset(self.final, 1, geometry(), 1000)
        current = deepcopy(self.removed)
        with self.assertRaisesRegex(RuntimeError, 'target-disconnected'):
            self.check.advance(current, self.origin2, self.trace([record]), self.clients2,
                               self.disconnected_geometry, 1)
            self.check.advance(current, self.origin2, self.trace([record]), self.clients2,
                               self.disconnected_geometry, 2)

    def test_reconnect_needs_exact_initial_geometry_and_reset(self):
        self.seed(); self.disconnect()
        changed = deepcopy(geometry()); changed[1]['scale'] = 1
        bad = reset(self.final, 2, changed, 3000)
        with self.assertRaisesRegex(RuntimeError, 'restore'):
            self.check.advance(bad, self.origin2, self.trace([self.removed, bad]),
                               self.clients2, changed, 3)
            self.check.advance(bad, self.origin2, self.trace([self.removed, bad]),
                               self.clients2, changed, 4)
        check = hotplug.HotplugCycle(
            self.baseline, self.origin0, self.clients0, geometry(), 3, 'HDMI-A-1')
        check.advance(self.held, self.origin1, self.trace(), self.clients1, geometry(), 0)
        check.advance(self.final, self.origin2, self.trace(), self.clients2, geometry(), .1)
        check.advance(self.removed, self.origin2, self.trace([self.removed]), self.clients2,
                      self.disconnected_geometry, 1)
        check.advance(self.removed, self.origin2, self.trace([self.removed]), self.clients2,
                      self.disconnected_geometry, 2)
        record = reset(self.final, 2, self.disconnected_geometry, 3000)
        with self.assertRaisesRegex(RuntimeError, 'restored'):
            check.advance(self.restored, self.origin2, self.trace([self.removed, record]),
                          self.clients2, geometry(), 3)
            check.advance(self.restored, self.origin2, self.trace([self.removed, record]),
                          self.clients2, geometry(), 4)

    def test_unexpected_connector_and_duplicate_identity_fail(self):
        self.seed(); self.disconnect()
        unexpected = [{'id': 'USB-C-1', 'x': 0, 'y': 0, 'width': 800, 'height': 600, 'scale': 1}]
        record = reset(self.final, 2, unexpected, 3000)
        with self.assertRaisesRegex(RuntimeError, 'unexpected'):
            self.check.advance(record, self.origin2, self.trace([self.removed, record]),
                               self.clients2, unexpected, 3)
        duplicate = [geometry()[0], dict(geometry()[1], id='HDMI-A-1')]
        with self.assertRaisesRegex(RuntimeError, 'Invalid physical output'):
            hotplug.valid_geometry(duplicate)

    def test_invalid_backend_and_ambiguous_target_fail(self):
        with self.assertRaises(RuntimeError):
            hotplug.HotplugCycle(self.baseline, dict(self.origin0, directMode=False),
                                 self.clients0, geometry(), 3, 'HDMI-A-1')
        with self.assertRaises(RuntimeError):
            hotplug.HotplugCycle(self.baseline, self.origin0, self.clients0,
                                 geometry(), 3, 'missing')


if __name__ == '__main__':
    unittest.main()
