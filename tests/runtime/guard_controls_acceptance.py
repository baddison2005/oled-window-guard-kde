"""Independent guards, opt-in unlock and silent empty checks in private KWin."""
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
 assert flags(False,False),host.status
 values=preferences.defaults();values.update(movementDisplays=[target['id']],mode='drift',maximumWindows=2,
     shiftRangePercent=10,quietSeconds=1,warningSeconds=3,intervalMinutes=240,
     soundEnabled=False,dimWindows=True,dimDisplays=False,dimmingDisplays=[target['id']],
     windowDimDelay=0,windowDimFade=0,resumeAfterUnlock=True)
 configure(values);arm('Brightness');wait(lambda:flags(False,True) and dim_state()['masks'].get(target['id']))
 assert controls()['scheduler']['phase']!='Running',controls()
 assert host.status['nextMoveCheck'] is None,host.status
 passed('brightness-only-start-does-not-arm-movement')
 arm('Movement');wait(lambda:flags(True,True) and dim_state()['masks'].get(target['id']))
 passed('starting-movement-keeps-brightness-running')
 before=check('WriterStatus')['writeCount'];start_move();intent('PauseMovement');finish()
 wait(lambda:flags(False,True) and not controls()['warningActive']>=0)
 assert check('WriterStatus')['writeCount']==before,host.status
 assert dim_state()['masks'].get(target['id']),dim_state()
 passed('pause-movement-during-warning-keeps-inactive-windows-dimmed')
 arm('Movement');start_move();intent('PauseBrightness')
 wait(lambda:flags(True,False) and not dim_state()['masks'].get(target['id']))
 finish();assert host.status['lastResult']=='committed' and host.status['restoreAvailable'],host.status
 intent('Restore',{'outputId':target['id']});finish()
 assert host.status['lastResult']=='committed',host.status
 passed('pause-brightness-during-warning-preserves-move-and-restore')
 # Exempt all owned normal windows. Force only the private scheduler deadline;
 # production warning, eligibility and native clocks still run normally.
 configure(dict(values,excludedApps=['oled-window-guard-runtime-test']))
 arm('Movement');serial=controls()['warningSerial'];writes=check('WriterStatus')['writeCount']
 intent('MoveNow',{'outputId':target['id']});finish()
 assert host.status['lastResult']=='skipped: no-eligible-windows',host.status
 assert controls()['warningSerial']==serial and check('WriterStatus')['writeCount']==writes
 passed('manual-move-on-no-eligible-windows-shows-no-warning')
 check('GuardControls','due')
 wait(lambda:controls()['scheduler']['lastResult'] is not None)
 scheduler=controls()['scheduler']
 assert scheduler['phase']=='Running' and scheduler['inFlight'] is None and scheduler['deadlines'][target['id']]>0,scheduler
 assert controls()['warningSerial']==serial and flags(True,False),controls()
 passed('automatic-empty-check-silently-advances-timer-and-stays-armed')
 generation=controls()['scheduler']['generation']
 updated=preferences.load_settings(host.settings_path).values;updated['excludedApps']=[]
 preferences.save_settings(host.settings_path,updated);intent('SettingsChanged',{'revision':revision(updated)})
 wait(lambda:controls()['scheduler']['phase']=='Running' and controls()['scheduler']['generation']>generation and not host.status['transactionActive'])
 check('GuardControls','due');wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
 assert controls()['warningSerial']>serial and flags(True,False),controls()
 intent('PauseMovement');finish();passed('newly-eligible-windows-are-discovered-by-next-check')
 # Every combination must resume without a new desktop click; no persistent
 # arming setting is involved, and paused components stay paused.
 for scope,expected in [('Movement',(True,False)),('Brightness',(False,True)),('',(True,True))]:
  configure(values);arm(scope);wait(lambda:flags(*expected))
  locked();unlocked();wait(lambda:flags(*expected))
  assert controls()['guards']['unlock'] is None,controls()
  passed('unlock-restores-only-'+(scope.lower() or 'both')+'-guarding')
 configure(values);arm('');locked();check('Verify','press');unlocked();dwell(.25)
 assert flags(False,False) and controls()['guards']['unlock'],controls()
 check('Verify','release');wait(lambda:flags(True,True))
 passed('held-button-after-unlock-delays-resume-until-physical-release')
 configure(values);arm('Movement');locked();time.sleep(2)
 wait(lambda:controls()['guards']['unlock'] is not None)
 unlocked();wait(lambda:flags(True,False))
 passed('sleep-like-heartbeat-gap-and-host-timeout-preserve-lock-resume')
 configure(values);arm('');locked()
 intent('PauseMovement');finish();unlocked();wait(lambda:flags(False,True))
 passed('manual-pause-while-locked-revokes-only-movement-resume')
 configure(values);arm('Movement');locked();check('Verify','topology');unlocked();wait(lambda:flags(True,False))
 passed('display-power-topology-change-while-locked-preserves-unlock-resume')
 configure(dict(values,resumeAfterUnlock=False));arm('');check('Verify','locked')
 wait(lambda:flags(False,False));unlocked();dwell(.4)
 assert flags(False,False) and controls()['guards']['unlock'] is None,controls()
 passed('unlock-opt-out-keeps-both-guards-paused')
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,
     controls=controls(),writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 import traceback
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),traceback=traceback.format_exc(),status=host.status,cases=results),indent=2))
finally:
 host.dashboard.warning_audio.stop();host.dashboard.warning_audio._process.waitForFinished(1000)
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
