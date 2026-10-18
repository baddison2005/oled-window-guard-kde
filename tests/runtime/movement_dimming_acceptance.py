"""Regression checks for multi-window drift and independent dimming, private KWin only."""
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
try:
 wait(lambda:len(host.status['displays'])==2 and host.status['planningReady'])
 outputs=check('WriterStatus')['desktop']['outputs'];target,other=outputs[0],outputs[1]
 values=preferences.defaults();values.update(movementDisplays=[target['id']],mode='drift',maximumWindows=2,
     shiftRangePercent=10,quietSeconds=0,warningSeconds=3,intervalMinutes=240)
 for order in (False,True):
  values.update(shiftHorizontalOrder=order,shiftVerticalOrder=False)
  configure(values);check('WriterArmFault','none')
  wait(lambda:host.status.get('nextMoveCheck') is not None)
  before=frames();intent('MoveNow',{'outputId':target['id']})
  wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
  wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed' and host.status['restoreAvailable'],host.status
  plan=check('WriterStatus')['state']['undoRecord']['plan']
  assert plan['metadata']['sourceMode']=='ordinary_shift' and len(plan['moves'])==2,plan
  after=frames();changed=[key for key in before if not close(before[key],after[key])]
  assert len(changed)==2,dict(before=before,after=after)
  assert all(target['id'] in w['outputIds'] for w in check('WriterStatus')['desktop']['windows'] if w['identity'] in changed)
  intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  assert all(close(value,frames()[key]) for key,value in before.items())
  results.append(dict(case='two-window-fallback' if order else 'two-window-drift',moved=2,restored=2,result='PASS'))
 # Withhold the second write acknowledgement to exercise rollback of the real
 # ordinary-drift route, including the write that may already have landed.
 configure(values);check('WriterArmFault','ordered-timeout-1');before=frames()
 intent('MoveNow',{'outputId':target['id']});wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult'].startswith('failed-rolled-back'),host.status
 assert all(close(value,frames()[key]) for key,value in before.items())
 results.append(dict(case='ordinary-second-write-timeout-rollback',result='PASS'))
 check('WriterArmFault','none')
 for effect in ('windows','display'):
  values.update(dimWindows=effect=='windows',dimDisplays=effect=='display',dimmingDisplays=[target['id']],
      windowDimDelay=1,displayDimDelay=1,windowDimFade=0,displayDimFade=0)
  configure(values)
  wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
  for kind in ('key','scroll','press'):
   immediate=check('Interact',kind,other['connector'])['runtime']
   assert immediate['dimming']['maskCount']>0,(effect,kind,immediate)
  results.append(dict(case='other-output-keeps-'+effect+'-dimmed',result='PASS'))
  if effect=='windows':
   check('Interact','focus',target['connector'])
   wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
   for kind in ('key','scroll','press'):
    immediate=check('Interact',kind,target['connector'])['runtime']
    assert immediate['dimming']['maskCount']>0,(kind,immediate)
   results.append(dict(case='active-window-keeps-inactive-window-dimmed',result='PASS'))
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),status=host.status,cases=results),indent=2))
finally:
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
