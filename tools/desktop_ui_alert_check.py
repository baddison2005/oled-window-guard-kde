#!/usr/bin/env python3
"""Operator-started, bounded installed-UI warning check using normal OWG intents."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RECEIPT_DIR = ROOT / "docs/evidence/installed-ui-alert-runs"


def restore_settings(path, values, raw):
    from oled_window_guard import preferences
    from oled_window_guard.session import call_host, revision
    preferences.save_settings(path, values)
    if raw is not None:
        descriptor, temporary = tempfile.mkstemp(prefix='.ui-check-', dir=path.parent)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    else:
        path.unlink(missing_ok=True)
    response = call_host('SettingsChanged', {'revision': revision(values)})
    if not response or response.get('ok') is not True:
        raise RuntimeError('Original settings were not acknowledged')


def run_check(receipt, output):
    from PySide6.QtCore import QCoreApplication
    from oled_window_guard import preferences
    from oled_window_guard.session import call_host, revision
    from tools import desktop_cancellation_round as driver
    from tools.desktop_rpm_smoke import FIXTURE, verify_rpm_runtime
    from tools.desktop_resize_refusal import capture, read_status
    from tools.desktop_gtk_batch import verify_fixture, verify_scope, verify_forward, verify_restored
    from tools.desktop_xwayland_batch import verify_round
    app = QCoreApplication.instance() or QCoreApplication([])
    receipt.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); original = None; raw = None; result = False
    arguments = sys.argv
    path = preferences.default_settings_path()
    def interrupt(*_): raise RuntimeError('Stopped or exceeded the 180-second deadline')
    signal.signal(signal.SIGTERM, interrupt); signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 180)
    with receipt.open('x') as stream:
        def record(kind, value):
            row = dict(elapsed=round(time.monotonic()-started,3), kind=kind, value=value)
            stream.write(json.dumps(row)+'\n'); stream.flush()
            print(json.dumps(row), flush=True)
        try:
            version = subprocess.check_output(['rpm','-q','--qf','%{EVR}','oled-window-guard'], text=True)
            if version != '1.0.4-1.fc44': raise RuntimeError('Install the tested movement RPM first')
            state = read_status()
            if not state.get('paused') or state.get('transactionActive'): raise RuntimeError('Pause OWG before Start')
            record('runtime', verify_rpm_runtime())
            loaded = preferences.load_settings(path)
            if loaded.error or loaded.values['paused'] is not True: raise RuntimeError(loaded.error or 'Saved settings must be paused')
            before = capture(); verify_scope(before)
            verify_fixture(json.loads(FIXTURE.read_text().splitlines()[-1]), before['outputs'], '4')
            original = loaded.values; raw = path.read_bytes() if path.exists() else None
            # Durable recovery copy precedes temporary settings.
            receipt.with_suffix('.settings-backup.json').write_text(json.dumps({'path':str(path),'values':original,'raw':raw.decode() if raw else None}))
            values = dict(original, soundEnabled=True, systemNotifications=True, dimWindows=False, dimDisplays=False)
            preferences.save_settings(path, values)
            reply = call_host('SettingsChanged', {'revision':revision(values)})
            if not reply or not reply.get('ok'): raise RuntimeError('Alert settings refused')
            record('instruction','Keep still through the Moving and Restoring countdowns. Listen for a sound and look for a summary notification after the check pauses guarding.')
            child = receipt.with_suffix('.round.jsonl'); forward = []
            def relay(kind, value, elapsed):
                record(kind, value)
                if kind == 'movement-result' and value.get('result') == 'committed':
                    forward.append(verify_forward(before, capture(), output))
            sys.argv = ['round','--receipt',str(child),'--output',output,'--case','move-restore','--delay','5']
            driver.main(observer=relay)
            verify_round([json.loads(line) for line in child.read_text().splitlines()])
            if len(forward) != 1: raise RuntimeError('Forward movement scope was not verified')
            verify_restored(before, capture())
            # Let the dashboard observe paused cleanup while test alerts remain enabled.
            time.sleep(2)
            result = True
        except Exception as exc: record('error',str(exc))
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0); signal.signal(signal.SIGTERM, signal.SIG_IGN)
            sys.argv = arguments
            try:
                paused = call_host('Pause')
                if not paused or not paused.get('ok'): raise RuntimeError('Cleanup Pause unconfirmed')
                if original is not None: restore_settings(path, original, raw)
                record('settings-restored', {'paused':True,'originalBytesRestored':original is not None and (path.read_bytes() == raw if raw is not None else not path.exists())})
            except Exception as exc: result=False; record('error','Cleanup: '+str(exc))
        record('result', {'automatedChecks':'PASS' if result else 'FAIL','physicalCountdownAndAlertObservationPending':True})
    return 0 if result else 1


def failure_details(receipt, stderr=""):
    """Keep actionable errors visible after worker exit, including startup failures."""
    errors = []
    if receipt and receipt.exists():
        for line in receipt.read_text(errors="replace").splitlines():
            try: row = json.loads(line)
            except ValueError: continue
            if row.get("kind") == "error" and str(row.get("value")) not in errors:
                errors.append(str(row.get("value")))
    if stderr.strip(): errors.append(stderr.strip()[-1500:])
    return "\n".join(errors) or "No detailed error was recorded; verify OWG is paused and the fixtures are visible."


def panel():
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget
    app = QApplication([])
    # Match the existing test panels: exclude the panel from movement candidates.
    app.setDesktopFileName("oled-window-guard")
    class Panel(QWidget):
        def __init__(self):
            super().__init__(); self.setWindowTitle('OWG installed interface & countdown check'); self.resize(600,330)
            layout=QVBoxLayout(self)
            help=QLabel('First inspect the installed dashboard while PAUSED: try each page, move it between displays, and try a per-display override then Reset.\n\n'
                'For the warning check, use the empty test desktops with TWO GTK4 disposable windows on EACH display. Put this panel and dashboard on the OTHER display. Click/release on both desktops.\n\n'
                'Start gives you 5 seconds to leave the mouse still. Observe a 10-second Moving countdown and a 10-second Restoring countdown. One window should move and return. Listen for warning sounds; a summary notification follows when guarding pauses.\n\n'
                'About 30–60 seconds; hard limit 3 minutes. Original settings are restored paused. No test starts until you click Start.')
            help.setWordWrap(True); layout.addWidget(help)
            self.output=QComboBox(); self.output.addItem('LG · DP-1','DP-1'); self.output.addItem('HDMI · HDMI-A-1','HDMI-A-1');layout.addWidget(self.output)
            self.start=QPushButton('Start countdown & alert check'); self.start.clicked.connect(self.begin);layout.addWidget(self.start)
            self.stop=QPushButton('Stop and restore settings paused');self.stop.setEnabled(False);self.stop.clicked.connect(self.cancel);layout.addWidget(self.stop)
            self.status=QLabel('Ready for your interface review.');self.status.setWordWrap(True);layout.addWidget(self.status)
            self.process=QProcess(self); self.process.readyReadStandardOutput.connect(self.read)
            self.process.finished.connect(self.done);self.process.errorOccurred.connect(self.failed_start)
            self.process.readyReadStandardError.connect(self.read_errors)
            self.buffer=b''; self.receipt=None; self.stderr=''
        def begin(self):
            self.receipt=RECEIPT_DIR/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.jsonl')
            self.buffer=b'';self.stderr='';self.start.setEnabled(False);self.output.setEnabled(False);self.stop.setEnabled(True)
            self.status.setText('Starting in 5 seconds. Stay still through both countdowns.')
            self.process.start(sys.executable,[str(Path(__file__).resolve()),'--run','--receipt',str(self.receipt),'--output',self.output.currentData()])
        def read(self):
            self.buffer+=bytes(self.process.readAllStandardOutput())
            while b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                try: row=json.loads(line)
                except ValueError: continue
                if row['kind']=='error':
                    self.status.setText('Check failed: '+str(row['value']))
                    print('OWG UI check error: '+str(row['value']),flush=True)
                elif row['kind']=='instruction':self.status.setText(row['value'])
        def read_errors(self):
            text=bytes(self.process.readAllStandardError()).decode(errors="replace")
            self.stderr=(self.stderr+text)[-8000:]
            if self.receipt and text:
                self.receipt.parent.mkdir(parents=True,exist_ok=True)
                with self.receipt.with_suffix('.stderr.log').open('a') as stream: stream.write(text)
        def cancel(self):
            self.process.terminate();self.status.setText('Stopping; waiting for Pause and settings restoration…')
        def failed_start(self,error):
            if error == QProcess.ProcessError.FailedToStart:
                self.status.setText('Could not start the check. No test settings were applied.');self.start.setEnabled(True);self.stop.setEnabled(False);self.output.setEnabled(True)
        def done(self,code,status):
            self.read(); self.read_errors(); self.start.setEnabled(True);self.output.setEnabled(True);self.stop.setEnabled(False)
            self.status.setText(('Movement/Restore verified; settings restored paused. Please report whether both countdowns, sounds and notifications appeared.' if code==0 and status==QProcess.ExitStatus.NormalExit else 'Check failed:\n'+failure_details(self.receipt,self.stderr)+'\nVerify the dashboard is paused before retrying.')+'\nReceipt: '+str(self.receipt))
        def closeEvent(self,event):
            if self.process.state()!=QProcess.ProcessState.NotRunning:self.cancel();event.ignore()
            else:event.accept()
    window=Panel();window.show()
    signal.signal(signal.SIGTERM,lambda *_:window.close())
    return app.exec()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');parser.add_argument('--receipt',type=Path);parser.add_argument('--output',choices=('DP-1','HDMI-A-1'),default='DP-1');args=parser.parse_args()
    if args.run and args.receipt is None:parser.error('--run requires --receipt')
    raise SystemExit(run_check(args.receipt,args.output) if args.run else panel())
