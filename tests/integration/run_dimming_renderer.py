#!/usr/bin/env python3
"""Actual dimming planes in an isolated virtual KWin; never uses the host bus."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]


def worker(root):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtDBus import QDBusConnection,QDBusInterface,QDBusMessage
    app=QCoreApplication([])
    ui=root/'ui';ui.mkdir();(ui/'core').mkdir()
    for name in ('DimmingController.qml','DimmingSurface.qml'):
        shutil.copy2(ROOT/'packages/kwin-script/contents/ui'/name,ui/name)
    for name in ('model.js','dimming_plan.js','dimming_engine.js','dimming_timing.js','dimming_options.js','dimming_context.js'):
        shutil.copy2(ROOT/'core'/name,ui/'core'/name)
    shutil.copy2(ROOT/'tests/integration/dimming_probe.qml',root/'probe.qml')
    env=os.environ.copy();env.update(QT_QPA_PLATFORM='wayland',QT_LOGGING_RULES='qml.debug=true',QT_FORCE_STDERR_LOGGING='1')
    with (root/'kwin.log').open('w') as log:
        kwin=subprocess.Popen(['kwin_wayland','--virtual','--width','900','--height','650','--output-count','2','--no-lockscreen'],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            def call(path,interface,method,*args):
                proxy=QDBusInterface('org.kde.KWin',path,interface,QDBusConnection.sessionBus());proxy.setTimeout(1000)
                message=proxy.call(method,*args)
                if message.type()==QDBusMessage.MessageType.ErrorMessage:raise RuntimeError(message.errorMessage())
                return message.arguments()[0] if message.arguments() else None
            deadline=time.monotonic()+15
            while True:
                app.processEvents()
                try:
                    index=call('/Scripting','org.kde.kwin.Scripting','loadDeclarativeScript',str(root/'probe.qml'),'owg-private-dimming');break
                except RuntimeError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.05)
            call('/Scripting/Script'+str(index),'org.kde.kwin.Script','run')
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                app.processEvents();text=(root/'kwin.log').read_text(errors='replace')
                if 'OWG_DIMMING_RESULT FAIL' in text:raise RuntimeError(text[-6000:])
                if 'OWG_DIMMING_RESULT PASS' in text:break
                if kwin.poll() is not None:raise RuntimeError('Private KWin exited: '+text[-6000:])
                time.sleep(.05)
            else:raise RuntimeError('Dimming probe timed out: '+text[-6000:])
            call('/Scripting','org.kde.kwin.Scripting','unloadScript','owg-private-dimming')
            time.sleep(.3)
            assert kwin.poll() is None,'KWin must survive dimming teardown'
            print('PASS: isolated renderer and dimming controller; artifacts:',root)
        finally:
            kwin.terminate()
            try:kwin.wait(timeout=5)
            except subprocess.TimeoutExpired:kwin.kill();kwin.wait(timeout=3)


def main():
    root=Path(tempfile.mkdtemp(prefix='owg-dimming-renderer-',dir='/tmp'))
    env=os.environ.copy()
    for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','QML_IMPORT_PATH','QML2_IMPORT_PATH','QT_PLUGIN_PATH','PYTHONPATH'):
        env.pop(key,None)
    for key,name in (('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')):
        folder=root/name;folder.mkdir(mode=0o700);env[key]=str(folder)
    result=subprocess.run(['dbus-run-session','--',sys.executable,str(Path(__file__).resolve()),'--worker',str(root)],env=env,timeout=45)
    print('Retained artifacts:',root)
    return result.returncode


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--worker':worker(Path(sys.argv[2]))
    else:raise SystemExit(main())
