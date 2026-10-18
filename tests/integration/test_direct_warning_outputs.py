"""Refuse stale, mixed, misplaced and out-of-order two-monitor warning evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_warning as warning
import direct_warning_outputs as matrix
from direct_output_scope import contained_stages, intersections
from test_input_direct import backend,evidence


def outputs():
    return [{'id':'HDMI-A-1','x':0,'y':0,'width':1920,'height':1080,'scale':1},
            {'id':'DP-1','x':1920,'y':0,'width':3413,'height':1440,'scale':1.5}]


def placement_log(row=None, frame=None):
    row=row or outputs()[1]
    frame=frame or {'x':3432,'y':12,'width':390,'height':126}
    return warning.PREFIX+'OUTPUT_JSON '+json.dumps(row)+'\n'+warning.PREFIX+'PLACEMENT_JSON '+json.dumps(frame)


def accepted_stages():
    success={'warningCount':1,'hiddenSurfaceCount':0,**{k:True for k in ('outsideInputDelivered','pointerSkipDelivered','focusUnchanged','scriptUnloaded')}}
    return [{'output':row,'warning':deepcopy(success),'placementScriptUnloaded':True} for row in outputs()]


class MatrixTests(unittest.TestCase):
    def test_centered_owned_windows_below_warning_on_each_actual_output(self):
        stages=contained_stages(outputs(),centered=True,top_inset=200)
        self.assertEqual([stage['name'] for stage in stages],['DP-1','HDMI-A-1'])
        for stage in stages:
            row=next(row for row in outputs() if row['id']==stage['name'])
            self.assertEqual(intersections(stage['frame'],outputs()),{stage['name']})
            self.assertEqual(stage['frame']['y'],row['y']+200)
            self.assertLessEqual(stage['frame']['y']+stage['frame']['height'],row['y']+row['height'])

    def test_no_shared_edge_required_for_separate_warning_stages(self):
        rows=outputs(); rows[1].update(x=-5000,y=2200)
        self.assertEqual(len(contained_stages(rows,centered=True,top_inset=200)),2)

    def test_warning_actual_mixed_scale_record_and_position_pass(self):
        self.assertEqual(matrix.warning_placement(placement_log(),outputs()[1],outputs())['x'],3432)

    def test_wrong_output_scale_missing_duplicate_and_bad_frame_fail(self):
        badrow=dict(outputs()[1],scale=1)
        logs=[placement_log(badrow),placement_log()+'\n'+placement_log(),warning.SHOWN,
              placement_log(frame={'x':0,'y':12,'width':390,'height':126}),
              placement_log(frame={'x':float('nan'),'y':12,'width':390,'height':126})]
        for log in logs:
            with self.assertRaises(RuntimeError): matrix.warning_placement(log,outputs()[1],outputs())

    def test_outside_input_requires_fresh_observer_origin_client_and_release(self):
        before,current,origin_before,origin,clients,held,released=evidence()
        self.assertTrue(matrix.complete_input(before,current,origin_before,origin,clients,held,released))
        for k in warning.KINDS:
            for target in ('observer','origin','client'):
                args=deepcopy([before,current,origin_before,origin,clients,held,released])
                if target=='observer': args[1][k]=args[0][k]
                elif target=='origin': args[3]['libinput'][k]=args[2]['libinput'][k]
                else: args[4][k]=0
                self.assertFalse(matrix.complete_input(*args))
        for index in (5,6):
            args=deepcopy([before,current,origin_before,origin,clients,held,released]); args[index]=False
            self.assertFalse(matrix.complete_input(*args))

    def test_previous_stage_activity_cannot_arm_new_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); log=directory/'kwin.log'
            log.write_text('\n'.join((warning.SHOWN,warning.SKIP,warning.HIDDEN))+'\n')
            check=matrix.StageWarning(directory,{},1,'HDMI-A-1')
            with log.open('a') as f: f.write(warning.SHOWN+'\n')
            before,current,origin_before,origin,clients,held,released=evidence()
            # New baselines already contain the first stage's totals.
            self.assertFalse(matrix.complete_input(current,current,origin,origin, {k:0 for k in warning.KINDS},False,False))
            check.advance({k:0 for k in warning.KINDS},False,False,current,origin,origin)
            self.assertFalse(check.armed)
            self.assertEqual(check.log().count(warning.SHOWN),1)
            self.assertNotIn(warning.SKIP,check.log())

    def test_fresh_stage_still_refuses_early_skip_and_duplicate_show(self):
        for suffix in (warning.SHOWN+'\n'+warning.SKIP,warning.SHOWN+'\n'+warning.SHOWN):
            with tempfile.TemporaryDirectory() as tmp:
                directory=Path(tmp); log=directory/'kwin.log'; log.write_text(warning.SHOWN+'\n'+warning.SKIP+'\n'+warning.HIDDEN+'\n')
                check=matrix.StageWarning(directory,{},1,'HDMI-A-1')
                with log.open('a') as f: f.write(suffix)
                with self.assertRaises(RuntimeError): check.advance({k:0 for k in warning.KINDS},False,False,{'buttonKnown':False},backend(),backend())

    def test_first_skip_prompt_describes_next_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); log=directory/'kwin.log'; log.write_text('')
            check=matrix.StageWarning(directory,{},0,'DP-1')
            log.write_text(warning.SHOWN)
            before,current,origin_before,origin,clients,held,released=evidence()
            check.advance(clients,held,released,current,origin,origin_before)
            text=(directory/'instructions.txt').read_text()
            self.assertIn('NOW CLICK SKIP',text); self.assertIn('other monitor',text)
            self.assertNotIn('closes automatically afterward',text)

    def test_two_sequential_complete_lifecycles_pass(self):
        matrix.validate_totals('\n'.join([warning.SHOWN,warning.SKIP,warning.HIDDEN]*2),accepted_stages())

    def test_overlap_duplicate_failed_and_incomplete_lifecycles_fail(self):
        logs=['\n'.join([warning.SHOWN,warning.SHOWN,warning.SKIP,warning.SKIP,warning.HIDDEN,warning.HIDDEN]),
              '\n'.join([warning.SHOWN,warning.SKIP,warning.HIDDEN]),
              '\n'.join([warning.SHOWN,warning.SKIP,warning.HIDDEN]*2+[warning.PREFIX+'FAIL expired'])]
        for log in logs:
            with self.assertRaises(RuntimeError): matrix.validate_totals(log,accepted_stages())

    def test_both_distinct_outputs_and_all_unloads_required(self):
        log='\n'.join([warning.SHOWN,warning.SKIP,warning.HIDDEN]*2)
        for key in ('pointerSkipDelivered','scriptUnloaded','focusUnchanged'):
            stages=accepted_stages(); stages[1]['warning'][key]=False
            with self.assertRaises(RuntimeError): matrix.validate_totals(log,stages)
        stages=accepted_stages(); stages[1]['output']=stages[0]['output']
        with self.assertRaises(RuntimeError): matrix.validate_totals(log,stages)
        stages=accepted_stages(); stages[1]['placementScriptUnloaded']=False
        with self.assertRaises(RuntimeError): matrix.validate_totals(log,stages)

    def test_reusing_fixture_upgrades_only_private_installed_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp); data=directory/'data'; env={'XDG_DATA_HOME':str(data)}
            with patch.object(warning.subprocess,'run') as run:
                installed=warning.install_fixture(directory,env)
                self.assertIn('--install',run.call_args.args[0])
                installed.parent.mkdir(parents=True); installed.write_text('old isolated copy')
                warning.install_fixture(directory,env)
                self.assertIn('--upgrade',run.call_args.args[0])
                self.assertTrue(installed.is_relative_to(directory))


if __name__=='__main__': unittest.main()
