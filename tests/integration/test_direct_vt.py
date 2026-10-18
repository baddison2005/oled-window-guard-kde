"""Refuse missing/stale VT boundaries and require new physical input after return."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import direct_vt as vt
from test_input_direct import backend, evidence
from run_input_direct import ROOT


def data():
    baseline = evidence()[0]
    baseline['sequence'] = 0
    geometry = [{'id':'DP-1','x':0,'y':0,'width':900,'height':650,'scale':1}]
    held = dict(baseline, **{k: 2 for k in vt.KINDS}, buttonKnown=True, buttonsDown=True, sequence=8)
    held['outputs'] = [{'id': 'DP-1', 'events': 8}]
    inactive = dict(held, sessionGeneration=1, sessionActivity='inactive', buttonKnown=False,
                    buttonsDown=False, ageMs=-1, active=False, monotonicMs=1000, outputGeometries=geometry)
    inactive['outputs'] = [{'id': 'DP-1', 'events': 0}]
    active = dict(inactive, sessionGeneration=2, sessionActivity='active', active=True, monotonicMs=6000)
    return baseline, held, inactive, active


class VTTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.held, self.inactive, self.active = data()
        self.origin = backend()
        self.facts = backend()
        self.facts['libinput'] = {k: 2 for k in vt.KINDS}
        self.clients = {k: 1 for k in vt.KINDS}
        self.check = vt.VTCycle(self.baseline, self.origin, {k: 0 for k in vt.KINDS}, 3)

    def advance(self, current=None, facts=None, rows=None, now=0, clients=None, topology=None, geometry=None):
        trace = {'schema': 1, 'epoch': self.baseline['epoch'], 'transitions': rows or [],
                 'topologyTransitions':topology or [],
                 'outputGeometries':geometry or self.active['outputGeometries']}
        self.check.advance(current or self.held, facts or self.facts, trace,
                           self.clients if clients is None else clients, now)

    def go_inactive(self):
        self.advance()
        facts = dict(self.facts, sessionActive=False)
        self.advance(self.inactive, facts, [self.inactive], now=1)
        self.advance(self.inactive, facts, [self.inactive], now=3)

    def return_active(self):
        self.go_inactive()
        self.advance(self.active, rows=[self.inactive,self.active], now=6)
        self.advance(self.active, rows=[self.inactive,self.active], now=6.6)

    def use_two_outputs(self):
        geometry = [{'id':'DP-1','x':1920,'y':0,'width':3413,'height':1440,'scale':1.5},
                    {'id':'HDMI-A-1','x':0,'y':0,'width':1920,'height':1080,'scale':1}]
        for state in (self.baseline, self.held, self.inactive, self.active):
            state['outputs'] = deepcopy(state['outputs']) + [{'id':'HDMI-A-1','events':0}]
            if 'outputGeometries' in state:
                state['outputGeometries'] = deepcopy(geometry)
        self.check = vt.VTCycle(self.baseline, self.origin, {k:0 for k in vt.KINDS}, 3)

    def test_two_mixed_scale_outputs_complete_inactive_and_fresh_recovery(self):
        self.use_two_outputs()
        self.return_active()
        self.assertEqual(self.check.stage, 'fresh')
        current = dict(self.active, **{k:4 for k in vt.KINDS}, sequence=16,
                       buttonKnown=True, buttonsDown=True)
        facts = dict(self.facts, libinput={k:4 for k in vt.KINDS})
        clients = {k:2 for k in vt.KINDS}
        rows = [self.inactive, self.active]
        self.advance(current, facts, rows, clients=clients, now=7)
        self.advance(dict(current, buttonsDown=False), facts, rows, clients=clients, now=8)
        self.assertEqual(self.check.stage, 'done')
        result = self.check.result
        self.assertEqual(result['initialOutputGeometries'], result['returnedOutputGeometries'])
        self.assertEqual(len(result['returnedOutputGeometries']), 2)
        self.assertTrue(result['resumedInput']['releaseObserved'])

    def test_stale_activity_on_second_output_cannot_pass_vt_boundary(self):
        self.use_two_outputs()
        self.advance()
        bad = deepcopy(self.inactive)
        bad['outputs'][1]['events'] = 1
        with self.assertRaisesRegex(RuntimeError, 'invalidate old input facts'):
            self.advance(bad, dict(self.facts, sessionActive=False), [bad], now=1)

    def test_requires_all_physical_client_and_observer_input_before_switch(self):
        for k in vt.KINDS:
            self.advance(clients=dict(self.clients, **{k: 0}))
            self.advance(facts=dict(self.facts, libinput=dict(self.facts['libinput'], **{k: 0})))
            self.advance(current=dict(self.held, **{k: 0}))
            self.assertEqual(self.check.stage, 'input')
        self.advance()
        self.assertEqual(self.check.stage, 'away')
        self.assertIn('NOW SWITCH TO KDE', self.check.message)

    def test_requires_button_held_at_switch_prompt(self):
        self.advance(current=dict(self.held, buttonsDown=False))
        self.assertEqual(self.check.stage, 'input')

    def test_observes_real_inactive_backend_and_reset(self):
        self.go_inactive()
        self.assertEqual(self.check.stage,'inactive')
        self.assertEqual(self.check.inactive_samples,2)
        self.assertEqual(self.check.inactive_span,2)

    def test_stale_button_age_or_output_counts_at_boundary_refused(self):
        self.advance()
        for fields in ({'buttonKnown':True},{'buttonsDown':True},{'ageMs':0},
                       {'outputs':[{'id':'DP-1','events':1}]}):
            row=dict(self.inactive,**fields)
            with self.assertRaises(RuntimeError):
                self.advance(row,dict(self.facts,sessionActive=False),[row])

    def test_no_activity_recording_while_inactive(self):
        self.go_inactive()
        with self.assertRaises(RuntimeError):
            self.advance(dict(self.inactive, keyboard=3),dict(self.facts,sessionActive=False),[self.inactive])
        facts=deepcopy(self.facts)
        facts['libinput']['pointer']+=1
        facts['sessionActive']=False
        with self.assertRaises(RuntimeError):
            self.advance(self.inactive,facts,[self.inactive])

    def test_return_requires_observed_inactive_interval(self):
        self.advance()
        with self.assertRaisesRegex(RuntimeError,'not observed long enough'):
            self.advance(self.active,rows=[self.inactive,self.active])

    def test_fresh_return_boundary_must_reset_stale_hold(self):
        self.go_inactive()
        for fields in ({'buttonKnown':True},{'buttonsDown':True},{'ageMs':0}):
            row=dict(self.active,**fields)
            with self.assertRaises(RuntimeError):
                self.advance(row,rows=[self.inactive,row])

    def test_input_between_boundary_records_refused(self):
        self.go_inactive()
        active=dict(self.active,keyboard=3)
        with self.assertRaisesRegex(RuntimeError,'between inactive and active'):
            self.advance(active,rows=[self.inactive,active])

    def test_missing_duplicate_and_truncated_records_refused(self):
        self.advance()
        for rows in ([self.active],[self.inactive,self.inactive],
                     [self.inactive,self.active,dict(self.active,sessionGeneration=3)]):
            with self.assertRaises(RuntimeError):
                self.advance(self.active,rows=rows)

    def test_epoch_topology_lock_sleep_change_refused(self):
        for fields in ({'epoch':'other'},{'topologyGeneration':1},{'lockState':'locked'},
                       {'sleepState':'sleeping'},{'outputIdentityComplete':False}):
            with self.assertRaises(RuntimeError):
                self.advance(dict(self.held,**fields))

    def test_straddled_boundary_read_waits_for_coherent_sample(self):
        self.advance()
        self.advance(self.held,dict(self.facts,sessionActive=False),[self.inactive])
        self.assertEqual(self.check.stage,'away')
        self.advance(self.inactive,self.facts,[self.inactive])
        self.assertEqual(self.check.stage,'away')

    def test_return_needs_new_client_libinput_and_held_release(self):
        self.return_active()
        self.assertEqual(self.check.stage,'fresh')
        self.advance(self.active,rows=[self.inactive,self.active])
        self.assertIsNone(self.check.result)
        final=dict(self.active,**{k:4 for k in vt.KINDS},sequence=16,buttonKnown=True,buttonsDown=True)
        facts=backend()
        facts['libinput']={k:4 for k in vt.KINDS}
        clients={k:2 for k in vt.KINDS}
        self.advance(final,facts,[self.inactive,self.active],clients=clients)
        self.assertIsNone(self.check.result)
        final['buttonsDown']=False
        self.advance(final,facts,[self.inactive,self.active],clients=clients)
        self.assertEqual(self.check.stage,'done')
        self.assertTrue(self.check.result['resumedInput']['releaseObserved'])

    def test_fresh_event_between_snapshot_and_origin_reads_waits(self):
        self.return_active()
        values={k:4 for k in vt.KINDS}
        values['keyboard']=self.active['keyboard']
        held=dict(self.active,**values,
                  sequence=14,buttonKnown=True,buttonsDown=True)
        facts=backend()
        facts['libinput']={k:4 for k in vt.KINDS}
        clients={k:2 for k in vt.KINDS}
        rows=[self.inactive,self.active]
        self.advance(held,facts,rows,clients=clients)
        released=dict(held,buttonsDown=False)
        self.advance(released,facts,rows,clients=clients)
        self.assertIsNone(self.check.result)
        self.advance(dict(released,keyboard=4,sequence=16),facts,rows,clients=clients)
        self.assertEqual(self.check.stage,'done')

    def test_synthetic_or_wrong_backend_refused(self):
        for fields in ({'outputBackend':'KWin::VirtualBackend'},{'sessionBackend':'KWin::NoopSession'},
                       {'injectionEnabled':True}):
            with self.assertRaises(RuntimeError):
                self.advance(facts=dict(self.facts,**fields))

    def test_return_refresh_requires_explicit_reset_and_settling(self):
        self.go_inactive()
        reset=dict(self.active,topologyGeneration=1,monotonicMs=6100)
        current=dict(reset,pointer=3,sequence=9)
        self.advance(current,rows=[self.inactive,self.active],topology=[reset],now=6.1)
        self.assertEqual(self.check.stage,'settle')
        second=dict(reset,topologyGeneration=2,monotonicMs=6200)
        current=dict(current,topologyGeneration=2)
        self.advance(current,rows=[self.inactive,self.active],topology=[reset,second],now=6.2)
        self.assertEqual(self.check.stage,'settle')
        self.advance(current,rows=[self.inactive,self.active],topology=[reset,second],now=6.8)
        self.assertEqual(self.check.stage,'fresh')
        self.assertEqual(self.check.return_baseline['topologyGeneration'],2)
        self.assertEqual(self.check.topology_resets,[reset,second])

    def test_refresh_with_stale_facts_or_missing_record_is_refused(self):
        self.go_inactive()
        current=dict(self.active,topologyGeneration=1)
        with self.assertRaises(RuntimeError):
            self.advance(current,rows=[self.inactive,self.active])
        bad=dict(current,monotonicMs=6100,buttonKnown=True)
        with self.assertRaises(RuntimeError):
            self.advance(current,rows=[self.inactive,self.active],topology=[bad])

    def test_refresh_during_inactivity_requires_frozen_counters(self):
        self.go_inactive()
        reset=dict(self.inactive,topologyGeneration=1,monotonicMs=2000,pointer=3)
        with self.assertRaises(RuntimeError):
            self.advance(dict(self.active,topologyGeneration=1),rows=[self.inactive,self.active],topology=[reset])

    def test_changed_output_identity_refused(self):
        self.go_inactive()
        reset=dict(self.active,topologyGeneration=1,monotonicMs=6100,
                   outputs=[{'id':'new-monitor','events':0}])
        with self.assertRaises(RuntimeError):
            self.advance(dict(self.active,topologyGeneration=1),rows=[self.inactive,self.active],topology=[reset])

    def test_captured_physical_deactivation_ordering_regression(self):
        path=ROOT/'docs/evidence/2026-10-01-direct-vt-attempt2.json'
        progress=json.loads(path.read_text())['progress']
        origin=deepcopy(progress['environment'])
        origin['sessionActive']=True
        origin['libinput']={k:0 for k in vt.KINDS}
        check=vt.VTCycle(progress['baseline'],origin,{k:0 for k in vt.KINDS},3)
        held_facts=dict(progress['environment'],sessionActive=True)
        empty_trace=dict(progress['transitions'],transitions=[],topologyTransitions=[])
        check.advance(progress['heldBeforeSwitch'],held_facts,empty_trace,progress['clientCounters'],0)
        self.assertEqual(check.stage,'away')
        check.advance(progress['current'],progress['environment'],progress['transitions'],progress['clientCounters'],1)
        self.assertEqual(check.stage,'inactive')

    def test_deactivation_reset_geometry_change_refused(self):
        self.advance()
        inactive=dict(self.inactive,topologyGeneration=1)
        reset=dict(inactive,sessionGeneration=0,sessionActivity='unknown',
                   outputGeometries=[dict(self.active['outputGeometries'][0],width=1000)])
        with self.assertRaises(RuntimeError):
            self.advance(inactive,dict(self.facts,sessionActive=False),[inactive],topology=[reset])

    def test_output_reset_read_before_inactive_signal_waits(self):
        self.advance()
        reset=dict(self.inactive,sessionGeneration=0,sessionActivity='unknown',topologyGeneration=1)
        self.advance(dict(reset),dict(self.facts,sessionActive=False),topology=[reset])
        self.assertEqual(self.check.stage,'away')

    def test_inactive_output_reset_with_frozen_counters_can_settle_after_return(self):
        self.go_inactive()
        reset=dict(self.inactive,topologyGeneration=1,monotonicMs=2000)
        active=dict(self.active,topologyGeneration=1)
        self.advance(active,rows=[self.inactive,active],topology=[reset],now=6)
        self.advance(active,rows=[self.inactive,active],topology=[reset],now=6.6)
        self.assertEqual(self.check.stage,'fresh')

    def test_geometry_change_after_fresh_baseline_is_refused(self):
        self.return_active()
        geometry=[dict(self.active['outputGeometries'][0],width=1000)]
        with self.assertRaises(RuntimeError):
            self.advance(self.active,rows=[self.inactive,self.active],geometry=geometry)

    def test_refuses_desktop_vt_as_spare(self):
        with self.assertRaises(RuntimeError):
            vt.VTCycle(self.baseline, dict(self.origin,sessionTerminal=2),{k:0 for k in vt.KINDS},2)


if __name__=='__main__':
    unittest.main()
