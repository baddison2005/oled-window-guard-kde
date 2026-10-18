"""Operator-started, no-write resize refusal on fixed Wayland fixtures.

Temporary imported zones follow observed positions. Only normal product intents
are used; no input injection or compositor geometry assignments.
"""
import argparse
import json
import math
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection,QDBusInterface,QDBusMessage
from oled_window_guard import preferences
from oled_window_guard.session import call_host,revision,SERVICE,PATH,INTERFACE
from oled_window_guard.layout_provider import LayoutProvider,GROUP,KEYS
from tools.build_planning_worker import build
from tools.desktop_constraints_batch import verify_fixture

FIXTURE=Path('/tmp/owg-desktop-physical-2026-10-11-resize-refusal-windows.jsonl')


def read_status(interface=None):
    if interface is None:
        interface=QDBusInterface(SERVICE,PATH,INTERFACE,QDBusConnection.sessionBus())
        interface.setTimeout(2000)
    message=interface.call('Status')
    if message.type()==QDBusMessage.MessageType.ErrorMessage or not message.arguments():
        raise RuntimeError('Controller status unavailable: '+message.errorMessage())
    try:result=json.loads(str(message.arguments()[0]))
    except (TypeError,ValueError) as error:raise RuntimeError('Invalid controller status reply') from error
    if not isinstance(result,dict) or any(type(result.get(k)) is not bool for k in ('paused','transactionActive','resumeReady')):
        raise RuntimeError('Invalid controller status fields')
    return result


def capture():
    """One-shot read-only script; no observer, writer, UI or signal connections."""
    source=(ROOT/'packages/kwin-script/contents/ui/main.qml').read_text()
    helpers=source[source.index('    function read('):source.index('    function snapshot(')]
    tag='OWG_RESIZE_CAPTURE_'+uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='owg-resize-capture-') as folder:
        folder=Path(folder)
        (folder/'model.js').write_text((ROOT/'core/model.js').read_text())
        entry=folder/'capture.qml'
        entry.write_text('import QtQuick\nimport org.kde.kwin\nimport "model.js" as Model\nQtObject {\n'
            'property string sessionEpoch: "resize-capture"\nproperty int topologyGeneration: 0\n'
            'property string topologyFingerprint: ""\nproperty int anonymousIdentityCounter: 0\n'
            'property var anonymousWindows: []\nproperty var anonymousIdentities: []\n'
            'property QtObject warningSurface: QtObject {function isOwnSurface(w) {return false}}\n'+helpers+
            '\nComponent.onCompleted: console.warn('+json.dumps(tag+' ') + '+JSON.stringify(capture()))\n}\n')
        bus=QDBusConnection.sessionBus()
        def call(path,interface,method,*args):
            proxy=QDBusInterface('org.kde.KWin',path,interface,bus);proxy.setTimeout(2000)
            reply=proxy.call(method,*args)
            if reply.type()==QDBusMessage.MessageType.ErrorMessage:raise RuntimeError(reply.errorMessage())
            return reply.arguments()[0] if reply.arguments() else None
        loaded=False
        try:
            index=call('/Scripting','org.kde.kwin.Scripting','loadDeclarativeScript',str(entry),tag)
            loaded=True
            call('/Scripting/Script'+str(index),'org.kde.kwin.Script','run')
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                log=subprocess.run(['journalctl','--user','-u','plasma-kwin_wayland.service','-n','200','--no-pager','-o','cat'],capture_output=True,text=True,check=True,timeout=3).stdout
                lines=[l for l in log.splitlines() if tag+' {' in l]
                if lines:return json.loads(lines[-1].split(tag+' ',1)[1])
                QCoreApplication.processEvents();time.sleep(.1)
            raise RuntimeError('Read-only compositor snapshot did not arrive')
        finally:
            if loaded:call('/Scripting','org.kde.kwin.Scripting','unloadScript',tag)


def overlaps(a,b):
    return min(a['x']+a['width'],b['x']+b['width'])>max(a['x'],b['x']) and min(a['y']+a['height'],b['y']+b['height'])>max(a['y'],b['y'])


