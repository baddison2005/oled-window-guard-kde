"""Real-file imported groups through product intents in disposable KWin."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QTimer
from oled_window_guard.dashboard import create_application
from oled_window_guard.session import SessionHost, register_host, revision
from oled_window_guard import preferences
from oled_window_guard.adapter import AdapterLifetime

app = create_application([])
path = Path(sys.argv[1])
host = SessionHost(app, settings_path=path/'settings.json')
assert register_host(host)
lifetime=AdapterLifetime()
assert lifetime.start() is None and lifetime.owned
bootstrap=dict(installedAdapterLoaded=True, owned=True)
cases = ['imported-drift-restore', 'imported-rotation-restore', 'ordinary-product-restore', 'imported-timeout',
         'imported-slow-readback',
         'imported-warning-change', 'imported-after-write-change', 'imported-restore-change',
         'product-restore-quiet-input', 'product-skip', 'product-pause', 'imported-invalid',
         'product-scheduled', 'product-observer-loss']
started = time.monotonic()
stage = 0
phase = 'setup'
ready_at = 0
baseline = None
committed = None
values = None
targets = None
request = None
fault_applied = False
results = []
failure = None

def check(method, *args):
    reply = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.kde.KWin',
        '--object-path', '/org/oledwindowguard/NativeCheck', '--method',
        'org.oledwindowguard.NativeCheck1.'+method, *args], check=True,
        capture_output=True, text=True, timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])

class DelayedProvider:
    """Test-only delay after a real write, leaving the GUI/heartbeat responsive."""
    def __init__(self, provider):
        self.provider=provider
        self.delayed=False
    def read_snapshot(self):
        if stage<len(cases) and cases[stage]=='imported-slow-readback' and not self.delayed and baseline:
            if check('WriterStatus')['writeCount']>baseline['writeCount']:
                self.delayed=True
                time.sleep(.85)
        return self.provider.read_snapshot()

host.layout_provider=DelayedProvider(host.layout_provider)

def source(change=False, invalid=False):
    layouts = [dict(id='left', name='Left', x=0, y=0, width=.5, height=1, groupId='pair'),
               dict(id='right', name='Right', x=.5, y=0, width=.5, height=1, groupId='pair')]
    if change:
        layouts[0]['width'] = .49
    if invalid:
        layouts[0].pop('id')
    text = (path/'config/kwinrc').read_text().split('[Script-windowlayouts]')[0]
    text += '[Script-windowlayouts]\n'
    for key, value in dict(CustomLayouts=layouts, CustomGroups=[dict(id='pair', name='Pair')],
                           GroupOrder=['pair'], LayoutPadding=8).items():
        text += key+'='+json.dumps(value, separators=(',', ':'))+'\n'
    (path/'config/kwinrc').write_text(text)

def frames(status):
    return {w['identity']: w['rect'] for w in status['desktop']['windows'] if w['identity'] in targets}

def same(a, b):
    return a.keys()==b.keys() and all(all(abs(a[i][k]-b[i][k])<1e-9 for k in a[i]) for i in a)

def save():
    preferences.save_settings(host.settings_path, values)
    assert host.submit('SettingsChanged', {'revision': revision(values)})['ok']

def finish(status, detail):
    global stage, phase
    results.append(dict(kind=cases[stage], detail=detail, runtimeStatus=dict(host.status),
                        writerResult=status['state']['lastResult'], writeCount=status['writeCount'],
                        finalFrames=frames(status)))
    (path/'imported-product-progress.json').write_text(json.dumps(results, indent=2))
    print('IMPORTED_PRODUCT_PASS '+cases[stage], flush=True)
    stage += 1
    phase = 'setup'

def tick():
    global stage, phase, ready_at, baseline, committed, values, targets, request, fault_applied, failure
    try:
        if time.monotonic()-started > 210:
            raise RuntimeError('acceptance-timeout '+str((stage, phase, host.status)))
        if stage == len(cases):
            status = check('WriterStatus')
            lifetime.stop()
            assert not check('Inspect')['present']
            bootstrap['ownedStopRemovedObserver']=True
            assert lifetime.start() is None and lifetime.owned
            outcome = dict(result='PASS', importedProduct=results, writeAudit=status['writeHistory'],
                           adapterLifetime=bootstrap,
                           normalSessionInstalled=False, movementReady=host.status['movementReady'],
                           dimmingReady=host.status['dimmingReady'], handInput=False)
            assert not outcome['dimmingReady']
            (path/'planning-result.json').write_text(json.dumps(outcome, indent=2))
            app.quit()
            return
        kind = cases[stage]
        if phase == 'setup':
            if not host.status.get('planningReady'):
                return
            assert host.submit('Pause')['ok']
            source(invalid=kind=='imported-invalid')
            check('GroupSetup', 'drift')
            check('PrepareWriter')
            check('WriterArmFault', 'none')
            ready_at = time.monotonic()+.4
            phase = 'configure'
            fault_applied = False
            return
        if phase == 'configure':
            if time.monotonic()<ready_at:
                return
            baseline = check('WriterStatus')
            targets = {w['identity']:w['rect'] for w in baseline['desktop']['windows']
                       if w['appId']=='oled-window-guard-runtime-test' and not w['focused'] and w['rect']['width']==220}
            assert len(targets)==2
            output = baseline['desktop']['outputs'][0]['id']
            values = preferences.defaults()
            values.update(paused=True, mode='drift' if kind=='ordinary-product-restore' else 'group',
                          groupId='kde:pair', rotateGroup=kind=='imported-rotation-restore',
                          movementDisplays=[output], maximumWindows=2, quietSeconds=1, warningSeconds=3,
                          excludedApps=['xterm', 'XTerm'], shiftRangePercent=10)
            save()
            if kind=='product-scheduled':
                values['intervalMinutes']=120
                values['movementOverrides']={output:{'intervalMinutes':1}}
                save()
            phase = 'resume'
            return
        if phase == 'resume':
            if not host.status.get('resumeReady'):
                return
            assert host.dashboard.pause_resume_button.isEnabled()
            host.dashboard._send('Resume')
            phase = 'resumed'
            return
        if phase == 'resumed':
            if not host.status.get('movementReady') or host.runtime_pending:
                return
            assert host.dashboard.move_now_button.isEnabled()
            if kind=='product-scheduled':
                check('WriterObserve', 'ordered')
                phase='scheduled'
                ready_at=time.monotonic()+75
                return
            # Use the actual dashboard dispatch, through the product transport.
            check("WriterObserve", "single" if kind=="ordinary-product-restore" else "ordered")
            if kind=='imported-timeout':
                assert check('WriterArmFault', 'ordered-timeout-0')['armed']
            host.dashboard._send('MoveNow')
            request = host.runtime_pending
            assert request
            phase = 'forward'
            return
        status = check('WriterStatus')
        if phase=='scheduled':
            assert time.monotonic()<ready_at, 'scheduled transaction timeout'
            if status['writeCount']-baseline['writeCount']<2 or host.status.get('transactionActive'):
                return
            assert status['state']['lastResult']['status']=='committed', status
            finish(status, 'real one-minute per-display override scheduled a guarded transaction')
            return
        if phase == 'forward':
            active_warning = status['warningActive']!=-1
            writes = status['writeCount']-baseline['writeCount']
            if not fault_applied and active_warning and kind in ('imported-warning-change', 'product-skip', 'product-pause', 'product-observer-loss'):
                if kind=='product-skip' and not host.status.get('skipAvailable'):
                    return
                fault_applied = True
                if kind=='imported-warning-change':
                    source(change=True)
                elif kind=='product-skip':
                    assert host.status['skipAvailable']
                    host.dashboard._send('Skip')
                elif kind=='product-pause':
                    host.dashboard._send('Pause')
                else:
                    # Last case: retire the companion's observer envelope while
                    # keeping its service alive. No new controller authority.
                    host.retired_observers.add(host.observer_epoch)
            if not fault_applied and writes and kind=='imported-after-write-change':
                source(change=True)
                fault_applied = True
            if host.runtime_pending or host.status.get('transactionActive') or status['warningActive']!=-1:
                return
            if kind in ('imported-warning-change', 'product-skip', 'product-pause', 'imported-invalid', 'product-observer-loss'):
                assert writes==0, (kind, status)
                assert same(frames(status), targets), status
                if kind=='product-observer-loss':
                    assert not host.status['resumeReady'] and not host.status['movementReady']
                finish(status, 'zero-write refusal/cancellation')
                return
            if kind=='imported-after-write-change':
                assert writes==1 and fault_applied, status
                assert status['state']['lastResult']['status']!='committed', status
                finish(status, 'one landed assignment; changed library prevents continuation/recovery')
                return
            if kind in ('imported-timeout','imported-slow-readback'):
                assert writes==2 and status['state']['lastResult']['status']=='failed-rolled-back', status
                assert same(frames(status), targets), status
                assert not host.status['restoreAvailable']
                if kind=='imported-slow-readback':
                    assert host.layout_provider.delayed
                finish(status, 'landed unconfirmed step reconciled and reversed with fresh imported provider reads')
                return
            if kind=='ordinary-product-restore':
                single = check('SingleStatus')
                assert single['state']['lastResult']['status']=='committed', single
            else:
                assert status['state']['lastResult']['status']=='committed', status
                assert writes==2, status
            committed = status
            if kind=='imported-restore-change':
                source(change=True)
            if kind=='product-restore-quiet-input':
                check('Verify', 'pointer')
            phase = 'restore'
            return
        if phase=='restore':
            if not host.status.get('restoreAvailable'):
                return
            check("WriterObserve", "ordered")
            host.dashboard._send('Restore')
            assert host.runtime_pending
            phase = 'restoring'
            return
        if phase=='restoring':
            if kind=='product-restore-quiet-input' and not fault_applied and status['state']['undoRecord'] is None and status['warningActive']==-1:
                check('Verify', 'pointer')
                fault_applied=True
            if host.runtime_pending or host.status.get('transactionActive') or status['warningActive']!=-1:
                return
            if kind in ('imported-restore-change', 'product-restore-quiet-input'):
                assert status['writeCount']==committed['writeCount'], status
                assert status['state']['undoRecord'] is None, status
                assert not host.status['restoreAvailable']
                finish(status, 'Restore request consumed inverse before quiet/warning; canceled input or changed library cannot revive it')
            else:
                assert status['state']['lastResult']['status']=='committed', status
                assert same(frames(status), targets), status
                assert not host.status['restoreAvailable']
                finish(status, 'product movement and one-shot exact Restore committed')
    except Exception as error:
        failure = str(error)
        (path/'planning-result.json').write_text(json.dumps(dict(result='FAIL', reason=failure,
            stage=stage, phase=phase, importedProduct=results, status=host.status), indent=2))
        app.quit()

timer = QTimer()
timer.setInterval(25)
timer.timeout.connect(tick)
timer.start()
app.exec()
host.layout_executor.shutdown(wait=False, cancel_futures=True)
host.dashboard.tray.hide()
sys.exit(1 if failure else 0)
