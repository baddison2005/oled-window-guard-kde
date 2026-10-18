"""Temporary session loss and display power recovery in a private KWin."""
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
 state=controls()['guards']
 return (state['movement']==movement and state['brightness']==brightness and
         (not movement or host.status.get('movementPaused') is False) and
         (not brightness or host.status.get('brightnessPaused') is False))
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
     dimWindows=True,windowDimDelay=0,windowDimFade=0,warningSeconds=3,quietSeconds=1,
     intervalMinutes=240,soundEnabled=False,resumeAfterUnlock=True)
 for scope,expected in [('Movement',(True,False)),('Brightness',(False,True)),('',(True,True))]:
  for enter,leave in [('sleep','wake'),('inactive','active')]:
   configure(values);arm(scope);wait(lambda:flags(*expected));writes=check('WriterStatus')['writeCount']
   check('Verify',enter);wait(lambda:controls()['guards']['unlock'] is not None)
   assert not controls()['guards']['movement'] and not controls()['guards']['brightness']
   wait(lambda:host.status.get('movementResumePending')==expected[0] and host.status.get('brightnessResumePending')==expected[1])
   assert not host.status['paused'] and not host.status['movementReady'] and not host.status['dimmingReady'],host.status
   # Freeze the host long enough to exercise both stale-heartbeat paths.
   time.sleep(1.7)
   check('Verify','topology');check('Verify',leave);wait(lambda:flags(*expected))
   assert check('WriterStatus')['writeCount']==writes
   assert controls()['guards']['unlock'] is None,controls()
   passed((scope.lower() or 'both')+'-'+enter+'-topology-and-stale-host-auto-resume')
 configure(values);arm('');check('Verify','sleep');check('Verify','inactive');check('Verify','locked')
 wait(lambda:controls()['guards']['unlock'] is not None)
 check('Verify','topology');check('Verify','wake');check('Verify','active');dwell(.2)
 assert not controls()['guards']['movement'] and not controls()['guards']['brightness']
 check('Verify','unlocked');wait(lambda:flags(True,True))
 passed('combined-sleep-session-lock-order-waits-for-unlock')
 configure(values);arm('');check('Verify','sleep');wait(lambda:controls()['guards']['unlock'] is not None)
 intent('PauseMovement');finish();check('Verify','wake');wait(lambda:flags(False,True))
 passed('manual-movement-pause-during-sleep-keeps-only-brightness-resume')
 configure(values);arm('');check('Verify','sleep');wait(lambda:controls()['guards']['unlock'] is not None)
 intent('Pause');check('Verify','wake');dwell(.3)
 assert not controls()['guards']['movement'] and not controls()['guards']['brightness'] and controls()['guards']['unlock'] is None
 passed('manual-global-pause-during-sleep-revokes-all-resume')
 configure(values);arm('');writes=check('WriterStatus')['writeCount'];start_move()
 check('Verify','sleep');wait(lambda:controls()['guards']['unlock'] is not None);check('Verify','wake')
 wait(lambda:flags(True,True) and not host.status['transactionActive'])
 assert check('WriterStatus')['writeCount']==writes and not host.status['restoreAvailable']
 passed('sleep-during-warning-discards-move-but-keeps-guarding')
 # Awake host loss still expires authority and must not auto-resume.
 configure(values);arm('');dwell(2.6);time.sleep(.8);wait(lambda:not controls()['guards']['movement'] and not controls()['guards']['brightness'])
 assert controls()['guards']['unlock'] is None
 passed('ordinary-awake-companion-loss-still-fails-closed')
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,
     controls=controls(),writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 import traceback
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),traceback=traceback.format_exc(),status=host.status,cases=results),indent=2))
finally:
 host.dashboard.warning_audio.stop();host.dashboard.warning_audio._process.waitForFinished(1000)
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
