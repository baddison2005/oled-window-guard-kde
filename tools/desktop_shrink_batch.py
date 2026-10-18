"""Operator-started GTK4 guarded two-pixel shrink/Restore on both outputs."""
import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QCoreApplication
from oled_window_guard import preferences
from oled_window_guard.layout_provider import LayoutProvider
from oled_window_guard.session import call_host,revision
from tools.build_planning_worker import build
from tools.desktop_resize_refusal import capture,read_status,test_geometry,TemporaryLibrary
from tools.desktop_gtk_batch import APP_ID,verify_fixture,verify_scope,verify_restored,READBACK_TOLERANCE

FIXTURE=Path('/tmp/owg-desktop-physical-gtk4-windows.jsonl')


def geometry(snapshot,output_name):
    verify_scope(snapshot)
    output=next(o for o in snapshot['outputs'] if o['connector']==output_name)
    targets=sorted((w for w in snapshot['windows'] if w.get('appId')==APP_ID and
                   w.get('outputIds')==[output['id']] and w.get('onCurrentDesktop') and
                   w.get('onCurrentActivity') and w.get('visible') and w.get('resizable') is True and
                   not w.get('focused')),key=lambda w:w['identity'])
    if not targets:raise RuntimeError('No unfocused resizable GTK4 target on '+output_name)
    return test_geometry(snapshot,output_name,target_identity=targets[0]['identity'])


def probe(worker,snapshot,parts,library):
    output,target,zones,obstacles,layouts,group=parts
    request=dict(worker=str(worker),snapshot=snapshot,output=output,identity=target['identity'],
                 zones=zones,obstacles=obstacles,library=library,groupId='kde:'+group['id'],expectShrink=True)
    response=subprocess.run(['node',str(ROOT/'tools/desktop_resize_probe.js')],input=json.dumps(request),
                            text=True,capture_output=True,timeout=5)
    if response.returncode:raise RuntimeError('Shrink preflight failed: '+response.stderr[-2048:])
    return json.loads(response.stdout)


def verify_shrink(before,after,identity,destination,fixture_before,fixture_after):
    original={w['identity']:w for w in before['windows'] if w.get('appId')==APP_ID}
    current={w['identity']:w for w in after['windows'] if w.get('appId')==APP_ID}
    if len(original)!=4 or set(original)!=set(current):raise RuntimeError('GTK4 target identities changed')
    for key,w in original.items():
        expected=destination if key==identity else w['rect']
        actual=current[key]
        if actual['outputIds']!=w['outputIds'] or any(abs(actual['rect'][k]-expected[k])>READBACK_TOLERANCE for k in ('x','y','width','height')):
            raise RuntimeError('Shrink geometry or unchanged other-window scope not verified')
    old={w['title']:w['clientSize'] for w in fixture_before['windows']}
    new={w['title']:w['clientSize'] for w in fixture_after['windows']}
    if set(old)!=set(new):raise RuntimeError('GTK4 client identities changed')
    changed=[title for title in old if old[title]!=new[title]]
    if len(changed)!=1 or new[changed[0]]!=[old[changed[0]][0]-2,old[changed[0]][1]]:
        raise RuntimeError('Expected exactly one actual GTK4 client width to shrink by two pixels')
    return dict(target=identity,clientTitle=changed[0],originalClientSize=old[changed[0]],
                shrunkenClientSize=new[changed[0]],otherFramesAndSizesUnchanged=True)


