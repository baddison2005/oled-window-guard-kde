"""Grace cutoff, idle focused windows, fresh planning and uninterrupted dimming, private KWin only."""
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
def finish_and_restore(before):
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed' and host.status['restoreAvailable'],host.status
 after=frames();changed=[key for key in before if not close(before[key],after[key])]
 assert len(changed)==2,dict(before=before,after=after)
 plan=check('WriterStatus')['state']['undoRecord']['plan'];assert len(plan['moves'])==2
 intent('Restore',{'outputId':target['id']})
 wait(lambda:bool(host.status.get('restorePreview')))
 drawing=host.status['restorePreview']
 assert len(drawing['destinations'])==2,drawing
 for move in drawing['destinations']:
  identity=next(k for k,v in after.items() if close(v,move['from']))
  assert close(move['to'],before[identity]),drawing
 assert any(w['moveNumber'] for w in drawing['windowInfo']),drawing
 (root/'restore-preview.json').write_text(json.dumps(dict(preview=drawing,before=before,after=after),indent=2))
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed',host.status
 assert all(close(value,frames()[key]) for key,value in before.items())
try:
 wait(lambda:len(host.status['displays'])==2 and host.status['planningReady'])
 outputs=check('WriterStatus')['desktop']['outputs'];target,other=outputs[0],outputs[1]
 trigger=Path(os.environ['OWG_WRITER_CLIENT_TRIGGER'])
 for role,window_type in [('Notification','notification'),('CriticalNotification','notification'),('OnScreenDisplay','osd')]:
  trigger.write_text(role)
  wait(lambda:any(w['windowType']==window_type for w in check('WriterStatus')['desktop']['windows']))
  trigger.write_text('overlay-hide')
  wait(lambda:not any(w['windowType'] in ('notification','osd') for w in check('WriterStatus')['desktop']['windows']))
 values=preferences.defaults();values.update(movementDisplays=[target['id']],mode='drift',maximumWindows=2,
     shiftRangePercent=10,quietSeconds=1,warningSeconds=3,intervalMinutes=240,
     shiftHorizontalOrder=False,shiftVerticalOrder=False,soundEnabled=True,dimWindows=True,dimDisplays=False,
     dimmingDisplays=[target['id']],windowDimDelay=0,windowDimFade=0)
 configure(values);check('WriterArmFault','none');check('Interact','focus',target['connector'])
 wait(lambda:host.status.get('livePreview') is not None)
 assert host.status['livePreview']['windows'] and not host.status['livePreview']['destinations']
 assert any(w['eligibility']=='eligible' for w in host.status['livePreview']['windowInfo']),host.status['livePreview']
 results.append(dict(case='current-positions-without-preview-click',result='PASS'))
 # A real compositor-owned full-output input-transparent surface must not
 # become a dimming occluder or an application planning obstacle.
 check('PassiveVisual','show');dwell(.2);visual=check('PassiveVisual','show')
 assert visual['created'] and visual['passive'],visual
 wait(lambda:host.status.get('dimming',{}).get('maskCount',0)>0)
 assert not any(w['appId']=='' and w['rect']['width']==900 and w['rect']['height']==650 for w in visual['desktop']['windows']),visual
 check('PassiveVisual','hide');results.append(dict(case='passive-full-monitor-compositor-overlay-ignored',result='PASS'))
 # A busy companion during initial renderer warm-up must not require a second
 # Start guarding click. The writer's existing authority checks stay strict.
 count=check('WriterStatus')['writeCount'];time.sleep(.65)
 wait(lambda:not host.status['paused'] and host.status['readiness']=='ready')
 assert check('WriterStatus')['writeCount']==count
 results.append(dict(case='first-resume-recovers-from-renderer-warmup-stall',result='PASS'))
 # Model the measured 910 ms cold multimedia initialization. Audio work must
 # happen outside the host after the arming recovery window has expired.
 dwell(2.6)
 audio=host.dashboard.warning_audio
 slow_audio=root/'slow-audio.py'
 slow_audio.write_text('import sys,time\nfor line in sys.stdin:\n time.sleep(.91)\n print("sent",flush=True)\n')
 original_audio_args=audio._process.arguments()
 audio._process.setArguments([str(slow_audio)])
 before=frames();intent('PlanPreview',{'outputId':target['id']})
 wait(lambda:host.status['planningResult'].get('status')=='ready')
 start_move();finish_and_restore(before)
 assert not host.status['paused'],host.status
 audio._process.kill();audio._process.waitForFinished(1000)
 audio._process.setArguments(original_audio_args)
 results.append(dict(case='910ms-cold-audio-does-not-block-preview-warning-heartbeat',result='PASS'))
 results.append(dict(case='validated-restore-preview-matches-inverse-destinations',result='PASS'))
 for early_drag in (False,True):
  configure(values);check('WriterArmFault','none');check('Interact','focus',target['connector'])
  intent('PlanPreview',{'outputId':target['id']});wait(lambda:host.status['planningResult'].get('status')=='ready')
  drawing=host.status['planningResult']['preview'];before=frames();start_move()
  wait(lambda:host.status['planningResult'].get('purpose')=='warning-preview')
  assert host.status['planningResult']['preview']['destinations']==drawing['destinations']
  if early_drag:
   check('Interact','hold',target['connector']);check('MoveActive','12','8');check('Interact','release',target['connector']);before=frames()
  dwell(1.3)
  wait(lambda:host.status['planningResult'].get('status')=='ready')
  final_drawing=host.status['planningResult']['preview']
  if early_drag:assert final_drawing['destinations']!=drawing['destinations']
  wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  assert host.status['committedWindows']==2 and host.status['plannedWindows']==2,host.status
  after=frames()
  for move in final_drawing['destinations']:
   identity=next(k for k,v in before.items() if close(v,move['from']))
   assert close(after[identity],move['to']),dict(before=before,after=after,drawing=final_drawing)
  intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  assert all(close(v,frames()[k]) for k,v in before.items())
  results.append(dict(case='grace-refresh-matches-executed-destinations' if early_drag else 'reviewed-preview-matches-executed-destinations',result='PASS'))
 configure(values);check('WriterArmFault','none');check('Interact','focus',target['connector']);start_move()
 wait(lambda:not host.status['transactionActive']);assert host.status['lastResult']=='committed',host.status
 check('MoveActive','12','8');after_manual=frames()
 intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult'].startswith('restore-skipped'),host.status
 assert host.status.get('restorePreview') is None,host.status
 assert all(close(v,frames()[k]) for k,v in after_manual.items())
 results.append(dict(case='refused-restore-never-publishes-destinations',result='PASS'))
 # Genuine compositor notification/OSD roles can appear after grace, remain
 # over geometry writes, and disappear during Restore without changing the
 # reviewed application plan. Ordinary dialogs retain strict protection.
 for role,window_type in [('Notification','notification'),('CriticalNotification','notification'),('OnScreenDisplay','osd')]:
  configure(values);check('WriterArmFault','none');check('Interact','focus',target['connector'])
  intent('PlanPreview',{'outputId':target['id']});wait(lambda:host.status['planningResult'].get('status')=='ready')
  reviewed=host.status['planningResult']['preview']['destinations'];before=frames();start_move();dwell(1.3)
  trigger.write_text(role)
  wait(lambda:any(w['windowType']==window_type for w in check('WriterStatus')['desktop']['windows']))
  wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  after=frames()
  for move in reviewed:
   identity=next(k for k,v in before.items() if close(v,move['from']))
   assert close(after[identity],move['to']),dict(role=role,reviewed=reviewed,after=after)
  intent('Restore',{'outputId':target['id']});wait(lambda:bool(host.status.get('restorePreview')))
  trigger.write_text('overlay-hide');wait(lambda:not host.status['transactionActive'])
  assert host.status['lastResult']=='committed',host.status
  assert all(close(v,frames()[k]) for k,v in before.items())
  results.append(dict(case=role.lower()+'-after-grace-preserves-reviewed-move-and-restore',result='PASS'))
 configure(values);check('WriterArmFault','ordered-settle-0');before=frames();start_move()
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed',host.status
 assert host.status['committedWindows']==2,host.status
 intent('Restore',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='committed',host.status
 results.append(dict(case='180ms-intermediate-client-frame-settles-before-readback',result='PASS'))
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
 configure(values);check('WriterArmFault','ordered-external-1');start_move()
 wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult'].startswith('failed-uncertain'),host.status
 assert host.status['movementPaused'] and not host.status['movementReady'],host.status
 assert not host.status['brightnessPaused'],host.status
 assert host.status['unverifiedWindows']==1 and host.status['lastMovementFailure']['geometry'],host.status
 count=check('WriterStatus')['writeCount'];failure=host.status['lastMovementFailure'];dwell(.6)
 assert check('WriterStatus')['writeCount']==count and host.status['lastMovementFailure']==failure
 (root/'uncertain-result.json').write_text(json.dumps(host.status,indent=2))
 results.append(dict(case='uncertain-write-pauses-movement-and-retains-brightness-guarding',result='PASS'))
 # Exercise the same desktop-file alias transport with the disposable app.
 values.update(excludedApps=['com.valvesoftware.Steam'],dimmingExcludedApps=['com.valvesoftware.Steam'])
 configure(values)
 host.alias_key=tuple(['com.valvesoftware.Steam']);host.application_aliases={'com.valvesoftware.Steam':['oled-window-guard-runtime-test']}
 dwell(.3);check('Interact','focus',target['connector']);dwell(.3)
 assert not dim_state()['masks'].get(target['id']),dim_state()
 before=frames()
 intent('MoveNow',{'outputId':target['id']});wait(lambda:not host.status['transactionActive'])
 assert host.status['lastResult']=='skipped: no-eligible-windows',host.status
 assert all(close(v,frames()[k]) for k,v in before.items())
 results.append(dict(case='desktop-class-alias-excludes-movement-and-dimming',result='PASS'))
 wait(lambda:bool(host.status['livePreview'].get('windowInfo')) and
     all(w['eligibility']=='excluded' for w in host.status['livePreview']['windowInfo'] if w['appId']=='oled-window-guard-runtime-test'))
 results.append(dict(case='preview-marks-exempt-app-windows-excluded',result='PASS'))
 # Exempt windows must also remain bright during whole-monitor dimming,
 # while the rest of the selected output continues to dim.
 values.update(dimDisplays=True,displayDimDelay=0,displayDimFade=0)
 configure(values);check('Interact','focus',other['connector'])
 wait(lambda:bool(dim_state()['masks'].get(target['id'])))
 masks=dim_state()['masks'][target['id']]
 def overlaps(a,b):return min(a['x']+a['width'],b['x']+b['width'])>max(a['x'],b['x']) and min(a['y']+a['height'],b['y']+b['height'])>max(a['y'],b['y'])
 assert all(m['wholeDisplay'] for m in masks),masks
 assert not any(overlaps(m['frame'],rect) for m in masks for rect in frames().values()),masks
 results.append(dict(case='desktop-class-alias-stays-bright-during-whole-monitor-dimming',result='PASS'))
 intent('Pause')
 (root/'planning-result.json').write_text(json.dumps(dict(result='PASS',cases=results,noNormalDesktopAccess=True,writeAudit=check('WriterStatus')['writeHistory']),indent=2))
except Exception as error:
 (root/'planning-result.json').write_text(json.dumps(dict(result='FAIL',reason=str(error),status=host.status,cases=results),indent=2))
finally:
 host.dashboard.warning_audio.stop();host.dashboard.warning_audio._process.waitForFinished(1000)
 host.submit('Pause');host.dashboard._read_executor.shutdown(wait=False,cancel_futures=True)
