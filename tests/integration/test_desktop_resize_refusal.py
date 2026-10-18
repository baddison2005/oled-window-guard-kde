import copy
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools import desktop_resize_refusal as runner
from tools.desktop_resize_refusal import TemporaryLibrary,test_geometry
from tools.build_planning_worker import build
from oled_window_guard.layout_provider import LayoutProvider


class ResizeRefusalTests(unittest.TestCase):
    def snapshot(self):
        area=dict(x=0,y=0,width=1920,height=1080)
        window=dict(identity='fixed-1',appId='oled-window-guard-physical-fixture',
            rect=dict(x=100,y=100,width=420,height=180),outputIds=['output-1'],
            visible=True,onCurrentDesktop=True,onCurrentActivity=True,focused=False,
            managed=True,modal=False,transient=False,movable=True,resizable=False,
            minimized=False,maximized=False,fullscreen=False,tiled=False,windowType='normal',skipReasons=[])
        return dict(outputs=[dict(id='output-1',connector='DP-1',geometry=area,usableRect=area,scale=1)],
                    windows=[window],focusedIdentity=None,topologyRevision='snapshot-1')

    def library(self,folder):
        raw=dict(CustomLayouts='[]',CustomGroups='[]',GroupOrder='[]',LayoutPadding='8')
        provider=LayoutProvider(runner=lambda argv,timeout:raw[argv[argv.index('--key')+1]],debounce_seconds=0)
        lib=TemporaryLibrary(Path(folder)/'backup.json',provider=provider,write=lambda k,v:raw.__setitem__(k,v))
        return lib,raw

    def probe(self,snapshot):
        with tempfile.TemporaryDirectory() as folder:
            output,target,zones,obstacles,layouts,group=test_geometry(snapshot,'DP-1')
            lib,raw=self.library(folder);imported=lib.apply(layouts,group)
            worker=Path(folder)/'worker.js';worker.write_text(build())
            request=dict(worker=str(worker),snapshot=snapshot,output=output,identity=target['identity'],
                         zones=zones,obstacles=obstacles,library=imported,groupId='kde:'+group['id'])
            result=subprocess.run(['node','tools/desktop_resize_probe.js'],input=json.dumps(request),text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            evidence=json.loads(result.stdout)
            self.assertEqual(evidence['fixed']['moves'],[])
            self.assertEqual(evidence['resizable']['moves'][0]['toRect']['width'],418)
            lib.restore();self.assertEqual(raw,lib.original)

    def test_actual_import_rounding_and_constraint_counterfactual(self):
        self.probe(self.snapshot())

    def test_subpixel_source_position_is_not_a_rounding_failure(self):
        snapshot=self.snapshot();snapshot['windows'][0]['rect'].update(x=433.49604474241244,y=574.097855926558)
        self.probe(snapshot)

    def test_library_external_edit_is_not_overwritten_on_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            *_,layouts,group=test_geometry(self.snapshot(),'DP-1')
            lib,raw=self.library(folder);lib.apply(layouts,group)
            raw['LayoutPadding']='99'
            with self.assertRaisesRegex(RuntimeError,'externally'):lib.restore()
            self.assertEqual(raw['LayoutPadding'],'99')

    def test_full_catalog_reuses_only_ungrouped_slots_and_restores_exact_values(self):
        with tempfile.TemporaryDirectory() as folder:
            *_,layouts,group=test_geometry(self.snapshot(),'DP-1')
            lib,raw=self.library(folder)
            raw['CustomGroups']=json.dumps([dict(id='existing',name='Existing')])
            catalog=[dict(id='layout-'+str(i),name='Original',x=0,y=0,width=.5,height=.5,
                          groupId='existing' if i<18 else '') for i in range(20)]
            raw['CustomLayouts']=json.dumps(catalog)
            lib.original=dict(raw);lib.expected=dict(raw)
            applied=lib.apply(layouts,group)
            self.assertEqual(len(applied['layouts']),20)
            changed=json.loads(raw['CustomLayouts'])
            self.assertEqual(changed[:18],catalog[:18])
            self.assertEqual([l['id'] for l in changed[18:]],['layout-18','layout-19'])
            self.assertTrue(all(l['groupId']==group['id'] for l in changed[18:]))
            lib.restore();self.assertEqual(raw,lib.original)

    def test_partial_write_failure_recovers_completed_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            *_,layouts,group=test_geometry(self.snapshot(),'DP-1')
            lib,raw=self.library(folder)
            def write(key,value):
                if key=='CustomGroups':raise RuntimeError('simulated write failure')
                raw[key]=value
            lib.write=write
            with self.assertRaisesRegex(RuntimeError,'write failure'):lib.apply(layouts,group)
            lib.restore();self.assertEqual(raw,lib.original)

    def test_focused_or_missing_fixed_target_cannot_count_as_refusal(self):
        snapshot=self.snapshot();snapshot['windows'][0]['focused']=True
        with self.assertRaisesRegex(RuntimeError,'Click/release'):test_geometry(snapshot,'DP-1')
        snapshot=self.snapshot();snapshot['windows'][0]['resizable']=True
        with self.assertRaisesRegex(RuntimeError,'one visible fixed'):test_geometry(snapshot,'DP-1')

    def run_batch(self,folder,unexpected_warning=False,probe_failure=False):
        folder=Path(folder);receipt=folder/'round.jsonl';receipt.with_suffix('.continue').touch()
        snapshot=self.snapshot()
        second=copy.deepcopy(snapshot['outputs'][0]);second.update(id='output-2',connector='HDMI-A-1')
        second['geometry']['x']=1920;second['usableRect']['x']=1920
        snapshot['outputs'].append(second)
        base=copy.deepcopy(snapshot['windows'][0])
        bounded=copy.deepcopy(base);bounded.update(identity='bounded-1',resizable=True);bounded['rect']['x']=1000
        snapshot['windows'].append(bounded)
        for w in (base,bounded):
            w=copy.deepcopy(w);w['identity']=w['identity'].replace('-1','-2');w['outputIds']=['output-2'];w['rect']['x']+=1920
            snapshot['windows'].append(w)
        fixture=folder/'fixture.jsonl'
        fixture.write_text(json.dumps(dict(monotonic=0,profile='fixed-bounded',platform='wayland',windows=[
            dict(title=w['identity']+(' [fixed]' if not w['resizable'] else ' [bounded]'),
                 screen='DP-1' if w['outputIds']==['output-1'] else 'HDMI-A-1',visible=True,
                 frame=list(w['rect'].values()),clientSize=[420,180],
                 minimumSize=[320,140] if w['resizable'] else [420,180],
                 maximumSize=[560,260] if w['resizable'] else [420,180]) for w in snapshot['windows']]))+'\n')
        settings=folder/'settings.json';runner.preferences.save_settings(settings,runner.preferences.defaults())
        lib,raw=self.library(folder)
        state=dict(paused=True,resumeReady=True,movementReady=False,transactionActive=False,skipAvailable=False,restoreAvailable=False)
        def host(action,payload=None):
            self.assertNotEqual(action,'Status','Status must use its separate D-Bus method')
            if action=='PlanPreview':state['planningResult']=dict(id='preview',status='no-plan',reason='no_group_rotation',moves=0)
            if action=='Resume':state.update(paused=False,movementReady=True)
            if action=='MoveNow':state.update(lastResult='no-plan: no_group_rotation',skipAvailable=unexpected_warning)
            if action=='Pause':state.update(paused=True,movementReady=False,skipAvailable=False)
            return dict(ok=True,status=copy.deepcopy(state),requestId='preview')
        from types import SimpleNamespace
        failed_probe=patch.object(runner.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='Error: Geometry changed\n',stdout='')) if probe_failure else nullcontext()
        with failed_probe,patch.object(runner,'FIXTURE',fixture),patch.object(runner,'capture',return_value=snapshot), \
             patch.object(runner.QCoreApplication,'instance',return_value=True), \
             patch.object(runner,'read_status',side_effect=lambda:copy.deepcopy(state)), \
             patch.object(runner,'call_host',side_effect=host),patch.object(runner,'TemporaryLibrary',return_value=lib), \
             patch.object(runner.preferences,'default_settings_path',return_value=settings), \
             patch.object(runner.time,'sleep'),patch.object(runner.signal,'signal'), \
             patch.object(runner.sys,'argv',['test','--receipt',str(receipt)]):
            runner.main()
        self.assertEqual(raw,lib.original)
        self.assertEqual(runner.preferences.load_settings(settings).values,runner.preferences.defaults())
        return [json.loads(line) for line in receipt.read_text().splitlines()]

    def test_both_product_refusals_restore_library_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            rows=self.run_batch(folder)
            self.assertFalse([r for r in rows if r['kind']=='error'])
            self.assertEqual([r['value']['output'] for r in rows if r['kind']=='resize-refusal-result'],['DP-1','HDMI-A-1'])
            self.assertEqual(rows[-1]['kind'],'final-pause')
            self.assertTrue(any(r['kind']=='library-restored' for r in rows))

    def test_unexpected_warning_fails_and_still_recovers_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            rows=self.run_batch(folder,unexpected_warning=True)
            self.assertTrue(any(r['kind']=='error' and 'Unexpected movement warning' in r['value'] for r in rows))
            self.assertFalse(any(r['kind']=='round-result' for r in rows))

    def test_probe_error_is_explained_and_cleanup_still_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            rows=self.run_batch(folder,probe_failure=True)
            self.assertTrue(any(r['kind']=='error' and r['value']=='Resize preflight: Geometry changed' for r in rows))
            self.assertTrue(any(r['kind']=='resize-probe-error' and 'Geometry changed' in r['value']['stderr'] for r in rows))
            self.assertTrue(any(r['kind']=='library-restored' for r in rows))
            self.assertFalse(any(r['kind'] in ('Resume','MoveNow') for r in rows))

    def test_status_transport_calls_status_method_and_accepts_plain_status_object(self):
        from unittest.mock import Mock
        from PySide6.QtDBus import QDBusMessage
        expected=dict(paused=True,resumeReady=True,transactionActive=False,readiness='ready')
        message=Mock();message.type.return_value=QDBusMessage.MessageType.ReplyMessage
        message.arguments.return_value=[json.dumps(expected)]
        interface=Mock();interface.call.return_value=message
        self.assertEqual(runner.read_status(interface),expected)
        interface.call.assert_called_once_with('Status')
        message.arguments.return_value=[json.dumps(dict(ok=False,reason='unsupported-action'))]
        with self.assertRaisesRegex(RuntimeError,'Invalid controller status fields'):runner.read_status(interface)


if __name__=='__main__':unittest.main()