def test_geometry(snapshot,output_name,*,target_identity=None):
    output=next(o for o in snapshot['outputs'] if o['connector']==output_name)
    if target_identity is None:
        targets=[w for w in snapshot['windows'] if w.get('appId')=='oled-window-guard-physical-fixture' and
                 output['id'] in w.get('outputIds',[]) and w.get('onCurrentDesktop') is True and w.get('resizable') is False]
        if len(targets)!=1:raise RuntimeError('Expected one visible fixed-size target on '+output_name)
    else:
        targets=[w for w in snapshot['windows'] if w.get('identity')==target_identity and
                 output['id'] in w.get('outputIds',[]) and w.get('onCurrentDesktop') is True and
                 w.get('onCurrentActivity') is True and w.get('visible') is True and w.get('resizable') is True]
        if len(targets)!=1:raise RuntimeError('Expected visible resizable target on '+output_name)
    target=targets[0];area=output['usableRect'];rect=target['rect']
    # Imported layout edges use KDE rounding. Keep the actual candidate frame
    # untouched; source membership already allows this subpixel difference.
    source=dict(x=math.floor(rect['x']+.5),y=math.floor(rect['y']+.5),
                width=math.floor(rect['x']+rect['width']+.5)-math.floor(rect['x']+.5),
                height=math.floor(rect['y']+rect['height']+.5)-math.floor(rect['y']+.5))
    if target.get('focused'):raise RuntimeError('Click/release on each empty desktop before Start')
    obstacles=[w['rect'] for w in snapshot['windows'] if w['identity']!=target['identity'] and w.get('rect') and
               w.get('visible') and w.get('onCurrentDesktop') and w.get('onCurrentActivity') and output['id'] in w.get('outputIds',[]) and w.get('windowType')!='desktop']
    destination=None
    for x in (area['x']+20,area['x']+area['width']-rect['width']-20):
        for y in (area['y']+20,area['y']+area['height']-rect['height']-20):
            candidate=dict(x=x,y=y,width=rect['width']-2,height=rect['height'])
            if not overlaps(rect,candidate) and not any(overlaps(candidate,o) for o in obstacles):
                destination=candidate;break
        if destination:break
    if not destination:raise RuntimeError('No clear destination; spread targets out and keep panel on the other monitor')
    layouts=[];group_id='owg-resize-refusal-'+output_name
    for index,r in enumerate((source,destination)):
        layouts.append(dict(id=group_id+'-'+str(index),name='OWG temporary resize zone',groupId=group_id,
            x=(r['x']-area['x'])/area['width'],y=(r['y']-area['y'])/area['height'],
            width=r['width']/area['width'],height=r['height']/area['height']))
    return output,target,[source,destination],obstacles,layouts,dict(id=group_id,name='OWG temporary resize refusal')


