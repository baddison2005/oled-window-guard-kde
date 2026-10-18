"""Countdown samples, held typing, real KWin drags and dimmed warnings; private session only."""
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
     shiftRangePercent=10,quietSeconds=4,warningSeconds=8,intervalMinutes=240,
     dimWindows=True,dimDisplays=False,dimmingDisplays=[target['id']],windowDimDelay=0,windowDimFade=0)
 configure(values);check('WriterArmFault','none');check('Interact','focus',target['connector'])
 wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
 wait(lambda:dim_state()['planes'][target['id']]['confirmed'])
 baseline=dim_state();samples=[]
 (root/'drag-baseline.json').write_text(json.dumps(baseline,indent=2))
 slot_ids=[slot["id"] for slot in baseline["planes"][target["id"]]["slots"]];assert slot_ids,baseline
 result=check('InteractiveMove','begin','0','0');assert result['interactive'],result
 for i in range(12):
  result=check('InteractiveMove','step','2','0');dwell(.05)
  samples.append(dict(interactive=result['interactive'],state=dim_state(),desktop=check('WriterStatus')['desktop'],dimming=host.status.get('dimming')))
 check('InteractiveMove','end','0','0');dwell(.2)
 (root/'drag-diagnostics.json').write_text(json.dumps(dict(baseline=baseline,samples=samples,after=dim_state()),indent=2))
 assert all(s['state']['masks'].get(target['id']) for s in samples),samples
 for sample in samples:
  plane=sample['state']['planes'][target['id']]
  assert plane['confirmed'] and plane['visible'],sample
  assert [slot['id'] for slot in plane['slots']]==slot_ids,sample
  assert any(slot['visible'] and slot['opacity']>.29 for slot in plane['slots']),sample
 results.append(dict(case='true-interactive-move-retains-visible-mask-delegates',result='PASS'))
 # Known held typing must not toggle the ability to begin a warning. It still
 # cancels synchronously if held at or arriving after the grace cutoff.
 check('Interact','focus',target['connector']);check('Interact','keyHold',target['connector'])
 for _ in range(5):
  dwell(.1);assert host.status.get('movementReady'),host.status
 before=frames();start_move();dwell(.1)
 assert dim_state()['planes'][target['id']]['stacking']<check('WarningStatus')['stacking']
 trace=[];started=time.monotonic();released=False;last_key=0
 while host.status['transactionActive']:
  elapsed=time.monotonic()-started
  if elapsed<2 and elapsed-last_key>.15:
   check('Interact','key',target['connector']);last_key=elapsed
  if elapsed>=2 and not released:check('Interact','keyRelease',target['connector']);released=True
  sample=check('WarningStatus')
  if sample['generation']>=0 and sample['confirmed']:trace.append(dict(elapsed=elapsed,**sample))
  assert not host.dashboard.move_now_button.isEnabled(),host.status
  dwell(.03)
 assert host.status['lastResult']=='committed',host.status
 assert 7.6<=time.monotonic()-started<=10
 assert trace and trace[0]['remaining']==8,trace[:4]
 assert all(a['remaining']>=b['remaining'] for a,b in zip(trace,trace[1:])),trace
 assert all(s['text']==str(s['remaining'])+' s' for s in trace),trace
 assert {s['remaining'] for s in trace}.issuperset({1,2,3,4,5,6,7,8}),trace
 (root/'countdown-trace.json').write_text(json.dumps(trace,indent=2))
 intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed',host.status
 assert all(close(value,frames()[key]) for key,value in before.items())
 results.append(dict(case='8-second-countdown-monotonic-through-grace-and-typing',result='PASS'))
 configure(values);check('Interact','focus',target['connector']);before=frames();start_move()
 check('Interact','keyHold',target['connector']);dwell(4.2)
 wait(lambda:not host.status['transactionActive']);check('Interact','keyRelease',target['connector'])
 assert host.status['lastResult'].startswith('cancelled'),host.status
 assert all(close(value,frames()[key]) for key,value in before.items())
 results.append(dict(case='held-key-cancels-at-grace-cutoff',result='PASS'))
 # The warning is an occluder only for window masks; whole-display dimming
 # covers the warning and the native paint plane must stack above it.
 values.update(dimWindows=False,dimDisplays=True,displayDimDelay=0,displayDimFade=0)
 configure(values);wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
 start_move();dwell(.2)
 state=dim_state();warning=check('WarningStatus');masks=state['masks'][target['id']]
 assert len(masks)==1 and masks[0]['frame']==target['geometry'],state
 assert state['planes'][target['id']]['stacking']>warning['stacking']>=0,dict(state=state,warning=warning)
 (root/'warning-dimming.json').write_text(json.dumps(dict(state=state,warning=warning),indent=2))
 intent('Skip');wait(lambda:not host.status['transactionActive'])
 results.append(dict(case='whole-display-mask-covers-warning-and-stacks-above-it',result='PASS'))
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error)[:1000],status=host.status,cases=results),indent=2))
finally:
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
