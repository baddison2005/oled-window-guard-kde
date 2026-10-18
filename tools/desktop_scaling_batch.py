#!/usr/bin/env python3
"""Fractional-scale group movement/Restore followed by baseline display recovery."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import desktop_cancellation_round as driver
from tools.desktop_scaling import topology, require_scales, wait_baseline
from oled_window_guard.session import call_host


def verify_fixture(snapshot):
    if snapshot.get('profile')!='fixed-bounded' or not snapshot.get('platform','').startswith('wayland'):
        raise RuntimeError('Expected constrained Wayland fixtures')
    windows=snapshot.get('windows',[])
    if len(windows)!=4:raise RuntimeError('Expected four constraint targets')
    for w in windows:
        if '[fixed]' in w.get('title',''):
            if any(w.get(k)!=[420,180] for k in ('clientSize','minimumSize','maximumSize')):
                raise RuntimeError('Fixed-size constraint not preserved')
        elif '[bounded]' in w.get('title',''):
            if w.get('minimumSize')!=[320,140] or w.get('maximumSize')!=[560,260]:
                raise RuntimeError('Bounded size constraints not preserved')
            size=w.get('clientSize',[])
            if len(size)!=2 or not 320<=size[0]<=560 or not 140<=size[1]<=260:
                raise RuntimeError('Bounded client size outside constraints')
        else:raise RuntimeError('Unknown constrained target')


def verify_round(rows):
    if any(r['kind']=='error' for r in rows):
        raise RuntimeError(next(r['value'] for r in rows if r['kind']=='error'))
    movement=next((r['value'] for r in rows if r['kind']=='movement-result'),{})
    if movement.get('result')!='committed' or movement.get('warningSeen') is not True:
        raise RuntimeError('Forward movement not committed after warning')
    restore=next((r['value'] for r in rows if r['kind']=='restore-result'),{})
    pause=next((r['value'] for r in reversed(rows) if r['kind']=='final-pause'),{})
    if restore.get('result')!='committed' or restore.get('inverseConsumed') is not True or restore.get('warningSeen') is not True or not pause or pause.get('ok') is not True:
        raise RuntimeError('Scheduled Restore or final Pause not verified')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with args.receipt.open('x') as stream:
        def record(kind,value):
            stream.write(json.dumps(dict(elapsed=time.monotonic()-started,kind=kind,value=value))+'\n')
            stream.flush()
        original_argv=sys.argv
        try:
            baseline=json.loads((ROOT/'docs/evidence/desktop-physical-2026-10-10/scaling-baseline.json').read_text())
            require_scales(topology())
            record('fractional-scaling',topology())
            fixture=Path('/tmp/owg-desktop-physical-2026-10-10-constrained-windows.jsonl')
            snapshot=json.loads(fixture.read_text().splitlines()[-1])
            verify_fixture(snapshot)
            for name in ('DP-1','HDMI-A-1'):
                if sum(w.get('visible') and w.get('screen')==name for w in snapshot['windows'])!=2:
                    raise RuntimeError('Expected two visible constrained fixtures per monitor')
            record('fixture-constraints',snapshot)
            for output in ('DP-1','HDMI-A-1'):
                if output=='HDMI-A-1':
                    confirmation=args.receipt.with_suffix('.continue')
                    record('operator-switch-panel','LG round finished. Move this panel to LG, then click Panel is on LG — continue. Keep HDMI targets in their left/right halves.')
                    deadline=time.monotonic()+180
                    while not confirmation.exists():
                        if time.monotonic()>=deadline:raise RuntimeError('Panel-switch confirmation timed out; HDMI not armed')
                        time.sleep(.1)
                    record('panel-switch-confirmed',True)
                child=args.receipt.with_name(args.receipt.stem+'-'+output+'.jsonl')
                record('batch-output',output)
                def relay(kind,value,elapsed):
                    if kind=='status':value=dict(value,batchOutput=output)
                    record('batch-stage-result' if kind=='round-result' else kind,value)
                sys.argv=['round','--receipt',str(child),'--output',output,'--case','group-restore','--delay','5']
                driver.main(observer=relay)
                verify_round([json.loads(line) for line in child.read_text().splitlines()])
                after=json.loads(fixture.read_text().splitlines()[-1])
                verify_fixture(after)
                before_sizes={w['title']:w['clientSize'] for w in snapshot['windows']}
                if {w['title']:w['clientSize'] for w in after['windows']}!=before_sizes:
                    raise RuntimeError('Client size changed during constrained group round')
                for line in fixture.read_text().splitlines():
                    sample=json.loads(line)
                    if sample['monotonic']>=snapshot['monotonic']:
                        verify_fixture(sample)
                        if {w['title']:w['clientSize'] for w in sample['windows']}!=before_sizes:
                            raise RuntimeError('Observed intermediate client resize during constrained round')
                require_scales(topology())
                record('constraint-sizes-preserved',dict(output=output,sizes=before_sizes))
            wait_baseline(baseline,record)
            record('round-result',dict(case='scaling-both',result='two-fractional-scale-group-rounds-and-baseline-recovery-verified',physicalObservationPending=True))
        except Exception as error:
            record('error',str(error))
        finally:
            sys.argv=original_argv
            record("final-pause",call_host("Pause"))


if __name__=='__main__':main()
