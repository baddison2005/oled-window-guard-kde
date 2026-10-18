#!/usr/bin/env python3
"""Operator-started RPM smoke check; no input injection or direct geometry writes."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from oled_window_guard import preferences
from oled_window_guard.adapter import installed_adapter
from oled_window_guard.session import SERVICE, call_host, revision
from tools import desktop_cancellation_round as driver
from tools.desktop_gtk_batch import verify_fixture, verify_scope, verify_forward, verify_restored
from tools.desktop_resize_refusal import capture, read_status
from tools.desktop_xwayland_batch import verify_round

FIXTURE = Path('/tmp/owg-live-rpm-gtk4-windows.jsonl')


def verify_rpm_runtime():
    bus = QDBusInterface('org.freedesktop.DBus', '/org/freedesktop/DBus',
                         'org.freedesktop.DBus', QDBusConnection.sessionBus())
    reply = bus.call('GetConnectionUnixProcessID', SERVICE)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage or not reply.arguments():
        raise RuntimeError('Cannot identify the controller process')
    pid = int(reply.arguments()[0])
    environment = (Path('/proc') / str(pid) / 'environ').read_bytes().split(b'\0')
    pythonpath = next((entry.split(b'=', 1)[1].decode() for entry in environment
                       if entry.startswith(b'PYTHONPATH=')), '')
    if pythonpath.split(':')[0] != '/usr/libexec/oled-window-guard':
        raise RuntimeError('Controller was not launched from the RPM')
    adapter = installed_adapter()
    if adapter is None or not adapter.is_relative_to('/usr/share/kwin/scripts/oled-window-guard-adapter'):
        raise RuntimeError('Adapter is shadowed by another installation')
    source = read_status().get('adapterSource')
    loaded = urlsplit(source or '')
    if (loaded.scheme != 'file' or loaded.netloc or loaded.query or loaded.fragment
            or Path(unquote(loaded.path)).resolve() != adapter.resolve()):
        raise RuntimeError('Running compositor adapter does not match the installed RPM; reopen OWG to refresh it')
    return dict(controllerPid=pid, pythonRoot=pythonpath.split(':')[0], adapter=str(adapter), loadedAdapterSource=source)


def verify_cancelled(rows):
    if any(row['kind'] in ('error', 'operator-warning-missed') for row in rows):
        raise RuntimeError('Input cancellation was not verified; inspect the round receipt')
    result = next((row['value'] for row in rows if row['kind'] == 'movement-result'), {})
    if (result.get('warningSeen') is not True or result.get('restoreAvailable') is not False or
            result.get('result') != 'cancelled: native-input'):
        raise RuntimeError('Expected native-input cancellation with no movement inverse')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    if args.preflight_only:
        state = read_status()
        print(json.dumps(dict(runtime=verify_rpm_runtime(), readiness=state['readiness'],
            suspendedReasons=state.get('suspendedReasons'), paused=state['paused'],
            transactionActive=state['transactionActive'], nativeObservation=state.get('nativeObservation'))))
        return
    if args.receipt is None:
        parser.error('--receipt is required for an operator-started test')
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    original_argv = sys.argv
    settings_path = preferences.default_settings_path()
    original = None
    original_bytes = None
    def interrupted(signum, frame):
        raise RuntimeError('RPM check stopped or exceeded its 240-second limit')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    with args.receipt.open('x') as stream:
        def record(kind, value):
            stream.write(json.dumps(dict(elapsed=time.monotonic()-started, kind=kind, value=value))+'\n')
            stream.flush()
        signal.setitimer(signal.ITIMER_REAL, 240)
        try:
            state = read_status()
            if not state['paused'] or state['transactionActive']:
                raise RuntimeError('Requires paused, idle product')
            record('rpm-runtime', verify_rpm_runtime())
            loaded = preferences.load_settings(settings_path)
            if loaded.error:
                raise RuntimeError(loaded.error)
            original = loaded.values
            original_bytes = settings_path.read_bytes() if settings_path.exists() else None
            record('original-settings', original)
            stages = (('DP-1', 'move-restore'), ('HDMI-A-1', 'move-restore'),
                      ('HDMI-A-1', 'cross-monitor'), ('HDMI-A-1', 'quit-warning'))
            for number, (output, case) in enumerate(stages):
                if number == 1:
                    record('operator-switch-panel', 'LG restored. Move this panel to LG, click/release on both desktops, then Continue. Keep HDMI targets uncovered. The next two warnings are move and Restore; stay idle.')
                    deadline = time.monotonic()+60
                    while not args.receipt.with_suffix('.continue').exists():
                        if time.monotonic() > deadline:
                            raise RuntimeError('Monitor setup timed out; stopped paused')
                        time.sleep(.1)
                before = capture()
                verify_scope(before)
                fixture = json.loads(FIXTURE.read_text().splitlines()[-1])
                verify_fixture(fixture, before['outputs'], '4')
                record('rpm-stage', dict(output=output, case=case))
                child = args.receipt.with_name(args.receipt.stem+'-'+str(number)+'-'+case+'.jsonl')
                forward = []
                def relay(kind, value, elapsed):
                    if kind == 'status':
                        value = dict(value, rpmCase=case, batchOutput=output)
                    record('batch-stage-result' if kind == 'round-result' else kind, value)
                    if kind == 'movement-result' and value.get('result') == 'committed':
                        forward.append(verify_forward(before, capture(), output))
                sys.argv = ['round', '--receipt', str(child), '--output', output,
                            '--case', case, '--delay', '5']
                driver.main(observer=relay)
                rows = [json.loads(line) for line in child.read_text().splitlines()]
                if any(row['kind'] == 'error' for row in rows):
                    raise RuntimeError(next(row['value'] for row in rows if row['kind'] == 'error'))
                if case == 'move-restore':
                    verify_round(rows)
                    if len(forward) != 1:
                        raise RuntimeError('Exactly one target must move on the selected output')
                elif case == 'cross-monitor':
                    verify_cancelled(rows)
                else:
                    result = next((row['value'] for row in rows if row['kind'] == 'round-result'), {})
                    if result.get('result') != 'quit-unload-and-paused-reopen-verified':
                        raise RuntimeError('Quit and paused reopen were not verified')
                    record('rpm-reopened-runtime', verify_rpm_runtime())
                verify_restored(before, capture())
                after_fixture = json.loads(FIXTURE.read_text().splitlines()[-1])
                sizes = {w['title']:w['clientSize'] for w in fixture['windows']}
                if sizes != {w['title']:w['clientSize'] for w in after_fixture['windows']}:
                    raise RuntimeError('Fixture client size changed')
                record('rpm-stage-verified', dict(output=output, case=case, framesRestoredOrUnchanged=True))
            record('round-result', dict(case='rpm-smoke', result='all-four-rpm-stages-verified', physicalObservationPending=True))
        except Exception as error:
            record('error', str(error) or type(error).__name__)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            sys.argv = original_argv
            response = call_host('Pause')
            record('cleanup-pause', response)
            try:
                if original is not None:
                    restored = dict(original, paused=True)
                    preferences.save_settings(settings_path, restored)
                    if original.get('paused') is True and original_bytes is not None:
                        settings_path.write_bytes(original_bytes)
                    response = call_host('SettingsChanged', {'revision': revision(restored)})
                    record('settings-restored', response)
                    if not response or not response.get('ok'):
                        raise RuntimeError('Original settings were not acknowledged')
                response = call_host('Pause')
                record('final-pause', response)
                if not response or not response.get('ok') or not read_status()['paused']:
                    raise RuntimeError('Final Pause unconfirmed')
            except Exception as error:
                record('error', 'Cleanup: '+str(error))


if __name__ == '__main__':
    main()
