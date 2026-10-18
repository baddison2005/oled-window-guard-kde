#!/usr/bin/env python3
"""Two-output synthetic warning regression; never physical/wheel acceptance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import direct_output_scope as placement
from direct_warning import HIDDEN, SKIP, check_log, unload_fixture
from direct_warning_outputs import StageWarning, client_counts, warning_placement
from run_input_direct import CLIENT, environment, private_environment, run_private_session, terminate, verify_environment, verify_probe


def worker(directory, plugin, backend):
    env=private_environment(directory,plugin,warning=True)
    env.pop('OWG_INPUT_PROBE_DIRECT')
    trigger=directory/'trigger'; trigger.write_text(''); env['OWG_INPUT_TEST_TRIGGER']=str(trigger)
    (directory/'instructions.txt').write_text('Private synthetic two-output warning preflight; no hardware acceptance.')
    kwin=client=None
    report={'status':'fail','scope':'virtual two-output warning fixture; no physical or wheel claim','stages':[]}
    report['scope']=backend+' two-output warning fixture; no physical or wheel claim'
    with (directory/'kwin.log').open('w') as log, (directory/'client.log').open('w') as client_log:
        try:
            command=['kwin_wayland','--width','1440','--height','1080','--output-count','2',
                     '--socket','owg-warning-outputs-0','--no-lockscreen','--no-kactivities']
            if backend=='virtual':
                command.append('--virtual')
            else:
                parent=Path(os.environ['OWG_PARENT_WAYLAND_SOCKET'])
                if not parent.is_socket(): raise RuntimeError('Host Wayland parent socket is unavailable')
                command.extend(['--wayland-display',str(parent)])
                env['WAYLAND_DISPLAY']=str(parent)
            kwin=subprocess.Popen(command,
                                  env=env,stdout=log,stderr=subprocess.STDOUT)
            def wait(check,seconds=10):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    if kwin.poll() is not None or (client is not None and client.poll() is not None):
                        raise RuntimeError('Private warning compositor/client exited')
                    try:
                        result=check()
                        if result: return result
                    except subprocess.CalledProcessError:
                        pass
                    time.sleep(.1)
                raise RuntimeError('Private two-output warning fixture timed out')
            wait(lambda:environment(env),25)
            if backend=='virtual':
                subprocess.run(['kscreen-doctor','output.Virtual-0.scale.1.5'],
                               env=dict(env,WAYLAND_DISPLAY='owg-warning-outputs-0'),check=True,
                               text=True,capture_output=True,timeout=10)
            time.sleep(.8)
            baseline,origin_before,outputs=wait(lambda:placement.coherent_state(env))
            if len(outputs)!=2 or {row['scale'] for row in outputs}!=({1,1.5} if backend=='virtual' else {1}):
                raise RuntimeError('Private output count/scale did not match readback')
            try: verify_environment(origin_before,1)
            except RuntimeError: pass
            else: raise RuntimeError('Virtual warning fixture met direct backend requirements')
            client_env=dict(env,WAYLAND_DISPLAY='owg-warning-outputs-0',OWG_INPUT_CLIENT_DIRECT='1',
                            OWG_INPUT_CLIENT_WINDOWED='1',OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory/'instructions.txt'),
                            OWG_INPUT_CLIENT_HEADING='Private two-output warning preflight')
            client=subprocess.Popen([sys.executable,'-u',str(CLIENT)],env=client_env,
                                    stdout=client_log,stderr=subprocess.STDOUT)
            wait(lambda:'CLIENT_READY' in (directory/'client.log').read_text())
            for index,stage in enumerate(placement.contained_stages(outputs,centered=True,top_inset=200)):
                package_id=placement.load_stage(directory,env,index,stage,outputs)
                ready=wait(lambda:placement.check_log((directory/'kwin.log').read_text(errors='replace'),index))
                owned=placement.validate_frame(ready,stage,outputs)
                check=StageWarning(directory,env,index,stage['name']); check.start(kwin,client)
                output=next(row for row in outputs if row['id']==stage['name'])
                frame=warning_placement(check.log(),output,outputs)
                before=client_counts(directory)
                trigger.write_text('outside-click\n')
                wait(lambda:client_counts(directory)['button']>before['button'])
                check_log(check.log())
                if SKIP in check.log() or HIDDEN in check.log(): raise RuntimeError('Outside click dismissed warning')
                trigger.write_text('outside-key\n')
                wait(lambda:client_counts(directory)['keyboard']>before['keyboard'])
                check_log(check.log())
                if SKIP in check.log() or HIDDEN in check.log(): raise RuntimeError('Outside key dismissed warning')
                trigger.write_text('click-warning\n')
                wait(lambda:HIDDEN in check.log())
                check_log(check.log())
                origin=environment(env)
                if any(origin['libinput'].values()): raise RuntimeError('Synthetic events were counted as physical')
                unload_fixture(env,check.log())
                placement.check_log((directory/'kwin.log').read_text(errors='replace'),index)
                placement.unload_stage(env,package_id)
                report['stages'].append(dict(output=output,ownedFrame=owned,warningFrame=frame,
                                             outsideClickDelivered=True,outsideKeyDelivered=True,skipDelivered=True,
                                             focusUnchanged=True,warningCount=1,hiddenSurfaceCount=0,
                                             warningScriptUnloaded=True,placementScriptUnloaded=True,
                                             physicalOriginCounters=origin['libinput']))
                print('WARNING_OUTPUTS_FIXTURE_STAGE: PASS',stage['name'],flush=True)
            report['outputs']=outputs
        except Exception as exc:
            report['error']=str(exc); raise
        finally:
            try:
                try: terminate(client,3)
                finally: terminate(kwin,8)
            finally: (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    report['status']='pass'; (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print('WARNING_OUTPUTS_PREFLIGHT: PASS (two outputs, outside click/key, Skip, geometry/scale readbacks, sequential unload; no physical or wheel claim)',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=('windowed','virtual'),default='windowed')
    parser.add_argument('--worker',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--plugin-dir',type=Path,default=Path(os.environ.get('OWG_INPUT_PROBE_PLUGIN_DIR','/tmp/owg-kwin-devel/probe_plugins')))
    args=parser.parse_args()
    if args.worker:
        worker(args.worker,args.plugin_dir,args.backend); return
    plugin=args.plugin_dir
    running=subprocess.run(['kwin_wayland','--version'],check=True,text=True,capture_output=True,timeout=5).stdout.strip()
    plugin=verify_probe(plugin,running)
    directory=Path(tempfile.mkdtemp(prefix='owg-warning-outputs-preflight-'))
    print('WARNING_OUTPUTS_PREFLIGHT_ARTIFACTS:',directory,flush=True)
    env=os.environ.copy()
    if args.backend=='windowed':
        parent=Path(os.environ.get('WAYLAND_DISPLAY','wayland-0'))
        if not parent.is_absolute(): parent=Path(os.environ['XDG_RUNTIME_DIR'])/parent
        if not parent.is_socket(): raise RuntimeError('Host Wayland parent socket is unavailable')
        env['OWG_PARENT_WAYLAND_SOCKET']=str(parent)
    for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SESSION_BUS_PID'): env.pop(key,None)
    code=run_private_session(['dbus-run-session','--',sys.executable,'-u',__file__,'--worker',str(directory),
                             '--plugin-dir',str(plugin),'--backend',args.backend],env,80)
    if code: raise SystemExit(code)


if __name__=='__main__': main()
