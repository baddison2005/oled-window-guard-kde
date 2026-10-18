#!/usr/bin/env python3
"""Operator-driven, paused lock/unlock acceptance. Never arms or locks the session."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from oled_window_guard.session import SERVICE, PATH, INTERFACE, call_host


def observe(status, record, clock=time.monotonic, sleep=time.sleep):
    phase = "lock"
    deadline = clock() + 120
    previous = None
    record("lock-instructions", "Lock the desktop normally now, then unlock normally. After unlocking, click/release once on each monitor.")
    while clock() < deadline:
        current = status()
        if current != previous:
            record("status", current)
            previous = current
        if current.get("paused") is not True or current.get("transactionActive") is not False or current.get("movementReady") is not False:
            raise RuntimeError("Expected paused, idle, unarmed product throughout lock recovery")
        reasons = current.get("suspendedReasons", [])
        # This comes from production native safety normalization, not shortcut input.
        locked = any(str(reason).startswith("session-not-authoritatively-ready/locked/") for reason in reasons)
        locking = any(str(reason).startswith("session-not-authoritatively-ready/locking/") for reason in reasons)
        if locked or locking:
            if current.get("resumeReady") is not False:
                raise RuntimeError("Resume unexpectedly available during lock")
        if phase == "lock" and locked:
            record("native-locked", current)
            phase = "unlock"
            deadline = clock() + 240
        elif phase == "unlock" and not locked and not locking:
            record("unlock-observed", current)
            record("recovery-instructions", "Click/release once on each monitor. Do not Resume.")
            phase = "recover"
            deadline = clock() + 90
        if phase == "recover" and current.get("resumeReady") is True:
            record("round-result", {"case": "lock-unlock", "result": "paused recovery verified",
                                    "physicalObservationPending": True,
                                    "scope": "paused native lock detection and recovery; no armed-warning cancellation tested"})
            return
        sleep(.1)
    raise RuntimeError("Timed out waiting for " + phase + "; no movement requested")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    interface = QDBusInterface(SERVICE, PATH, INTERFACE, QDBusConnection.sessionBus())
    interface.setTimeout(1000)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("x") as log:
        started = time.monotonic()
        def record(kind, value):
            log.write(json.dumps({"elapsed": time.monotonic()-started, "kind": kind, "value": value})+"\n")
            log.flush()
        def status():
            reply = interface.call("Status")
            if reply.type() == QDBusMessage.MessageType.ErrorMessage or not reply.arguments():
                raise RuntimeError("Controller status unavailable")
            return json.loads(str(reply.arguments()[0]))
        try:
            initial = status()
            record("initial", initial)
            if initial.get("paused") is not True or initial.get("transactionActive") is not False:
                raise RuntimeError("Start requires paused, idle product")
            if initial.get("resumeReady") is not True:
                raise RuntimeError("Click/release on both monitors before starting; native safety must initially be ready")
            observe(status, record)
        except Exception as error:
            record("error", str(error))
        finally:
            pause = call_host("Pause")
            record("final-pause", pause)
            if not pause or not pause.get("ok"):
                raise RuntimeError("Pause unconfirmed; use dashboard Pause")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
