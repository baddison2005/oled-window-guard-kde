"""Real XWayland placement and inverse readback in a private compositor."""
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
 check('PrepareWriter');check('OrderedSetup');check('Interact','focus',other['connector'])
 wait(lambda:host.status.get('resumeReady'))
 intent('Resume');wait(lambda:not host.status['paused'] and not host.status['transactionActive'])
def frames():
 return {w['identity']:w['rect'] for w in check('WriterStatus')['desktop']['windows'] if w['appId']=='oled-window-guard-runtime-test' and w['windowType']=='normal'}
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
 values=preferences.defaults();values.update(movementDisplays=[target['id']],mode='drift',maximumWindows=2,
     shiftRangePercent=10,quietSeconds=1,warningSeconds=3,intervalMinutes=240,
     shiftHorizontalOrder=False,shiftVerticalOrder=False,soundEnabled=False)
 # XWayland applications (including Steam) must exercise the production
 # ordered writer too; merely enumerating an iconified X11 window misses this.
 assert os.environ.get('OWG_PRIVATE_X11_DISPLAY'), 'Private XWayland fixture unavailable'
 xenv=dict(os.environ,DISPLAY=os.environ['OWG_PRIVATE_X11_DISPLAY'])
 if os.environ.get('OWG_PRIVATE_X11_AUTHORITY'):xenv['XAUTHORITY']=os.environ['OWG_PRIVATE_X11_AUTHORITY']
 xclient=subprocess.Popen(['xterm','-geometry','30x4+600+20'],env=xenv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  def xwindows():return [w for w in check('WriterStatus')['desktop']['windows'] if 'xterm' in w['appId'].lower() and not w['minimized']]
  wait(lambda:len(xwindows())==1)
  xvalues=dict(values,excludedApps=['oled-window-guard-runtime-test'],maximumWindows=1)
  configure(xvalues);check('WriterArmFault','none')
  before=xwindows()[0]['rect'];intent('PlanPreview',{'outputId':target['id']})
  wait(lambda:host.status['planningResult'].get('status')=='ready')
  (root/'xwayland-preview.json').write_text(json.dumps(host.status['planningResult'],indent=2))
  start_move();wait(lambda:not host.status['transactionActive'])
  (root/'xwayland-result.json').write_text(json.dumps(dict(status=host.status,writer=check('WriterStatus')),indent=2))
  assert host.status['lastResult']=='committed' and host.status['committedWindows']==1,host.status
  intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed' and close(before,xwindows()[0]['rect']),host.status
  results.append(dict(case='xwayland-fractional-move-and-restore',result='PASS'))
  # Mixed Wayland/XWayland coordinated moves can temporarily cross source
  # frames. All participants must reach the previewed layout and restore.
  mixed=dict(values,excludedApps=[],maximumWindows=3,shiftRangePercent=100,
             shiftHorizontalOrder=True,shiftVerticalOrder=True,allowTransientOverlap=True)
  configure(mixed);check('WriterArmFault','none')
  before_mixed=frames();before_x=xwindows()[0]['rect']
  intent('PlanPreview',{'outputId':target['id']})
  wait(lambda:host.status['planningResult'].get('status')=='ready')
  reviewed=host.status['planningResult']['preview']
  assert any(w['appId']=='xterm' and w['moveNumber']>0 for w in reviewed['windowInfo']),reviewed
  start_move();wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed' and host.status['restoreAvailable'],host.status
  assert not close(before_x,xwindows()[0]['rect']),host.status
  intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed' and close(before_x,xwindows()[0]['rect']),host.status
  assert all(close(v,frames()[k]) for k,v in before_mixed.items())
  results.append(dict(case='mixed-xwayland-wayland-coordination-and-restore',result='PASS'))
 finally:
  xclient.terminate();xclient.wait(timeout=3)
  wait(lambda:not xwindows())
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),status=host.status,cases=results),indent=2))
finally:
 host.dashboard.warning_audio.stop();host.dashboard.warning_audio._process.waitForFinished(1000)
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
