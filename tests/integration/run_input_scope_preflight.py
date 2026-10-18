#!/usr/bin/env python3
"""Virtual two-output regression of physical scope fixture; no hardware claim."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from direct_output_scope import (check_log, coherent_state, keyboard_result, load_stage,
                                 plan, unload_stage, validate_frame)
from run_input_direct import (CLIENT, private_environment, run_private_session, terminate,
                              verify_environment, verify_probe)


def worker(directory, plugin):
    env = private_environment(directory, plugin, warning=True)
    # Match authenticated-lock logging: readiness must survive debug suppression.
    env['QT_LOGGING_RULES'] = 'kwin_scripting.debug=true;qml.debug=false;qt.qml.debug=false'
    env.pop('OWG_INPUT_PROBE_DIRECT')
    trigger = directory/'trigger'
    trigger.write_text('')
    env['OWG_INPUT_TEST_TRIGGER'] = str(trigger)
    instructions = directory/'instructions.txt'
    instructions.write_text('Private synthetic fixture preflight; no physical acceptance.')
    kwin = client = None
    report = {'status':'fail','scope':'virtual output-scope fixture; not physical input', 'stages':[]}
    with (directory/'kwin.log').open('w') as log, (directory/'client.log').open('w') as client_log:
        try:
            kwin = subprocess.Popen(['kwin_wayland','--virtual','--width','900','--height','650',
                                     '--output-count','2','--socket','owg-scope-0','--no-lockscreen','--no-kactivities'],
                                    env=env,stdout=log,stderr=subprocess.STDOUT)
            def wait(check, seconds=10):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    if kwin.poll() is not None or (client and client.poll() is not None):
                        raise RuntimeError('Private scope compositor/client exited')
                    try:
                        found=check()
                        if found: return found
                    except subprocess.CalledProcessError:
                        pass
                    time.sleep(.1)
                raise RuntimeError('Private scope fixture timed out')
            initial=wait(lambda:coherent_state(env),25)
            _, facts, outputs=initial
            if any(facts['libinput'].values()): raise RuntimeError('Virtual input had physical origin')
            try: verify_environment(facts,1)
            except RuntimeError: pass
            else: raise RuntimeError('Virtual fixture met direct backend requirements')
            client_env=dict(env,WAYLAND_DISPLAY='owg-scope-0',OWG_INPUT_CLIENT_DIRECT='1',
                            OWG_INPUT_CLIENT_WINDOWED='1',OWG_INPUT_CLIENT_INSTRUCTIONS=str(instructions),
                            OWG_INPUT_CLIENT_HEADING='Private output-scope preflight')
            client=subprocess.Popen([sys.executable,'-u',str(CLIENT)],env=client_env,
                                    stdout=client_log,stderr=subprocess.STDOUT)
            wait(lambda:'CLIENT_READY' in (directory/'client.log').read_text())
            for index,stage in enumerate(plan(outputs)):
                package_id=load_stage(directory,env,index,stage,outputs)
                ready=wait(lambda:check_log((directory/'kwin.log').read_text(errors='replace'),index))
                frame=validate_frame(ready,stage,outputs)
                before,origin_before,_=wait(lambda:coherent_state(env))
                client_before=(directory/'client.log').read_text().count('CLIENT_EVENT keyboard')
                trigger.write_text('keyboard-only\n')
                def delivered():
                    sample=coherent_state(env)
                    return sample if sample and sample[0]['keyboard']>=before['keyboard']+2 and (directory/'client.log').read_text().count('CLIENT_EVENT keyboard')>client_before else None
                after,origin_after,_=wait(delivered)
                expected={row['id']:2 if row['id'] in stage['expectedOutputs'] else 0 for row in outputs}
                old={row['id']:row['events'] for row in before['outputs']}
                delta={row['id']:row['events']-old[row['id']] for row in after['outputs']}
                if delta!=expected: raise RuntimeError('Synthetic fixture attribution mismatch: '+str(delta))
                client_delta=(directory/'client.log').read_text().count('CLIENT_EVENT keyboard')-client_before
                if keyboard_result(before,after,origin_before,origin_after,client_delta,stage['expectedOutputs']) is not None:
                    raise RuntimeError('Synthetic events passed physical evidence check')
                check_log((directory/'kwin.log').read_text(errors='replace'),index)
                unload_stage(env,package_id)
                report['stages'].append(dict(name=stage['name'],frame=frame,outputEventDeltas=delta,
                                             scriptUnloaded=True,syntheticPhysicalAcceptanceRefused=True))
                print('SCOPE_FIXTURE_STAGE: PASS',stage['name'],flush=True)
            report['outputs']=outputs
            report['qmlDebugDisabled']=True
            report['placementMarkersVisible']=True
            # Loss of the only owned client must stop the compositor-side guard.
            guard_id=load_stage(directory,env,3,plan(outputs)[0],outputs)
            wait(lambda:check_log((directory/'kwin.log').read_text(errors='replace'),3))
            terminate(client,3)
            end=time.monotonic()+5
            marker='OWG_DIRECT_SCOPE FAIL 3 owned-focus-lost'
            while marker not in (directory/'kwin.log').read_text(errors='replace'):
                if kwin.poll() is not None or time.monotonic()>=end:
                    raise RuntimeError('Scope guard did not reject owned-client loss')
                time.sleep(.1)
            unload_stage(env,guard_id)
            report['ownedClientLossRefused']=True
            print('SCOPE_FOCUS_GUARD: owned-client loss refused',flush=True)
        except Exception as exc:
            report['error']=str(exc)
            raise
        finally:
            try:
                try: terminate(client,3)
                finally: terminate(kwin,8)
            finally: (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    report['status']='pass'
    (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print('SCOPE_PREFLIGHT: PASS (virtual owned placement, key delivery, scope, unload; physical acceptance refused)',flush=True)


def main():
    if len(sys.argv)==4 and sys.argv[1]=='--worker':
        worker(Path(sys.argv[2]),Path(sys.argv[3])); return
    if len(sys.argv)!=1: raise RuntimeError('usage: run_input_scope_preflight.py')
    plugin=Path(os.environ.get('OWG_INPUT_PROBE_PLUGIN_DIR','/tmp/owg-kwin-devel/probe_plugins'))
    running=subprocess.run(['kwin_wayland','--version'],check=True,text=True,capture_output=True,timeout=5).stdout.strip()
    plugin=verify_probe(plugin,running)
    directory=Path(tempfile.mkdtemp(prefix='owg-scope-preflight-'))
    print('SCOPE_PREFLIGHT_ARTIFACTS:',directory,flush=True)
    env=os.environ.copy()
    for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SESSION_BUS_PID'): env.pop(key,None)
    code=run_private_session(['dbus-run-session','--',sys.executable,'-u',__file__,'--worker',str(directory),str(plugin)],env,60)
    if code: raise SystemExit(code)


if __name__=='__main__': main()
