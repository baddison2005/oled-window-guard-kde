import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import desktop_gtk_batch as batch
from tools.desktop_gtk_batch import verify_fixture,verify_forward,verify_restored,verify_scope,APP_ID


class GtkTests(unittest.TestCase):
    def snapshot(self):
        outputs=[dict(id=n,connector=n,geometry=dict(x=i*1920,y=0,width=1920,height=1080)) for i,n in enumerate(('DP-1','HDMI-A-1'))]
        windows=[dict(identity=str(i),appId=APP_ID,outputIds=[outputs[i//2]['id']],
                      visible=True,onCurrentDesktop=True,onCurrentActivity=True,managed=True,movable=True,windowType='normal',
                      rect=dict(x=(i//2)*1920+(i%2)*600,y=100,width=420,height=180)) for i in range(4)]
        return dict(outputs=outputs,windows=windows)

    def fixture(self):
        return dict(toolkit='GTK3',platform='wayland',displayType='GdkWaylandDisplay',appId=APP_ID,
                    windows=[dict(visible=True,clientSize=[420,180],monitorGeometry=[(i//2)*1920,0,1920,1080]) for i in range(4)])

    def test_real_wayland_backend_and_two_visible_targets_per_output_required(self):
        s=self.fixture();verify_fixture(s,self.snapshot()['outputs'])
        for key,value in (('platform','xcb'),('displayType','GdkX11Display'),('toolkit','Qt')):
            bad=copy.deepcopy(s);bad[key]=value
            with self.assertRaises(RuntimeError):verify_fixture(bad,self.snapshot()['outputs'])
        s['windows'][0]['monitorGeometry']=[1920,0,1920,1080]
        with self.assertRaisesRegex(RuntimeError,'TWO'):verify_fixture(s,self.snapshot()['outputs'])

    def test_gtk4_requires_explicit_version_and_rejects_gtk3_receipt(self):
        sample=self.fixture();sample['toolkit']='GTK4'
        verify_fixture(sample,self.snapshot()['outputs'],'4')
        with self.assertRaisesRegex(RuntimeError,'GTK3'):verify_fixture(sample,self.snapshot()['outputs'])
        with self.assertRaisesRegex(RuntimeError,'GTK4'):verify_fixture(self.fixture(),self.snapshot()['outputs'],'4')

    def test_one_window_moves_and_restores_with_other_output_unchanged(self):
        before=self.snapshot();moved=copy.deepcopy(before);moved['windows'][0]['rect']['x']+=50
        self.assertEqual(verify_forward(before,moved,'DP-1')['movedIdentity'],'0')
        verify_restored(before,before)
        with self.assertRaises(RuntimeError):verify_restored(before,moved)

    def test_wrong_output_resize_and_missing_target_fail(self):
        before=self.snapshot()
        for index,key in ((2,'x'),(0,'width')):
            moved=copy.deepcopy(before);moved['windows'][index]['rect'][key]+=50
            with self.assertRaises(RuntimeError):verify_forward(before,moved,'DP-1')
        moved=copy.deepcopy(before);moved['windows'].pop()
        with self.assertRaises(RuntimeError):verify_forward(before,moved,'DP-1')

    def test_restore_tolerance_matches_production_half_pixel(self):
        before=self.snapshot();after=copy.deepcopy(before);after['windows'][0]['rect']['x']+=.5
        verify_restored(before,after)
        after['windows'][0]['rect']['x']+=.01
        with self.assertRaises(RuntimeError):verify_restored(before,after)

    def test_current_desktop_scope_rejects_hidden_fixture_and_working_app(self):
        s=self.snapshot();verify_scope(s)
        s['windows'][0]['onCurrentDesktop']=False
        with self.assertRaisesRegex(RuntimeError,'CURRENT'):verify_scope(s)
        s=self.snapshot();other=copy.deepcopy(s['windows'][0]);other.update(identity='work',appId='working-app')
        s['windows'].append(other)
        with self.assertRaisesRegex(RuntimeError,'empty test'):verify_scope(s)

    def test_batch_verifies_both_outputs_and_restores_original_settings(self):
        before=self.snapshot();lg=copy.deepcopy(before);lg['windows'][0]['rect']['x']+=50
        hdmi=copy.deepcopy(before);hdmi['windows'][2]['rect']['x']+=50
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);receipt=folder/'round.jsonl';receipt.with_suffix('.continue').touch()
            fixture=folder/'fixture.jsonl';sample=self.fixture();sample['monotonic']=0
            for i,w in enumerate(sample['windows']):w['title']=str(i)
            fixture.write_text(json.dumps(sample)+'\n')
            settings=folder/'settings.json';original=batch.preferences.defaults()
            batch.preferences.save_settings(settings,original)
            def run_child(observer):
                path=Path(batch.sys.argv[batch.sys.argv.index('--receipt')+1])
                movement=dict(result='committed',warningSeen=True)
                observer('movement-result',movement,0)
                rows=[dict(kind='movement-result',value=movement),
                      dict(kind='restore-result',value=dict(result='committed',warningSeen=True,inverseConsumed=True)),
                      dict(kind='final-pause',value=dict(ok=True))]
                path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
                batch.preferences.save_settings(settings,dict(original,warningSeconds=10,quietSeconds=5))
            with patch.object(batch,'FIXTURE',fixture),patch.object(batch,'capture',side_effect=[before,lg,before,before,hdmi,before]), \
                 patch.object(batch,'read_status',return_value=dict(paused=True,transactionActive=False)), \
                 patch.object(batch,'call_host',return_value=dict(ok=True)),patch.object(batch.driver,'main',side_effect=run_child), \
                 patch.object(batch.preferences,'default_settings_path',return_value=settings), \
                 patch.object(batch.QCoreApplication,'instance',return_value=True),patch.object(batch.signal,'signal'),patch.object(batch.signal,'setitimer') as timer, \
                 patch.object(batch.time,'sleep'),patch.object(batch.sys,'argv',['test','--receipt',str(receipt)]):
                batch.main()
            rows=[json.loads(line) for line in receipt.read_text().splitlines()]
            self.assertFalse(any(r['kind']=='error' for r in rows))
            self.assertEqual([c.args for c in timer.call_args_list],[(batch.signal.ITIMER_REAL,285),(batch.signal.ITIMER_REAL,0)])
            self.assertEqual([r['value']['output'] for r in rows if r['kind']=='gtk-forward-verified'],['DP-1','HDMI-A-1'])
            self.assertEqual(len([r for r in rows if r['kind']=='gtk-restore-verified']),2)
            self.assertEqual(rows[-1]['kind'],'final-pause')
            self.assertEqual(batch.preferences.load_settings(settings).values,original)


if __name__=='__main__':unittest.main()
