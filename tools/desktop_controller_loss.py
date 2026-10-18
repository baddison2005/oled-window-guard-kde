"""Supervised controller-only crash; retain the adapter through warning expiry."""
import os
from pathlib import Path
import signal
import time

from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from oled_window_guard.session import SERVICE
from tools.desktop_quit_round import lifecycle, open_product


def bus_value(service, path, interface, method, argument):
    proxy=QDBusInterface(service,path,interface,QDBusConnection.sessionBus())
    proxy.setTimeout(1000)
    reply=proxy.call(method,argument)
    if reply.type()==QDBusMessage.MessageType.ErrorMessage or not reply.arguments():
        raise RuntimeError("Controller-loss query failed: "+method)
    return reply.arguments()[0]


def crash_controller():
    # Resolve the exact bus owner and pin its process, avoiding PID reuse.
    owner=bus_value("org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","GetNameOwner",SERVICE)
    pid=bus_value("org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","GetConnectionUnixProcessID",owner)
    if type(pid) is not int or pid<=1 or pid==os.getpid():
        raise RuntimeError("Invalid controller PID")
    fd=os.pidfd_open(pid)
    try:
        proc=Path("/proc")/str(pid)
        args=(proc/"cmdline").read_bytes().split(b"\0")
        launcher=str(Path.home()/".local/bin/oled-window-guard").encode()
        if proc.stat().st_uid!=os.getuid() or launcher not in args or b"open" not in args:
            raise RuntimeError("Bus owner is not the installed user's dashboard")
        if bus_value("org.freedesktop.DBus","/org/freedesktop/DBus","org.freedesktop.DBus","GetNameOwner",SERVICE)!=owner:
            raise RuntimeError("Controller owner changed before crash")
        signal.pidfd_send_signal(fd,signal.SIGKILL)
    finally:
        os.close(fd)
    return {"pid":pid,"owner":owner,"signal":"SIGKILL","scope":"installed controller only"}


def unload_orphan():
    if lifecycle()["controllerOwned"]:
        raise RuntimeError("Refusing orphan cleanup while controller is present")
    value=bus_value("org.kde.KWin","/Scripting","org.kde.kwin.Scripting","unloadScript","oled-window-guard-adapter")
    if value is not True:
        raise RuntimeError("Orphan adapter unload refused")


def controller_loss_during_warning(current,status,record,inspect=lifecycle,
                                   crash=crash_controller,cleanup=unload_orphan,
                                   reopen=open_product,clock=time.monotonic,sleep=time.sleep):
    if current.get("skipAvailable") is not True or current.get("transactionActive") is not True or current.get("warningRemainingSeconds",0)<5:
        raise RuntimeError("Controller loss requires an active warning with at least 5 seconds remaining")
    if inspect()!={"controllerOwned":True,"adapterLoaded":True}:
        raise RuntimeError("Expected owned controller and loaded adapter")
    crashed=False
    try:
        record("controller-crash",crash())
        crashed=True
        deadline=clock()+5
        while inspect()["controllerOwned"]:
            if clock()>=deadline:raise RuntimeError("Controller did not disappear")
            sleep(.1)
        record("controller-lost",inspect())
        # Keep the orphan loaded past the original countdown: do not mask a
        # failed heartbeat cancellation with an early unload/reopen.
        deadline=clock()+current["warningRemainingSeconds"]+2
        record("controller-loss-dwell","Stay idle; checking beyond original warning expiry")
        while clock()<deadline:
            if inspect()["controllerOwned"]:
                raise RuntimeError("Unexpected controller restart during loss dwell")
            sleep(.25)
        record("controller-loss-dwell-complete",inspect())
    finally:
        if crashed:
            record("orphan-cleanup","Explicit harness recovery, not automatic product teardown")
            if inspect()["controllerOwned"] is False and inspect()["adapterLoaded"]:
                cleanup()
            record("reopen-launch",reopen())
            deadline=clock()+15
            while True:
                state=inspect()
                if state["controllerOwned"] and state["adapterLoaded"]:
                    fresh=status()
                    if any(fresh.get(k) is not v for k,v in dict(paused=True,movementReady=False,transactionActive=False,restoreAvailable=False).items()):
                        raise RuntimeError("Reopened product is not paused and idle without inverse")
                    record("reopened-paused",fresh)
                    break
                if clock()>=deadline:raise RuntimeError("Controller-loss recovery timed out; reopen manually")
                sleep(.1)
    return fresh
