"""Lifecycle checks used by the operator-started warning/Quit round."""
import time

from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from oled_window_guard.session import SERVICE
from tools.desktop_acceptance_launch import launch


def lifecycle():
    bus = QDBusConnection.sessionBus()
    def query(service, path, interface, method, argument):
        proxy = QDBusInterface(service, path, interface, bus)
        proxy.setTimeout(1000)
        reply = proxy.call(method, argument)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage or not reply.arguments() or type(reply.arguments()[0]) is not bool:
            raise RuntimeError("Lifecycle query unavailable: " + method)
        return reply.arguments()[0]
    return {
        "controllerOwned": query("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", SERVICE),
        "adapterLoaded": query("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "isScriptLoaded", "oled-window-guard-adapter")}


def open_product():
    return launch("dashboard")


def quit_during_warning(current, intent, status, record, inspect=lifecycle,
                        reopen=open_product, clock=time.monotonic, sleep=time.sleep):
    if current.get("skipAvailable") is not True or current.get("transactionActive") is not True or current.get("warningRemainingSeconds", 0) < 5:
        raise RuntimeError("Quit requires an active warning with at least 5 seconds remaining")
    before = inspect()
    record("before-quit", before)
    if before != {"controllerOwned": True, "adapterLoaded": True}:
        raise RuntimeError("Expected owned controller and loaded adapter before Quit")
    try:
        intent("Quit")
        deadline = clock()+10
        while True:
            state = inspect()
            record("quit-lifecycle", state)
            if not state["controllerOwned"] and not state["adapterLoaded"]:
                break
            if clock() >= deadline:
                raise RuntimeError("Quit did not unload controller and adapter")
            sleep(.1)
        # Verify teardown persists rather than mistaking a transient gap for Quit.
        sleep(2)
        if inspect() != {"controllerOwned": False, "adapterLoaded": False}:
            raise RuntimeError("Controller or adapter returned before Open")
        record("quit-verified", {"controllerOwned": False, "adapterLoaded": False})
    finally:
        record("reopening", "Normal Open; no Resume will be sent")
        record("reopen-launch", reopen())
        deadline = clock()+15
        while True:
            state = inspect()
            if state["controllerOwned"] and state["adapterLoaded"]:
                fresh = status()
                if fresh.get("paused") is not True or fresh.get("movementReady") is not False or fresh.get("transactionActive") is not False or fresh.get("restoreAvailable") is not False:
                    raise RuntimeError("Reopened product did not remain paused and idle without an inverse")
                record("reopened-paused", fresh)
                break
            if clock() >= deadline:
                raise RuntimeError("Normal Open did not restore controller and adapter; reopen manually")
            sleep(.1)
    return fresh
