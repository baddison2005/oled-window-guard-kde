"""Operator-started native lock cancellation, without shortcut input injection."""
import time
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage


def lock_session():
    proxy=QDBusInterface("org.freedesktop.ScreenSaver","/ScreenSaver",
                        "org.freedesktop.ScreenSaver",QDBusConnection.sessionBus())
    proxy.setTimeout(1000)
    reply=proxy.call("Lock")
    if reply.type()==QDBusMessage.MessageType.ErrorMessage:
        raise RuntimeError("Normal desktop Lock refused: "+reply.errorMessage())


def lock_during_warning(current,status,record,lock=lock_session,clock=time.monotonic,sleep=time.sleep):
    if current.get("skipAvailable") is not True or current.get("transactionActive") is not True or current.get("warningRemainingSeconds",0)<5:
        raise RuntimeError("Lock requires an active warning with at least 5 seconds remaining")
    record("lock-request",{"warningRemainingSeconds":current["warningRemainingSeconds"],
                           "method":"org.freedesktop.ScreenSaver.Lock; no injected input"})
    lock()
    record("lock-request-acknowledged",True)
    phase="lock"
    deadline=clock()+10
    previous=None
    while clock()<deadline:
        current=status()
        if current!=previous:
            record("lock-status",current)
            previous=current
        reasons=current.get("suspendedReasons",[])
        locked=any(str(reason).startswith("session-not-authoritatively-ready/locked/") for reason in reasons)
        locking=any(str(reason).startswith("session-not-authoritatively-ready/locking/") for reason in reasons)
        if phase!="lock" or locked:
            if (current.get("paused") is not True or current.get("movementReady") is not False or
                    current.get("transactionActive") is not False or current.get("skipAvailable") is not False or
                    current.get("restoreAvailable") is not False):
                raise RuntimeError("Lock did not leave product disarmed, idle and without an inverse")
            if current.get("lastResult")!="cancelled: native-lock":
                raise RuntimeError("Expected native-lock cancellation, got "+str(current.get("lastResult")))
        if locked and current.get("resumeReady") is not False:
            raise RuntimeError("Resume unexpectedly available while locked")
        if phase=="lock" and locked:
            record("warning-lock-cancelled",current)
            phase="unlock"
            deadline=clock()+240
        elif phase=="unlock" and not locked and not locking:
            record("warning-unlocked",current)
            record("recovery-instructions","Click/release once on each monitor. Do not Resume.")
            phase="recover"
            deadline=clock()+90
        if phase=="recover" and current.get("resumeReady") is True:
            record("lock-recovery-verified",current)
            return current
        sleep(.1)
    raise RuntimeError("Timed out waiting for warning lock "+phase)
