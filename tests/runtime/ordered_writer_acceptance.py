"""Private KWin ordered-writer acceptance with owned Wayland windows."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QTimer
from PySide6.QtDBus import QDBusConnection
from oled_window_guard.dashboard import create_application
from oled_window_guard.session import SessionHost,register_host,revision
from oled_window_guard import preferences

app=create_application([]);path=Path(sys.argv[1])
host=SessionHost(app,settings_path=path/'settings.json');assert register_host(host)
started=time.monotonic();phase='configure';stage=0;ready_at=0;failure=None;bus_lost=False
results=[];baseline=None;targets=None;values=None;steps=None;fallback_refusals=0
cases=[('strict-swap-commit','none'),('coordinated-commit','none'),
       ('input-warning','none'),('settings-warning','none'),('companion-warning','none'),('first-step-timeout','ordered-timeout-0'),
       ('middle-step-timeout','ordered-timeout-1'),('last-step-timeout','ordered-timeout-2'),
       ('reverse-timeout','ordered-rollback-timeout'),('input-after-middle-write','ordered-input-1'),
       ('external-middle-frame','ordered-external-1')]


def check(method,*args):
    reply=subprocess.run(['gdbus','call','--session','--dest','org.kde.KWin','--object-path','/org/oledwindowguard/NativeCheck',
        '--method','org.oledwindowguard.NativeCheck1.'+method,*args],check=True,capture_output=True,text=True,timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])


def equal(a,b):
    return all(abs(a[k]-b[k])<=1e-9 for k in ('x','y','width','height'))


def tick():
    global phase,stage,ready_at,baseline,targets,values,steps,failure,bus_lost,fallback_refusals
    try:
        if time.monotonic()-started>120:raise RuntimeError('ordered acceptance timeout '+str((stage,phase)))
        if stage==len(cases):
            status=check('WriterStatus')
            assert not status['state'].get('tx') and status['warningActive']==-1,status
            outcome={'result':'PASS','orderedWriter':results,'writeAudit':status['writeHistory'],
                     'movementReady':host.status['movementReady'],'dimmingReady':host.status['dimmingReady'],
                     'normalSessionInstalled':False,'handInput':False}
            assert not outcome['movementReady'] and not outcome['dimmingReady']
            (path/'planning-result.json').write_text(json.dumps(outcome,indent=2));app.quit();return
        kind,fault=cases[stage]
        if phase=='configure':
            if not host.status.get('planningReady'):return
            targets=check('OrderedSetup');assert len(targets)==2,targets
            baseline=check('WriterStatus')
            windows=[w for w in baseline['desktop']['windows'] if w['identity'] in [t['identity'] for t in targets]]
            assert len(windows)==2 and all(w['focused'] is False for w in windows),windows
            values=preferences.defaults()
            values.update(paused=False,mode='drift' if kind=='coordinated-commit' else 'swap',
                          shiftHorizontalOrder=kind=='coordinated-commit',movementDisplays=windows[0]['outputIds'],avoidFocusedWindow=True,
                          shiftRangePercent=100,quietSeconds=1,warningSeconds=3,maximumWindows=2,
                          allowTransientOverlap=False,excludedApps=['xterm','XTerm'])
            preferences.save_settings(host.settings_path,values);assert host.submit('SettingsChanged',{'revision':revision(values)})['ok']
            assert check('PrepareWriter')['present'];assert check('WriterArmFault',fault)['armed']
            ready_at=time.monotonic()+1.6;phase='quiet';return
        if phase=='quiet':
            if time.monotonic()<ready_at:return
            if host.submit('PlanPreview',{'outputId':values['movementDisplays'][0]})['ok']:phase='preview'
            return
        if phase=='preview':
            report=host.status.get('planningResult',{})
            if report.get('status')=='ready':
                begin=check('WriterStart')
                if kind=='coordinated-commit' and report.get('reason')=='ordinary_shift_fallback':
                    assert not begin['accepted'] and begin['writeCount']==baseline['writeCount'],begin
                    fallback_refusals+=1
                    assert fallback_refusals<=8,'coordinated planner exhausted bounded fresh requests'
                    phase='quiet';ready_at=time.monotonic()+.3;return
                assert begin['accepted'],begin
                assert begin['state']['phase']=='Warning' and begin['writeCount']==baseline['writeCount'],begin
                phase='running';ready_at=time.monotonic()+.6;print('ORDERED_STARTED '+kind,flush=True)
            elif report.get('status') in ('rejected','discarded','no-plan'):raise RuntimeError('ordered preview failed '+str(report))
            return
        status=check('WriterStatus')
        if phase=='running' and kind.endswith('-warning') and time.monotonic()>=ready_at:
            assert status['state']['phase']=='Warning' and status['writeCount']==baseline['writeCount'],status
            if kind=='input-warning':
                assert check('Verify','pointer')['ok']
            elif kind=='settings-warning':
                values['maximumWindows']=3
                preferences.save_settings(host.settings_path,values);host.submit('SettingsChanged',{'revision':revision(values)})
            else:
                assert QDBusConnection.sessionBus().unregisterService('org.oledwindowguard.Controller1');bus_lost=True
            phase='canceled';return
        if status['state'].get('tx'):
            steps=status['state']['tx']['steps']
        if status['state']['phase']=='Warning' or status['state'].get('tx') or status['warningActive']!=-1:return
        result=status['state']['lastResult'];assert result,status
        delta=status['writeCount']-baseline['writeCount']
        audit=status['writeHistory'][len(baseline['writeHistory']):]
        writes=[r for r in audit if 'actor' not in r]
        assert len(writes)==delta,(writes,delta)
        forward=[r for r in writes if not r['rollback']];reverse=[r for r in writes if r['rollback']]
        assert [r['stepIndex'] for r in forward]==list(range(len(forward))),writes
        if kind.endswith('-warning'):
            assert phase=='canceled' and result['status']=='cancelled' and delta==0,status
            for t in targets:
                live=next(w for w in status['desktop']['windows'] if w['identity']==t['identity'])
                assert equal(live['rect'],t['frame']),status
        elif kind=='coordinated-commit':
            assert result['status']=='committed' and delta==2 and not reverse,status
            assert len(result['alteredFrames'])==2,result
        elif fault=='none':
            assert result['status']=='committed' and delta==3 and not reverse,status
            assert [r['stepIndex'] for r in writes]==[0,1,2],writes
            for t in targets:
                live=next(w for w in status['desktop']['windows'] if w['identity']==t['identity'])
                other=next(o for o in targets if o['identity']!=t['identity'])
                assert equal(live['rect'],other['frame']),status
        elif fault.startswith('ordered-timeout-'):
            index=int(fault[-1]);assert result['status']=='failed-rolled-back' and delta==2*(index+1),status
            assert len(reverse)==len(forward)==index+1,writes
            for f,r in zip(reversed(forward),reverse):
                assert f['identity']==r['identity'] and equal(f['from'],r['to']) and equal(f['to'],r['from']),writes
            for t in targets:
                live=next(w for w in status['desktop']['windows'] if w['identity']==t['identity'])
                assert equal(live['rect'],t['frame']),status
            assert not result['alteredFrames'],result
        elif fault=='ordered-rollback-timeout':
            assert result['status']=='failed-uncertain' and delta==3 and len(reverse)==1,status
            assert result['alteredFrames'] and result['unverifiedFrames'],result
        elif fault=='ordered-input-1':
            assert result['status']=='cancelled-write-outcome-unknown' and delta==2 and not reverse,status
            assert status['fault']['synchronousCanceled'],status
        else:
            assert result['status']=='failed-uncertain' and delta==2 and not reverse,status
            live=next(w for w in status['desktop']['windows'] if w['identity']==writes[-1]['identity'])
            assert equal(live['rect'],status['fault']['externalFrame']),status
            assert result['unverifiedFrames'],result
        results.append({'kind':kind,'writeDelta':delta,'result':result,'fault':status['fault'],'initialTargets':targets,
                        'writes':writes,'finalTargets':[w for w in status['desktop']['windows'] if w['identity'] in [t['identity'] for t in targets]],
                        'warningHidden':True,'ordinaryFallbackRefusals':fallback_refusals if kind=='coordinated-commit' else 0})
        (path/'ordered-writer-progress.json').write_text(json.dumps(results,indent=2))
        print('ORDERED_PASS '+kind,flush=True)
        if bus_lost:
            assert QDBusConnection.sessionBus().registerService('org.oledwindowguard.Controller1');bus_lost=False
        stage+=1;phase='configure';steps=None
    except Exception as error:
        failure=str(error)
        (path/'planning-result.json').write_text(json.dumps({'result':'FAIL','reason':failure,'stage':stage,'phase':phase,'orderedWriter':results},indent=2));app.quit()


timer=QTimer();timer.setInterval(50);timer.timeout.connect(tick);timer.start()
app.exec();host.dashboard.tray.hide();sys.exit(1 if failure else 0)
