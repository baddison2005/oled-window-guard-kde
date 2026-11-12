#!/usr/bin/env python3
"""Real warning placement/readback on a private bus and virtual KWin only."""
import argparse,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def worker(root):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtDBus import QDBusConnection,QDBusInterface,QDBusMessage
    app=QCoreApplication([]);bus=QDBusConnection.sessionBus()
    log=(root/'kwin.log').open('w')
    kwin=subprocess.Popen(['kwin_wayland','--virtual','--width','1100','--height','900','--output-count','2','--no-lockscreen'],stdout=log,stderr=subprocess.STDOUT)
    def call(path,interface,method,*args):
        p=QDBusInterface('org.kde.KWin',path,interface,bus);p.setTimeout(2000);r=p.call(method,*args)
        if r.type()==QDBusMessage.MessageType.ErrorMessage:raise RuntimeError(r.errorMessage())
        return r.arguments()[0] if r.arguments() else None
    try:
        end=time.monotonic()+15
        while not bus.interface().isServiceRegistered('org.kde.KWin').value():
            app.processEvents();time.sleep(.03)
            if time.monotonic()>end or kwin.poll() is not None:raise RuntimeError('Private KWin did not start')
        index=call('/Scripting','org.kde.kwin.Scripting','loadDeclarativeScript',str(root/'probe.qml'),'owg-private-warning-placement')
        assert index>=0
        call('/Scripting','org.kde.kwin.Scripting','start')
        end=time.monotonic()+45
        while time.monotonic()<end:
            app.processEvents();data=(root/'kwin.log').read_text(errors='replace')
            if 'OWG_PLACEMENT_FAIL ' in data:raise RuntimeError(data[-5000:])
            if 'OWG_PLACEMENT_PASS' in data:break
            if kwin.poll() is not None:raise RuntimeError('Private KWin exited: '+data[-4000:])
            time.sleep(.03)
        else:raise RuntimeError('Warning placement timed out: '+data[-4000:])
        rows=[json.loads(line.split('OWG_PLACEMENT_SAMPLE ',1)[1]) for line in data.splitlines() if 'OWG_PLACEMENT_SAMPLE ' in line]
        assert len(rows)==18,len(rows)
        call('/Scripting','org.kde.kwin.Scripting','unloadScript','owg-private-warning-placement')
        report=dict(result='PASS',cases=rows,normalDesktopUntouched=True,noApplicationGeometryWrites=True,
            limits='Virtual output readback and callback Skip; no physical input or movement acceptance claimed.')
        (root/'result.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS 18 warning placement/lifecycle cases; artifacts:',root)
    finally:
        kwin.terminate()
        try:kwin.wait(timeout=8)
        except subprocess.TimeoutExpired:kwin.kill();kwin.wait(timeout=3)
        log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--worker',type=Path);parser.add_argument('--package',type=Path)
    args=parser.parse_args()
    if args.worker:return worker(args.worker)
    root=Path(tempfile.mkdtemp(prefix='owg-warning-placement-'));ui=root/'ui';(ui/'core').mkdir(parents=True)
    source=args.package/'contents/ui' if args.package else ROOT/'packages/kwin-script/contents/ui'
    shutil.copy2(source/'WarningSurface.qml',ui/'WarningSurface.qml')
    for name in ('warning_clock.js','warning_placement.js'):
        shutil.copy2(source/'core'/name if args.package else ROOT/'core'/name,ui/'core'/name)
    shutil.copy2(ROOT/'tests/integration/warning_placement_probe.qml',root/'probe.qml')
    env=os.environ.copy()
    for key in ('DBUS_SESSION_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','QML_IMPORT_PATH','QML2_IMPORT_PATH','QT_PLUGIN_PATH','PYTHONPATH'):
        env.pop(key,None)
    for key,name in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')]:
        p=root/name;p.mkdir(mode=0o700);env[key]=str(p)
    env.update(QT_QPA_PLATFORM='wayland',WAYLAND_DISPLAY='wayland-0',DBUS_SYSTEM_BUS_ADDRESS='unix:path=/nonexistent-owg-system-bus',QT_FORCE_STDERR_LOGGING='1',QT_LOGGING_RULES='qml.debug=true')
    result=subprocess.run(['dbus-run-session','--',sys.executable,str(Path(__file__).resolve()),'--worker',str(root)],env=env,timeout=65)
    print('Artifacts:',root);return result.returncode


if __name__=='__main__':raise SystemExit(main())
