#!/usr/bin/env python3
"""Real Qt tray-menu export and Cairo attachment on a private bus/virtual KWin."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def gui(root, app_root):
    sys.path.insert(0, str(app_root))
    from PySide6.QtCore import QTimer
    from oled_window_guard.dashboard import Dashboard, ControllerIntentClient, create_application
    from oled_window_guard.autostart import LoginStartup
    app = create_application([])
    def submit(action, payload):
        with (root/'intents.jsonl').open('a') as out:
            out.write(json.dumps(dict(action=action,payload=payload))+'\n')
        return {'ok':True}
    class Layouts:
        def read_snapshot(self):return dict(status='missing',diagnostics=[],groups=[],layouts=[])
    w = Dashboard(ControllerIntentClient(submit), settings_path=root/'settings.json',
        layout_provider=Layouts(),app_ids=[],login_startup=LoginStartup(root/'config'))
    w.settings['soundEnabled']=False;w.settings['systemNotifications']=False
    w.settings['movementDisplays']=['DP-1']
    w.set_displays([dict(id='DP-1',name='Private display')])
    last=None
    def update():
        nonlocal last
        if (root/'stop').exists():
            w.dock_menu.stop();w.tray.hide();app.quit();return
        try:data=(root/'state.json').read_text()
        except OSError:return
        if data!=last:
            w.update_status(json.loads(data));last=data
    timer=QTimer();timer.setInterval(30);timer.timeout.connect(update);timer.start()
    w.show();update()
    return app.exec()


def worker(root, app_root):
    import dbus,dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    bus=dbus.SessionBus();context=GLib.MainContext.default()
    exports=[];attachments=[]
    watcher_name=dbus.service.BusName('org.kde.StatusNotifierWatcher',bus)
    class Watcher(dbus.service.Object):
        @dbus.service.method('org.kde.StatusNotifierWatcher',in_signature='s',out_signature='',sender_keyword='sender')
        def RegisterStatusNotifierItem(self,item,sender=None):
            exports.append(str(sender)+str(item) if str(item).startswith('/') else str(item))
            self.StatusNotifierItemRegistered(exports[-1])
        @dbus.service.signal('org.kde.StatusNotifierWatcher',signature='s')
        def StatusNotifierItemRegistered(self,item):pass
        @dbus.service.method('org.freedesktop.DBus.Properties',in_signature='ss',out_signature='v')
        def Get(self,interface,name):
            return self.GetAll(interface)[name]
        @dbus.service.method('org.freedesktop.DBus.Properties',in_signature='s',out_signature='a{sv}')
        def GetAll(self,interface):
            return dict(IsStatusNotifierHostRegistered=dbus.Boolean(True),ProtocolVersion=dbus.Int32(0),
                        RegisteredStatusNotifierItems=dbus.Array(exports,signature='s'))
    watcher=Watcher(watcher_name,'/StatusNotifierWatcher')
    dock_name=dbus.service.BusName('org.cairodock.CairoDock',bus)
    class Dock(dbus.service.Object):
        @dbus.service.method('org.cairodock.CairoDock',in_signature='sss',out_signature='')
        def SetMenu(self,service,path,query):
            assert str(query)=='class=oled-window-guard'
            attachments.append((str(service),str(path),str(query)))
    dock=Dock(dock_name,'/org/cairodock/CairoDock')
    log=(root/'kwin.log').open('w');gui_log=(root/'gui.log').open('w')
    env=dict(os.environ,QT_QPA_PLATFORM='wayland',WAYLAND_DISPLAY='wayland-0',PYTHONDONTWRITEBYTECODE='1')
    env.pop('DISPLAY',None)
    kwin=subprocess.Popen(['kwin_wayland','--virtual','--width','1100','--height','800','--no-lockscreen'],env=env,stdout=log,stderr=subprocess.STDOUT)
    child=None
    def pump():
        while context.pending():context.iteration(False)
    def wait(predicate, seconds=12):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            pump()
            if predicate():return
            if kwin.poll() is not None:raise RuntimeError('Private KWin exited')
            if child is not None and child.poll() is not None:raise RuntimeError((root/'gui.log').read_text())
            time.sleep(.02)
        raise RuntimeError('Dynamic menu wait timed out; '+str(root)+' exports='+repr(exports)+' gui='+ (root/'gui.log').read_text())
    def state(**changes):
        sample=dict(readiness='ready',paused=True,movementPaused=True,brightnessPaused=True,
            resumeReady=True,movementResumeReady=True,brightnessResumeReady=True,canPause=True,
            planningReady=True,movementReady=False,transactionActive=False,skipAvailable=False,
            restoreAvailable=False,restoreOutputId='DP-1',capabilities=dict(restoreBrightness=False))
        sample.update(changes)
        temporary=root/'state.tmp';temporary.write_text(json.dumps(sample));temporary.replace(root/'state.json')
    def menu():
        service,path,_=attachments[-1]
        reply=bus.get_object(service,path).GetLayout(dbus.Int32(0),dbus.Int32(1),dbus.Array([],signature='s'),
                dbus_interface='com.canonical.dbusmenu')
        result={}
        for row in reply[1][2]:
            props=row[1]
            if 'label' in props:result[str(props['label'])]=dict(id=int(row[0]),enabled=bool(props.get('enabled',True)))
        return result
    cases=[]
    try:
        wait(lambda:(Path(os.environ['XDG_RUNTIME_DIR'])/'wayland-0').exists())
        state()
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gui',str(root),'--app-root',str(app_root)],env=env,stdout=gui_log,stderr=subprocess.STDOUT)
        wait(lambda:bool(attachments))
        wait(lambda:'Start movement guarding' in menu())
        m=menu();assert len(m)==11,m
        assert 'Start brightness guarding' in m and 'Start selected guarding' in m
        for title in ['Move Now','Skip','Restore previous position','Restore Brightness','Dim selected display now','Dim all guarded displays now']:assert not m[title]['enabled'],m
        cases.append('both-paused-start-labels-and-disabled-actions')
        # Activate a real exported Qt menu item; the injected GUI client records
        # the intent without loading any writer or real movement controller.
        service,path,_=attachments[-1]
        bus.get_object(service,path).Event(dbus.Int32(m['Start movement guarding']['id']),'clicked',dbus.Int32(0),dbus.UInt32(0),dbus_interface='com.canonical.dbusmenu')
        wait(lambda:(root/'intents.jsonl').exists())
        assert json.loads((root/'intents.jsonl').read_text().splitlines()[-1])['action']=='ResumeMovement'
        cases.append('exported-start-dispatches-existing-guard-intent')
        state(paused=False,movementPaused=False,movementReady=True)
        wait(lambda:'Pause movement guarding' in menu())
        m=menu();assert m['Move Now']['enabled'] and not m['Restore Brightness']['enabled']
        cases.append('movement-active-brightness-paused')
        state(paused=False,brightnessPaused=False,dimDisplayOutputs=['DP-1'],capabilities=dict(restoreBrightness=True))
        wait(lambda:'Pause brightness guarding' in menu() and 'Start movement guarding' in menu())
        m=menu();assert not m['Move Now']['enabled'] and m['Restore Brightness']['enabled']
        assert m['Dim selected display now']['enabled'] and m['Dim all guarded displays now']['enabled']
        for title,action,payload in [('Dim selected display now','DimDisplayNow',{'outputId':'DP-1'}),('Dim all guarded displays now','DimAllDisplaysNow',{})]:
            bus.get_object(service,path).Event(dbus.Int32(m[title]['id']),'clicked',dbus.Int32(0),dbus.UInt32(0),dbus_interface='com.canonical.dbusmenu')
            wait(lambda:json.loads((root/'intents.jsonl').read_text().splitlines()[-1])['action']==action)
            assert json.loads((root/'intents.jsonl').read_text().splitlines()[-1])['payload']==payload
        cases.append('manual-dimming-actions-dispatch-selected-and-all-scopes')
        cases.append('brightness-active-movement-paused')
        state(paused=False,movementPaused=False,brightnessPaused=False,movementReady=True,
            transactionActive=True,skipAvailable=True,capabilities=dict(restoreBrightness=True))
        wait(lambda:menu().get('Skip',{}).get('enabled'))
        m=menu();assert not m['Move Now']['enabled'] and not m['Restore previous position']['enabled']
        assert 'Pause all guarding' in m
        cases.append('warning-skip-enabled-move-and-restore-disabled')
        # Forget the fake dock's attachment while keeping its bus owner; the
        # periodic idempotent bind must discover/rebind an added/reloaded icon.
        attachments.clear();wait(lambda:bool(attachments))
        cases.append('attachment-refresh-after-launcher-reload')
        (root/'stop').write_text('stop')
        end=time.monotonic()+8
        while child.poll() is None and time.monotonic()<end:pump();time.sleep(.02)
        assert child.poll()==0,(root/'gui.log').read_text()
        pump();assert attachments[-1][:2]==('',''),attachments
        cases.append('quit-detaches-live-menu')
        result=dict(result='PASS',cases=cases,privateVirtualKwin=True,normalDesktopUntouched=True,appRoot=str(app_root))
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        if child and child.poll() is None:child.terminate();child.wait(timeout=10)
        kwin.terminate();kwin.wait(timeout=10);log.close();gui_log.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root',type=Path,default=ROOT)
    parser.add_argument('--worker',type=Path);parser.add_argument('--gui',type=Path)
    args=parser.parse_args()
    if args.gui:return gui(args.gui,args.app_root)
    if args.worker:return worker(args.worker,args.app_root)
    root=Path(tempfile.mkdtemp(prefix='owg-dynamic-menu-'))
    env=dict(os.environ,DBUS_SYSTEM_BUS_ADDRESS='unix:path=/nonexistent-owg-system-bus')
    for key in ['DBUS_SESSION_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','PYTHONPATH','QT_QPA_PLATFORM']:
        env.pop(key,None)
    for key in ['XDG_CONFIG_HOME','XDG_CACHE_HOME','XDG_DATA_HOME','XDG_RUNTIME_DIR']:
        path=root/key;path.mkdir(mode=0o700);env[key]=str(path)
    result=subprocess.run(['dbus-run-session','--',sys.executable,str(Path(__file__).resolve()),'--worker',str(root),
        '--app-root',str(args.app_root.resolve())],env=env,timeout=90)
    print('Artifacts:',root)
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
