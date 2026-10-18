#!/usr/bin/env python3
"""One supervised disposable-window round; always request Pause on exit.

Uses the installed product's normal intent API. No input injection or window
assignments. The operator must use an empty desktop with disposable windows.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from oled_window_guard import preferences
from oled_window_guard.session import SERVICE, PATH, INTERFACE, call_host, revision
from tools.desktop_quit_round import quit_during_warning
from tools.desktop_warning_lock import lock_during_warning
from tools.desktop_controller_loss import controller_loss_during_warning
from tools.desktop_library_change import PaddingChange
from tools.desktop_target_loss import FIXTURE_RECEIPT, check_fixture_scope, close_targets
from oled_window_guard.layout_provider import LayoutProvider
from tools.desktop_stale_restore import request_stale_restore
from tools.desktop_reconfigure_warning import reconfigure_during_warning, require_fixed_installation
from tools.desktop_topology_warning import disconnect_during_warning


def imported_minis(snapshot):
    if snapshot.get("status")!="available" or snapshot.get("executionReady") is not True or not snapshot.get("revision"):
        raise RuntimeError("Imported layout library is not execution-ready")
    groups=[g for g in snapshot.get("groups",[]) if g.get("name")=="Minis"]
    if len(groups)!=1:
        raise RuntimeError("Expected one existing imported Minis group")
    group=groups[0]
    layouts=[l for l in snapshot.get("layouts",[]) if l.get("id") in group.get("layoutIds",[]) and l.get("groupId")==group["id"]]
    for x in (0,2/3):
        if not any(all(abs(l.get(k,-1)-v)<.00001 for k,v in (("x",x),("y",0),("width",1/3),("height",1/3))) for l in layouts):
            raise RuntimeError("Minis top-corner zones changed; review placement before testing")
    return "kde:"+group["id"]


def main(observer=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", default="DP-1")
    parser.add_argument("--input-kind",choices=("mouse","keyboard"),default="mouse")
    parser.add_argument("--case",choices=("topology-warning","reconfigure-warning","cancellation","cross-monitor","skip","pause","move-restore","scheduled-restore","quit-warning","controller-loss","target-loss","lock-warning","group-restore","group-rotation-restore","imported-group-restore","imported-rotation-restore","imported-change-warning","imported-change-restore","stale-restore","stale-resize"),default="cancellation")
    parser.add_argument("--delay", type=float, default=15)
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    interface = QDBusInterface(SERVICE, PATH, INTERFACE, QDBusConnection.sessionBus())
    interface.setTimeout(1000)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("x") as log:
        started = time.monotonic()
        library_change = None
        library_changed = False
        targets_closed = False
        loss_completed = False
        quit_completed = False
        lock_completed = False
        reconfigure_completed = False
        topology_completed = False

        def record(kind, value):
            log.write(json.dumps({"elapsed": time.monotonic()-started,
                                  "kind": kind, "value": value})+"\n")
            log.flush()
            if observer:observer(kind,value,time.monotonic()-started)

        def status():
            reply = interface.call("Status")
            if reply.type() == QDBusMessage.MessageType.ErrorMessage or not reply.arguments():
                raise RuntimeError("Controller status unavailable")
            return json.loads(str(reply.arguments()[0]))

        def intent(action, payload=None):
            reply = call_host(action, payload)
            record(action, reply)
            if not reply or not reply.get("ok"):
                raise RuntimeError(f"{action} refused")
            return reply

        def wait_transaction(phase):
            nonlocal quit_completed,lock_completed,loss_completed,targets_closed,library_changed,reconfigure_completed,topology_completed
            deadline=time.monotonic()+45
            warning_seen=False
            action_sent=False
            previous=None
            while time.monotonic()<deadline:
                current=status()
                key={name:current.get(name) for name in (
                    "paused","transactionActive","skipAvailable","lastResult",
                    "warningRemainingSeconds","restoreAvailable")}
                if key!=previous:
                    record("status",dict(current,testPhase=phase))
                    previous=key
                warning_seen |= current.get("skipAvailable") is True
                if current.get("skipAvailable") and not action_sent:
                    remaining=current.get("warningRemainingSeconds",settings["warningSeconds"])
                    if args.case in ("stale-restore","stale-resize","imported-change-restore") and phase=="restore":
                        intent("Skip")
                        raise RuntimeError("Unexpected stale Restore warning; skipped before execution")
                    elif args.case=="imported-change-warning" and remaining<=settings["warningSeconds"]-3:
                        action_sent=True
                        library_change.apply()
                        library_changed=True
                    elif args.case=="target-loss" and remaining<=settings["warningSeconds"]-3:
                        action_sent=True
                        close_targets(Path(FIXTURE_RECEIPT),args.output,record)
                        targets_closed=True
                    elif args.case=="topology-warning":
                        recovered=disconnect_during_warning(current,args.output,status,intent,record)
                        topology_completed=True
                        return recovered,warning_seen
                    elif args.case=="reconfigure-warning" and remaining<=settings["warningSeconds"]-3:
                        recovered=reconfigure_during_warning(current,status,record)
                        reconfigure_completed=True
                        return recovered,warning_seen
                    elif args.case=="controller-loss" and remaining<=settings["warningSeconds"]-3:
                        recovered=controller_loss_during_warning(current,status,record)
                        loss_completed=True
                        return recovered,warning_seen
                    elif args.case=="quit-warning" and remaining<=settings["warningSeconds"]-3:
                        reopened=quit_during_warning(current,intent,status,record)
                        quit_completed=True
                        return reopened,warning_seen
                    elif args.case=="lock-warning" and remaining<=settings["warningSeconds"]-3:
                        recovered=lock_during_warning(current,status,record)
                        lock_completed=True
                        return recovered,warning_seen
                    elif args.case in ("skip","pause") and remaining<=settings["warningSeconds"]-3:
                        action_sent=True
                        response=intent("Skip" if args.case=="skip" else "Pause")
                        if args.case=="pause":
                            return response["status"],warning_seen
                    elif args.case in ("cancellation","cross-monitor") and remaining<=5:
                        action_sent=True
                        record("operator-warning-missed","automatic Skip before warning expiry")
                        intent("Skip")
                if not current["transactionActive"]:
                    return current,warning_seen
                time.sleep(.1)
            raise RuntimeError(phase+" timed out")

        try:
            if args.case=="reconfigure-warning":
                require_fixed_installation()
            input_output=("HDMI-A-1" if args.output=="DP-1" else "DP-1") if args.case=="cross-monitor" else None
            record("operator-test",{"output":args.output,"inputOutput":input_output,
                                    "inputKind":args.input_kind if args.case in ("cancellation","cross-monitor") else None,"case":args.case})
            initial = status()
            record("initial", initial)
            if not initial["paused"] or initial["transactionActive"]:
                raise RuntimeError("Start requires paused, idle product")
            # Final Pause reloads configuration. The next batched round may
            # briefly see an empty output list before capabilities return.
            initial_deadline=time.monotonic()+10
            while not any(row.get("name")==args.output for row in initial.get("displays",[])):
                if time.monotonic()>=initial_deadline:
                    raise RuntimeError("Target display unavailable after paused startup: "+args.output)
                record("waiting-for-display-status",initial)
                time.sleep(.1)
                initial=status()
                if not initial.get("paused") or initial.get("transactionActive"):
                    raise RuntimeError("Product left paused idle state while awaiting display status")
            output = next(row["id"] for row in initial["displays"] if row["name"] == args.output)
            selected=[output]
            if args.case=="cross-monitor":
                other=next(row["id"] for row in initial["displays"] if row["name"]==input_output)
                if other==output:raise RuntimeError("Cross-monitor test requires distinct outputs")
                selected.append(other)
                record("cross-monitor-scope",{"movementOutput":args.output,"inputOutput":input_output,
                                              "selectedOutputIds":selected,"inputAttribution":"operator-confirmed; no input injection"})
            if args.case=="target-loss":
                check_fixture_scope(Path(FIXTURE_RECEIPT),args.output)
            path = preferences.default_settings_path()
            loaded = preferences.load_settings(path)
            if loaded.error:
                raise RuntimeError(loaded.error)
            record("original-settings", {"existed": path.exists(), "values": loaded.values})
            settings = dict(loaded.values, paused=True, movementDisplays=selected,
                            intervalMinutes=240, mode="drift", maximumWindows=1,
                            warningSeconds=10, quietSeconds=5, avoidFocusedWindow=True,
                            movementOverrides={})
            if args.case in ("group-restore","group-rotation-restore","stale-restore","stale-resize"):
                settings.update(mode="group",maximumWindows=2,groupId="builtin.horizontal-halves",
                                rotateGroup=args.case=="group-rotation-restore",shiftRangePercent=10,allowTransientOverlap=False)
                record("group-test-scope",{"groupId":"builtin.horizontal-halves","maximumWindows":2,
                                           "rotateGroup":settings["rotateGroup"],"output":args.output})
            if args.case in ("imported-group-restore","imported-rotation-restore","imported-change-warning","imported-change-restore"):
                library=LayoutProvider().read_snapshot()
                group_id=imported_minis(library)
                record("imported-library",library)
                settings.update(mode="group",maximumWindows=2,groupId=group_id,
                                rotateGroup=args.case=="imported-rotation-restore",shiftRangePercent=10,allowTransientOverlap=False)
                record("group-test-scope",{"groupId":group_id,"maximumWindows":2,
                                           "rotateGroup":settings["rotateGroup"],"output":args.output,"libraryRevision":library["revision"]})
            if args.case in ("imported-change-warning","imported-change-restore"):
                library_change=PaddingChange(args.receipt.with_suffix(".library-backup.json"),record)
            if args.case=="scheduled-restore":
                settings["intervalMinutes"]=1
            preferences.save_settings(path, settings)
            intent("SettingsChanged", {"revision": revision(settings)})
            record("waiting-for-operator", {"delay": args.delay, "output": args.output})
            print("WAITING: supervised disposable-window test; follow the control panel instructions.", flush=True)
            while time.monotonic()-started < args.delay:
                time.sleep(.25)
            current = status()
            readiness_deadline=time.monotonic()+15
            while not current["resumeReady"] and time.monotonic()<readiness_deadline:
                record("waiting-for-safety",current)
                time.sleep(.25)
                current=status()
            record("before-resume", current)
            if not current["resumeReady"]:
                raise RuntimeError("Native safety facts not ready; no movement requested")
            if args.case in ("topology-warning","reconfigure-warning","move-restore","scheduled-restore","cross-monitor","quit-warning","controller-loss","target-loss","lock-warning","group-restore","group-rotation-restore","imported-group-restore","imported-rotation-restore","imported-change-warning","imported-change-restore","stale-restore","stale-resize"):
                preview=intent("PlanPreview",{"outputId":output})
                preview_deadline=time.monotonic()+10
                while time.monotonic()<preview_deadline:
                    current=status()
                    result=current.get("planningResult",{})
                    if result.get("id")==preview.get("requestId") and result.get("status") not in ("queued","planning"):
                        record("planning-preflight",result)
                        if result.get("status")!="ready" or not result.get("moves"):
                            raise RuntimeError("No movement requested: planning preflight "+str(result.get("status"))+": "+str(result.get("reason")))
                        if args.case in ("group-restore","group-rotation-restore","imported-group-restore","imported-rotation-restore","imported-change-warning","imported-change-restore","stale-restore","stale-resize") and result.get("moves")!=2:
                            raise RuntimeError("No movement requested: group preflight requires exactly two moves; got "+str(result.get("moves")))
                        if args.case in ("group-rotation-restore","imported-rotation-restore") and result.get("reason")!="group-rotation":
                            raise RuntimeError("No movement requested: rotation preview required; got "+str(result.get("reason")))
                        break
                    time.sleep(.1)
                else:raise RuntimeError("Planning preflight timed out; no movement requested")
            intent("Resume")
            ready_deadline = time.monotonic()+5
            while not status()["movementReady"]:
                if time.monotonic()>ready_deadline:
                    raise RuntimeError("Resume did not arm")
                time.sleep(.1)
            if args.case=="scheduled-restore":
                # Resume's command acknowledgement can briefly count as an
                # active transaction; it is not a scheduled movement.
                while status().get("transactionActive"):
                    if time.monotonic()>ready_deadline:
                        raise RuntimeError("Resume command did not settle")
                    time.sleep(.1)
                record("waiting-for-schedule",{"output":args.output,"intervalSeconds":60})
                deadline=time.monotonic()+90
                while not status().get("transactionActive"):
                    if time.monotonic()>=deadline:raise RuntimeError("Scheduled transaction did not start")
                    time.sleep(.1)
                record("scheduled-transaction-observed",status())
            else:
                intent("MoveNow", {"outputId": output})
            current,warning_seen=wait_transaction("movement")
            record("movement-result",{"warningSeen":warning_seen,"result":current["lastResult"],
                                       "restoreAvailable":current.get("restoreAvailable")})
            if args.case=="imported-change-warning":
                if not library_changed or not warning_seen or current.get("restoreAvailable") or not current["lastResult"].startswith(("cancelled","rejected")) or "native-input" in current["lastResult"]:
                    raise RuntimeError("Library warning change did not produce verified cancellation without inverse: "+current["lastResult"])
                record("library-warning-result",current["lastResult"])
            if args.case=="imported-change-restore":
                if not warning_seen or current["lastResult"]!="committed" or not current.get("restoreAvailable"):
                    raise RuntimeError("Forward movement did not commit with inverse")
                library_change.apply()
                intent("Restore")
                current,second_warning=wait_transaction("restore")
                if second_warning or current.get("restoreAvailable") or not current["lastResult"].startswith(("cancelled","rejected","restore-skipped")) or "native-input" in current["lastResult"]:
                    raise RuntimeError("Changed-library Restore was not refused without warning: "+current["lastResult"])
                record("library-restore-result",dict(result=current["lastResult"],warningSeen=False,inverseConsumed=True))
            if args.case=="target-loss":
                if not targets_closed or not warning_seen or current.get("restoreAvailable") or not current["lastResult"].startswith(("cancelled:","rejected:")) or current["lastResult"]=="cancelled: native-input":
                    raise RuntimeError("Target loss did not produce verified refusal/cancellation without inverse: "+current["lastResult"])
            if args.case=="controller-loss" and not loss_completed:
                raise RuntimeError("Controller loss during warning was not verified")
            if args.case=="reconfigure-warning" and not reconfigure_completed:
                raise RuntimeError("KDE reload during warning was not verified")
            if args.case=="topology-warning" and not topology_completed:
                raise RuntimeError("Cable disconnect during warning was not verified")
            if args.case=="quit-warning" and not quit_completed:
                raise RuntimeError("Quit during an active warning was not verified")
            if args.case=="lock-warning" and not lock_completed:
                raise RuntimeError("Lock during an active warning was not verified")
            if args.case=="cross-monitor" and (not warning_seen or current["lastResult"]!="cancelled: native-input" or current.get("restoreAvailable")):
                raise RuntimeError("Cross-monitor warning did not end with native-input cancellation and no inverse")
            if args.case in ("stale-restore","stale-resize"):
                if not warning_seen or current["lastResult"]!="committed" or not current.get("restoreAvailable"):
                    raise RuntimeError("Forward movement did not commit with inverse; stale Restore not requested")
                request_stale_restore(status,intent,record,args.receipt.with_suffix(".continue"),
                                      change="resize" if args.case=="stale-resize" else "move")
                current,restore_warning=wait_transaction("restore")
                if restore_warning or current["lastResult"]!="restore-skipped: desktop-changed" or current.get("restoreAvailable"):
                    raise RuntimeError("Expected consumed stale Restore refusal: "+str(current.get("lastResult")))
                record("stale-restore-result",{"result":current["lastResult"],"warningSeen":False,"inverseConsumed":True})
            if args.case in ("move-restore","scheduled-restore","group-restore","group-rotation-restore","imported-group-restore","imported-rotation-restore"):
                if not warning_seen or current["lastResult"]!="committed" or not current.get("restoreAvailable"):
                    raise RuntimeError("Movement did not commit with an available inverse; Restore not requested: " + str(current.get("lastResult")))
                record("view-moved-window","Stay idle: Restore will be requested in 4 seconds")
                time.sleep(4)
                if not status().get("restoreAvailable"):
                    raise RuntimeError("Inverse no longer available; Restore not requested")
                intent("Restore")
                current,restore_warning=wait_transaction("restore")
                if not restore_warning or current["lastResult"]!="committed" or current.get("restoreAvailable"):
                    raise RuntimeError("Restore did not commit and consume its inverse")
                record("restore-result",{"warningSeen":restore_warning,"result":current["lastResult"],
                                          "inverseConsumed":not current.get("restoreAvailable")})
            if library_change:library_change.restore()
            if args.case in ("imported-group-restore","imported-rotation-restore","imported-change-warning","imported-change-restore"):
                after=LayoutProvider().read_snapshot()
                record("imported-library-after",after)
                if imported_minis(after)!=group_id or after["revision"]!=library["revision"]:
                    raise RuntimeError("Imported library changed during the round; stable-library acceptance not established")
            record("round-result",{"case":args.case,"warningSeen":warning_seen,
                                   "result":"controller-loss-dwell-and-harness-recovery-verified" if loss_completed else "quit-unload-and-paused-reopen-verified" if quit_completed else current["lastResult"],
                                   "physicalObservationPending":True})
        except Exception as error:
            record("error", str(error) or type(error).__name__)
            print(str(error), flush=True)
        finally:
            if library_change:
                try:library_change.restore()
                except Exception as error:record("error", "Library cleanup: "+str(error))
            pause = call_host("Pause")
            record("final-pause", pause)
            print("Round finished; Pause requested. Receipt:", args.receipt, flush=True)
            if not pause or not pause.get("ok"):
                raise RuntimeError("Could not confirm Pause; pause the dashboard manually")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
