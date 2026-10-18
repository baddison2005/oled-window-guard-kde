#!/usr/bin/env python3
"""Check production membership helpers against real isolated per-output desktops.

No normal-session installation, input injection or application geometry writes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

ROOT=Path(__file__).resolve().parents[2]


def worker(folder):
    app=QCoreApplication([])
    env=os.environ.copy()
    for key,name in (("XDG_CONFIG_HOME","config"),("XDG_DATA_HOME","data"),
                     ("XDG_CACHE_HOME","cache"),("XDG_RUNTIME_DIR","runtime")):
        path=folder/name;path.mkdir(mode=0o700);env[key]=str(path)
    (folder/'config/kwinrc').write_text('[Desktops]\nNumber=2\nRows=1\n[Windows]\nPerOutputVirtualDesktops=true\n')
    env.update(QT_QPA_PLATFORM='wayland',QT_LOGGING_RULES='qml.debug=true',QT_FORCE_STDERR_LOGGING='1')
    source=(ROOT/'packages/kwin-script/contents/ui/main.qml').read_text()
    helpers=source[source.index('    function read('):source.index('    function outputsNow(')]
    entry=folder/'check.qml'
    entry.write_text('''import QtQuick
import org.kde.kwin
QtObject {
''' + helpers + '''
 Component.onCompleted: {
  try {
   const screens=Workspace.screens, desktops=Workspace.desktops
   if (screens.length!==2 || desktops.length!==2) throw Error("expected two outputs and desktops")
   Workspace.setCurrentDesktopForScreen(desktops[0],screens[0])
   Workspace.setCurrentDesktopForScreen(desktops[1],screens[1])
   const windows=[{output:screens[0],desktops:[desktops[0]],onAllDesktops:false},
                  {output:screens[1],desktops:[desktops[1]],onAllDesktops:false}]
   const visible=windows.map(function(w){return desktopMembership(w)})
   const legacy=windows.map(function(w){return membership(w,"desktops",Workspace.currentDesktop,"onAllDesktops")})
   const inactive=windows.map(function(w,i){return desktopMembership({output:w.output,desktops:[desktops[1-i]],onAllDesktops:false})})
   const actual=screens.map(function(s){return Workspace.currentDesktopForScreen(s).id})
   console.log("OWG_OUTPUT_DESKTOPS",JSON.stringify({visible:visible,inactive:inactive,legacy:legacy,desktops:actual}))
  } catch(error) {console.log("OWG_OUTPUT_DESKTOPS",JSON.stringify({error:String(error)}))}
 }
}
''')
    log=folder/'kwin.log'
    with log.open('w') as output:
        process=subprocess.Popen(['kwin_wayland','--virtual','--width','900','--height','650',
                                  '--output-count','2','--no-lockscreen'],env=env,stdout=output,stderr=subprocess.STDOUT)
        bus=QDBusConnection.sessionBus()
        def call(path,interface,method,*args):
            proxy=QDBusInterface('org.kde.KWin',path,interface,bus);proxy.setTimeout(2000)
            reply=proxy.call(method,*args)
            if reply.type()==QDBusMessage.MessageType.ErrorMessage:raise RuntimeError(reply.errorMessage())
            return reply.arguments()[0] if reply.arguments() else None
        try:
            deadline=time.monotonic()+20
            while not bus.interface().isServiceRegistered('org.kde.KWin').value():
                app.processEvents()
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('private compositor startup failed')
                time.sleep(.05)
            index=call('/Scripting','org.kde.kwin.Scripting','loadDeclarativeScript',str(entry),'owg-output-desktops')
            call('/Scripting/Script'+str(index),'org.kde.kwin.Script','run')
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                app.processEvents()
                lines=[line for line in log.read_text(errors='replace').splitlines() if 'OWG_OUTPUT_DESKTOPS {' in line]
                if lines:break
                time.sleep(.05)
            else:raise RuntimeError(log.read_text(errors='replace'))
            result=json.loads(lines[-1].split('OWG_OUTPUT_DESKTOPS ',1)[1])
            assert result.get('visible')==[True,True],result
            assert result['inactive']==[False,False],result
            assert result['desktops'][0]!=result['desktops'][1],result
            assert result['legacy'].count(False)==1,result
            result.update(result='PASS',normalSessionInstalled=False,applicationGeometryWrites=False)
            (folder/'results.json').write_text(json.dumps(result,indent=2)+'\n')
            call('/Scripting','org.kde.kwin.Scripting','unloadScript','owg-output-desktops')
            print('PASS isolated per-output desktop visibility; legacy active-screen check reproduced false exclusion. Artifacts:',folder)
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--worker':worker(Path(sys.argv[2]))
    else:
        folder=Path(tempfile.mkdtemp(prefix='owg-output-desktops-'))
        raise SystemExit(subprocess.call(['dbus-run-session','--',sys.executable,__file__,'--worker',str(folder)]))
