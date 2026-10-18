#!/usr/bin/env python3
"""Four imported-library freshness checks with reversible padding changes."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import desktop_cancellation_round as driver


def verify_round(rows,case):
    if any(r['kind']=='error' for r in rows):
        raise RuntimeError(next(r['value'] for r in rows if r['kind']=='error'))
    kinds={r['kind'] for r in rows}
    needed={'library-changed','library-restored','round-result','final-pause',
            'library-warning-result' if case=='imported-change-warning' else 'library-restore-result'}
    if not needed<=kinds:
        raise RuntimeError('Library change/refusal/restoration not verified')
    pause=next(r['value'] for r in reversed(rows) if r['kind']=='final-pause')
    if not pause or pause.get('ok') is not True:
        raise RuntimeError('Final Pause not acknowledged')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    def stop(signum,frame):raise SystemExit("Operator stopped library-change batch")
    signal.signal(signal.SIGTERM,stop)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with args.receipt.open('x') as stream:
        def record(kind,value):
            stream.write(json.dumps(dict(elapsed=time.monotonic()-started,kind=kind,value=value))+'\n')
            stream.flush()
        original_argv=sys.argv
        try:
            stages=[(output,case) for output in ('DP-1','HDMI-A-1') for case in ('imported-change-warning','imported-change-restore')]
            for output,case in stages:
                child=args.receipt.with_name(args.receipt.stem+'-'+output+'-'+case+'.jsonl')
                record('library-stage',dict(output=output,case=case))
                def relay(kind,value,elapsed):
                    if kind=='status':value=dict(value,batchOutput=output)
                    record('batch-stage-result' if kind=='round-result' else kind,value)
                sys.argv=['round','--receipt',str(child),'--output',output,'--case',case,'--delay','5']
                driver.main(observer=relay)
                verify_round([json.loads(line) for line in child.read_text().splitlines()],case)
            record('round-result',dict(case='imported-change-both',result='four-library-change-rounds-verified',physicalObservationPending=True))
        except Exception as error:
            record('error',str(error) or type(error).__name__)
        finally:
            sys.argv=original_argv


if __name__=='__main__':main()
