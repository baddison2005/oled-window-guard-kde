"""Read-only verification of fractional scaling and baseline recovery."""
import json
import subprocess
import time


def topology():
    raw=json.loads(subprocess.run(['kscreen-doctor','-j'],check=True,capture_output=True,text=True,timeout=5).stdout)
    result={}
    for output in raw['outputs']:
        if not output.get('enabled') or not output.get('connected'):continue
        mode=next(m for m in output['modes'] if m['id']==output['currentModeId'])
        result[output['name']]=dict(scale=output['scale'],pos=output['pos'],rotation=output['rotation'],mode=dict(size=mode['size'],refreshRate=mode['refreshRate']))
    return result


def require_scales(current):
    expected={'DP-1':1.25,'HDMI-A-1':1.5}
    if set(current)!=set(expected) or any(abs(current[name]['scale']-scale)>.001 for name,scale in expected.items()):
        raise RuntimeError('Before Start, set LG to 125% and HDMI to 150% in Display Configuration')


def wait_baseline(baseline,record,read=topology,clock=time.monotonic,sleep=time.sleep):
    record('operator-restore-scales','Both scaled rounds finished paused. Set BOTH monitors back to 100% in Display Configuration, restore the original bottom-aligned arrangement (HDMI to the right of LG), and Apply. Then click/release on both monitors.')
    deadline=clock()+240
    while clock()<deadline:
        current=read()
        if current==baseline:
            record('scale-baseline-restored',current)
            return
        sleep(1)
    raise RuntimeError('Original display scales/layout not yet restored; use saved baseline receipt')
