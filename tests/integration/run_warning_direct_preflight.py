#!/usr/bin/env python3
"""Windowed regression of the direct warning fixture; no hardware claim."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from direct_warning import WarningCheck, SHOWN, SKIP, HIDDEN, check_log, unload_fixture
from run_input_direct import CLIENT, environment, private_environment, run_private_session, terminate, verify_environment, verify_probe


def worker(directory, plugin):
    host_socket = Path(os.environ['OWG_PARENT_WAYLAND_SOCKET'])
    if not host_socket.is_socket():
        raise RuntimeError('Host Wayland parent socket is unavailable')
    env = private_environment(directory, plugin, warning=True)
    env.pop('OWG_INPUT_PROBE_DIRECT')
    env['WAYLAND_DISPLAY'] = str(host_socket)
    trigger = directory/'input-trigger'
    trigger.write_text('')
    env['OWG_INPUT_TEST_TRIGGER'] = str(trigger)
    kwin = client = None
    with (directory/'kwin.log').open('w') as log, (directory/'client.log').open('w') as client_log:
        try:
            kwin = subprocess.Popen(['kwin_wayland','--wayland-display',str(host_socket),'--socket','owg-direct-0',
                                     '--width','900','--height','650','--no-lockscreen','--no-kactivities'],
                                    env=env,stdout=log,stderr=subprocess.STDOUT)
            def wait_until(check, seconds=10):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    if kwin.poll() is not None or (client is not None and client.poll() is not None):
                        raise RuntimeError('Private fixture compositor/client exited')
                    try:
                        if check(): return
                    except subprocess.CalledProcessError:
                        pass
                    time.sleep(.1)
                raise RuntimeError('Fixture regression timed out')
            wait_until(lambda: environment(env))
            check=WarningCheck(directory,env)
            client_env=dict(env,WAYLAND_DISPLAY='owg-direct-0',OWG_INPUT_CLIENT_DIRECT='1',
                            OWG_INPUT_WARNING_INSTRUCTIONS=str(directory/'instructions.txt'))
            client=subprocess.Popen([sys.executable,'-u',str(CLIENT)],env=client_env,
                                    stdout=client_log,stderr=subprocess.STDOUT)
            wait_until(lambda: 'CLIENT_READY' in (directory/'client.log').read_text())
            check.start(kwin,client)
            check.ready()
            counts=(directory/'client.log').read_text()
            trigger.write_text('outside-click\n')
            wait_until(lambda: (directory/'client.log').read_text().count('CLIENT_EVENT button')>
                       counts.count('CLIENT_EVENT button'))
            check_log(check.log())
            if SKIP in check.log() or HIDDEN in check.log():
                raise RuntimeError('Outside click dismissed fixture warning')
            trigger.write_text('outside-key\n')
            wait_until(lambda: (directory/'client.log').read_text().count('CLIENT_EVENT keyboard')>
                       counts.count('CLIENT_EVENT keyboard'))
            check_log(check.log())
            if SKIP in check.log() or HIDDEN in check.log():
                raise RuntimeError('Outside key dismissed fixture warning')
            trigger.write_text('click-warning\n')
            wait_until(lambda: HIDDEN in check.log())
            facts=environment(env)
            if any(facts['libinput'].values()):
                raise RuntimeError('Synthetic input was counted as physical')
            try: verify_environment(facts,1)
            except RuntimeError: pass
            else: raise RuntimeError('Virtual fixture met direct backend requirements')
            unload_fixture(env,check.log())
        finally:
            try: terminate(client,3)
            finally: terminate(kwin,8)
    print('WARNING_DIRECT_PREFLIGHT: PASS (windowed synthetic click/key/Skip, focus, one surface, unload; no direct or wheel claim)',flush=True)


def main():
    if len(sys.argv)==4 and sys.argv[1]=='--worker':
        worker(Path(sys.argv[2]),Path(sys.argv[3]))
        return
    plugin=Path(os.environ.get('OWG_INPUT_PROBE_PLUGIN_DIR','/tmp/owg-kwin-devel/probe_plugins'))
    running=subprocess.run(['kwin_wayland','--version'],check=True,text=True,capture_output=True).stdout.strip()
    plugin=verify_probe(plugin,running)
    host_socket=Path(os.environ.get('WAYLAND_DISPLAY','wayland-0'))
    if not host_socket.is_absolute():
        host_socket=Path(os.environ['XDG_RUNTIME_DIR'])/host_socket
    if not host_socket.is_socket():
        raise RuntimeError('Host Wayland parent socket is unavailable')
    directory=Path(tempfile.mkdtemp(prefix='owg-warning-direct-preflight-'))
    print('PREFLIGHT_ARTIFACTS:',directory,flush=True)
    env=dict(os.environ,OWG_PARENT_WAYLAND_SOCKET=str(host_socket))
    for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SESSION_BUS_PID'):
        env.pop(key,None)
    code=run_private_session(['dbus-run-session','--',sys.executable,'-u',__file__,
                              '--worker',str(directory),str(plugin)],env,60)
    if code:
        raise SystemExit(code)


if __name__=='__main__':
    main()
