"""Grace cutoff, idle focused windows, fresh planning and uninterrupted dimming, private KWin only."""
import ast,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
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
def configure(values):
 intent('Pause');wait(lambda:not host.status['transactionActive'])
 values['paused']=True
 preferences.save_settings(host.settings_path,values);intent('SettingsChanged',{'revision':revision(values)})
 check('PrepareWriter');check('Interact','focus',other['connector'])
 wait(lambda:host.status.get('resumeReady'))
 intent('Resume');wait(lambda:not host.status['paused'] and not host.status['transactionActive'])
def frames():
 return {w['identity']:w['rect'] for w in check('WriterStatus')['desktop']['windows'] if w['appId']=='oled-window-guard-runtime-test'}
def close(a,b):return all(abs(a[k]-b[k])<=.5 for k in a)
def dim_state():return check('DimmingStatus')
def dwell(seconds):
 until=time.monotonic()+seconds
 while time.monotonic()<until:app.processEvents();time.sleep(.01)
def start_move():
 intent('MoveNow',{'outputId':target['id']});wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
def finish_and_restore(before):
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed' and host.status['restoreAvailable'],host.status
 after=frames();changed=[key for key in before if not close(before[key],after[key])]
 assert len(changed)==2,dict(before=before,after=after)
 plan=check('WriterStatus')['state']['undoRecord']['plan'];assert len(plan['moves'])==2
 intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed',host.status
 assert all(close(value,frames()[key]) for key,value in before.items())
try:
 wait(lambda:len(host.status['displays'])==2 and host.status['planningReady'])
 outputs=check('WriterStatus')['desktop']['outputs'];target,other=outputs[0],outputs[1]
 values=preferences.defaults();values.update(movementDisplays=[target['id']],mode='drift',maximumWindows=2,
     shiftRangePercent=10,quietSeconds=1,warningSeconds=3,intervalMinutes=240,
     shiftHorizontalOrder=False,shiftVerticalOrder=False)
 configure(values);check('WriterArmFault','none')
 # A focused application stays eligible when interaction stops within grace.
 check('Interact','focus',target['connector']);before=frames();start_move()
 for kind in ('key','scroll'):
  assert check('Interact',kind,target['connector'])['runtime']['busy']
 finish_and_restore(before)
 results.append(dict(case='focused-idle-and-early-input-move-and-restore',result='PASS'))
 # Early manual geometry changes are planned from their new positions after
 # the warning; no stale plan or second warning may be used.
 configure(values);check('Interact','focus',target['connector']);start_move()
 check('Interact','hold',target['connector']);assert check('MoveActive','12','8')['moved']
 check('Interact','release',target['connector']);before=frames()
 assert check('Interact','pointer',target['connector'])['runtime']['busy']
 finish_and_restore(before)
 results.append(dict(case='early-drag-fresh-plan-and-restore',result='PASS'))
 for kind in ('key','scroll','press','drag'):
  configure(values);check('Interact','focus',target['connector']);before=frames();count=check('WriterStatus')['writeCount']
  start_move();dwell(1.15)
  assert not check('Interact',kind,target['connector'])['runtime']['busy']
  wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult'].startswith('cancelled'),host.status
  assert check('WriterStatus')['writeCount']==count
  assert all(close(value,frames()[key]) for key,value in before.items())
  results.append(dict(case='late-'+kind+'-synchronous-cancel',result='PASS'))
 # A press inside grace still held at cutoff must block a write, without a
 # further motion event. Global session loss must always cancel during grace.
 configure(values);check('Interact','focus',target['connector']);before=frames();start_move()
 assert check('Interact','hold',target['connector'])['runtime']['busy'];dwell(1.2)
 wait(lambda:not host.status['transactionActive']);check('Interact','release',target['connector'])
 assert all(close(value,frames()[key]) for key,value in before.items())
 results.append(dict(case='hold-across-grace-cutoff',result='PASS'))
 configure(values);before=frames();start_move();check('Verify','locking')
 wait(lambda:not host.status['transactionActive']);assert host.status['paused']
 assert all(close(value,frames()[key]) for key,value in before.items())
 results.append(dict(case='lock-inside-grace-cancels',result='PASS'))
 check('Verify','locked') # complete the private lock transition before reset/unlock
 for effect in ('windows','display'):
  values.update(dimWindows=effect=='windows',dimDisplays=effect=='display',dimmingDisplays=[target['id']],
      windowDimDelay=1,displayDimDelay=1,windowDimFade=0,displayDimFade=0)
  configure(values)
  if effect=='windows':check('Interact','focus',target['connector'])
  wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
  before_dim=dim_state()['state']['outputs'][target['id']]
  if effect=='windows':
   check('Interact','hold',target['connector'])
   for _ in range(5):
    check('MoveActive','2','0');check('Interact','pointer',target['connector'])
    assert dim_state()['masks'][target['id']]
    assert dim_state()['state']['outputs'][target['id']]['windowDelay']==before_dim['windowDelay']
   check('Interact','release',target['connector'])
   results.append(dict(case='inactive-window-stays-dimmed-during-drag',result='PASS'))
  before=frames();start_move()
  for _ in range(6):
   dwell(.35);state=dim_state();assert state['masks'][target['id']],state
   key='windowDelay' if effect=='windows' else 'displayDelay'
   assert state['state']['outputs'][target['id']][key]==before_dim[key],state
  # Other-output interaction after cutoff also preserves warning and dimming.
  if effect=='display':assert check('Interact','key',other['connector'])['runtime']['busy']
  wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  after_dim=dim_state();assert after_dim['masks'][target['id']]
  assert after_dim['state']['outputs'][target['id']][key]==before_dim[key]
  intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  assert dim_state()['state']['outputs'][target['id']][key]==before_dim[key]
  results.append(dict(case=effect+'-dimming-through-warning-move-and-restore',result='PASS'))
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),status=host.status,cases=results),indent=2))
finally:
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
