#!/usr/bin/env python3
"""Headless private-bus reproduction of QML callbacks during warning teardown.

No host compositor connection, native input injection or client geometry writes.
The extra private QML destructor models the writer's hideWarning teardown path.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.build_kwin_package import materialize


def worker(folder,mode,trigger,native_module,expect_crash,baseline_ui):
    bus=os.environ.get('DBUS_SESSION_BUS_ADDRESS')
    parent=os.environ.get('OWG_TEARDOWN_PARENT_BUS')
    if not bus or parent is None or bus==parent:
        raise RuntimeError('Refusing a worker without a distinct private bus')
    env=os.environ.copy()
    for key in ('DISPLAY','WAYLAND_DISPLAY','QML_IMPORT_PATH','QT_PLUGIN_PATH'):
        env.pop(key,None)
    for key,name in (('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),
                     ('XDG_CACHE_HOME','cache'),('XDG_RUNTIME_DIR','runtime')):
        target=folder/name;target.mkdir(mode=0o700,exist_ok=True);env[key]=str(target)
    env.update(QT_QPA_PLATFORM='wayland',KDE_SESSION_VERSION='6',
               QT_LOGGING_RULES='kwin_scripting.debug=true;qml.debug=true',
               QT_FORCE_STDERR_LOGGING='1',QT_QUICK_BACKEND='software')
    package=folder/'package';materialize(package,native_module=native_module)
    if baseline_ui:
        import shutil
        for name in ('main.qml','GuardedWriter.qml','OrderedGuardedWriter.qml'):
            shutil.copy2(baseline_ui/name,package/'contents/ui'/name)
    metadata=json.loads((package/'metadata.json').read_text())
    metadata['KPlugin']['Id']='owg-private-teardown'
    (package/'metadata.json').write_text(json.dumps(metadata))
    main=package/'contents/ui/main.qml'
    source=main.read_text()
    guard='adapter.workspaceSignals.target=null;' if mode=='disconnect-first' else ''
    destructor='' if mode in ('visible-only','production-writer','ordered-writer') else '''
    property QtObject teardownProbe: QtObject {
        Component.onDestruction: {
            print("TEARDOWN_HIDE_BEGIN")
            GUARD
            adapter.warningSurface.hideForGeneration(1)
            print("TEARDOWN_HIDE_END")
        }
    }
'''.replace('GUARD',guard)
    extra='''
    property Connections privateWarningSignals: Connections {
        target: adapter.warningSurface
        function onShown(generation) { print("TEARDOWN_WARNING_SHOWN") }
    }
    property Timer privateShow: Timer {
        interval: 500; running: true
        onTriggered: {
            adapter.warningSurface.showForOutput(1,{x:0,y:0,width:900,height:650},30000)
            print("TEARDOWN_WARNING_REQUESTED")
            print("TEARDOWN_NATIVE_AVAILABLE="+adapter.planning.nativeObservation.immediateCancellation)
            WRITER_SETUP
        }
    }
'''.replace('WRITER_SETUP','''
            adapter.runtime.timer.stop()
            adapter.writer.desktopEvents.target=null
            adapter.writer.inputEvents.target=null
            adapter.writer.warningEvents.target=null
            adapter.writer.verification.running=false
            adapter.writer.state={phase:"Warning",generation:1,pendingCommand:null,
                transaction:{id:"private-warning",warningRequested:true,move:{identity:"private"}}}
            print("TEARDOWN_PRODUCTION_WRITER_BUSY="+adapter.writer.busy)
''' if mode=='production-writer' else '''
            adapter.runtime.timer.stop()
            adapter.orderedWriter.desktopEvents.target=null
            adapter.orderedWriter.inputEvents.target=null
            adapter.orderedWriter.warningEvents.target=null
            adapter.orderedWriter.verification.running=false
            adapter.orderedWriter.warningProposal={privateProbe:true}
            print("TEARDOWN_ORDERED_WRITER_BUSY="+adapter.orderedWriter.busy)
''' if mode=='ordered-writer' else '')+destructor
    main.write_text(source[:source.rfind('}')]+extra+'}\n')
    subprocess.run(['kpackagetool6','--type','KWin/Script','--install',str(package)],
                   env=env,check=True,capture_output=True,text=True,timeout=10)
    def command(path,method,*args):
        result=subprocess.run(['gdbus','call','--session','--dest','org.kde.KWin',
            '--object-path',path,'--method',method,*args],env=env,capture_output=True,text=True,timeout=3)
        if result.returncode:raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()
    with (folder/'kwin.log').open('w') as log:
        kwin=subprocess.Popen(['kwin_wayland','--virtual','--width','900','--height','650',
                '--output-count','1','--no-lockscreen'],env=env,stdout=log,stderr=subprocess.STDOUT,
                preexec_fn=lambda:resource.setrlimit(resource.RLIMIT_CORE,(0,0)))
        try:
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if kwin.poll() is not None:raise RuntimeError('Private compositor exited before readiness')
                try:
                    command('/Scripting','org.kde.kwin.Scripting.isScriptLoaded','owg-private-teardown')
                    break
                except RuntimeError:time.sleep(.2)
            else:raise RuntimeError('Private scripting unavailable')
            # The scripting endpoint appears before KWin's startup reconfigure.
            # Let that default-off package sweep finish before loading our probe.
            time.sleep(2)
            loaded=command('/Scripting','org.kde.kwin.Scripting.loadDeclarativeScript',str(main),'owg-private-teardown')
            script_id=int(ast.literal_eval(loaded)[0])
            if script_id<0:raise RuntimeError('Private script refused')
            command('/Scripting/Script'+str(script_id),'org.kde.kwin.Script.run')
            deadline=time.monotonic()+8
            while 'TEARDOWN_WARNING_SHOWN' not in (folder/'kwin.log').read_text():
                if kwin.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Private warning unavailable')
                time.sleep(.1)
            time.sleep(1)
            if native_module and 'TEARDOWN_NATIVE_AVAILABLE=true' not in (folder/'kwin.log').read_text():
                raise RuntimeError('Bundled native observer was not available')
            unload_error=None
            try:
                if trigger=='reconfigure':command('/KWin','org.kde.KWin.reconfigure')
                else:command('/Scripting','org.kde.kwin.Scripting.unloadScript','owg-private-teardown')
            except RuntimeError as error:unload_error=str(error)
            time.sleep(1)
            result=dict(mode=mode,trigger=trigger,nativeBundled=native_module is not None,
                        privatePid=kwin.pid,exitCode=kwin.poll(),unloadError=unload_error,
                        log=str(folder/'kwin.log'))
            (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result),flush=True)
            if expect_crash:
                if kwin.poll()!=-11:raise RuntimeError('Expected SIGSEGV not reproduced')
            else:
                if kwin.poll() is not None or unload_error:raise RuntimeError('Private teardown failed')
                deadline=time.monotonic()+5
                while command('/Scripting','org.kde.kwin.Scripting.isScriptLoaded','owg-private-teardown')!='(false,)':
                    if time.monotonic()>deadline:raise RuntimeError('Private adapter still loaded')
                    time.sleep(.1)
            result['verified']=True
            result['baselineUI']=str(baseline_ui) if baseline_ui else None
            (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        finally:
            if kwin.poll() is None:
                kwin.terminate()
                try:kwin.wait(timeout=5)
                except subprocess.TimeoutExpired:kwin.kill();kwin.wait()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=('visible-only','hide-teardown','disconnect-first','production-writer','ordered-writer'),default='production-writer')
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--trigger',choices=('unload','reconfigure'),default='unload')
    parser.add_argument('--native-module',type=Path)
    parser.add_argument('--expect-crash',action='store_true')
    parser.add_argument('--baseline-ui',type=Path,help='Archived pre-fix QML, used only inside private KWin')
    args=parser.parse_args()
    if args.worker:
        worker(args.worker,args.mode,args.trigger,args.native_module,args.expect_crash,args.baseline_ui);return 0
    folder=Path(tempfile.mkdtemp(prefix='owg-private-teardown-'))
    with (folder/'runner.log').open('w') as log:
        command=['dbus-run-session','--',sys.executable,__file__,
            '--worker',str(folder),'--mode',args.mode,'--trigger',args.trigger]
        if args.native_module:command.extend(['--native-module',str(args.native_module.resolve())])
        if args.expect_crash:command.append('--expect-crash')
        if args.baseline_ui:command.extend(['--baseline-ui',str(args.baseline_ui.resolve())])
        env=os.environ.copy();env['OWG_TEARDOWN_PARENT_BUS']=env.get('DBUS_SESSION_BUS_ADDRESS','')
        done=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=45)
    print((folder/'runner.log').read_text());print('ARTIFACTS:',folder)
    return done.returncode


if __name__=='__main__':raise SystemExit(main())
