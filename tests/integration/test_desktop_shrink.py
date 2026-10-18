import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import desktop_shrink_batch as batch
from tools.desktop_resize_refusal import TemporaryLibrary
from oled_window_guard.layout_provider import LayoutProvider
from tests.integration import test_desktop_gtk as gtk_tests
from tools.build_planning_worker import build


class ShrinkTests(unittest.TestCase):
    def snapshot(self):
        sample=gtk_tests.GtkTests().snapshot()
        for w in sample['windows']:
            w.update(resizable=True,focused=False,modal=False,transient=False,minimized=False,
                     maximized=False,fullscreen=False,tiled=False,skipReasons=[])
        for o in sample['outputs']:o.update(usableRect=dict(o['geometry']),scale=1)
        sample.update(focusedIdentity=None,topologyRevision='shrink-snapshot')
        return sample

    def fixture(self):
        s=gtk_tests.GtkTests().fixture();s.update(toolkit='GTK4',monotonic=0)
        for i,w in enumerate(s['windows']):w['title']=str(i)
        return s

    def library(self,folder):
        raw=dict(CustomLayouts='[]',CustomGroups='[]',GroupOrder='[]',LayoutPadding='8')
        provider=LayoutProvider(runner=lambda argv,timeout:raw[argv[argv.index('--key')+1]],debounce_seconds=0)
        return TemporaryLibrary(folder/'backup.json',provider=provider,write=lambda k,v:raw.__setitem__(k,v)),raw

    def test_actual_production_plan_requires_explicit_resize_permission(self):
        s=self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);lib,raw=self.library(folder);parts=batch.geometry(s,'DP-1')
            imported=lib.apply(parts[4],parts[5]);worker=folder/'worker.js';worker.write_text(build())
            result=batch.probe(worker,s,parts,imported)
            move=result['productionPlan']['plan']['moves'][0]
            self.assertEqual(move['identity'],parts[1]['identity'])
            self.assertEqual(move['toRect']['width'],418)
            self.assertTrue(move['permitsRoundingResize'])
            fixed=copy.deepcopy(s);fixed['windows'][0]['resizable']=False
            with self.assertRaises(RuntimeError):batch.probe(worker,fixed,parts,imported)
            lib.restore();self.assertEqual(raw,lib.original)

    def test_measurement_rejects_other_window_changes_or_missing_client_resize(self):
        s=self.snapshot();parts=batch.geometry(s,'DP-1');m=copy.deepcopy(s)
        m['windows'][0]['rect']=parts[2][1]
        before=self.fixture();after=copy.deepcopy(before);after['windows'][0]['clientSize']=[418,180]
        batch.verify_shrink(s,m,'0',parts[2][1],before,after)
        with self.assertRaises(RuntimeError):batch.verify_shrink(s,m,'0',parts[2][1],before,before)
        m['windows'][2]['rect']['x']+=2
        with self.assertRaises(RuntimeError):batch.verify_shrink(s,m,'0',parts[2][1],before,after)

    def run_batch(self,folder,cancel=False):
        receipt=folder/'round.jsonl';receipt.with_suffix('.continue').touch()
        fixture=folder/'fixture.jsonl';base_fixture=self.fixture();fixture.write_text(json.dumps(base_fixture)+'\n')
        baseline=self.snapshot();current=copy.deepcopy(baseline)
        settings=folder/'settings.json';original=batch.preferences.defaults();batch.preferences.save_settings(settings,original)
        lib,raw=self.library(folder)
        state=dict(paused=True,transactionActive=False,resumeReady=True,movementReady=False,skipAvailable=False,restoreAvailable=False)
        pending=[]
        def host(action,payload=None):
            if action=='PlanPreview':state['planningResult']=dict(id='preview',status='ready',moves=1,reason='group-rotation')
            if action=='Resume':state.update(paused=False,movementReady=True)
            if action in ('MoveNow','Restore'):
                ending=dict(state,transactionActive=False,skipAvailable=False,lastResult='cancelled: native-input' if cancel else 'committed',restoreAvailable=action=='MoveNow')
                pending.extend([dict(state,transactionActive=True,skipAvailable=True,warningRemainingSeconds=10),ending])
                if action=='MoveNow' and not cancel:
                    name=next(o['connector'] for o in baseline['outputs'] if o['id']==payload['outputId'])
                    parts=batch.geometry(baseline,name);target=parts[1]['identity']
                    current['windows'][int(target)]['rect']=parts[2][1]
                    f=copy.deepcopy(base_fixture);f['windows'][int(target)]['clientSize']=[418,180]
                    fixture.write_text(json.dumps(f)+'\n')
                if action=='Restore':
                    current['windows']=copy.deepcopy(baseline['windows']);fixture.write_text(json.dumps(base_fixture)+'\n')
            if action=='Pause':state.update(paused=True,movementReady=False,transactionActive=False,skipAvailable=False,restoreAvailable=False)
            return dict(ok=True,requestId='preview')
        def status():
            if pending:state.update(pending.pop(0))
            return copy.deepcopy(state)
        with patch.object(batch,'FIXTURE',fixture),patch.object(batch,'capture',side_effect=lambda:copy.deepcopy(current)), \
             patch.object(batch,'call_host',side_effect=host),patch.object(batch,'read_status',side_effect=status), \
             patch.object(batch,'TemporaryLibrary',return_value=lib),patch.object(batch.preferences,'default_settings_path',return_value=settings), \
             patch.object(batch.QCoreApplication,'instance',return_value=True),patch.object(batch.signal,'signal'), \
             patch.object(batch.signal,'setitimer') as timer,patch.object(batch.time,'sleep'), \
             patch.object(batch.sys,'argv',['test','--receipt',str(receipt)]):
            batch.main()
        self.assertEqual(raw,lib.original)
        self.assertEqual(batch.preferences.load_settings(settings).values,original)
        self.assertEqual(timer.call_args_list[-1].args,(batch.signal.ITIMER_REAL,0))
        return [json.loads(x) for x in receipt.read_text().splitlines()]

    def test_two_product_rounds_verify_client_shrink_restore_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            rows=self.run_batch(Path(directory))
            self.assertFalse([r for r in rows if r['kind']=='error'])
            self.assertEqual([r['value']['output'] for r in rows if r['kind']=='shrink-restore-verified'],['DP-1','HDMI-A-1'])
            self.assertTrue(any(r['kind']=='round-result' for r in rows))
            self.assertEqual(rows[-1]['kind'],'final-pause')

    def test_cancelled_move_never_restores_and_recovers_settings_and_library(self):
        with tempfile.TemporaryDirectory() as directory:
            rows=self.run_batch(Path(directory),cancel=True)
            self.assertTrue(any(r['kind']=='error' for r in rows))
            self.assertFalse(any(r['kind'] in ('Restore','round-result') for r in rows))
            self.assertTrue(any(r['kind']=='library-restored' for r in rows))


if __name__=='__main__':unittest.main()