def preflight_only():
    snapshot=capture();sample=json.loads(FIXTURE.read_text().splitlines()[-1])
    verify_fixture(sample,snapshot['outputs'],'4')
    # Clone the current imported catalog in memory: no user library writes.
    provider=LayoutProvider();raw=provider._sample()
    with tempfile.TemporaryDirectory(prefix='owg-shrink-preflight-') as folder:
        folder=Path(folder);worker=folder/'worker.js';worker.write_text(build())
        outputs=[]
        for name in ('DP-1','HDMI-A-1'):
            memory=dict(raw)
            clone=LayoutProvider(runner=lambda argv,timeout:memory[argv[argv.index('--key')+1]],debounce_seconds=0)
            library=TemporaryLibrary(folder/(name+'-backup.json'),provider=clone,write=lambda k,v:memory.__setitem__(k,v))
            parts=geometry(snapshot,name)
            imported=library.apply(parts[4],parts[5]);planned=probe(worker,snapshot,parts,imported)
            outputs.append(dict(output=name,target=parts[1]['identity'],destination=parts[2][1],plan=planned))
        return dict(readOnly=True,toolkit='GTK4',outputs=outputs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args();app=QCoreApplication.instance() or QCoreApplication([])
    if args.preflight_only:print(json.dumps(preflight_only()));return
    if not args.receipt:parser.error('--receipt required')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    original=None;expected=None;library=None;started=time.monotonic()
    def interrupted(signum,frame):raise RuntimeError('Shrink test stopped or exceeded 285-second limit')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGALRM,interrupted)
    with args.receipt.open('x') as stream:
        def record(kind,value):
            stream.write(json.dumps(dict(elapsed=time.monotonic()-started,kind=kind,value=value))+'\n');stream.flush()
        def intent(action,payload=None):
            reply=call_host(action,payload);record(action,reply)
            if not reply or not reply.get('ok'):raise RuntimeError(action+' refused')
            return reply
        def wait_ready(key,seconds=15):
            deadline=time.monotonic()+seconds
            while not read_status().get(key):
                if time.monotonic()>deadline:raise RuntimeError(key+' did not recover; click/release both desktops before Start')
                time.sleep(.1)
        def transaction(phase):
            deadline=time.monotonic()+35;warning=False;previous=None
            while time.monotonic()<deadline:
                state=read_status();warning|=state.get('skipAvailable') is True
                compact={k:state.get(k) for k in ('paused','transactionActive','skipAvailable','warningRemainingSeconds','lastResult','restoreAvailable')}
                if compact!=previous:record('status',dict(state,testPhase=phase));previous=compact
                if not state['transactionActive']:
                    if not warning or state.get('lastResult')!='committed':raise RuntimeError(phase+' did not commit after its warning')
                    if bool(state.get('restoreAvailable'))!=(phase=='movement'):raise RuntimeError('Inverse state did not match '+phase)
                    record(phase+'-result',dict(result='committed',warningSeen=True,inverseConsumed=phase=='restore'))
                    return
                time.sleep(.1)
            raise RuntimeError(phase+' transaction timed out')
        signal.setitimer(signal.ITIMER_REAL,285)
        try:
            state=read_status()
            if not state['paused'] or state['transactionActive']:raise RuntimeError('Requires paused idle product')
            loaded=preferences.load_settings(preferences.default_settings_path())
            if loaded.error:raise RuntimeError(loaded.error)
            original=loaded.values;record('original-settings',original)
            library=TemporaryLibrary(args.receipt.with_suffix('.library-backup.json'))
            worker=args.receipt.with_suffix('.worker.js');worker.write_text(build())
            for name in ('DP-1','HDMI-A-1'):
                if name=='HDMI-A-1':
                    record('operator-switch-panel','LG shrink/Restore verified. Move panel to LG, click/release on both desktops, then Continue. Keep HDMI targets uncovered.')
                    deadline=time.monotonic()+120
                    while not args.receipt.with_suffix('.continue').exists():
                        if time.monotonic()>deadline:raise RuntimeError('Panel confirmation timed out')
                        time.sleep(.1)
                record('batch-output',name);time.sleep(5)
                before=capture();fixture_before=json.loads(FIXTURE.read_text().splitlines()[-1])
                verify_fixture(fixture_before,before['outputs'],'4');parts=geometry(before,name)
                output,target,zones,obstacles,layouts,group=parts
                imported=library.apply(layouts,group)
                record('shrink-preflight',probe(worker,before,parts,imported))
                expected=dict(original,paused=True,movementDisplays=[output['id']],mode='group',groupId='kde:'+group['id'],
                              rotateGroup=True,maximumWindows=1,intervalMinutes=240,warningSeconds=10,quietSeconds=5,
                              avoidFocusedWindow=True,movementOverrides={},sizeTolerance=.1)
                preferences.save_settings(preferences.default_settings_path(),expected)
                intent('SettingsChanged',{'revision':revision(expected)});wait_ready('resumeReady')
                preview=intent('PlanPreview',{'outputId':output['id']});deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    result=read_status().get('planningResult',{})
                    if result.get('id')==preview.get('requestId') and result.get('status') not in ('queued','planning'):
                        if result.get('status')!='ready' or result.get('moves')!=1 or result.get('reason')!='group-rotation':
                            raise RuntimeError('Expected exactly one shrink rotation: '+str(result))
                        record('shrink-preview',result);break
                    time.sleep(.1)
                else:raise RuntimeError('Shrink preview timed out')
                intent('Resume');wait_ready('movementReady',5)
                intent('MoveNow',{'outputId':output['id']});transaction('movement')
                moved=capture();fixture_moved=json.loads(FIXTURE.read_text().splitlines()[-1])
                evidence=verify_shrink(before,moved,target['identity'],zones[1],fixture_before,fixture_moved)
                record('shrink-forward-verified',dict(evidence,output=name))
                time.sleep(3);intent('Restore');transaction('restore')
                restored=capture();verify_restored(before,restored)
                fixture_after=json.loads(FIXTURE.read_text().splitlines()[-1])
                if {w['title']:w['clientSize'] for w in fixture_after['windows']}!={w['title']:w['clientSize'] for w in fixture_before['windows']}:
                    raise RuntimeError('Original GTK4 client sizes did not restore')
                record('shrink-restore-verified',dict(output=name,allFramesAndClientSizesRestored=True,inverseConsumed=True))
                intent('Pause')
            record('round-result',dict(case='shrink-both',result='both-gtk4-shrink-and-restore-rounds-verified',physicalObservationPending=True))
        except Exception as error:record('error',str(error) or type(error).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGTERM,signal.SIG_IGN)
            response=call_host('Pause');record('cleanup-pause',response)
            if not response or not response.get('ok'):record('error','Cleanup Pause unconfirmed')
            try:
                if library:
                    library.restore();record('library-restored',dict(revision=library.provider._revision(library.original)))
                if original is not None and expected is not None:
                    current=preferences.load_settings(preferences.default_settings_path())
                    if current.error or dict(current.values,paused=True)!=dict(expected,paused=True):raise RuntimeError('Settings changed externally; use original-settings backup for recovery')
                    recovered=dict(original,paused=True);preferences.save_settings(preferences.default_settings_path(),recovered)
                    response=call_host('SettingsChanged',{'revision':revision(recovered)});record('settings-restored',response)
                    if not response or not response.get('ok'):raise RuntimeError('Settings recovery unconfirmed')
            except Exception as error:record('error','Cleanup: '+str(error))
            response=call_host('Pause');record('final-pause',response)
            if not response or not response.get('ok'):record('error','Final Pause unconfirmed')


if __name__=='__main__':main()
