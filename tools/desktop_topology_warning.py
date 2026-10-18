"""Operator cable removal during a warning; no display configuration writes."""
import time


def topology_evidence(baseline, fresh):
    """Distinguish topology disarm from the command's first cancellation reason."""
    def identity(event):
        return tuple(event.get(k) for k in ('epoch','generation','reason','at'))
    prior={identity(e) for e in baseline.get('cancellationTrace',[])}
    events=[e for e in fresh.get('cancellationTrace',[]) if identity(e) not in prior]
    for first in events:
        if not first.get('commandId') or first.get('armed') is not True:
            continue
        if first.get('reason')=='native-topology':
            return dict(mode='topology-first',first=first,topology=first)
        if first.get('reason')!='native-input':continue
        for topology in events:
            if (topology.get('reason')=='native-topology' and topology.get('epoch')==first.get('epoch')
                    and topology.get('generation',-1)>first.get('generation',-1)
                    and topology.get('topologyGeneration',-1)>first.get('topologyGeneration',-1)
                    and 0<=topology.get('at',0)-first.get('at',0)<=250):
                return dict(mode='input-before-topology',first=first,topology=topology)
    return None


def disconnect_during_warning(current,output,status,intent,record,clock=time.monotonic,sleep=time.sleep):
    if not current.get('skipAvailable') or not current.get('transactionActive'):
        raise RuntimeError('Cable test requires an active movement warning')
    expected={r['name']:r['id'] for r in current.get('displays',[])}
    if set(expected)!={'DP-1','HDMI-A-1'}:raise RuntimeError('Both monitors required before disconnect')
    other='HDMI-A-1' if output=='DP-1' else 'DP-1'
    record('topology-warning-baseline',dict(output=output,status=current))
    record('topology-instructions','Warning is visible: unplug ONLY '+output+' now. Keep mouse/keyboard still. Leave the cable disconnected until prompted.')
    deadline=clock()+12
    while clock()<deadline:
        fresh=status(); names={r['name'] for r in fresh.get('displays',[])}
        if output not in names and other in names:break
        if not fresh.get('transactionActive'):
            raise RuntimeError('Warning ended before target disconnect was observed; cancellation case not established')
        if fresh.get('warningRemainingSeconds',10)<=3:
            intent('Skip')
            raise RuntimeError('Disconnect missed; warning skipped before movement')
        sleep(.1)
    else:raise RuntimeError('Target disconnect not observed')
    record('warning-output-disconnected',dict(output=output,status=fresh))
    cancel_deadline=clock()+2
    while fresh.get('transactionActive') and clock()<cancel_deadline:
        sleep(.1);fresh=status()
    result=fresh.get('lastResult','')
    record('topology-cancellation-diagnostics',dict(output=output,result=result,
        trace=fresh.get('cancellationTrace',[]),status=fresh))
    evidence=topology_evidence(current,fresh)
    if (fresh.get('transactionActive') or fresh.get('restoreAvailable') or fresh.get('paused') is not True
            or not result.startswith(('cancelled:','rejected:')) or not evidence):
        raise RuntimeError('Topology cancellation not verified: '+result)
    record('topology-warning-cancelled',dict(output=output,result=result,evidence=evidence))
    def checked():
        sample=status()
        if any(sample.get(k) is not v for k,v in dict(paused=True,transactionActive=False,movementReady=False,restoreAvailable=False,skipAvailable=False).items()):
            raise RuntimeError('Product did not remain paused without movement or inverse')
        return sample
    dwell=max(5,current.get('warningRemainingSeconds',10)+2)
    record('topology-instructions','Disconnect observed; keep '+output+' disconnected for about '+str(round(dwell))+' seconds. Wait for the reconnect prompt.')
    end=clock()+dwell
    while clock()<end:
        fresh=checked()
        if output in {r['name'] for r in fresh.get('displays',[])}:
            raise RuntimeError('Monitor reconnected before warning-expiry dwell completed')
        sleep(.1)
    record('topology-instructions','Reconnect '+output+' now. When both monitors settle, click/release on EACH desktop. Do not Resume or Start again.')
    deadline=clock()+120
    while clock()<deadline:
        fresh=checked()
        if {r['name']:r['id'] for r in fresh.get('displays',[])}==expected and fresh.get('resumeReady') is True and fresh.get('nativeObservation',{}).get('available') is True:
            record('topology-warning-recovered',dict(output=output,oldWarningExpired=True,noResumeAfterDisconnect=True,status=fresh))
            return fresh
        sleep(.1)
    raise RuntimeError('Both displays and ready input facts did not recover while paused')
