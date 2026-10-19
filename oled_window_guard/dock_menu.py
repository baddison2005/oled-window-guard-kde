"""Attach Qt's existing live tray menu to Cairo-Dock; no controller or persisted state."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import json
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QObject, QTimer
from PySide6.QtDBus import QDBus, QDBusConnection, QDBusMessage

DOCK = 'org.cairodock.CairoDock'
DOCK_PATH = '/org/cairodock/CairoDock'
QUERY = 'class=oled-window-guard'
WATCHER = 'org.kde.StatusNotifierWatcher'
CONTROLLER = 'org.oledwindowguard.Controller1'


class MenuBus:
    """Bounded synchronous IPC used only from the background worker."""
    def __init__(self):
        self.bus = QDBusConnection.sessionBus()

    def call(self, service, path, interface, method, *args):
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setArguments(list(args))
        reply = self.bus.call(message, QDBus.CallMode.Block, 750)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            raise RuntimeError(reply.errorMessage())
        value = reply.arguments()[0] if reply.arguments() else None
        if hasattr(value, 'variant'):
            value = value.variant()
        if hasattr(value, 'path'):
            value = value.path()
        return value

    def owner(self, name):
        try:
            return self.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetNameOwner', name)
        except RuntimeError:
            return None

    def pid(self, name):
        return self.call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetConnectionUnixProcessID', name)


def own_tray_menu(bus, pid):
    items = bus.call(WATCHER, '/StatusNotifierWatcher', 'org.freedesktop.DBus.Properties', 'Get',
                     WATCHER, 'RegisteredStatusNotifierItems')
    if not isinstance(items, list) or len(items) > 128:
        return None
    candidates = set()
    for item in items:
        if not isinstance(item, str) or not item or len(item) > 512:
            continue
        service, separator, suffix = item.partition('/')
        path = '/' + suffix if separator else '/StatusNotifierItem'
        try:
            if bus.pid(service) != pid:
                continue
            menu = bus.call(service, path, 'org.freedesktop.DBus.Properties', 'Get', 'org.kde.StatusNotifierItem', 'Menu')
            if not isinstance(menu, str) or not menu.startswith('/') or menu == '/' or len(menu) > 256:
                continue
            version = bus.call(service, menu, 'org.freedesktop.DBus.Properties', 'Get', 'com.canonical.dbusmenu', 'Version')
            if version == 2:
                candidates.add((service, menu))
        except RuntimeError:
            continue
    return next(iter(candidates)) if len(candidates) == 1 else None


class MenuAttachment:
    def __init__(self, pid, bus_factory=MenuBus):
        self.pid = pid
        self.bus_factory = bus_factory
        self.attached_owner = None

    def bind(self):
        bus = self.bus_factory()
        owner = bus.owner(DOCK)
        if not owner:
            self.attached_owner = None
            return False
        menu = own_tray_menu(bus, self.pid)
        if not menu:
            self.detach()
            return False
        # SetMenu is idempotent, and repeating it also covers a launcher that
        # was removed/re-added while the dock's bus owner stayed the same.
        bus.call(DOCK, DOCK_PATH, DOCK, 'SetMenu', *menu, QUERY)
        self.attached_owner = owner
        return True

    def detach(self):
        if not self.attached_owner:
            return
        bus = self.bus_factory()
        try:
            if bus.owner(DOCK) != self.attached_owner:
                return
            controller = bus.owner(CONTROLLER)
            if controller and bus.pid(controller) != self.pid:
                return  # A replacement controller owns its own attachment.
            bus.call(DOCK, DOCK_PATH, DOCK, 'SetMenu', '', '', QUERY)
        finally:
            self.attached_owner = None


class ProcessAttachment:
    """Keep Qt's synchronous calls out of the GUI process, including its GIL.

    Some platform tray implementations need Python's main thread to service
    their export. A separate short-lived client avoids self-call deadlocks.
    The helper has no controller intents or settings access.
    """
    def __init__(self, pid):
        self.pid = pid
        self.attached_owner = None

    def _run(self, operation):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
            operation, str(self.pid), self.attached_owner or ''],
            capture_output=True, text=True, timeout=6, check=True)
        response = json.loads(result.stdout)
        self.attached_owner = response['owner']
        return response['connected']

    def bind(self):
        return self._run('bind')

    def detach(self):
        if self.attached_owner:
            self._run('detach')


class CairoDockMenu(QObject):
    """Refresh attachment off the GUI thread; Qt exports all live QAction state."""
    def __init__(self, parent=None, *, attachment=None):
        super().__init__(parent)
        self.attachment = attachment or ProcessAttachment(os.getpid())
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='owg-dock-menu')
        self.future = None
        self.stopped = False
        self.connected = False
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)
        self.destroyed.connect(lambda: self._shutdown())

    def refresh(self):
        if self.stopped:
            return
        if self.future is not None:
            if not self.future.done():
                return
            try:
                self.connected = self.future.result()
            except Exception as error:
                self.connected = False
                logging.getLogger(__name__).debug('Optional Cairo-Dock menu unavailable: %s', error)
        self.future = self.executor.submit(self.attachment.bind)

    def stop(self):
        if self.stopped:
            return
        self.timer.stop()
        self._shutdown()

    def _shutdown(self):
        if self.stopped:
            return
        self.stopped = True
        # FIFO cleanup follows an in-flight attachment and never blocks the UI.
        try:
            self.executor.submit(self.attachment.detach)
        except RuntimeError:
            pass  # Interpreter teardown may already have joined the worker.
        self.executor.shutdown(wait=False)


if __name__ == '__main__':
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication([])
    operation, pid, owner = sys.argv[1:]
    if operation not in ('bind', 'detach') or int(pid) <= 0:
        raise SystemExit(2)
    attachment = MenuAttachment(int(pid))
    attachment.attached_owner = owner or None
    connected = attachment.bind() if operation == 'bind' else attachment.detach()
    print(json.dumps(dict(connected=bool(connected), owner=attachment.attached_owner)))
