#!/usr/bin/env python3
"""Operator-started dimming check; no movement intents or input injection."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication,QLabel,QPushButton,QVBoxLayout,QWidget
from oled_window_guard import preferences
from oled_window_guard.session import call_host,revision
from tools.desktop_gtk_batch import verify_fixture,verify_scope,verify_restored
from tools.desktop_gtk_windows import APP_ID
from tools.desktop_resize_refusal import capture,read_status
from tools.desktop_rpm_smoke import verify_rpm_runtime


def verify_hold(elapsed, masks, keyboard, previous_keyboard):
    """Input clears alone must not be mistaken for a shortcut's five-second hold."""
    if keyboard!=previous_keyboard:
        return 'restart'
    if masks:
        return 'failed'
    return 'verified' if elapsed>=4 else 'waiting'


class Check(QWidget):
    def __init__(self,brightness_only=False):
        super().__init__()
        self.brightness_only=brightness_only
        self.setWindowTitle('OWG dimming — window, monitor and brightness check')
        layout=QVBoxLayout(self)
        self.label=QLabel('Use the empty test desktops with TWO uncovered GTK4 disposable windows on EACH monitor.\n'
            'Keep this panel and the dashboard on HDMI. Click/release on both desktops, then Start.\n'
            'Follow the prompts to click a disposable window on LG and then HDMI in each mode.\n'
            'This tests dimming only: no movement warnings or window movement are expected.\n'
            'Allow about 2–3 minutes. The check stops after 4 minutes and restores settings paused.')
        self.label.setWordWrap(True);layout.addWidget(self.label)
        if brightness_only:
            self.label.setText('Brightness-only retry: keep the four disposable windows on the empty test desktops.\n'
                'Put this panel and dashboard on HDMI. Click/release on both desktops, then Start.\n'
                'LG will dim. Verify Restore Brightness, wait for dimming to return, then test Meta+Shift+B.\n'
                'Window and monitor focus checks already passed; they will not be repeated. Allow about one minute.')
        self.start=QPushButton('Start dimming check');self.start.clicked.connect(self.begin);layout.addWidget(self.start)
        self.advance=QPushButton('Continue');self.advance.setEnabled(False);self.advance.clicked.connect(self.next_stage);layout.addWidget(self.advance)
        self.stop=QPushButton('Stop and restore settings paused');self.stop.setEnabled(False);self.stop.clicked.connect(lambda:self.finish('STOPPED'));layout.addWidget(self.stop)
        self.timer=QTimer(self);self.timer.setInterval(350);self.timer.timeout.connect(self.poll)
        self.phase='idle';self.polling=False;self.original=None;self.raw=None;self.log=None;self.started=0
        self.seen=set();self.last_capture=0;self.last_keyboard=None;self.bright_since=None
        self.resize(580,290)

    def record(self,kind,value):
        if self.log:
            self.log.write(json.dumps(dict(elapsed=time.monotonic()-self.started,kind=kind,value=value))+'\n');self.log.flush()

    def intent(self,action,payload=None):
        if action not in ('Pause','SettingsChanged','Resume','RestoreBrightness'):
            raise RuntimeError('Dimming check cannot request movement')
        result=call_host(action,payload);self.record(action,result)
        if not result or not result.get('ok'):raise RuntimeError(action+' refused')
        return result

    def configure(self,monitor):
        self.intent('Pause')
        values=dict(self.original,paused=True,movementDisplays=[],dimmingDisplays=self.output_ids,
            dimWindows=not monitor,dimDisplays=monitor,windowDimPercent=30,displayDimPercent=40,
            windowDimDelay=1,displayDimDelay=1,windowDimFade=1,displayDimFade=1,
            dimmingExcludedApps=[],dimmingOverrides={},brightnessShortcutEnabled=True)
        preferences.save_settings(self.path,values)
        self.intent('SettingsChanged',{'revision':revision(values)})
        self.phase='monitor-start' if monitor else 'window-start';self.phase_started=time.monotonic();self.seen=set()
        self.advance.setEnabled(False);self.advance.setText('Continue after observing both monitors')

    def begin(self):
        if self.phase!='idle':return
        self.started=time.monotonic()
        folder=Path('/tmp/owg-desktop-dimming-checks');folder.mkdir(exist_ok=True)
        self.receipt=folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.jsonl')
        self.log=self.receipt.open('x');self.start.setEnabled(False);self.stop.setEnabled(True)
        try:
            if subprocess.check_output(['rpm','-q','--qf','%{EVR}','oled-window-guard'],text=True)!='0.1.0-5.fc44':
                raise RuntimeError('Expected the tested dimming RPM')
            status=read_status()
            if not status['paused'] or status['transactionActive']:raise RuntimeError('Requires paused, idle product')
            self.record('runtime',verify_rpm_runtime())
            self.before=capture();verify_scope(self.before)
            fixture=json.loads(Path('/tmp/owg-live-rpm-gtk4-windows.jsonl').read_text().splitlines()[-1])
            verify_fixture(fixture,self.before['outputs'],'4')
            self.output_ids=[o['id'] for o in self.before['outputs']]
            self.output_names={o['id']:o['connector'] for o in self.before['outputs']}
            self.path=preferences.default_settings_path();loaded=preferences.load_settings(self.path)
            if loaded.error:raise RuntimeError(loaded.error)
            self.original=loaded.values;self.raw=self.path.read_bytes() if self.path.exists() else None
            self.record('original-settings',self.original);self.configure(self.brightness_only)
            self.label.setText('Preparing monitor dimming for the brightness-only retry. Keep this panel on HDMI.' if self.brightness_only else 'Preparing window dimming. Keep the panel on HDMI; no windows will move.')
            self.timer.start()
        except Exception as error:self.finish('ERROR: '+str(error))

    def next_stage(self):
        if self.polling:return
        try:
            if self.phase=='window':
                self.record('window-observation-confirmed',sorted(self.seen));self.configure(True)
                self.label.setText('Preparing monitor dimming. Keep the panel on HDMI.')
            elif self.phase=='monitor':
                self.record('monitor-observation-confirmed',sorted(self.seen))
                self.intent('RestoreBrightness');self.phase='button-hold';self.phase_started=time.monotonic();self.saw_bright=False
                self.advance.setEnabled(False);self.label.setText('Restore Brightness requested. All content should brighten for about five seconds, then dim again. Stay idle.')
        except Exception as error:self.finish('ERROR: '+str(error))

    def poll(self):
        if self.polling or self.phase in ('idle','done'):return
        self.polling=True
        try:
            now=time.monotonic()
            if now-self.started>=240:raise RuntimeError('Four-minute check limit reached')
            status=read_status();dim=status.get('dimming',{});masks=dim.get('maskCount',0)
            if status.get('movementReady') or status.get('skipAvailable'):
                raise RuntimeError('Movement unexpectedly became available')
            if dim.get('healthy') is False:raise RuntimeError('Renderer failed: '+str(dim.get('reason')))
            if self.phase.endswith('-start'):
                if now-self.phase_started>20:raise RuntimeError('Dimming not ready: '+str(status.get('suspendedReasons'))+' / '+str(dim.get('reason')))
                if status['paused'] and status.get('resumeReady'):
                    self.intent('Resume')
                if status.get('dimmingReady') and masks:
                    self.phase=self.phase.split('-')[0]
                    if self.brightness_only:
                        self.label.setText('LG should now be dimmed; this panel on HDMI remains bright.\nClick Verify Restore Brightness. All content should brighten for about five seconds, then dim again.\nThe next prompt will test Meta+Shift+B.')
                        self.advance.setText('Verify Restore Brightness');self.advance.setEnabled(True)
                        return
                    self.label.setText(('WINDOW DIMMING: click one LG disposable window, wait 2 seconds, then one HDMI disposable window and wait 2 seconds.\nThe clicked window should be bright and the others dim. Click through the dimmed window normally.\n' if self.phase=='window' else
                        'MONITOR DIMMING: with this panel on HDMI, LG should dim while HDMI stays bright.\nClick one LG disposable window and wait 2 seconds: LG should brighten and HDMI dim.\nThen click an HDMI disposable window and wait 2 seconds: HDMI bright, LG dim.\n')+'Return to this panel and Continue after checking both monitors.')
                return
            if self.phase in ('window','monitor'):
                if now-self.last_capture>=1:
                    self.last_capture=now;sample=capture();verify_restored(self.before,sample)
                    focused=next((w for w in sample['windows'] if w['identity']==sample['focusedIdentity'] and w.get('appId')==APP_ID),None)
                    if focused and status.get('dimmingReady') and masks:
                        for output in focused['outputIds']:
                            if output in self.output_names and self.output_names[output] not in self.seen:
                                self.seen.add(self.output_names[output]);self.record(self.phase+'-focus-observed',dict(output=self.output_names[output],identity=focused['identity'],dimming=dim))
                    self.advance.setEnabled(self.brightness_only or self.seen=={'DP-1','HDMI-A-1'})
            elif self.phase=='button-hold':
                if not masks and dim.get('reason')=='restore-hold':self.saw_bright=True
                if now-self.phase_started<4 and masks:raise RuntimeError('Restore Brightness did not hold')
                if self.saw_bright and masks and now-self.phase_started>=4:
                    self.record('button-restore-verified',dim);self.phase='shortcut';self.phase_started=now
                    self.label.setText('SHORTCUT CHECK: press and release Meta+Shift+B once (Meta is the Windows/Super key).\nAll content should brighten and stay bright for about five seconds. Then this check ends paused.\nKeep the mouse still and do not hold the keys.')
                elif now-self.phase_started>15:raise RuntimeError('Brightness did not recover into dimming after its hold')
            elif self.phase=='shortcut':
                if now-self.phase_started>35:raise RuntimeError('Shortcut brightness hold was not verified')
                keyboard=next((event.get('keyboard') for event in reversed(status.get('cancellationTrace',[])) if event.get('keyboard') is not None),None)
                if not masks and dim.get('reason')=='restore-hold' and status.get('dimmingReady'):
                    if self.bright_since is None:self.bright_since=now;self.last_keyboard=keyboard
                    result=verify_hold(now-self.bright_since,masks,keyboard,self.last_keyboard)
                    if result=='restart':self.bright_since=now;self.last_keyboard=keyboard
                    elif result=='verified':
                        self.record('shortcut-restore-verified',dict(stableKeyboardCount=keyboard,brightSeconds=now-self.bright_since));self.finish('PASS')
                else:self.bright_since=None
        except Exception as error:self.finish('ERROR: '+str(error))
        finally:self.polling=False

    def finish(self,result):
        if self.phase=='done':return
        self.phase='done';self.timer.stop()
        errors=[]
        try:
            self.intent('Pause')
            if self.original is not None:
                restored=dict(self.original,paused=True);preferences.save_settings(self.path,restored)
                if self.raw is not None and self.original.get('paused') is True:self.path.write_bytes(self.raw)
                self.intent('SettingsChanged',{'revision':revision(restored)})
                self.intent('Pause')
                self.record('settings-restored',dict(originalBytesPreserved=self.raw is not None and self.path.read_bytes()==self.raw))
            if hasattr(self,'before'):verify_restored(self.before,capture())
        except Exception as error:errors.append(str(error))
        if errors:result+='; CLEANUP: '+'; '.join(errors)
        self.record('result',dict(result=result,physicalObservationPending=True))
        if self.log:self.log.close();self.log=None
        self.label.setText(result+'\nSettings restored paused. No windows should have moved.\nReturn to the CLI and report window/monitor dimming, click-through, brightness button and shortcut.\nReceipt: '+str(getattr(self,'receipt','none')))
        self.advance.setEnabled(False);self.stop.setEnabled(False)

    def closeEvent(self,event):
        if self.phase not in ('idle','done'):self.finish('STOPPED: panel closed')
        event.accept()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brightness-only',action='store_true');args=parser.parse_args()
    app=QApplication([]);QGuiApplication.setDesktopFileName('oled-window-guard')
    panel=Check(args.brightness_only);panel.show();return app.exec()


if __name__=='__main__':raise SystemExit(main())
