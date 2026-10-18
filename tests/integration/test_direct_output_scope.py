"""Geometry, origin, privacy and coherent sampling refusals for physical scope."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_output_scope as scope
from test_input_direct import backend, evidence


def outputs():
    return [{'id': 'DP-1', 'x': 0, 'y': 0, 'width': 5120, 'height': 2160, 'scale': 1},
            {'id': 'HDMI-A-1', 'x': 5120, 'y': 1080, 'width': 1920, 'height': 1080, 'scale': 1}]


def matrix(expected=('DP-1',)):
    before = evidence()[0]
    before['outputs'] = [{'id': row['id'], 'events': 10} for row in outputs()]
    after = deepcopy(before)
    after['keyboard'] = 2
    after['sequence'] = 2
    for row in after['outputs']:
        row['events'] += 2 if row['id'] in expected else 0
    origin_before, origin_after = backend(), backend()
    origin_after['libinput']['keyboard'] = 2
    return [before, after, origin_before, origin_after, 1, list(expected)]


class ScopeTests(unittest.TestCase):
    def test_actual_host_offset_geometry_yields_two_contained_and_spanning(self):
        stages = scope.plan(outputs())
        self.assertEqual([s['expectedOutputs'] for s in stages], [['DP-1'], ['HDMI-A-1'], ['DP-1', 'HDMI-A-1']])
        for stage in stages:
            self.assertEqual(scope.intersections(stage['frame'], outputs()), set(stage['expectedOutputs']))

    def test_order_independent_negative_origin_and_vertical_shared_edge(self):
        rows = outputs()
        rows[0].update(x=-5120, y=0)
        rows[1].update(x=0, y=1080)
        self.assertEqual(scope.plan(list(reversed(rows)))[0]['name'], 'DP-1')
        rows[0].update(x=0, y=-2160)
        rows[1].update(x=100, y=0)
        self.assertEqual(len(scope.intersections(scope.plan(rows)[2]['frame'], rows)), 2)

    def test_refuses_missing_duplicate_small_mirrored_and_gapped_outputs(self):
        variants = [outputs()[:1], outputs()+[outputs()[0]], [outputs()[0],outputs()[0]]]
        small, mirror, gap = outputs(), outputs(), outputs()
        small[1]['height'] = 300
        mirror[1].update(x=0, y=0)
        gap[1]['x'] += 100
        for rows in variants+[small,mirror,gap]:
            with self.assertRaises(RuntimeError): scope.plan(rows)

    def test_frame_requires_actual_owned_focus_and_expected_intersection(self):
        stage = scope.plan(outputs())[2]
        record = {'frame': stage['frame'], 'focusOwned': True}
        self.assertEqual(scope.validate_frame(record, stage, outputs()), stage['frame'])
        for bad in [dict(record,focusOwned=False), dict(record,frame=dict(stage['frame'],x=0)),
                    dict(record,frame=dict(stage['frame'],width=float('nan'))), {'focusOwned':True}]:
            with self.assertRaises(RuntimeError): scope.validate_frame(bad, stage, outputs())

    def test_contained_and_spanning_physical_scope_pass(self):
        for expected in [('DP-1',), ('HDMI-A-1',), ('DP-1','HDMI-A-1')]:
            result = scope.keyboard_result(*matrix(expected))
            self.assertEqual(result['libinputKeyboard'],2)
            self.assertEqual(result['outputEventDeltas'], {row['id']:2 if row['id'] in expected else 0 for row in outputs()})

    def test_keyboard_requires_fresh_client_observer_and_physical_counts(self):
        for target in ('observer','origin','client'):
            args=matrix()
            if target=='observer': args[1]['keyboard']=0
            elif target=='origin': args[3]['libinput']['keyboard']=0
            else: args[4]=0
            self.assertIsNone(scope.keyboard_result(*args))

    def test_non_keyboard_activity_restarts_baseline(self):
        for k in ('pointer','button','axis'):
            for physical in (False,True):
                args=matrix()
                (args[3]['libinput'] if physical else args[1])[k]=1
                with self.assertRaises(scope.NonKeyboardInput): scope.keyboard_result(*args)

    def test_wrong_output_scope_and_partial_physical_origin_fail(self):
        for change in ('scope','origin'):
            args=matrix()
            if change=='scope': args[1]['outputs'][1]['events'] += 2
            else: args[3]['libinput']['keyboard'] = 3
            with self.assertRaises(RuntimeError): scope.keyboard_result(*args)

    def test_lifecycle_changes_fail(self):
        for k,v in [('epoch','new'),('sessionGeneration',1),('topologyGeneration',1),('lockState','locked'),('sessionActivity','inactive')]:
            args=matrix(); args[1][k]=v
            with self.assertRaises(RuntimeError): scope.keyboard_result(*args)

    def test_bad_counts_and_identities_fail(self):
        for bad in (True,-1,'2'):
            args=matrix(); args[3]['libinput']['keyboard']=bad
            with self.assertRaises(RuntimeError): scope.keyboard_result(*args)
            args=matrix(); args[4]=bad
            with self.assertRaises(RuntimeError): scope.keyboard_result(*args)
        args=matrix(); args[1]['outputs'][1]['id']='DP-1'
        with self.assertRaises(RuntimeError): scope.keyboard_result(*args)
        args=matrix(); args[5]=[]
        with self.assertRaises(RuntimeError): scope.keyboard_result(*args)

    def test_readiness_duplicates_and_focus_failure_cannot_pass(self):
        ready=scope.PREFIX+'READY 0 '+json.dumps({'focusOwned':True})
        self.assertTrue(scope.check_log(ready,0)['focusOwned'])
        self.assertIsNone(scope.check_log(ready,1))
        for log in (ready+'\n'+ready, ready+'\n'+scope.PREFIX+'FAIL 0 owned-focus-lost'):
            with self.assertRaises(RuntimeError): scope.check_log(log,0)

    def test_snapshot_origin_read_race_waits_for_coherent_sample(self):
        first,last=matrix()[:2]
        first['sequence']=0; last['sequence']=2
        trace={'schema':1,'epoch':first['epoch'],'outputGeometries':outputs()}
        with patch.object(scope,'snapshot',side_effect=[first,last]), patch.object(scope,'environment',return_value=backend()), patch.object(scope,'transitions',return_value=trace):
            self.assertIsNone(scope.coherent_state({}))

    def test_coherent_sample_can_return_the_exact_transition_read(self):
        current=matrix()[0]
        trace={'schema':1,'epoch':current['epoch'],'outputGeometries':outputs()}
        with patch.object(scope,'snapshot',return_value=current), patch.object(scope,'environment',return_value=backend()), patch.object(scope,'transitions',return_value=trace):
            self.assertIs(scope.coherent_state({},include_trace=True)[3],trace)

    def test_focus_change_during_sampling_requires_another_read(self):
        first = matrix()[0]
        first['focusGeneration'] = 2
        last = dict(first, focusGeneration=3)
        trace = {'schema':1,'epoch':first['epoch'],'outputGeometries':outputs()}
        with patch.object(scope,'snapshot',side_effect=[first,last]), patch.object(scope,'environment',return_value=backend()), patch.object(scope,'transitions',return_value=trace):
            self.assertIsNone(scope.coherent_state({}))

    def test_coherent_sample_requires_matching_epoch_and_valid_geometry(self):
        current=matrix()[0]
        trace={'schema':1,'epoch':current['epoch'],'outputGeometries':outputs()}
        with patch.object(scope,'snapshot',return_value=current), patch.object(scope,'environment',return_value=backend()), patch.object(scope,'transitions',return_value=trace):
            self.assertEqual(scope.coherent_state({})[2], outputs())
            trace['epoch']='stale'
            with self.assertRaises(RuntimeError): scope.coherent_state({})


if __name__ == '__main__': unittest.main()
