"""Exercise the actual SessionHost exchange with fixture-owned private KWin."""
import json
import ast
import os
import subprocess
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PySide6.QtCore import QTimer
from oled_window_guard.dashboard import create_application
from oled_window_guard.session import SessionHost, register_host, revision
from oled_window_guard import preferences

app=create_application([])
path=Path(sys.argv[1])
host=SessionHost(app,settings_path=path / "settings.json")
if not register_host(host):
    raise RuntimeError("private companion registration failed")
started=time.monotonic()
stage=0
requested=False
results=[]
failure=None
native_checks=[]
native_stage=0
native_wait_until=0
hand_input=os.environ.get("OWG_TEST_HAND_INPUT")=="1"
hand_baseline=None
local_checks=[]
local_stage=0
local_requested=False
physical_stage=0
physical_phase="prepare"
physical_until=0
physical_checks=[]
writer_stage=0
writer_phase="configure"
writer_wait=0
writer_checks=[]


def local_check(method, *arguments):
    reply=subprocess.run(["gdbus","call","--session","--dest","org.kde.KWin",
        "--object-path","/org/oledwindowguard/NativeCheck","--method",
        "org.oledwindowguard.NativeCheck1."+method,*arguments],check=True,capture_output=True,text=True,timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])


def native_snapshot():
    reply=subprocess.run(["gdbus","call","--session","--dest","org.kde.KWin",
        "--object-path","/org/oledwindowguard/InputProbe","--method",
        "org.oledwindowguard.InputProbe1.Snapshot"],check=True,capture_output=True,text=True,timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])


def prompt(message):
    (path / "hand-instructions.txt").write_text(message)
    print("HAND_CONSUMER: "+message.replace("\n"," "),flush=True)


def output_id():
    # Owned clients are placed by KWin on the last virtual output. Select the
    # actual contained frame rather than assuming the first output has clients.
    rows=(path / "kwin.log").read_text(errors="replace").splitlines()
    for line in reversed(rows):
        if "OLED_WINDOW_GUARD_SNAPSHOT " not in line:
            continue
        snapshot=json.loads(line.split("OLED_WINDOW_GUARD_SNAPSHOT ",1)[1])["snapshot"]
        for window in snapshot["windows"]:
            if window["appId"]=="oled-window-guard-runtime-test" and (os.environ.get("OWG_TEST_GUARDED_WRITER")!="1" or window["focused"] is False) and window["rect"]["width"]>100 and len(window["outputIds"])==1:
                return window["outputIds"][0]
    raise RuntimeError("no contained owned client in live snapshot")


def effect(action):
    subprocess.run(["gdbus","call","--session","--dest","org.kde.KWin","--object-path","/Effects",
                    "--method","org.kde.kwin.Effects."+action,"libowg_input_probe"],check=True,capture_output=True,timeout=3)


