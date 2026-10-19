#!/usr/bin/env python3
"""Exercise retained launcher CLI routes on a private bus; the live menu replaces fixed desktop actions."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ['Open', 'PauseMovement', 'PauseBrightness', 'Pause', 'MoveNow', 'Skip', 'Restore', 'RestoreBrightness', 'Quit']


def worker(root, app_root, desktop):
    from PySide6.QtCore import QCoreApplication, QObject, Slot
    from PySide6.QtDBus import QDBusConnection
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    app = QCoreApplication([])
    bus = QDBusConnection.sessionBus()
    service = 'org.oledwindowguard.Controller1'
    calls = []

    class Controller(QObject):
        @Slot(str, str, result=str)
        def Intent(self, action, payload):
            calls.append((action, json.loads(payload)))
            return json.dumps({'ok': True})

    controller = Controller()
    assert bus.registerService(service)
    assert bus.registerObject('/org/oledwindowguard/Controller1', service, controller,
                              QDBusConnection.RegisterOption.ExportAllSlots)
    info = Gio.DesktopAppInfo.new_from_filename(str(desktop))
    assert info is not None and list(info.list_actions()) == []
    sys.path.insert(0, str(app_root))
    from oled_window_guard.__main__ import COMMANDS
    context = GLib.MainContext.default()
    names = []
    for index, action in enumerate(ACTIONS):
        names.append(action)
        command = next(key for key, value in COMMANDS.items() if value == action)
        process = subprocess.Popen(['oled-window-guard', command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        until = time.monotonic() + 8
        while len(calls) <= index and time.monotonic() < until:
            app.processEvents()
            while context.pending():
                context.iteration(False)
            time.sleep(.01)
        assert calls == [(a, {}) for a in ACTIONS[:index + 1]], (action, calls)
        assert process.wait(timeout=5) == 0
    bus.unregisterObject('/org/oledwindowguard/Controller1')
    bus.unregisterService(service)
    missing = []
    for command in ['pause-movement', 'pause-brightness', 'pause', 'move-now', 'skip', 'restore', 'restore-brightness', 'quit']:
        result = subprocess.run(['oled-window-guard', command], capture_output=True, text=True, timeout=8)
        assert result.returncode == 2, (command, result.returncode, result.stderr)
        missing.append(command)
    assert not bus.interface().isServiceRegistered(service).value()
    result = dict(result='PASS', privateBus=True, actionNames=names,
                  actions=[a for a, _ in calls], controllerMissingRefusals=missing,
                  noNewController=True, appRoot=str(app_root), desktop=str(desktop))
    (root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=ROOT)
    parser.add_argument('--desktop', type=Path, default=ROOT / 'packages/desktop/oled-window-guard.desktop')
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.app_root, args.desktop)
    root = Path(tempfile.mkdtemp(prefix='owg-launcher-actions-'))
    bin_dir = root / 'bin'
    bin_dir.mkdir()
    launcher = bin_dir / 'oled-window-guard'
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -m oled_window_guard "$@"\n')
    launcher.chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(args.app_root.resolve()), PYTHONDONTWRITEBYTECODE='1',
               QT_QPA_PLATFORM='offscreen', PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
    for key in ['DBUS_SESSION_BUS_ADDRESS', 'DBUS_SYSTEM_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY']:
        env.pop(key, None)
    for key in ['XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR']:
        path = root / key
        path.mkdir(mode=0o700)
        env[key] = str(path)
    result = subprocess.run(['dbus-run-session', '--', sys.executable, str(Path(__file__).resolve()),
                             '--worker', str(root), '--app-root', str(args.app_root.resolve()),
                             '--desktop', str(args.desktop.resolve())], env=env, cwd=root, timeout=100)
    print('Artifacts:', root)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
