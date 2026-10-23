#!/usr/bin/env python3
"""Private KWin: package-toggle isolation and real QML/native object continuity.

The fixture seeds guard selection flags with its scheduler disconnected. It
never grants native input facts or creates a movement plan or client windows.
"""
import argparse,json,os,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def worker(root,native):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtDBus import QDBusConnection,QDBusInterface,QDBusMessage
    sys.path.insert(0,str(ROOT))
    from tests.integration.run_native_startup import Login
    from tools.build_kwin_package import materialize
    from oled_window_guard.adapter import PLUGIN,RUNTIME_PLUGIN
    app=QCoreApplication([]);bus=QDBusConnection.sessionBus();login=Login()
    assert bus.registerService('org.freedesktop.login1') and bus.registerVirtualObject('/org/freedesktop/login1',login)
    package=root/'data/kwin/scripts'/PLUGIN;materialize(package,native_module=native)
    main=package/'contents'/json.loads((package/'contents/runtime-build.json').read_text())['main']
    probe=root/'continuity.qml'
    probe.write_text('''import QtQuick
QtObject {
 id:probe
 property var instance:null
 property bool initialized:false
 property Timer sample:Timer {
  interval:150;running:true;repeat:true
  onTriggered: {
   if(!probe.instance)return
   if(!probe.initialized){
    probe.instance.runtime.timer.stop()
    probe.instance.runtime.nativeEvents.target=null
    probe.instance.runtime.guards=Object.assign({},probe.instance.runtime.guards,{movement:true,brightness:true})
    probe.initialized=true
   }
   console.warn("OWG_CONTINUITY",JSON.stringify({epoch:probe.instance.sessionEpoch,
    guards:probe.instance.runtime.guards,native:probe.instance.planning.nativeObservation.immediateCancellation}))
  }
 }
 Component.onCompleted:{
  console.warn("OWG_CONTINUITY_CREATING")
  const component=Qt.createComponent(MAIN)
  instance=component.createObject(probe)
  if(!instance)throw new Error(component.errorString())
 }
}'''.replace('MAIN',json.dumps(main.as_uri())))
    log=(root/'kwin.log').open('w');env=dict(os.environ,QT_QPA_PLATFORM='wayland',WAYLAND_DISPLAY='wayland-0',QT_FORCE_STDERR_LOGGING='1',QT_LOGGING_RULES='qml.debug=true;kwin_scripting.debug=true')
    kwin=subprocess.Popen(['kwin_wayland','--virtual','--width','900','--height','650','--no-lockscreen'],env=env,stdout=log,stderr=subprocess.STDOUT)
    def call(path,iface,method,*args):
        p=QDBusInterface('org.kde.KWin',path,iface,bus);p.setTimeout(2000);r=p.call(method,*args)
        if r.type()==QDBusMessage.MessageType.ErrorMessage:raise RuntimeError(r.errorMessage())
        return r.arguments()[0] if r.arguments() else None
    def scripting(method,*args):return call('/Scripting','org.kde.kwin.Scripting',method,*args)
    def wait(predicate,seconds=12):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents()
            if predicate():return
            if kwin.poll() is not None:break
            time.sleep(.03)
        raise RuntimeError('Continuity timeout; '+str(root))
    def samples():
        return [json.loads(line.split('OWG_CONTINUITY ',1)[1]) for line in (root/'kwin.log').read_text(errors='replace').splitlines() if 'OWG_CONTINUITY {' in line]
    def load(name):
        index=scripting('loadDeclarativeScript',str(probe),name);assert index>=0
        call('/Scripting/Script'+str(index),'org.kde.kwin.Script','run')
        start=len(samples());wait(lambda:len(samples())>start and samples()[-1]['native'])
        return samples()[-1]
    cases=[]
    try:
        wait(lambda:bus.interface().isServiceRegistered('org.kde.KWin').value())
        end=time.monotonic()+2
        while time.monotonic()<end:app.processEvents();time.sleep(.03)
        load(PLUGIN)
        call('/KWin','org.kde.KWin','reconfigure')
        wait(lambda:scripting('isScriptLoaded',PLUGIN) is False)
        cases.append('old-package-identity-reproduces-reconfigure-unload')
        initial=load(RUNTIME_PLUGIN)
        for round in range(4):
            # Window Layouts uses both global entry points when saving its
            # layouts/mappings or enabling a declarative helper.
            call('/KWin','org.kde.KWin','reconfigure');scripting('start')
            before=len(samples());wait(lambda:len(samples())>before+2)
            current=samples()[-1]
            assert scripting('isScriptLoaded',RUNTIME_PLUGIN) is True
            assert current['epoch']==initial['epoch'] and current['native']
            assert current['guards']['movement'] and current['guards']['brightness'],current
        cases.append('four-configure-and-global-start-cycles-preserve-real-adapter-native-and-guard-flags')
        scripting('unloadScript',RUNTIME_PLUGIN);wait(lambda:not scripting('isScriptLoaded',RUNTIME_PLUGIN))
        result=dict(result='PASS',cases=cases,normalDesktopUntouched=True,noGeometryWrites=True,
            limits='Real QML/native lifecycle; guard flags seeded by fixture with scheduler disconnected. Physical guard/input acceptance is separate.')
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:kwin.terminate();kwin.wait(timeout=10);log.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('native',type=Path);p.add_argument('--worker',type=Path);a=p.parse_args()
    if a.worker:return worker(a.worker,a.native)
    root=Path(tempfile.mkdtemp(prefix='owg-reconfigure-continuity-'));env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    for key in ['DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','PYTHONPATH','QML_IMPORT_PATH','QT_PLUGIN_PATH']:env.pop(key,None)
    for key,name in [('XDG_CONFIG_HOME','config'),('XDG_CACHE_HOME','cache'),('XDG_DATA_HOME','data'),('XDG_RUNTIME_DIR','runtime')]:
        directory=root/name;directory.mkdir(mode=0o700);env[key]=str(directory)
    command=['dbus-run-session','--',sys.executable,str(Path(__file__).resolve()),str(a.native.resolve()),'--worker',str(root)]
    # Use the isolated session as the fake system bus before Qt connects.
    command=['dbus-run-session','--','sh','-c','export DBUS_SYSTEM_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"; exec "$@"','owg-private',*command[2:]]
    result=subprocess.run(command,env=env,timeout=75);print('Artifacts:',root);return result.returncode
if __name__=='__main__':raise SystemExit(main())
