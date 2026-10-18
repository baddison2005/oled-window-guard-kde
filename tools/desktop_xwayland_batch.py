#!/usr/bin/env python3
"""Two XWayland single-window movement/Restore rounds in one operator run."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import desktop_cancellation_round as driver


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
            fixture=Path('/tmp/owg-desktop-physical-2026-10-10-xwayland-windows.jsonl')
            snapshot=json.loads(fixture.read_text().splitlines()[-1])
            if snapshot.get('platform')!='xcb':raise RuntimeError('Expected XWayland xcb fixtures')
            for name in ('DP-1','HDMI-A-1'):
                if sum(w.get('visible') and w.get('screen')==name for w in snapshot['windows'])!=2:
                    raise RuntimeError('Expected two visible XWayland fixtures per monitor')
            record('fixture-backend',snapshot)
            for output in ('DP-1','HDMI-A-1'):
                child=args.receipt.with_name(args.receipt.stem+'-'+output+'.jsonl')
                record('batch-output',output)
                def relay(kind,value,elapsed):
                    if kind=='status':value=dict(value,batchOutput=output)
                    record('batch-stage-result' if kind=='round-result' else kind,value)
                sys.argv=['round','--receipt',str(child),'--output',output,'--case','move-restore','--delay','5']
                driver.main(observer=relay)
                verify_round([json.loads(line) for line in child.read_text().splitlines()])
            record('round-result',dict(case='xwayland-both',result='two-xwayland-move-and-restore-rounds-verified',physicalObservationPending=True))
        except Exception as error:
            record('error',str(error))
        finally:
            sys.argv=original_argv


if __name__=='__main__':main()
