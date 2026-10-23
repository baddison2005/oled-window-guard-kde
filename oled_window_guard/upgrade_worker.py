"""User-session upgrade coordinator; only DNF runs through administrator authorization."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .updates import PACKAGE, _run, package_update_status, validate_rpm
from .preferences import default_settings_path


class SessionBridge:
    def __init__(self):
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtDBus import QDBusConnection
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.bus = QDBusConnection.sessionBus()
        if not self.bus.isConnected() or not self.bus.registerService('org.oledwindowguard.Updater1'):
            raise RuntimeError('Another upgrade is running, or the desktop session is unavailable')

    def call(self, service, path, interface, method, *args):
        from PySide6.QtDBus import QDBusInterface, QDBusMessage
        proxy = QDBusInterface(service, path, interface, self.bus)
        proxy.setTimeout(2000)
        result = proxy.call(method, *args)
        if result.type() == QDBusMessage.MessageType.ErrorMessage:
            raise RuntimeError(result.errorMessage())
        return result.arguments()[0] if result.arguments() else None

    def quit(self, expected_pid):
        service = 'org.oledwindowguard.Controller1'
        owner = self.bus.interface().serviceOwner(service).value()
        if not owner or self.bus.interface().servicePid(owner).value() != expected_pid:
            raise RuntimeError('The OWG controller changed. Check for updates again')
        reply = json.loads(self.call(service, '/org/oledwindowguard/Controller1', service, 'Intent', 'Quit', '{}'))
        if reply.get('ok') is not True:
            raise RuntimeError('OWG could not close safely for the upgrade')

    def wait_closed(self):
        until = time.monotonic() + 12
        while self.bus.interface().isServiceRegistered('org.oledwindowguard.Controller1').value():
            if time.monotonic() >= until:
                raise RuntimeError('OWG did not finish closing; the upgrade was not started')
            self.app.processEvents()
            time.sleep(.05)
        from .adapter import PLUGIN, RUNTIME_PLUGIN
        until = time.monotonic() + 5
        while True:
            loaded = [self.call('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting',
                                'isScriptLoaded', name) for name in (PLUGIN, RUNTIME_PLUGIN)]
            if all(value is False for value in loaded):
                break
            if time.monotonic() >= until:
                raise RuntimeError('The previous compositor runtime did not unload; the upgrade was not started')
            self.app.processEvents()
            time.sleep(.05)


def install_rpm(path):
    return subprocess.run(['/usr/bin/pkexec', '/usr/bin/dnf', '--disablerepo=*',
        '--setopt=localpkg_gpgcheck=False', '-y', 'install', str(path)],
        stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=subprocess.STDOUT).returncode


def reopen(folder):
    env = dict(os.environ, OWG_UPGRADE_RESULT=str(folder / 'result.json'))
    with (folder / 'reopen.log').open('w') as log:
        subprocess.Popen(['/usr/bin/oled-window-guard', 'open'], cwd='/tmp', env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def coordinate(folder, parent_pid, *, runner=_run, bridge=None, installer=install_rpm, restart=reopen, settings_paths=None):
    folder = Path(folder)
    stopped = False
    result = {'result': 'failed', 'message': 'Upgrade did not complete.'}
    try:
        offer, path = validate_rpm(folder, runner=runner)
        before_version = package_update_status(runner=runner)['version']
        settings = default_settings_path()
        paths = settings_paths if settings_paths is not None else [settings, settings.parent.parent / 'autostart/oled-window-guard.desktop']
        records = []
        for index, item in enumerate(paths):
            item = Path(item)
            record = {'path': str(item), 'exists': item.exists()}
            if item.exists():
                data = item.read_bytes()
                backup = folder / f'settings-{index}.before'
                backup.write_bytes(data)
                backup.chmod(0o600)
                record['sha256'] = hashlib.sha256(data).hexdigest()
            records.append(record)
        (folder / 'settings-backup.json').write_text(json.dumps(records, indent=2))
        bridge = bridge or SessionBridge()
        bridge.quit(parent_pid)
        stopped = True
        bridge.wait_closed()
        # Recheck bytes, identity and installed runtime immediately before DNF.
        validate_rpm(folder, runner=runner)
        code = installer(path)
        if code == 126:
            if package_update_status(runner=runner)['version'] != before_version:
                raise RuntimeError('The package changed during an interrupted authorization; check the installed version')
            result = {'result': 'cancelled', 'message': 'Upgrade cancelled. OWG reopened paused.'}
        elif code:
            raise RuntimeError('Administrator authorization or DNF installation failed. See the upgrade log: ' + str(folder / 'upgrade.log'))
        else:
            version = runner(['rpm', '-q', '--qf', '%{VERSION}-%{RELEASE}', PACKAGE])
            if version != f"{offer['version']}-{offer['release']}":
                raise RuntimeError('The installed package version does not match the requested upgrade')
            runner(['rpm', '-V', PACKAGE])
            for record in records:
                item = Path(record['path'])
                if item.exists() != record['exists'] or (item.exists() and hashlib.sha256(item.read_bytes()).hexdigest() != record['sha256']):
                    raise RuntimeError('Preferences changed during installation; the original backup is in ' + str(folder))
            result = {'result': 'success', 'message': f"Upgraded to {offer['version']}. Your settings were retained. Guarding is paused.",
                      'previousVersion': before_version, 'installedVersion': version, 'settingsPreserved': True}
    except Exception as error:
        result = {'result': 'failed', 'message': ('Upgrade not completed: ' + str(error))[:900] + ('. OWG reopened paused.' if stopped else '')}
    # A preflight failure must not write through an untrusted directory.
    if not folder.is_symlink() and folder.is_dir() and folder.stat().st_uid == os.getuid() and not folder.stat().st_mode & 0o077:
        temporary = folder / 'result.tmp'
        temporary.write_text(json.dumps(result))
        temporary.replace(folder / 'result.json')
    if stopped:
        restart(folder)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('parent_pid', type=int)
    args = parser.parse_args()
    result = coordinate(args.folder, args.parent_pid)
    print(json.dumps(result), flush=True)
    return 0 if result['result'] in ('success', 'cancelled') else 1


if __name__ == '__main__':
    raise SystemExit(main())
