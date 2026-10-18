"""Scoped input and persistent preview through real product intents, private KWin only."""
import ast,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
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
try:
 wait(lambda:len(host.status['displays'])==2 and host.status['planningReady'])
 outputs=check('WriterStatus')['desktop']['outputs'];target,other=outputs[0],outputs[1]
 values=preferences.defaults();values.update(movementDisplays=[o['id'] for o in outputs],mode='drift',maximumWindows=1,shiftRangePercent=70,quietSeconds=0,warningSeconds=3,intervalMinutes=240)
 preferences.save_settings(host.settings_path,values);intent('SettingsChanged',{'revision':revision(values)})
 check('PrepareWriter');check('WriterArmFault','none');check('Interact','focus',other['connector'])
 wait(lambda:host.status.get('resumeReady'))
 for attempt in range(2):
  request=intent('PlanPreview',{'outputId':target['id']})
  wait(lambda:host.status.get('planningResult',{}).get('id')==request['requestId'] and host.status['planningResult']['status'] not in ('queued','planning'))
  result=host.status['planningResult'];assert result['status']=='ready' and result['moves'],result
  check('Interact','pointer',target['connector']);check('Interact','pointer',other['connector'])
  wait(lambda:host.dashboard.movement_preview.data is not None)
  assert host.status['planningResult']['status']=='ready',host.status
  results.append(dict(case='repeat-preview-with-motion',attempt=attempt,result='PASS'))
 for kind,on_target,cancels in [('pointer',True,False),('pointer',False,False),('scroll',False,False),('key',False,False),('focus-away',False,False),('scroll',True,True),('key',True,True),('drag',True,True)]:
  intent('Pause');check('PrepareWriter')
  check('Interact','focus',target['connector'] if (kind=='key' and on_target) or kind=='focus-away' else other['connector'])
  wait(lambda:host.status.get('resumeReady') and not host.status['transactionActive'])
  intent('Resume');wait(lambda:host.status.get('movementReady') and not host.status['transactionActive'])
  before=check('WriterStatus')['writeCount']
  intent('MoveNow',{'outputId':target['id']})
  wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
  immediate=check('Interact','focus' if kind=='focus-away' else kind,(target if on_target else other)['connector'])['runtime']
  if cancels:assert not immediate['busy'],immediate
  else:assert immediate['busy'],immediate
  wait(lambda:not host.status['transactionActive'])
  if cancels:
   assert host.status['lastResult'].startswith('cancelled'),host.status
   assert check('WriterStatus')['writeCount']==before
  else:
   assert host.status['lastResult']=='committed' and host.status['restoreAvailable'],host.status
   intent('Restore',{'outputId':target['id']})
   wait(lambda:not host.status['transactionActive'])
   assert host.status['lastResult']=='committed',host.status
  results.append(dict(case=kind,targetDisplay=on_target,expectedCancel=cancels,synchronous=True,result='PASS'))
 for effect in ('windows','display'):
  for behavior in ('stay-bright','restart-timers'):
   intent('Pause');values.update(paused=True,dimWindows=effect=='windows',dimDisplays=effect=='display',dimmingDisplays=[target['id']],windowDimDelay=1,displayDimDelay=1,windowDimFade=0,displayDimFade=0,desktopClickBehavior=behavior)
   preferences.save_settings(host.settings_path,values);intent('SettingsChanged',{'revision':revision(values)})
   check('PrepareWriter');check('Interact','focus',other['connector'])
   wait(lambda:host.status.get('resumeReady') and not host.status['transactionActive'])
   intent('Resume');wait(lambda:not host.status['paused'] and not host.status['transactionActive'])
   immediate=check('Interact','desktopClick',target['connector'])['runtime']
   assert immediate['dimming']['maskCount']==0,immediate
   until=time.monotonic()+1.5
   while time.monotonic()<until:app.processEvents();time.sleep(.01)
   count=host.status.get('dimming',{}).get('maskCount',0)
   assert (count>0)==(behavior=='restart-timers'),dict(effect=effect,behavior=behavior,status=host.status)
   if behavior=='restart-timers':
    immediate=check('Interact','desktopClick',target['connector'])['runtime']
    assert immediate['dimming']['maskCount']==0,immediate
    wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
   results.append(dict(case='desktop-click-dimming',effect=effect,behavior=behavior,result='PASS'))
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),status=host.status,cases=results),indent=2))
finally:
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
