"""Real private output hotplug, independent target retention and fresh tokens."""
import ast,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import os
if os.environ.get("OWG_TEST_APP_ROOT"):sys.path.insert(0,os.environ["OWG_TEST_APP_ROOT"])
from oled_window_guard.dashboard import create_application
from oled_window_guard.session import SessionHost,register_host,revision
from oled_window_guard import preferences
app=create_application([]);root=Path(sys.argv[1]);host=SessionHost(app,settings_path=root/'settings.json');assert register_host(host)
results=[]
# Preserve the exact rejection if a protocol check fails, instead of reporting
# only the subsequently unavailable dashboard state.
_original_clear=host.clear_runtime
def _record_clear():
 error=sys.exception()
 if error is not None:
  (root/'runtime-rejection.json').write_text(json.dumps(dict(error=str(error)),indent=2))
 return _original_clear()
host.clear_runtime=_record_clear
_original_apply=host.apply_runtime
def _record_apply(runtime,values):
 try:return _original_apply(runtime,values)
 except Exception as error:
  (root/'runtime-rejected-packet.json').write_text(json.dumps(dict(error=str(error),runtime=runtime),indent=2))
  raise
host.apply_runtime=_record_apply
def check(method,*args):
 r=subprocess.run(['gdbus','call','--session','--dest','org.kde.KWin','--object-path','/org/oledwindowguard/NativeCheck','--method','org.oledwindowguard.NativeCheck1.'+method,*args],capture_output=True,text=True,check=True,timeout=3)
 return json.loads(ast.literal_eval(r.stdout.strip())[0])
def wait(predicate,timeout=12):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  app.processEvents()
  if predicate():return
  time.sleep(.01)
 raise RuntimeError('Timed out: '+json.dumps(host.status))
def intent(action,payload=None):
 r=host.submit(action,payload);assert r['ok'],r
 return r

def dwell(seconds):
 until=time.monotonic()+seconds
 while time.monotonic()<until:app.processEvents();time.sleep(.01)
def controls():return check('GuardControls','status')
def dim_state():return check('DimmingStatus')
def flags(movement,brightness):
 return (host.status.get('movementPaused') is not movement and
         host.status.get('brightnessPaused') is not brightness and
         controls()['guards']['movement']==movement and controls()['guards']['brightness']==brightness)
def configure(values):
 intent('Pause');wait(lambda:not host.status['transactionActive'])
 values=dict(values,paused=True)
 preferences.save_settings(host.settings_path,values);intent('SettingsChanged',{'revision':revision(values)})
 check('PrepareWriter');check('OrderedSetup');check('WriterArmFault','none');check('Interact','focus',other['connector'])
 dwell(.25);wait(lambda:host.status.get('movementResumeReady') and host.status.get('brightnessResumeReady'))
def arm(scope):
 intent('Resume'+scope);wait(lambda:not host.status['transactionActive'])
def start_move():
 intent('MoveNow',{'outputId':target['id']});wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
def finish():wait(lambda:not host.status['transactionActive'])
def locked():
 check('Verify','locked');wait(lambda:flags(False,False))
 assert controls()['guards']['unlock'],controls()
def unlocked():check('Verify','unlocked')
def passed(name):results.append(dict(case=name,result='PASS'))
try:
 wait(lambda:len(host.status['displays'])==2 and host.status['planningReady'])
 outputs=check('WriterStatus')['desktop']['outputs'];target,other=outputs[0],outputs[1]
 values=preferences.defaults();values.update(movementDisplays=[target['id']],dimmingDisplays=[target['id']],
     dimWindows=True,dimDisplays=True,windowDimDelay=0,windowDimFade=0,displayDimDelay=0,displayDimFade=0,
     warningSeconds=3,quietSeconds=1,intervalMinutes=240,soundEnabled=False,resumeAfterUnlock=True)
 configure(values);arm('');wait(lambda:flags(True,True) and dim_state()['masks'].get(target['id']))
 deadline=controls()['scheduler']['deadlines'][target['id']]
 check('TopologyOutput','add','OWG-extra');wait(lambda:len(host.status['displays'])==3)
 wait(lambda:flags(True,True));assert controls()['scheduler']['deadlines'][target['id']]==deadline,controls()
 passed('unselected-output-addition-preserves-both-guards-and-deadline')
 check('TopologyOutput','remove','OWG-extra');wait(lambda:len(host.status['displays'])==2)
 wait(lambda:flags(True,True) and dim_state()['masks'].get(target['id']))
 assert controls()['scheduler']['deadlines'][target['id']]==deadline,controls()
 assert check('Inspect')['snapshot']['buttonKnown'],check('Inspect')
 passed('unselected-output-removal-preserves-dimming-input-knowledge-and-deadline')
 count=check('WriterStatus')['writeCount'];start_move()
 check('TopologyOutput','remove',other['connector']);wait(lambda:len(host.status['displays'])==1)
 finish();wait(lambda:flags(True,True) and dim_state()['masks'].get(target['id']))
 assert check('WriterStatus')['writeCount']==count,host.status
 assert host.status['lastResult'] in ('cancelled: native-topology','cancelled: native-input'),host.status
 assert any(event['reason']=='native-topology' for event in host.status['cancellationTrace']),host.status
 wait(lambda:host.status.get('warningRemainingSeconds',0)==0)
 assert host.status['movementActiveDisplays']==[target['id']],host.status
 passed('unrelated-disconnect-cancels-stale-warning-without-pausing-remaining-guards')
 # Reintroducing a selected screen must not silently arm its former scope.
 check('TopologyOutput','add',other['connector']);wait(lambda:len(host.status['displays'])==2)
 wait(lambda:flags(True,True));assert host.status['movementActiveDisplays']==[target['id']],host.status
 # Explicit selection/start now arms both. Removal retains only the survivor.
 values.update(movementDisplays=[target['id'],other['id']],dimmingDisplays=[target['id'],other['id']])
 configure(values);arm('');wait(lambda:len(host.status['movementActiveDisplays'])==2)
 check('TopologyOutput','remove',other['connector']);wait(lambda:len(host.status['displays'])==1)
 wait(lambda:flags(True,True) and host.status['movementActiveDisplays']==[target['id']])
 passed('one-of-two-guarded-displays-disconnects-with-survivor-still-guarded')
 check('TopologyOutput','add',other['connector']);wait(lambda:len(host.status['displays'])==2)
 dwell(.2);assert host.status['movementActiveDisplays']==[target['id']],host.status
 passed('reconnected-target-does-not-auto-arm-while-another-target-remains-active')
 # Different movement/brightness scopes must pause independently.
 values.update(movementDisplays=[target['id']],dimmingDisplays=[other['id']])
 configure(values);arm('');wait(lambda:flags(True,True))
 check('TopologyOutput','remove',other['connector']);wait(lambda:len(host.status['displays'])==1)
 wait(lambda:flags(True,False));passed('brightness-target-disconnect-leaves-movement-guarding-active')
 check('TopologyOutput','add',other['connector']);wait(lambda:len(host.status['displays'])==2)
 values.update(dimmingDisplays=[target['id']]);configure(values);arm('')
 check('TopologyOutput','remove',target['connector']);wait(lambda:len(host.status['displays'])==1)
 wait(lambda:flags(False,False));passed('only-guarded-monitor-disconnection-pauses-both-guards')
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,
     controls=controls(),writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 import traceback
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),traceback=traceback.format_exc(),status=host.status,cases=results),indent=2))
finally:
 host.dashboard.warning_audio.stop();host.dashboard.warning_audio._process.waitForFinished(1000)
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
