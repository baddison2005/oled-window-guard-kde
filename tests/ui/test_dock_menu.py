import threading
import time
import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from oled_window_guard.dock_menu import CairoDockMenu, MenuAttachment, own_tray_menu, DOCK, CONTROLLER


class FakeBus:
    def __init__(self):
        self.dock_owner = ':1.10'
        self.controller_owner = ':1.20'
        self.menu_pid = 42
        self.controller_pid = 42
        self.items = [':1.30/StatusNotifierItem']
        self.calls = []

    def owner(self, name):
        return self.dock_owner if name == DOCK else self.controller_owner if name == CONTROLLER else None

    def pid(self, name):
        return self.menu_pid if name == ':1.30' else self.controller_pid

    def call(self, service, path, interface, method, *args):
        if method == 'SetMenu':
            self.calls.append(args)
            return None
        if args[-1] == 'RegisteredStatusNotifierItems':
            return self.items
        if args[-1] == 'Menu':
            return '/MenuBar'
        if args[-1] == 'Version':
            return 2
        raise AssertionError((service, path, interface, method, args))


class DockMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_attach_reuses_only_the_own_pid_menu_and_fixed_icon_query(self):
        bus = FakeBus(); link = MenuAttachment(42, lambda: bus)
        self.assertTrue(link.bind())
        self.assertEqual(bus.calls, [(':1.30', '/MenuBar', 'class=oled-window-guard')])
        bus.menu_pid = 43
        self.assertFalse(link.bind())
        self.assertEqual(bus.calls[-1], ('', '', 'class=oled-window-guard'))
        self.assertNotIn((':1.30', '/MenuBar', 'class=oled-window-guard'), bus.calls[1:])

    def test_missing_dock_and_ambiguous_own_menu_never_attach(self):
        bus = FakeBus(); bus.dock_owner = None
        self.assertFalse(MenuAttachment(42, lambda: bus).bind())
        self.assertEqual(bus.calls, [])
        bus.dock_owner = ':1.10'; bus.items.append(':1.31/StatusNotifierItem')
        self.assertIsNone(own_tray_menu(bus, 42))
        self.assertFalse(MenuAttachment(42, lambda: bus).bind())
        self.assertEqual(bus.calls, [])

    def test_refresh_covers_restarted_dock_and_cleanup_respects_new_controller(self):
        bus = FakeBus(); link = MenuAttachment(42, lambda: bus)
        link.bind(); bus.dock_owner = ':1.99'; link.bind()
        self.assertEqual(len(bus.calls), 2)
        self.assertEqual(link.attached_owner, ':1.99')
        bus.controller_pid = 99
        link.detach()
        self.assertEqual(len(bus.calls), 2)
        self.assertIsNone(link.attached_owner)

    def test_blocked_dock_ipc_does_not_block_gui_timers(self):
        entered = threading.Event(); release = threading.Event(); detached = []
        class SlowAttachment:
            def bind(self):
                entered.set(); release.wait(2); return True
            def detach(self):
                detached.append(True)
        link = CairoDockMenu(attachment=SlowAttachment())
        ticks = []; timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: ticks.append(1)); timer.start()
        try:
            end = time.monotonic() + .25
            while time.monotonic() < end:
                self.app.processEvents(); time.sleep(.005)
            self.assertTrue(entered.is_set())
            self.assertGreaterEqual(len(ticks), 4)
            self.assertFalse(link.future.done())
        finally:
            timer.stop(); link.stop(); release.set(); link.executor.shutdown(wait=True)
            link.deleteLater(); self.app.processEvents()
        self.assertEqual(detached, [True])
