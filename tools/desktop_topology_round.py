#!/usr/bin/env python3
"""Operator-driven, paused two-monitor cable-cycle acceptance. Never arms movement."""
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
    initial=status()
    expected={row["name"]:row["id"] for row in initial.get("displays",[])}
    if set(expected)!={"DP-1","HDMI-A-1"}:
        raise RuntimeError("Expected both known monitors before topology test")
    def checked():
        current=status()
        if any(current.get(k) is not v for k,v in dict(paused=True,transactionActive=False,movementReady=False,restoreAvailable=False).items()):
            raise RuntimeError("Window Guard did not stay paused, idle and without inverse")
        return current
    for target,other in (("DP-1","HDMI-A-1"),("HDMI-A-1","DP-1")):
        record("topology-instructions","Put this panel on "+other+", then unplug ONLY the "+target+" display cable. Keep it disconnected until prompted to reconnect.")
        deadline=clock()+180
        while True:
            current=checked()
            names={r["name"] for r in current.get("displays",[])}
            if target not in names:
                if other not in names:raise RuntimeError("Both monitors disappeared")
                break
            if clock()>=deadline:raise RuntimeError("Target disconnect not observed: "+target)
            sleep(.1)
        record("output-disconnected",dict(output=target,status=current))
        dwell_end=clock()+5
        while clock()<dwell_end:
            current=checked()
            if target in {r["name"] for r in current.get("displays",[])}:
                raise RuntimeError("Monitor reconnected before five-second dwell completed")
            sleep(.1)
        record("topology-instructions","Reconnect "+target+" now. When both displays settle, click/release once on EACH monitor. Do not Resume.")
        deadline=clock()+120
        while True:
            current=checked()
            displays={r["name"]:r["id"] for r in current.get("displays",[])}
            if displays==expected and current.get("resumeReady") is True:
                break
            if clock()>=deadline:raise RuntimeError("Topology/input recovery not ready: "+target)
            sleep(.1)
        record("output-recovered",dict(output=target,status=current))
    record("round-result",dict(case="topology-both",result="two-paused-disconnect-and-recovery-rounds-verified",physicalObservationPending=True,scope="Paused recovery only; no armed topology cancellation tested"))


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