def tick():
    global stage,requested,failure,native_stage,native_wait_until,hand_baseline,local_stage,local_requested,physical_stage,physical_phase,physical_until,writer_stage,writer_phase,writer_wait
    try:
        if time.monotonic()-started>(360 if hand_input else 45 if os.environ.get("OWG_NATIVE_CHECK_PLUGIN_DIR") else 30):
            raise RuntimeError("companion planning integration timed out: "+json.dumps(host.status))
        if not host.status.get("planningReady"):
            return
        if os.environ.get("OWG_NATIVE_OBSERVER_QML_PATH"):
            assert host.status.get("nativeObservation", {}).get("immediateCancellation"),host.status
        modes=[] if hand_input else [("drift",False),("swap",False),("drift",True),("group",False)]
        if stage<len(modes):
            mode,ordered=modes[stage]
            if not requested:
                values=preferences.defaults()
                values.update(movementDisplays=[output_id()],avoidFocusedWindow=False,
                              mode=mode,shiftRangePercent=100,shiftHorizontalOrder=ordered,
                              groupId="builtin.horizontal-halves")
                preferences.save_settings(host.settings_path,values)
                assert host.submit("SettingsChanged",{"revision":revision(values)})["ok"]
                # Let the observer receive the saved revision before requesting.
                requested="configured"
                return
            if requested=="configured":
                host.dashboard.preview_display.setCurrentIndex(host.dashboard.preview_display.findData(output_id()))
                host.dashboard._send("PlanPreview")
                if not host.preview_pending:
                    raise RuntimeError("dashboard preview did not enqueue request: "+host.dashboard.reason_label.text())
                requested=True
                return
            result=host.status.get("planningResult",{})
            if result.get("status") in ("ready","no-plan"):
                drawing=result.get("preview")
                assert drawing and drawing["outputId"]==output_id(), result
                assert len(drawing["destinations"])==result["moves"] and len(drawing["windows"])<=32, result
                assert host.dashboard.movement_preview.data==drawing, "dashboard did not render the accepted preview"
                results.append(dict(result,mode=mode,ordered=ordered))
                assert host.status["paused"] and not host.status["movementReady"] and not host.status["dimmingReady"]
                stage+=1;requested=False
            elif result.get("status") in ("rejected","discarded"):
                raise RuntimeError("preview failed: "+str(result))
        else:
            assert hand_input or any(result["moves"]>0 for result in results),"no nonempty live preview"
            local_kinds=["pointer","press","axis","key","release","focus","locking","locked","unlocked",
                         "sleep","wake","inactive","active","touch","topology"]
            if os.environ.get("OWG_NATIVE_CHECK_PLUGIN_DIR") and not hand_input and os.environ.get("OWG_TEST_GUARDED_WRITER")!="1" and local_stage<len(local_kinds):
                if not local_requested:
                    values=preferences.defaults()
                    values.update(movementDisplays=[output_id()],avoidFocusedWindow=False,shiftRangePercent=100)
                    preferences.save_settings(host.settings_path,values)
                    host.submit("SettingsChanged",{"revision":revision(values)})
                    local_requested="configured"
                    return
                if local_requested=="configured":
                    if not host.submit("PlanPreview",{"outputId":output_id()})["ok"]:
                        return
                    local_requested="preview"
                    return
                result=host.status.get("planningResult",{})
                if result.get("status")=="ready":
                    if local_stage==0:
                        baseline=local_check("Inspect")["snapshot"]
                        assert local_check("Arm","pointer",baseline["outputs"][0]["id"])["armed"]
                    checked=local_check("Verify",local_kinds[local_stage])
                    if local_stage==0:
                        receipt=local_check("PhysicalResult")
                        assert receipt.get("ok") and receipt["synchronous"],receipt
                        checked["passiveReceipt"]=receipt
                    assert checked.get("ok") and checked["hadProposal"],checked
                    sample=checked["snapshot"]
                    if local_kinds[local_stage]=="press":
                        assert sample["buttonKnown"] and sample["buttonsDown"]
                    if local_kinds[local_stage]=="release":
                        assert sample["buttonKnown"] and not sample["buttonsDown"]
                    if local_stage>=6:
                        assert not sample["buttonKnown"] and not sample["buttonsDown"] and sample["ageMs"]==-1
                        assert all(row["events"]==0 for row in sample["outputs"])
                    if local_kinds[local_stage]=="touch":
                        assert sample["otherInputObserved"]
                    local_checks.append(checked);local_stage+=1;local_requested=False
                    (path/'native-local-progress.json').write_text(json.dumps(local_checks,indent=2))
                elif result.get("status") in ("discarded","rejected","no-plan"):
                    raise RuntimeError("local cancellation preview failed: "+str(result))
                return
            if os.environ.get("OWG_NATIVE_CHECK_PLUGIN_DIR") and not hand_input and os.environ.get("OWG_TEST_GUARDED_WRITER")!="1":
                assert local_check("Duplicate").get("ok"),"duplicate native observer was not refused"
            if os.environ.get("OWG_TEST_GUARDED_WRITER")=="1" and writer_stage<3:
                if writer_phase=="configure":
                    values=preferences.defaults()
                    values.update(paused=False,movementDisplays=[output_id()],avoidFocusedWindow=True,
                                  shiftRangePercent=10,quietSeconds=1,warningSeconds=3,maximumWindows=1)
                    preferences.save_settings(host.settings_path,values)
                    host.submit("SettingsChanged",{"revision":revision(values)})
                    if writer_stage>0:
                        prepared=local_check("PrepareWriter")
                        assert prepared.get("present"),prepared
                    writer_wait=time.monotonic()+1.6;writer_phase="wait";return
                if writer_phase=="wait":
                    if time.monotonic()<writer_wait:return
                    if host.submit("PlanPreview",{"outputId":output_id()})["ok"]: writer_phase="preview"
                    return
                result=host.status.get("planningResult",{})
                if writer_phase=="preview":
                    if result.get("status")=="ready":
                        checked=local_check("WriterStart")
                        if writer_stage==0:
                            assert not checked["accepted"] and checked["writeCount"]==0,checked
                            writer_checks.append({"kind":"unknown-session-refused",**checked})
                            writer_stage+=1;writer_phase="configure";return
                        assert checked["accepted"] and checked["state"]["phase"]=="Warning",checked
                        writer_phase="warning";writer_wait=time.monotonic()+.6
                    elif result.get("status") in ("rejected","discarded","no-plan"):
                        raise RuntimeError("guarded preview failed: "+str(result))
                    return
                checked=local_check("WriterStatus")
                if writer_stage==1:
                    if time.monotonic()<writer_wait:return
                    canceled=local_check("Verify","pointer")
                    checked=local_check("WriterStatus")
                    assert canceled["ok"] and checked["state"]["phase"]=="Paused" and checked["writeCount"]==0,checked
                    writer_checks.append({"kind":"warning-input-canceled",**checked,"cancellation":canceled})
                    writer_stage+=1;writer_phase="configure";return
                if checked["state"].get("lastResult",{}).get("status")=="committed":
                    assert checked["writeCount"]==1 and checked["warningActive"]==-1,checked
                    writer_checks.append({"kind":"warning-write-readback-committed",**checked})
                    writer_stage+=1
                    (path/"guarded-writer-result.json").write_text(json.dumps(writer_checks,indent=2))
                elif checked["state"]["phase"]=="Paused":
                    raise RuntimeError("guarded write failed: "+str(checked))
                return
            if hand_input and os.environ.get("OWG_NATIVE_CHECK_PLUGIN_DIR"):
                stages=[(kind,output) for kind in ("pointer","press","axis","key") for output in (0,1)]
                if physical_stage<len(stages):
                    kind,output=stages[physical_stage];target="WL-"+str(output)
                    sample=local_check("Inspect")["snapshot"]
                    if physical_phase=="prepare":
                        prompt("Stage "+str(physical_stage+1)+"/8: "+kind+" on "+target+".\nClick inside "+target+" now, then keep the mouse and keyboard STILL until READY.")
                        physical_until=time.monotonic()+3;physical_phase="quiet"
                        return
                    if physical_phase=="quiet":
                        if time.monotonic()<physical_until or sample["buttonsDown"] or sample["ageMs"]<1500:
                            return
                        values=preferences.defaults()
                        values.update(movementDisplays=[output_id()],avoidFocusedWindow=False,shiftRangePercent=100)
                        preferences.save_settings(host.settings_path,values)
                        host.submit("SettingsChanged",{"revision":revision(values)})
                        physical_phase="configured";return
                    if physical_phase=="configured":
                        if host.submit("PlanPreview",{"outputId":output_id()})["ok"]:
                            physical_phase="preview"
                        return
                    result=host.status.get("planningResult",{})
                    if physical_phase=="preview":
                        if result.get("status")=="ready":
                            armed=local_check("Arm",kind,target)
                            assert armed.get("armed"),armed
                            action={"pointer":"Move the pointer slightly", "press":"Click once without moving the pointer", "axis":"Turn the wheel without moving the pointer", "key":"Press and release A without moving the pointer"}[kind]
                            prompt("READY "+str(physical_stage+1)+"/8 — "+action+" INSIDE "+target+".\nKeep still afterwards. No windows will move.")
                            physical_phase="armed"
                        elif result.get("status")=="discarded":
                            physical_phase="prepare"
                        elif result.get("status") in ("no-plan","rejected"):
                            raise RuntimeError("physical preview failed: "+str(result))
                        return
                    checked=local_check("PhysicalResult")
                    if not checked:return
                    if not checked.get("ok"):
                        if checked.get("synchronous") and checked.get("oldTokenRejected") and checked.get("proposalCleared"):
                            prompt("A different input canceled safely. Re-arming this stage.")
                            physical_phase="prepare";return
                        raise RuntimeError("physical synchronous cancellation failed: "+str(checked))
                    physical_checks.append(checked)
                    (path/"native-physical-progress.json").write_text(json.dumps(physical_checks,indent=2))
                    physical_stage+=1;physical_phase="prepare"
                    return
                native_stage=2
            if (hand_input or os.environ.get("OWG_TEST_NATIVE_CONSUMER")=="1") and native_stage<(2 if hand_input else 3):
                if time.monotonic()<native_wait_until:
                    return
                if not requested:
                    if hand_input:
                        quiet=native_snapshot()
                        if quiet["buttonsDown"] or (quiet["ageMs"]>=0 and quiet["ageMs"]<1000):
                            return
                        prompt("Keep the pointer outside both nested displays for a moment.\nPreparing a read-only preview; no windows will move.")
                    values=preferences.defaults()
                    values.update(movementDisplays=[output_id()],avoidFocusedWindow=False,shiftRangePercent=100)
                    preferences.save_settings(host.settings_path,values)
                    host.submit("SettingsChanged",{"revision":revision(values)})
                    requested="configured"
                    return
                if requested=="configured":
                    assert host.submit("PlanPreview",{"outputId":output_id()})["ok"]
                    requested="preview"
                    return
                result=host.status.get("planningResult",{})
                reasons=["native-input","native-session-or-topology","native-unavailable"]
                if requested=="preview" and result.get("status")=="ready":
                    assert result["moves"]>0
                    if hand_input:
                        hand_baseline=native_snapshot()
                        prompt("READY — move the pointer INSIDE nested display WL-"+str(native_stage)+
                               (" (small preview candidate)." if native_stage==0 else " (large instruction window).")+
                               "\nThen move the pointer outside both test displays.")
                    elif native_stage<2:
                        (path / "input-trigger").write_text("keyboard-only\n" if native_stage==0 else "sleep\n")
                    else:
                        effect("unloadEffect")
                    requested="cancel"
                    return
                if requested=="cancel" and result.get("status")=="discarded":
                    if hand_input:
                        assert result["reason"] in ("native-input","native-focus","focus"),result
                        fresh=native_snapshot()
                        target="WL-"+str(native_stage)
                        counts=lambda s: {row["id"]:row["events"] for row in s["outputs"]}
                        delta=counts(fresh).get(target,0)-counts(hand_baseline).get(target,0)
                        assert fresh["epoch"]==hand_baseline["epoch"],"native observer changed during hand stage"
                        assert all(fresh[key]==hand_baseline[key] for key in ("sessionGeneration","topologyGeneration")),"session or topology changed during hand stage"
                        if delta<=0:
                            prompt("Input canceled the preview on another display.\nMove OUTSIDE both displays; the test will re-arm for "+target+".")
                            requested=False;native_wait_until=time.monotonic()+2
                            return
                        native_checks.append({"output":target,"reason":result["reason"],"events":delta,
                                              "baseline":hand_baseline,"fresh":fresh})
                    else:
                        assert result["reason"]==reasons[native_stage],result
                        native_checks.append(result["reason"])
                    if not hand_input and native_stage==1:
                        (path / "input-trigger").write_text("wake\n")
                    elif not hand_input and native_stage==2:
                        effect("loadEffect")
                    native_stage+=1;requested=False;native_wait_until=time.monotonic()+(2 if hand_input else .5)
                    if hand_input:
                        prompt("WL-"+str(native_stage-1)+" passed.\nMove OUTSIDE both displays and wait for the next READY prompt.")
                elif hand_input and requested=="preview" and result.get("status")=="discarded":
                    requested=False;native_wait_until=time.monotonic()+2
                elif requested=="preview" and result.get("status") in ("no-plan","rejected"):
                    raise RuntimeError("no usable cancellation preview: "+str(result))
                return
            response=host.submit("PlanPreview",{"outputId":output_id()})
            assert response["ok"]
            host.submit("Pause")
            assert host.preview_pending is None and host.preview_command is None
            outcome={"result":"PASS","previews":results,"queuedPauseCanceled":True,
                "movementReady":False,"dimmingReady":False,"nativeCancellation":native_checks,
                "handInput":hand_input,"localCancellation":local_checks,"physicalCancellation":physical_checks,"guardedWriter":writer_checks}
            (path / "planning-result.json").write_text(json.dumps(outcome,indent=2))
            app.quit()
    except Exception as error:
        failure=str(error)
        (path / "planning-result.json").write_text(json.dumps({"result":"FAIL","reason":failure,"status":host.status,
            "localCancellation":local_checks,"nextLocalStage":local_stage},indent=2))
        app.quit()


timer=QTimer();timer.setInterval(100);timer.timeout.connect(tick);timer.start()
app.exec()
host.dashboard.tray.hide()
sys.exit(1 if failure else 0)
