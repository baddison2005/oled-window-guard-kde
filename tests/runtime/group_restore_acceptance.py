"""Private KWin built-in group and one-shot Restore acceptance."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QTimer
from oled_window_guard.dashboard import create_application
from oled_window_guard.session import SessionHost,register_host,revision
from oled_window_guard import preferences

app=create_application([]);path=Path(sys.argv[1])
host=SessionHost(app,settings_path=path/'settings.json');assert register_host(host)
started=time.monotonic();phase='configure';stage=0;ready_at=0;failure=None
results=[];targets=None;values=None;baseline=None;committed=None;restore_baseline=None;start_reply=None
cases=['group-drift-restore','group-rotation-restore','group-shrink-restore','swap-restore',
       'restore-timeout','restore-warning-input','restore-settings-changed','restore-external-change','single-shift-restore','single-xwayland-restore']


def check(method,*args):
    reply=subprocess.run(['gdbus','call','--session','--dest','org.kde.KWin','--object-path','/org/oledwindowguard/NativeCheck',
        '--method','org.oledwindowguard.NativeCheck1.'+method,*args],check=True,capture_output=True,text=True,timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])


def equal(a,b):return all(abs(a[k]-b[k])<=1e-9 for k in ('x','y','width','height'))

def status():return check('SingleStatus' if cases[stage].startswith('single-') and phase in ('preview','forward') else 'WriterStatus')

def final_frames(s):return [{'identity':w['identity'],'frame':w['rect']} for w in s['desktop']['windows'] if w['identity'] in [t['identity'] for t in targets]]

def same_frames(a,b):return all(any(x['identity']==y['identity'] and equal(x['frame'],y['frame']) for y in b) for x in a) and len(a)==len(b)


def finish(s):
    global stage,phase
    result=s['state']['lastResult'];repeat=check('WriterRestore')
    assert not repeat['accepted'] and repeat['writeCount']==s['writeCount'],repeat
    assert repeat['state']['lastResult']['reason']=='undo-unavailable',repeat
    results.append({'kind':cases[stage],'forwardResult':committed['state']['lastResult'],'restoreResult':result,
        'settings':values,'initialTargets':targets,'finalTargets':final_frames(s),'forwardWrites':committed['writeCount']-baseline['writeCount'],
        'restoreWrites':s['writeCount']-restore_baseline['writeCount'],'repeatRefused':True,'warningHidden':s['warningActive']==-1,
        'restoreStart':start_reply})
    (path/'group-restore-progress.json').write_text(json.dumps(results,indent=2))
    print('GROUP_RESTORE_PASS '+cases[stage],flush=True);stage+=1;phase='configure'


def tick():
    global phase,stage,ready_at,failure,targets,values,baseline,committed,restore_baseline,start_reply
    try:
        if time.monotonic()-started>120:raise RuntimeError('group/restore timeout '+str((stage,phase)))
        if stage==len(cases):
            s=check('WriterStatus')
            assert not s['state'].get('tx') and s['warningActive']==-1
            outcome={'result':'PASS','groupRestore':results,'writeAudit':s['writeHistory'],'normalSessionInstalled':False,
                     'movementReady':host.status['movementReady'],'dimmingReady':host.status['dimmingReady'],'handInput':False}
            assert not outcome['movementReady'] and not outcome['dimmingReady']
            (path/'planning-result.json').write_text(json.dumps(outcome,indent=2));app.quit();return
        kind=cases[stage]
        if phase=='configure':
            if not host.status.get('planningReady'):return
            check('GroupSetup','shrink' if kind=='group-shrink-restore' else 'drift')
            if kind=='single-xwayland-restore':assert check('WriterTarget','xwayland')['identity']
            ready_at=time.monotonic()+.3;phase='configured';return
        if phase=='configured':
            if time.monotonic()<ready_at:return
            baseline=check('SingleStatus' if kind.startswith('single-') else 'WriterStatus')
            windows=[w for w in baseline['desktop']['windows'] if w['appId'] in ('oled-window-guard-runtime-test','xterm') and not w['focused'] and not w['minimized'] and w['rect']['width']>=200]
            targets=[{'identity':w['identity'],'frame':w['rect']} for w in windows];assert len(targets)==(3 if kind=='single-xwayland-restore' else 2),windows
            output=baseline['desktop']['outputs'][0]['id']
            values=preferences.defaults()
            values.update(paused=False,mode='swap' if kind=='swap-restore' else 'drift' if kind.startswith('single-') else 'group',
                groupId='builtin.thirds' if kind=='group-shrink-restore' else 'builtin.horizontal-halves',
                rotateGroup=kind!='group-drift-restore',movementDisplays=[output],avoidFocusedWindow=True,
                shiftRangePercent=10,quietSeconds=1,warningSeconds=3,maximumWindows=1 if kind in ('group-shrink-restore','single-shift-restore','single-xwayland-restore') else 2,
                allowTransientOverlap=False,excludedApps=['oled-window-guard-runtime-test'] if kind=='single-xwayland-restore' else ['xterm','XTerm'])
            if kind=="group-drift-restore":
                values["groupId"]="builtin.thirds"
                values["movementOverrides"]={output:{"groupId":"builtin.horizontal-halves"}}
            preferences.save_settings(host.settings_path,values);assert host.submit('SettingsChanged',{'revision':revision(values)})['ok']
            assert check('PrepareWriter')['present'];assert check('WriterArmFault','none')['armed']
            ready_at=time.monotonic()+1.6;phase='quiet';return
        if phase=='quiet':
            if time.monotonic()<ready_at:return
            if host.submit('PlanPreview',{'outputId':values['movementDisplays'][0]})['ok']:phase='preview'
            return
        if phase=='preview':
            report=host.status.get('planningResult',{})
            if report.get('status')=='ready':
                begin=check('SingleStart' if kind.startswith('single-') else 'WriterStart');assert begin['accepted'],begin
                phase='forward';print('GROUP_RESTORE_STARTED '+kind,flush=True)
            elif report.get('status') in ('rejected','discarded','no-plan'):raise RuntimeError('group preview failed '+str(report))
            return
        s=status()
        if phase=='forward':
            if s['state'].get('tx') or s['state'].get('transaction') or s['warningActive']!=-1 or s['state']['phase']=='Warning':return
            assert s['state']['lastResult']['status']=='committed',s
            if kind.startswith('single-'):assert s['undoRecord'],s
            else:assert s['state']['undoRecord'],s
            committed=s
            if kind=='restore-settings-changed':
                values['maximumWindows']=3;preferences.save_settings(host.settings_path,values)
                assert host.submit('SettingsChanged',{'revision':revision(values)})['ok']
                ready_at=time.monotonic()+.4;phase='restore-wait';return
            if kind=='restore-external-change':
                check('GroupSetup','external');ready_at=time.monotonic()+.3;phase='restore-wait';return
            phase='restore-wait';ready_at=time.monotonic();return
        if phase=='restore-wait':
            if time.monotonic()<ready_at:return
            fault='ordered-timeout-0' if kind=='restore-timeout' else 'none'
            assert check('WriterArmFault',fault)['armed']
            restore_baseline=check('WriterStatus');start_reply=check('WriterRestore')
            if kind in ('restore-settings-changed','restore-external-change'):
                assert not start_reply['accepted'],start_reply
                assert start_reply['state']['lastResult']['reason']==('settings-changed' if kind=='restore-settings-changed' else 'desktop-changed'),start_reply
                s=check('WriterStatus');assert s['writeCount']==restore_baseline['writeCount'],s
                finish(s);return
            assert start_reply['accepted'] and start_reply['state']['undoRecord'] is None,start_reply
            phase='restore';ready_at=time.monotonic()+.6;return
        if phase=='restore' and kind=='restore-warning-input' and time.monotonic()>=ready_at:
            assert s['state']['phase']=='Warning' and s['writeCount']==restore_baseline['writeCount'],s
            # Verify also checks the local controller/token cancellation receipt.
            check('Verify','pointer');phase='restore-canceled';return
        if s['state'].get('tx') or s['state']['phase']=='Warning' or s['warningActive']!=-1:return
        result=s['state']['lastResult'];delta=s['writeCount']-restore_baseline['writeCount']
        assert s['state']['undoRecord'] is None,s
        if kind=='restore-timeout':
            assert result['status']=='failed-rolled-back' and delta==2,s
            assert same_frames(final_frames(s),final_frames(committed)),s
        elif kind=='restore-warning-input':
            assert result['status']=='cancelled' and delta==0,s
            assert same_frames(final_frames(s),final_frames(committed)),s
        else:
            assert result['status']=='committed' and delta>0,s
            assert same_frames(final_frames(s),targets),s
            if kind=='group-shrink-restore':
                assert committed['writeCount']-baseline['writeCount']==2 and delta==2,s
                assert any(t['frame']['width']==301 for t in targets),targets
        finish(s)
    except Exception as error:
        failure=str(error)
        (path/'planning-result.json').write_text(json.dumps({'result':'FAIL','reason':failure,'stage':stage,'phase':phase,'groupRestore':results},indent=2));app.quit()


timer=QTimer();timer.setInterval(50);timer.timeout.connect(tick);timer.start()
app.exec();host.dashboard.tray.hide();sys.exit(1 if failure else 0)
