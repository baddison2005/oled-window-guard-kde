"""One operator start, KDE reload during LG and HDMI movement warnings."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import desktop_cancellation_round as driver
from oled_window_guard.session import call_host
from tools.desktop_reconfigure_warning import require_fixed_installation


def verify_round(rows):
    errors=[r['value'] for r in rows if r['kind']=='error']
    if errors:raise RuntimeError(str(errors[0]))
    recovery=next((r['value'] for r in rows if r['kind']=='reconfigure-recovery-verified'),{})
    if not all(recovery.get(k) is True for k in ('observerUnloaded','sameController','oldWarningExpired')):
        raise RuntimeError('KDE reload recovery not verified')
    pause=next((r['value'] for r in reversed(rows) if r['kind']=='final-pause'),{})
    if not pause or pause.get('ok') is not True:raise RuntimeError('Final Pause unconfirmed')


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
        original=sys.argv
        try:
            require_fixed_installation()
            for output in ('DP-1','HDMI-A-1'):
                if output=='HDMI-A-1':
                    record('operator-input-recovery','LG check finished paused. Click/release once on EACH monitor, then click Input refreshed — continue. Leave all windows in place.')
                    deadline=time.monotonic()+180
                    while not args.receipt.with_suffix('.continue').exists():
                        if time.monotonic()>=deadline:raise RuntimeError('Input confirmation timed out; HDMI not armed')
                        time.sleep(.1)
                child=args.receipt.with_name(args.receipt.stem+'-'+output+'.jsonl')
                record('batch-output',output)
                def relay(kind,value,elapsed):
                    record('batch-stage-result' if kind=='round-result' else kind,value)
                sys.argv=['round','--receipt',str(child),'--output',output,
                          '--case','reconfigure-warning','--delay','5']
                driver.main(observer=relay)
                verify_round([json.loads(line) for line in child.read_text().splitlines()])
            record('round-result',dict(case='reconfigure-both',result='both-warning-reloads-and-paused-recovery-verified',physicalObservationPending=True))
        except Exception as error:
            record('error',str(error) or type(error).__name__)
        finally:
            sys.argv=original
            record('final-pause',call_host('Pause'))


if __name__=='__main__':main()
