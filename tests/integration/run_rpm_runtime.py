#!/usr/bin/env python3
"""Run exact extracted RPM application files in a private virtual KWin session.

Never contacts the normal desktop bus or arms window movement.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
SERVICE='org.oledwindowguard.Controller1'
PATH='/org/oledwindowguard/Controller1'
INTERFACE=SERVICE


def worker(root):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtDBus import QDBusConnection,QDBusInterface,QDBusMessage
    sys.path.insert(0,str(ROOT))
    from tests.integration.run_native_startup import Login
    app=QCoreApplication([]);bus=QDBusConnection.sessionBus();login=Login()
    assert bus.registerVirtualObject('/org/freedesktop/login1',login)
    assert bus.registerService('org.freedesktop.login1')
    package=root/'payload/usr/share/kwin/scripts/oled-window-guard-adapter'
    app_root=root/'payload/usr/libexec/oled-window-guard'
    sys.path.insert(0,str(app_root))
    import oled_window_guard
    from oled_window_guard import preferences
    assert Path(oled_window_guard.__file__).is_relative_to(app_root)
    settings=preferences.default_settings_path()
    # A retained unpaused preference must not grant startup writer authority.
    preferences.save_settings(settings,dict(preferences.defaults(),paused=False,resumeAfterUnlock=True,startMinimized=True))
    initial_hashes={str(p.relative_to(root/'payload')):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (root/'payload').rglob('*') if p.is_file()}
    log=(root/'kwin.log').open('w');dashboard_log=(root/'dashboard.log').open('w')
    env=os.environ.copy();env.update(QT_QPA_PLATFORM='wayland',WAYLAND_DISPLAY='wayland-0',
        PYTHONPATH=str(app_root),XDG_DATA_DIRS=str(root/'payload/usr/share')+':/usr/share',
        QT_LOGGING_RULES='qml.debug=true;kwin_scripting.debug=true',QT_FORCE_STDERR_LOGGING='1')
    kwin=subprocess.Popen(['kwin_wayland','--virtual','--width','900','--height','650',
                           '--output-count','2','--no-lockscreen'],env=env,stdout=log,stderr=subprocess.STDOUT)
    dashboard=None;observations=[]
    def call(service,path,interface,method,*args):
        proxy=QDBusInterface(service,path,interface,bus);proxy.setTimeout(2000)
        reply=proxy.call(method,*args)
        if reply.type()==QDBusMessage.MessageType.ErrorMessage:raise RuntimeError(reply.errorMessage())
        return reply.arguments()[0] if reply.arguments() else None
    def scripting(method,*args):return call('org.kde.KWin','/Scripting','org.kde.kwin.Scripting',method,*args)
    def present(service):return bus.interface().isServiceRegistered(service).value()
    def wait(predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            app.processEvents()
            if kwin.poll() is not None:raise RuntimeError('Private KWin exited: '+(root/'kwin.log').read_text()[-3000:])
            if predicate():return
            time.sleep(.05)
        raise RuntimeError('Private RPM runtime wait timed out; see retained logs')
    def status():return json.loads(call(SERVICE,PATH,INTERFACE,'Status'))
    def intent(action):
        assert action in ('Open','Quit'),'No movement intent allowed'
        response=json.loads(call(SERVICE,PATH,INTERFACE,'Intent',action,'{}'))
        assert response.get('ok'),response
        return response
    def live():
        if not present(SERVICE):return False
        sample=status()
        assert sample['paused'] and not any(sample.get(k) for k in ('transactionActive','movementReady','skipAvailable','restoreAvailable'))
        return sample.get('planningReady') is True and sample.get('nativeObservation',{}).get('available') is True and len(sample.get('displays',[]))==2
    def record(case):
        sample=status();assert live()
        observations.append(dict(case=case,status=sample,controllerOwner=bus.interface().serviceOwner(SERVICE).value()))
    def launch():
        return subprocess.Popen([sys.executable,'-m','oled_window_guard','open'],cwd=root,
                                env=env,stdout=dashboard_log,stderr=subprocess.STDOUT)
    try:
        wait(lambda:present('org.kde.KWin'))
        assert not scripting('isScriptLoaded','oled-window-guard-adapter')
        dashboard=launch();wait(live);record('retained-unpaused-settings-starts-paused')
        owner=observations[-1]['controllerOwner']
        duplicate=subprocess.run([sys.executable,'-m','oled_window_guard','open'],cwd=root,env=env,
                                 stdout=dashboard_log,stderr=subprocess.STDOUT,timeout=10)
        assert duplicate.returncode==0
        assert bus.interface().serviceOwner(SERVICE).value()==owner
        record('duplicate-open-keeps-controller-owner')
        # An abrupt controller exit leaves its KWin script alive. Reopening
        # must replace that orphan, rather than adopting old dimming code and
        # losing ownership of its eventual Quit cleanup.
        orphan_epoch=status().get('observerEpoch')
        dashboard.kill();dashboard.wait(timeout=10)
        wait(lambda:not present(SERVICE))
        assert scripting('isScriptLoaded','oled-window-guard-adapter') is True
        dashboard=launch()
        wait(lambda:live() and bool(status().get('observerEpoch')) and status()['observerEpoch']!=orphan_epoch)
        expected_main=package/'contents'/json.loads((package/'contents/runtime-build.json').read_text())['main']
        assert Path(status()['adapterSource'].removeprefix('file://')).resolve()==expected_main.resolve()
        record('orphaned-adapter-replaced-with-fresh-installed-code-paused')
        owner=observations[-1]['controllerOwner']
        assert scripting('unloadScript','oled-window-guard-adapter') is True
        wait(lambda:scripting('isScriptLoaded','oled-window-guard-adapter') is True)
        wait(live);record('owned-adapter-unload-recovery-paused')
        assert bus.interface().serviceOwner(SERVICE).value()==owner
        assert preferences.load_settings(settings).values['paused'] is True
        if (app_root/'oled_window_guard/upgrade_worker.py').is_file():
            # Exercise the real session handoff and adapter unload. Only the
            # administrator transaction is substituted with an authorization
            # cancellation; no normal-session package database is modified.
            from oled_window_guard.upgrade_worker import coordinate
            from oled_window_guard.updates import PACKAGE, REPOSITORY, _run
            staged=root/'upgrade';staged.mkdir(mode=0o700)
            fields=_run(['rpm','-qp','--qf','%{VERSION}\n%{RELEASE}',str(root/'tested.rpm')]).splitlines()
            version,release=fields
            name=f'{PACKAGE}-{version}-{release}.x86_64.rpm'
            shutil.copy2(root/'tested.rpm',staged/name)
            (staged/'offer.json').write_text(json.dumps(dict(version=version,release=release,name=name,
                url=f'{REPOSITORY}/releases/download/v{version}/{name}',size=(staged/name).stat().st_size,
                sha256=hashlib.sha256((staged/name).read_bytes()).hexdigest())))
            def query(argv):
                if argv==['rpm','-q','--qf','%{VERSION}-%{RELEASE}',PACKAGE]:return '0.0.0-1.fc44'
                return _run(argv)
            def reopen_cancelled(folder):
                nonlocal dashboard
                dashboard=launch()
            before=settings.read_bytes()
            outcome=coordinate(staged,dashboard.pid,runner=query,installer=lambda _:126,
                restart=reopen_cancelled,settings_paths=[settings])
            assert outcome['result']=='cancelled',outcome
            wait(live);assert settings.read_bytes()==before
            record('cancelled-upgrade-unloads-runtime-and-reopens-paused-with-settings-preserved')
        intent('Quit');wait(lambda:not present(SERVICE))
        dashboard.wait(timeout=10);assert dashboard.returncode==0
        assert not scripting('isScriptLoaded','oled-window-guard-adapter')
        observations.append(dict(case='quit-removes-controller-and-owned-adapter'))
        dashboard=launch();wait(live);record('reopen-new-controller-paused')
        assert observations[-1]['controllerOwner']!=owner
        intent('Quit');wait(lambda:not present(SERVICE));dashboard.wait(timeout=10)
        assert not scripting('isScriptLoaded','oled-window-guard-adapter')
        final_hashes={str(p.relative_to(root/'payload')):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (root/'payload').rglob('*') if p.is_file()}
        assert final_hashes==initial_hashes,'Test must not modify RPM payload'
        assert 'OLED_WINDOW_GUARD_SNAPSHOT ' not in (root/'kwin.log').read_text(errors='replace'), 'Routine snapshot logging must be off'

        result=dict(result='PASS',routineSnapshotLoggingDisabled=True,cases=observations,exactExtractedPayloadUnmodified=True,
                    privateVirtualKwin=True,outputCount=2,normalDesktopUntouched=True,
                    noResumeOrMoveNow=True,fakePrivateLogind=True,
                    limits='Paused runtime/lifecycle only; synthetic private logind and virtual outputs do not establish physical input, DRM, suspend or package-manager dependency acceptance.')
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print('PASS: exact RPM paused startup, duplicate Open, orphan replacement, adapter recovery, Quit/reopen; artifacts:',root)
    finally:
        if dashboard and dashboard.poll() is None:
            dashboard.terminate()
            try:dashboard.wait(timeout=5)
            except subprocess.TimeoutExpired:dashboard.kill();dashboard.wait(timeout=5)
        kwin.terminate()
        try:kwin.wait(timeout=10)
        except subprocess.TimeoutExpired:kwin.kill();kwin.wait(timeout=5)
        log.close();dashboard_log.close()


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--worker':return worker(Path(sys.argv[2]))
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('rpm',type=Path)
    args=parser.parse_args();rpm=args.rpm.resolve()
    sys.path.insert(0,str(ROOT))
    from tests.integration.run_native_rpm_install import inspect
    info=inspect(rpm)
    root=Path(tempfile.mkdtemp(prefix='owg-rpm-runtime-'));payload=root/'payload';payload.mkdir()
    shutil.copy2(rpm,root/'tested.rpm')
    data=subprocess.run(['rpm2cpio',str(rpm)],check=True,capture_output=True).stdout
    subprocess.run(['cpio','-idm','--quiet','--no-absolute-filenames'],input=data,cwd=payload,check=True,capture_output=True)
    env=os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE']='1'
    for key,name in (('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')):
        path=root/name;path.mkdir(mode=0o700);env[key]=str(path)
    for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS','QML_IMPORT_PATH','QML2_IMPORT_PATH','QT_PLUGIN_PATH','PYTHONPATH','DISPLAY','WAYLAND_DISPLAY'):
        env.pop(key,None)
    (root/'rpm-provenance.json').write_text(json.dumps(dict(rpmSHA256=info['sha256'],evr=info['evr']),indent=2)+'\n')
    # The private bus becomes the fake system bus before Qt creates connections.
    command=['dbus-run-session','--',sys.executable,str(Path(__file__).resolve()),'--worker',str(root)]
    with (root/'runner.log').open('w') as stream:
        result=subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=90)
    print((root/'runner.log').read_text(errors='replace'))
    print('Retained artifacts:',root)
    return result.returncode


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--worker':
        os.environ['DBUS_SYSTEM_BUS_ADDRESS']=os.environ['DBUS_SESSION_BUS_ADDRESS']
    raise SystemExit(main())
