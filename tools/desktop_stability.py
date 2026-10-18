"""Bounded continuous normal scheduling: three GTK cycles per output, one Resume."""
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
from tools.desktop_resize_refusal import capture,read_status
from tools.desktop_gtk_batch import FIXTURE,APP_ID,verify_fixture,verify_scope,verify_forward,verify_restored

COUNTS={'DP-1':3,'HDMI-A-1':3}
INTERVALS={'DP-1':60,'HDMI-A-1':65}
WARNING_SECONDS=3
PAUSED_DWELL=70
MAX_RUN_SECONDS=285


def moved_output(before,after):
    original={w['identity']:w for w in before['windows'] if w.get('appId')==APP_ID}
    current={w['identity']:w for w in after['windows'] if w.get('appId')==APP_ID}
    if set(original)!=set(current) or len(original)!=4:raise RuntimeError('GTK target identities changed')
    changed=[w for identity,w in original.items() if any(abs(w['rect'][k]-current[identity]['rect'][k])>.5 for k in ('x','y'))]
    if len(changed)!=1:raise RuntimeError('Expected one scheduled GTK window movement')
    ids=changed[0]['outputIds']
    if len(ids)!=1:raise RuntimeError('Moved GTK window spans outputs')
    name=next(o['connector'] for o in before['outputs'] if o['id']==ids[0])
    verify_forward(before,after,name)
    return name


def verify_sizes(fixture,before):
    sizes={w['title']:w['clientSize'] for w in before['windows']}
    for line in fixture.read_text().splitlines():
        sample=json.loads(line)
        if sample['monotonic']>=before['monotonic']:
            if sample.get('platform')!='wayland' or {w['title']:w['clientSize'] for w in sample['windows']}!=sizes:
                raise RuntimeError('GTK client sizes or identities changed during continuous scheduling')


def paused_observation(status,observe,record,baseline,done=None,clock=time.monotonic,sleep=time.sleep,dwell=PAUSED_DWELL):
    def paused(s):
        if any(s.get(k) is not v for k,v in dict(paused=True,transactionActive=False,
                skipAvailable=False,restoreAvailable=False,movementReady=False).items()):
            raise RuntimeError('Product rearmed or retained a transaction/inverse during paused dwell')
    deadline=clock()+2
    while True:
        s=status();paused(s)
        if s.get('resumeReady') is True:break
        # Pause intentionally returns unavailable_status until the next exchange.
        # Permit only that initial reset, not live unsafe facts or later loss.
        reset=(s.get('displays')==[] and s.get('planningReady') is False and
               s.get('nativeObservation',{}).get('available') is False)
        if not reset or clock()>=deadline:
            record('failed-paused-status',s)
            raise RuntimeError('Paused readiness did not recover after status reset')
        record('paused-readiness-settling',dict(remainingSeconds=max(0,deadline-clock())))
        sleep(.1)
    record('paused-readiness-recovered',dict(resumeReady=True,paused=True))
    record('stability-paused-dwell',dict(seconds=dwell,completed=done or {}))
    end=clock()+dwell;last_progress=-1
    while clock()<end:
        s=status();paused(s)
        if s.get('resumeReady') is not True:
            record('failed-paused-status',s)
            raise RuntimeError('Readiness lost during paused dwell; uninterrupted awake observation not established')
        if int(clock())!=last_progress:
            record('stability-paused-wait',dict(remainingSeconds=max(0,round(end-clock())),completed=done or {}))
            last_progress=int(clock())
        sleep(.1)
    verify_restored(baseline,observe())
    record('stability-paused-verified',dict(seconds=dwell,completed=done or {},allFramesUnchanged=True))