class TemporaryLibrary:
    def __init__(self,backup,provider=None,write=None):
        self.provider=provider or LayoutProvider();self.write=write or self._write
        self.original=self.provider._sample();self.expected=dict(self.original)
        self.provider._validate(self.original)
        if any(self.original[k]==self.provider._missing_token(k) for k in KEYS):
            raise RuntimeError('All layout keys must exist for exact reversible testing')
        self.applied=False
        backup.write_text(json.dumps(dict(configFile=str(self.provider.config_file),group=GROUP,original=self.original),indent=2)+'\n')
    def _write(self,key,value):
        subprocess.run(['kwriteconfig6','--file',str(self.provider.config_file),'--group',GROUP,'--key',key,value],check=True,timeout=5)
    def apply(self,layouts,group):
        if self.provider._sample()!=self.expected:raise RuntimeError('Layout library changed externally; no edit applied')
        temporary=dict(self.original)
        catalog=json.loads(temporary['CustomLayouts'])
        if len(catalog)+len(layouts)>20:
            slots=[i for i,item in enumerate(catalog) if not item.get('groupId')]
            if len(slots)<len(layouts):raise RuntimeError('Full layout catalog requires two ungrouped slots for reversible test zones')
            for slot,layout in zip(slots,layouts):
                catalog[slot]=dict(layout,id=catalog[slot]['id'])
        else:catalog+=layouts
        temporary['CustomLayouts']=json.dumps(catalog)
        temporary['CustomGroups']=json.dumps(json.loads(temporary['CustomGroups'])+[group])
        temporary['GroupOrder']=json.dumps(json.loads(temporary['GroupOrder'])+[group['id']])
        temporary['LayoutPadding']='0'
        self.provider._validate(temporary)
        self.applied=True
        for key in KEYS:
            if temporary[key]!=self.expected[key]:
                self.write(key,temporary[key]);self.expected[key]=temporary[key]
        if self.provider._sample()!=self.expected:raise RuntimeError('Temporary library write not verified')
        result=self.provider.read_snapshot()
        if not result.get('executionReady'):raise RuntimeError('Temporary library is not ready')
        return result
    def restore(self):
        if not self.applied:return
        if self.provider._sample()!=self.expected:raise RuntimeError('Library changed externally; refusing overwrite. Use saved backup for recovery.')
        for key in KEYS:
            if self.expected[key]!=self.original[key]:self.write(key,self.original[key]);self.expected[key]=self.original[key]
        if self.provider._sample()!=self.original:raise RuntimeError('Original library not recovered')
        self.applied=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--capture-only',action='store_true')
    parser.add_argument('--status-only',action='store_true')
    args=parser.parse_args();app=QCoreApplication.instance() or QCoreApplication([])
    if args.status_only:
        state=read_status();print(json.dumps({k:state.get(k) for k in ('readiness','paused','resumeReady','transactionActive')}));return
    if args.capture_only:
        sample=capture();print(json.dumps(dict(outputs=[o['connector'] for o in sample['outputs']],windows=len(sample['windows']),readOnly=True)));return
    if not args.receipt:parser.error('--receipt required')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();library=None;original=None
    def interrupted(signum,frame):raise RuntimeError('Operator stopped test')
    signal.signal(signal.SIGTERM,interrupted)
    with args.receipt.open('x') as stream:
        def record(kind,value):stream.write(json.dumps(dict(elapsed=time.monotonic()-started,kind=kind,value=value))+'\n');stream.flush()
        def intent(action,payload=None):
            reply=call_host(action,payload);record(action,reply)
            if not reply or reply.get('ok') is not True:raise RuntimeError(action+' refused: '+str(reply.get('reason') if reply else 'controller unreachable'))
            return reply
        def status():
            return read_status()
        def idle():
            s=status()
            if not s.get('paused') or s.get('transactionActive'):raise RuntimeError('Requires paused idle product')
        def unchanged(before):
            rows=[json.loads(line) for line in FIXTURE.read_text().splitlines()]
            baseline={w['title']:(w['frame'],w['clientSize']) for w in before['windows']}
            for sample in rows:
                if sample['monotonic']<before['monotonic']:continue
                verify_fixture(sample)
                if {w['title']:(w['frame'],w['clientSize']) for w in sample['windows']}!=baseline:
                    raise RuntimeError('Disposable frame or client size changed during refusal round')
        try:
            idle();loaded=preferences.load_settings(preferences.default_settings_path())
            if loaded.error:raise RuntimeError(loaded.error)
            original=loaded.values
            record('original-settings',original)
            library=TemporaryLibrary(args.receipt.with_suffix('.library-backup.json'))
            worker=args.receipt.with_suffix('.worker.js');worker.write_text(build())
            for output_name in ('DP-1','HDMI-A-1'):
                if output_name=='HDMI-A-1':
                    record('operator-switch-panel','LG refusal passed. Move panel to LG, then click/release on both desktops and Continue. Keep all fixtures uncovered.')
                    deadline=time.monotonic()+180
                    while not args.receipt.with_suffix('.continue').exists():
                        if time.monotonic()>deadline:raise RuntimeError('Panel confirmation timed out')
                        time.sleep(.1)
                record('batch-output',output_name);time.sleep(5);idle()
                fixture=json.loads(FIXTURE.read_text().splitlines()[-1]);verify_fixture(fixture)
                if any(sum(w['visible'] and w['screen']==n for w in fixture['windows'])!=2 for n in ('DP-1','HDMI-A-1')):
                    raise RuntimeError('Two visible fixtures per monitor required')
                snapshot=capture()
                output,target,zones,obstacles,layouts,group=test_geometry(snapshot,output_name)
                imported=library.apply(layouts,group)
                probe=subprocess.run(['node',str(ROOT/'tools/desktop_resize_probe.js')],input=json.dumps(dict(worker=str(worker),snapshot=snapshot,output=output,identity=target['identity'],zones=zones,obstacles=obstacles,library=imported,groupId='kde:'+group['id'])),text=True,capture_output=True,timeout=5)
                if probe.returncode:
                    record('resize-probe-error',dict(exitCode=probe.returncode,stderr=probe.stderr[:4096]))
                    reason=next((line.removeprefix('Error: ') for line in probe.stderr.splitlines() if line.startswith('Error: ')), 'Probe failed; see resize-probe-error receipt')
                    raise RuntimeError('Resize preflight: '+reason)
                record('resize-counterfactual',json.loads(probe.stdout))
                settings=dict(original,paused=True,movementDisplays=[output['id']],mode='group',groupId='kde:'+group['id'],rotateGroup=True,maximumWindows=1,intervalMinutes=240,warningSeconds=10,quietSeconds=5,avoidFocusedWindow=True,movementOverrides={})
                preferences.save_settings(preferences.default_settings_path(),settings)
                intent('SettingsChanged',{'revision':revision(settings)})
                deadline=time.monotonic()+10
                while not status().get('resumeReady'):
                    if time.monotonic()>deadline:raise RuntimeError('Native input facts not ready; click/release on both desktops before Start')
                    time.sleep(.1)
                preview=intent('PlanPreview',{'outputId':output['id']});deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    s=status();result=s.get('planningResult',{})
                    if result.get('id')==preview.get('requestId') and result.get('status') not in ('queued','planning'):
                        if result.get('status')!='no-plan' or result.get('reason')!='no_group_rotation' or result.get('moves')!=0:
                            raise RuntimeError('Expected constraint-specific no-plan preview: '+str(result))
                        record('resize-refusal-preview',dict(output=output_name,result=result,libraryRevision=imported['revision']));break
                    time.sleep(.1)
                else:raise RuntimeError('Refusal preview timed out')
                intent('Resume');deadline=time.monotonic()+5
                while not status().get('movementReady'):
                    if time.monotonic()>deadline:raise RuntimeError('Resume did not arm')
                    time.sleep(.1)
                intent('MoveNow',{'outputId':output['id']});deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    s=status()
                    if s.get('skipAvailable') or s.get('restoreAvailable'):
                        intent('Pause');raise RuntimeError('Unexpected movement warning or inverse')
                    if not s.get('transactionActive'):
                        if s.get('lastResult')!='no-plan: no_group_rotation':raise RuntimeError('Unexpected refusal result: '+str(s.get('lastResult')))
                        break
                    time.sleep(.1)
                else:raise RuntimeError('Refusal transaction timed out')
                intent('Pause');time.sleep(2);unchanged(fixture)
                after=capture()
                def fixture_frames(sample):
                    return {w['identity']:w['rect'] for w in sample['windows'] if w.get('appId')=='oled-window-guard-physical-fixture'}
                if fixture_frames(after)!=fixture_frames(snapshot):raise RuntimeError('Compositor-observed fixture geometry changed')
                if next(w for w in after['windows'] if w['identity']==target['identity'])['resizable'] is not False:
                    raise RuntimeError('Target lost its compositor-observed fixed-size constraint')
                record('resize-refusal-result',dict(output=output_name,result=s['lastResult'],warningSeen=False,inverseAvailable=False,allFixtureFramesAndSizesUnchanged=True))
            record('round-result',dict(case='resize-refusal-both',result='both-fixed-size-resize-refusals-verified',physicalObservationPending=True))
        except Exception as error:record('error',str(error) or type(error).__name__)
        finally:
            # Block further writes first, then recover exact library/settings.
            pause=call_host('Pause');record('cleanup-pause',pause)
            if not pause.get('ok'):record('error','Cleanup Pause unconfirmed')
            try:
                if library:
                    library.restore();record('library-restored',dict(revision=library.provider._revision(library.original)))
                if original is not None:
                    restored=dict(original,paused=True);preferences.save_settings(preferences.default_settings_path(),restored)
                    restored_reply=call_host('SettingsChanged',{'revision':revision(restored)})
                    record('settings-restored',restored_reply)
                    if not restored_reply.get('ok'):raise RuntimeError('Restored settings not acknowledged')
            except Exception as error:record('error','Cleanup: '+str(error))
            pause=call_host('Pause');record('final-pause',pause)
            if not pause.get('ok'):record('error','Final Pause unconfirmed')


if __name__=='__main__':main()
