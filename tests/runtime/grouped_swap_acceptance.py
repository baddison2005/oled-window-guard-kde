"""Whole-group assignments, native fitting, Restore and partial failure, private KWin only."""
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
def configure(values):
 intent('Pause');wait(lambda:not host.status['transactionActive'])
 values['paused']=True
 preferences.save_settings(host.settings_path,values);intent('SettingsChanged',{'revision':revision(values)})
 check('PrepareWriter');check('GroupedSwapSetup');check('Interact','focus',other['connector'])
 dwell(.25)
 wait(lambda:host.status.get('resumeReady'))
 intent('Resume');wait(lambda:not host.status['paused'] and not host.status['transactionActive'] and host.status.get('planningReady'))
def frames():
 return {w['identity']:w['rect'] for w in check('WriterStatus')['desktop']['windows'] if w['appId']=='oled-window-guard-runtime-test' and w['windowType']=='normal' and target['id'] in w['outputIds'] and w['rect']['width']>100}
def close(a,b):return all(abs(a[k]-b[k])<=.5 for k in a)
def dim_state():return check('DimmingStatus')
def dwell(seconds):
 until=time.monotonic()+seconds
 while time.monotonic()<until:app.processEvents();time.sleep(.01)
def start_move():
 intent('MoveNow',{'outputId':target['id']});wait(lambda:host.status.get('warningRemainingSeconds',0)>0)
try:
 wait(lambda:len(host.status['displays'])==2 and host.status['planningReady'])
 outputs=check('WriterStatus')['desktop']['outputs'];target,other=outputs[0],outputs[1]
 values=preferences.defaults();values.update(movementDisplays=[target['id']],mode='swap',maximumWindows=4,
     keepSimilarWindowsTogether=True,sizeTolerance=.1,quietSeconds=1,warningSeconds=3,intervalMinutes=240,allowTransientOverlap=True,soundEnabled=False)
 for limit in [4,3]:
  configure(dict(values,maximumWindows=limit));check('WriterArmFault','none');before=frames();assert len(before)==4,before
  block=[identity for identity,r in before.items() if r['x']==599]
  assert len(block)==2,dict(block=block,before=before)
  intent('PlanPreview',{'outputId':target['id']});wait(lambda:host.status['planningResult'].get('status')=='ready')
  drawing=host.status['planningResult']['preview'];assert len(drawing['destinations'])==limit,drawing
  start_move();wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed' and host.status['restoreAvailable'] and host.status['committedWindows']==limit,host.status
  after=frames();assert after[block[0]]['x']==after[block[1]]['x']
  assert abs(abs(after[block[0]]['y']-after[block[1]]['y'])-200)<.01,after
  for move in drawing['destinations']:
   identity=next(k for k,v in before.items() if close(v,move['from']));assert close(after[identity],move['to']),dict(after=after,drawing=drawing)
  intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  assert all(close(v,frames()[k]) for k,v in before.items()),dict(before=before,after=frames())
  results.append(dict(case='whole-groups-'+str(limit)+'-windows-preview-fit-and-restore',result='PASS',preview=drawing))
 configure(dict(values));check('WriterArmFault','ordered-timeout-1');before=frames();start_move()
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult'].startswith('failed-rolled-back'),host.status
 assert all(close(v,frames()[k]) for k,v in before.items()),dict(before=before,after=frames())
 results.append(dict(case='whole-group-partial-failure-restores-all-original-geometry',result='PASS'))
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 import traceback
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),traceback=traceback.format_exc(),status=host.status,cases=results),indent=2))
 raise