def run_cycles(status,intent,observe,record,baseline,counts=COUNTS,intervals=INTERVALS,
               clock=time.monotonic,sleep=time.sleep,dwell=PAUSED_DWELL):
    """Testable monitor loop. It cannot issue MoveNow, settings changes or Resume."""
    done={name:0 for name in counts};armed_at=clock()
    def wait_transaction(phase,scheduled=False):
        deadline=clock()+(max(intervals.values())+35 if scheduled else 35)
        active=False;warning=False;last=None;last_progress=-1
        while clock()<deadline:
            s=status()
            if s.get('paused') is True:raise RuntimeError('Product paused during continuous scheduling: '+str(s.get('lastResult')))
            active|=s.get('transactionActive') is True
            warning|=s.get('skipAvailable') is True
            key=(s.get('transactionActive'),s.get('skipAvailable'),int(s.get('warningRemainingSeconds',0)),s.get('lastResult'))
            if key!=last:
                record('status',{k:s.get(k) for k in ('paused','transactionActive','skipAvailable','warningRemainingSeconds','restoreAvailable','lastResult')}|{'testPhase':phase})
                last=key
            if scheduled and not active and int(clock())!=last_progress:
                record('stability-wait',dict(completed=done,elapsedSeconds=round(clock()-armed_at),message='Waiting for the normal timer. Stay idle.'))
                last_progress=int(clock())
            if active and not s.get('transactionActive'):
                if not warning or s.get('lastResult')!='committed':
                    record('failed-transaction-status',s)
                    raise RuntimeError(phase+' did not commit after a warning: '+str(s.get('lastResult')))
                return s
            sleep(.1)
        raise RuntimeError(phase+' timed out')
    while any(done[name]<counts[name] for name in counts):
        completed=wait_transaction('scheduled movement',scheduled=True)
        if clock()-armed_at<min(intervals.values())-5:raise RuntimeError('First movement occurred before normal timer deadline')
        if not completed.get('restoreAvailable'):raise RuntimeError('Scheduled movement has no inverse')
        forward=observe();output=moved_output(baseline,forward)
        if output not in done or done[output]>=counts[output]:raise RuntimeError('Unexpected extra scheduled cycle on '+output)
        record('scheduled-cycle-committed',dict(output=output,cycle=done[output]+1,elapsedSeconds=clock()-armed_at,otherFramesUnchanged=True))
        baseline=forward
        done[output]+=1
        record('stability-cycle-verified',dict(output=output,cycle=done[output],otherFramesUnchanged=True,completed=dict(done)))
    intent('Pause')
    paused_observation(status,observe,record,baseline,done,clock,sleep,dwell)
    return done


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--paused-only',action='store_true')
    args=parser.parse_args();app=QCoreApplication.instance() or QCoreApplication([])
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();original=None;expected=None;applied=False
    def interrupted(signum,frame):raise RuntimeError('Operator stopped stability test')
    signal.signal(signal.SIGTERM,interrupted)
    limit=90 if args.paused_only else MAX_RUN_SECONDS
    def timed_out(signum,frame):raise RuntimeError(str(limit)+'-second physical test limit reached; stopping for cleanup')
    signal.signal(signal.SIGALRM,timed_out)
    with args.receipt.open('x') as stream:
        def record(kind,value):
            stream.write(json.dumps(dict(elapsed=time.monotonic()-started,kind=kind,value=value))+'\n');stream.flush()
        def intent(action,payload=None):
            allowed=('Pause','SettingsChanged') if args.paused_only else ('Pause','Resume','SettingsChanged','PlanPreview')
            if action not in allowed:raise RuntimeError('Forbidden stability intent: '+action)
            response=call_host(action,payload);record(action,response)
            if not response or not response.get('ok'):raise RuntimeError(action+' refused: '+str(response.get('reason') if response else 'controller unavailable'))
            return response
        signal.setitimer(signal.ITIMER_REAL,limit)
        try:
            state=read_status()
            if not state['paused'] or state['transactionActive']:raise RuntimeError('Requires paused idle product')
            loaded=preferences.load_settings(preferences.default_settings_path())
            if loaded.error:raise RuntimeError(loaded.error)
            original=loaded.values
            args.receipt.with_suffix('.original-settings.json').write_text(json.dumps(original,indent=2)+'\n')
            fixture=json.loads(FIXTURE.read_text().splitlines()[-1]);baseline=capture()
            verify_fixture(fixture,baseline['outputs']);verify_scope(baseline)
            record('gtk-fixture',fixture);record('original-settings',original)
            ids={o['connector']:o['id'] for o in baseline['outputs']}
            expected=dict(original,paused=True,movementDisplays=list(ids.values()),mode='drift',maximumWindows=1,
                intervalMinutes=1,warningSeconds=WARNING_SECONDS,quietSeconds=5,avoidFocusedWindow=True,
                shiftHorizontalOrder=False,shiftVerticalOrder=False,
                movementOverrides={ids[name]:dict(intervalMinutes=seconds/60) for name,seconds in INTERVALS.items()})
            preferences.save_settings(preferences.default_settings_path(),expected);applied=True
            intent('SettingsChanged',{'revision':revision(expected)})
            record('stability-setup',dict(counts=COUNTS if not args.paused_only else {},intervalSeconds=INTERVALS,warningSeconds=WARNING_SECONDS,pausedDwellSeconds=PAUSED_DWELL,estimatedTotalSeconds=75 if args.paused_only else 285,maxRunSeconds=limit))
            time.sleep(5)
            deadline=time.monotonic()+15
            while not read_status().get('resumeReady'):
                if time.monotonic()>deadline:raise RuntimeError('Input facts not ready; click/release on both desktops before Start')
                time.sleep(.1)
            if args.paused_only:
                intent('Pause')
                paused_observation(read_status,capture,record,baseline)
                verify_sizes(FIXTURE,fixture)
                record('round-result',dict(case='stability-paused',result='awake-paused-readiness-and-unchanged-frames-verified',
                    pausedDwellSeconds=PAUSED_DWELL,noResume=True,noMoveNow=True,clientSizesPreserved=True,physicalObservationPending=True))
                return
            for output in ('DP-1','HDMI-A-1'):
                preview=intent('PlanPreview',{'outputId':ids[output]});deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    result=read_status().get('planningResult',{})
                    if result.get('id')==preview.get('requestId') and result.get('status') not in ('queued','planning'):
                        if result.get('status')!='ready' or result.get('moves')!=1:raise RuntimeError('Expected one eligible preview on '+output+': '+str(result))
                        record('stability-preflight',dict(output=output,result=result));break
                    time.sleep(.1)
                else:raise RuntimeError('Planning preflight timed out')
            intent('Resume');deadline=time.monotonic()+5
            while True:
                s=read_status()
                if s.get('movementReady') and not s.get('transactionActive'):break
                if time.monotonic()>deadline:raise RuntimeError('Resume did not settle')
                time.sleep(.1)
            record('continuous-resume-settled',s)
            done=run_cycles(read_status,intent,capture,record,baseline)
            verify_sizes(FIXTURE,fixture)
            record('round-result',dict(case='stability',result='six-continuous-scheduled-movements-and-paused-dwell-verified',completed=done,
                clientSizesPreserved=True,singleResume=True,noMoveNow=True,physicalObservationPending=True))
        except Exception as error:record('error',str(error) or type(error).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGTERM,signal.SIG_IGN)
            response=call_host('Pause');record('cleanup-pause',response)
            if not response or not response.get('ok'):record('error','Cleanup Pause unconfirmed')
            if applied and original is not None:
                try:
                    current=preferences.load_settings(preferences.default_settings_path())
                    if current.error or dict(current.values,paused=True)!=dict(expected,paused=True):
                        raise RuntimeError('Settings changed externally; original backup retained, refusing overwrite')
                    restored=dict(original,paused=True);preferences.save_settings(preferences.default_settings_path(),restored)
                    response=call_host('SettingsChanged',{'revision':revision(restored)});record('settings-restored',response)
                    if not response or not response.get('ok'):raise RuntimeError('Original settings not acknowledged')
                except Exception as error:record('error','Cleanup: '+str(error))
            response=call_host('Pause');record('final-pause',response)
            if not response or not response.get('ok'):record('error','Final Pause unconfirmed')


if __name__=='__main__':main()
