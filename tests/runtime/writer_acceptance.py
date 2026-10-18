"""Actual isolated KWin writer failure/rollback and toolkit acceptance.

Only the separate opt-in test effect can inject faults. The production writer
and observer have no fault switches. All changed windows belong to this harness.
"""
import ast
import json
import os
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
host=SessionHost(app,settings_path=path/'settings.json')
assert register_host(host)
started=time.monotonic();phase='configure';stage=0;ready_at=0;baseline=None;target=None;values=None;bus_lost=False;failure=None
results=[]
cases=[('wayland-commit','wayland','none'),('wayland-timeout-rollback','wayland','timeout'),
       ('wayland-input-after-write','wayland','input'),('wayland-external-frame','wayland','external'),
       ('wayland-client-constraint','wayland','constraint'),('xwayland-commit','xwayland','none'),
       ('xwayland-timeout-rollback','xwayland','timeout'),('settings-warning','wayland','none'),
       ('companion-warning','wayland','none'),('target-close-warning','wayland','none')]


def check(method,*args):
    reply=subprocess.run(['gdbus','call','--session','--dest','org.kde.KWin','--object-path','/org/oledwindowguard/NativeCheck',
        '--method','org.oledwindowguard.NativeCheck1.'+method,*args],check=True,capture_output=True,text=True,timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])


def frame_equal(a,b):
    return all(abs(a[key]-b[key])<=1e-9 for key in ('x','y','width','height'))


def save():
    (path/'writer-failure-progress.json').write_text(json.dumps(results,indent=2))


def tick():
    global phase,stage,ready_at,baseline,target,values,bus_lost,failure
    try:
        if time.monotonic()-started>120:raise RuntimeError('writer acceptance timed out at '+str((stage,phase)))
        if stage==len(cases):
            status=check('WriterStatus')
            assert not status['state'].get('transaction') and status['warningActive']==-1
            outcome={'result':'PASS','writerFailures':results,'writeAudit':status['writeHistory'],
                     'movementReady':host.status['movementReady'],'dimmingReady':host.status['dimmingReady'],
                     'normalSessionInstalled':False,'handInput':False}
            assert not outcome['movementReady'] and not outcome['dimmingReady']
            (path/'planning-result.json').write_text(json.dumps(outcome,indent=2));app.quit();return
        kind,toolkit,fault=cases[stage]
        if phase=='configure':
            if not host.status.get('planningReady'):return
            target=check('WriterTarget',toolkit);assert target.get('identity'),('owned target missing',toolkit,target)
            baseline=check('WriterStatus')
            window=next(w for w in baseline['desktop']['windows'] if w['identity']==target['identity'])
            assert window['focused'] is False and len(window['outputIds'])==1,window
            values=preferences.defaults()
            values.update(paused=False,movementDisplays=window['outputIds'],avoidFocusedWindow=True,shiftRangePercent=10,
                          quietSeconds=1,warningSeconds=3,maximumWindows=1,
                          excludedApps=['oled-window-guard-runtime-test'] if toolkit=='xwayland' else ['xterm','XTerm'])
            preferences.save_settings(host.settings_path,values);host.submit('SettingsChanged',{'revision':revision(values)})
            assert check('PrepareWriter')['present']
            assert check('WriterArmFault',fault)['armed']
            ready_at=time.monotonic()+1.6;phase='quiet';return
        if phase=='quiet':
            if time.monotonic()<ready_at:return
            if host.submit('PlanPreview',{'outputId':values['movementDisplays'][0]})['ok']:phase='preview'
            return
        if phase=='preview':
            report=host.status.get('planningResult',{})
            if report.get('status')=='ready':
                begin=check('WriterStart');assert begin.get('accepted'),begin
                tx=begin['state']['transaction'];assert tx['move']['identity']==target['identity'],tx
                target['plannedMove']=tx['move'];phase='running';ready_at=time.monotonic()+.6
                print('WRITER_CASE_STARTED '+kind,flush=True)
            elif report.get('status') in ('rejected','discarded','no-plan'):raise RuntimeError('writer preview failed: '+str(report))
            return
        status=check('WriterStatus')
        if phase=='running' and kind.endswith('-warning') and time.monotonic()>=ready_at:
            assert status['state']['phase']=='Warning' and status['writeCount']==baseline['writeCount'],status
            if kind=='settings-warning':
                values['shiftRangePercent']=11
                preferences.save_settings(host.settings_path,values);host.submit('SettingsChanged',{'revision':revision(values)})
            elif kind=='companion-warning':
                assert QDBusConnection.sessionBus().unregisterService('org.oledwindowguard.Controller1');bus_lost=True
            else:
                (path/'writer-client-trigger').write_text('destroy')
            phase='canceled';return
        if status['state'].get('transaction') or status['warningActive']!=-1:return
        delta=status['writeCount']-baseline['writeCount'];result=status['state']['lastResult'];assert result,result
        live=next((w for w in status['desktop']['windows'] if w['identity']==target['identity']),None)
        if fault=='timeout':
            assert result['status']=='failed-rolled-back' and delta==2 and live, status
            assert frame_equal(live['rect'],target['plannedMove']['fromRect']),status
        elif fault=='input':
            assert result['status']=='cancelled-write-outcome-unknown' and delta==1 and status['fault']['synchronousCanceled'],status
            assert live and frame_equal(live['rect'],target['plannedMove']['toRect']),status
        elif fault in ('external','constraint'):
            assert result['status']=='failed' and result['reason']=='external-frame-won' and delta==1,status
            expected=status['fault']['externalFrame' if fault=='external' else 'clientFrame']
            assert live and frame_equal(live['rect'],expected),status
            if fault=='constraint':assert live['rect']['width']>target['plannedMove']['toRect']['width'],status
        elif kind.endswith('-warning'):
            assert phase=='canceled' and result['status']=='cancelled' and delta==0,status
            if kind=='target-close-warning':assert live is None,status
            else:assert live and frame_equal(live['rect'],target['plannedMove']['fromRect']),status
        else:
            assert result['status']=='committed' and delta==1 and live,status
            assert frame_equal(live['rect'],result['frame']),status
        results.append({'kind':kind,'toolkit':toolkit,'writeDelta':delta,'result':result,'fault':status['fault'],
                        'target':target,'finalFrame':live['rect'] if live else None,'warningHidden':True})
        save();print('WRITER_CASE_PASS '+kind,flush=True)
        if bus_lost:
            assert QDBusConnection.sessionBus().registerService('org.oledwindowguard.Controller1');bus_lost=False
        stage+=1;phase='configure'
    except Exception as error:
        failure=str(error)
        (path/'planning-result.json').write_text(json.dumps({'result':'FAIL','reason':failure,'stage':stage,'phase':phase,
            'writerFailures':results},indent=2));app.quit()


timer=QTimer();timer.setInterval(50);timer.timeout.connect(tick);timer.start()
app.exec();host.dashboard.tray.hide()
sys.exit(1 if failure else 0)
