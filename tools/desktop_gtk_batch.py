"""One Start: GTK3/4 Wayland single Move/Restore on LG and HDMI."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QCoreApplication
from oled_window_guard import preferences
from oled_window_guard.session import call_host,revision
from tools import desktop_cancellation_round as driver
from tools.desktop_xwayland_batch import verify_round
from tools.desktop_resize_refusal import capture,read_status
from tools.desktop_gtk_windows import APP_ID

FIXTURE=Path('/tmp/owg-desktop-physical-gtk-windows.jsonl')
READBACK_TOLERANCE=.5


def verify_scope(snapshot):
    for output in snapshot['outputs']:
        visible=[w for w in snapshot['windows'] if w.get('visible') is True and
                 w.get('onCurrentDesktop') is True and w.get('onCurrentActivity') is True and
                 output['id'] in w.get('outputIds',[])]
        if sum(w.get('appId')==APP_ID for w in visible)!=2:
            raise RuntimeError('Two GTK windows must be on the CURRENT test desktop of '+output['connector'])
        if any(w.get('appId') not in (APP_ID,'oled-window-guard') and w.get('managed') is True and
               w.get('movable') is True and w.get('windowType')=='normal' for w in visible):
            raise RuntimeError('Use an empty test desktop; other movable applications are present')


def verify_fixture(sample,outputs,gtk_version="3"):
    if {o.get('connector') for o in outputs}!={'DP-1','HDMI-A-1'}:raise RuntimeError('Both expected outputs required')
    if sample.get('toolkit')!='GTK'+gtk_version or sample.get('platform')!='wayland' or 'Wayland' not in sample.get('displayType','') or sample.get('appId')!=APP_ID:
        raise RuntimeError('Expected GTK'+gtk_version+' Wayland fixtures')
    windows=sample.get('windows',[])
    if len(windows)!=4 or not all(w.get('visible') and len(w.get('clientSize',[]))==2 for w in windows):
        raise RuntimeError('Four visible GTK targets required')
    for output in outputs:
        geometry=[output['geometry'][k] for k in ('x','y','width','height')]
        if sum(w.get('monitorGeometry')==geometry for w in windows)!=2:
            raise RuntimeError('Place TWO GTK targets on '+output['connector'])


def verify_restored(before,after):
    def frames(sample):return {w['identity']:w['rect'] for w in sample['windows'] if w.get('appId')==APP_ID}
    original=frames(before);restored=frames(after)
    if len(original)!=4 or set(original)!=set(restored):raise RuntimeError('GTK target identity changed')
    # Match the production readback tolerance, not ideal integer coordinates.
    if any(abs(rect[k]-restored[identity][k])>READBACK_TOLERANCE for identity,rect in original.items() for k in ('x','y','width','height')):
        raise RuntimeError('GTK compositor frames did not restore within production readback tolerance')


def verify_forward(before,moved,output_name):
    originals={w['identity']:w for w in before['windows'] if w.get('appId')==APP_ID}
    current={w['identity']:w for w in moved['windows'] if w.get('appId')==APP_ID}
    if len(originals)!=4 or set(originals)!=set(current):raise RuntimeError('GTK target identity changed during movement')
    output=next(o['id'] for o in before['outputs'] if o['connector']==output_name)
    changed=[]
    for identity,w in originals.items():
        target=current[identity]
        if any(abs(w['rect'][k]-target['rect'][k])>READBACK_TOLERANCE for k in ('width','height')):
            raise RuntimeError('GTK frame size changed during movement')
        if any(abs(w['rect'][k]-target['rect'][k])>READBACK_TOLERANCE for k in ('x','y')):
            if w['outputIds']!=[output] or target['outputIds']!=[output]:raise RuntimeError('GTK movement affected another output')
            changed.append(identity)
    if len(changed)!=1:raise RuntimeError('Expected exactly one GTK window to move')
    return dict(output=output_name,movedIdentity=changed[0],otherFramesUnchanged=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--gtk-version',choices=('3','4'),default='3')
    args=parser.parse_args();app=QCoreApplication.instance() or QCoreApplication([])
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    fixture=Path('/tmp/owg-desktop-physical-gtk4-windows.jsonl') if args.gtk_version=='4' else FIXTURE
    started=time.monotonic();original_argv=sys.argv;original=None
    def interrupted(signum,frame):raise RuntimeError('Operator stopped GTK test')
    signal.signal(signal.SIGTERM,interrupted)
    def timed_out(signum,frame):raise RuntimeError('GTK batch exceeded 285-second time limit')
    signal.signal(signal.SIGALRM,timed_out)
    with args.receipt.open('x') as stream:
        signal.setitimer(signal.ITIMER_REAL,285)
        def record(kind,value):
            stream.write(json.dumps(dict(elapsed=time.monotonic()-started,kind=kind,value=value))+'\n');stream.flush()
        try:
            state=read_status()
            if not state['paused'] or state['transactionActive']:raise RuntimeError('GTK test requires paused idle product')
            loaded=preferences.load_settings(preferences.default_settings_path())
            if loaded.error:raise RuntimeError(loaded.error)
            original=loaded.values;record('original-settings',original)
            for output in ('DP-1','HDMI-A-1'):
                if output=='HDMI-A-1':
                    record('operator-switch-panel','LG GTK round finished. Move this panel to LG, click/release on both desktops, and Continue. Keep HDMI GTK windows uncovered.')
                    deadline=time.monotonic()+180
                    while not args.receipt.with_suffix('.continue').exists():
                        if time.monotonic()>deadline:raise RuntimeError('HDMI setup confirmation timed out')
                        time.sleep(.1)
                snapshot=json.loads(fixture.read_text().splitlines()[-1])
                before=capture();verify_fixture(snapshot,before['outputs'],args.gtk_version);verify_scope(before)
                record('gtk-fixture',snapshot);record('batch-output',output)
                child=args.receipt.with_name(args.receipt.stem+'-'+output+'.jsonl')
                forward=[]
                def relay(kind,value,elapsed):
                    record('batch-stage-result' if kind=='round-result' else kind,value)
                    if kind=='movement-result' and value.get('result')=='committed':
                        evidence=verify_forward(before,capture(),output)
                        forward.append(evidence);record('gtk-forward-verified',evidence)
                sys.argv=['round','--receipt',str(child),'--output',output,'--case','move-restore','--delay','5']
                driver.main(observer=relay)
                verify_round([json.loads(line) for line in child.read_text().splitlines()])
                if len(forward)!=1:raise RuntimeError('Forward GTK scope not verified')
                after=capture();verify_restored(before,after)
                after_fixture=json.loads(fixture.read_text().splitlines()[-1]);verify_fixture(after_fixture,after['outputs'],args.gtk_version)
                sizes={w['title']:w['clientSize'] for w in snapshot['windows']}
                for line in fixture.read_text().splitlines():
                    sample=json.loads(line)
                    if sample['monotonic']>=snapshot['monotonic'] and {w['title']:w['clientSize'] for w in sample['windows']}!=sizes:
                        raise RuntimeError('GTK client size changed during Move/Restore')
                record('gtk-restore-verified',dict(output=output,allFramesRestored=True,clientSizesPreserved=True,readbackTolerance=READBACK_TOLERANCE))
            record('round-result',dict(case='gtk4-both' if args.gtk_version=='4' else 'gtk-both',toolkit='GTK'+args.gtk_version,result='both-gtk-wayland-move-restore-rounds-verified',physicalObservationPending=True))
        except Exception as error:record('error',str(error) or type(error).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGTERM,signal.SIG_IGN)
            sys.argv=original_argv
            pause=call_host('Pause');record('cleanup-pause',pause)
            if not pause or not pause.get('ok'):record('error','Cleanup Pause unconfirmed')
            if original is not None:
                try:
                    restored=dict(original,paused=True)
                    preferences.save_settings(preferences.default_settings_path(),restored)
                    response=call_host('SettingsChanged',{'revision':revision(restored)})
                    record('settings-restored',response)
                    if not response or not response.get('ok'):raise RuntimeError('Original settings not acknowledged')
                except Exception as error:record('error','Cleanup: '+str(error))
            response=call_host('Pause');record('final-pause',response)
            if not response or not response.get('ok'):record('error','Final Pause unconfirmed')


if __name__=='__main__':main()
