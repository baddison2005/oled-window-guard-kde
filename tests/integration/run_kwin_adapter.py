#!/usr/bin/env python3
"""Exercise the read-only adapter in a disposable nested KWin/Wayland session."""
import json
import ast
import glob
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from run_input_direct import environment as input_environment, verify_environment
from direct_vt import TOPOLOGY_TRACE_LIMIT, transitions as activity_transitions

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/kwin-script"
CLIENT = ROOT / "tests/runtime/owned_window.py"


def run(temp):
    hand_input = os.environ.get("OWG_TEST_HAND_INPUT") == "1"
    product_acceptance=os.environ.get("OWG_TEST_IMPORTED_PRODUCT")=="1"
    group_acceptance=os.environ.get("OWG_TEST_GROUP_RESTORE")=="1"
    ordered_acceptance=os.environ.get("OWG_TEST_ORDERED_WRITER")=="1"
    writer_acceptance=ordered_acceptance or os.environ.get("OWG_TEST_WRITER_FAILURES")=="1"
    env = os.environ.copy()
    env.update({
        "XDG_CONFIG_HOME": str(temp / "config"), "XDG_DATA_HOME": str(temp / "data"),
        "XDG_CACHE_HOME": str(temp / "cache"), "XDG_RUNTIME_DIR": str(temp / "runtime"),
        "QT_QPA_PLATFORM": "wayland", "KDE_SESSION_VERSION": "6",
        "QT_ASSUME_STDERR_HAS_CONSOLE": "1", "QT_FORCE_STDERR_LOGGING": "1",
        "QT_LOGGING_RULES": "kwin_scripting.debug=true;qml.debug=true",
        "QSG_USE_SIMPLE_ANIMATION_DRIVER": "1",
    })
    for name in ("config", "data", "cache"):
        (temp / name).mkdir()
    if writer_acceptance:
        env["OWG_WRITER_CLIENT_TRIGGER"]=str(temp/"writer-client-trigger")
        (temp/"writer-client-trigger").write_text("")
    input_probe_plugins = os.environ.get("OWG_INPUT_PROBE_PLUGIN_DIR")
    native_qml = os.environ.get("OWG_NATIVE_OBSERVER_QML_PATH")
    native_check = os.environ.get("OWG_NATIVE_CHECK_PLUGIN_DIR")
    if native_qml:
        module = pathlib.Path(native_qml).resolve()
        if not module.is_relative_to(pathlib.Path('/tmp')) or not (module/'org/oledwindowguard/Native/libowg_native_observer.so').is_file():
            raise RuntimeError("native observer test requires an explicit private /tmp QML module")
        if os.environ.get("OWG_TEST_PACKAGED_NATIVE") != "1":
            env["QML_IMPORT_PATH"] = str(module)
        else:
            env.pop("QML_IMPORT_PATH",None)
    if input_probe_plugins:
        env["QT_PLUGIN_PATH"] = input_probe_plugins
        env["OWG_INPUT_PROBE_ISOLATED"] = "1"
        if not hand_input:
            env["OWG_INPUT_TEST_TRIGGER"] = str(temp / "input-trigger")
        else:
            env.pop("OWG_INPUT_TEST_TRIGGER", None)
        (temp / "config/kwinrc").write_text("[Plugins]\nlibowg_input_probeEnabled=true\n")
        (temp / "input-trigger").write_text("")
    if native_check:
        module=pathlib.Path(native_check).resolve()
        if not module.is_relative_to(pathlib.Path('/tmp')) or not (module/'kwin/effects/plugins/libowg_native_check.so').is_file():
            raise RuntimeError("native cancellation checks require an isolated /tmp test effect")
        env["QT_PLUGIN_PATH"] = str(module)+(os.pathsep+input_probe_plugins if input_probe_plugins else "")
        env["OWG_NATIVE_CHECK_ISOLATED"] = "1"
        config=temp/'config/kwinrc'
        config.write_text((config.read_text() if config.exists() else '[Plugins]\n')+'libowg_native_checkEnabled=true\n')
    # Snapshot evidence is explicitly enabled only inside this private test session.
    config = temp / "config/kwinrc"
    with config.open("a") as stream:
        stream.write("\n[Script-oled-window-guard-adapter]\nDiagnosticSnapshots=true\n")
    runtime = temp / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from tools.build_kwin_package import materialize
    package = temp / "package"
    packaged=os.environ.get("OWG_TEST_PACKAGE_PATH")
    if packaged:
        source_package=pathlib.Path(packaged).resolve()
        if not source_package.is_relative_to(pathlib.Path('/tmp')) or not (source_package/'metadata.json').is_file():
            raise RuntimeError("packaged payload acceptance requires an explicit private /tmp KPackage")
        shutil.copytree(source_package,package)
    else:
        materialize(package,native_module=native_qml if os.environ.get("OWG_TEST_PACKAGED_NATIVE")=="1" else None)
    subprocess.run(["kpackagetool6", "--type", "KWin/Script", "--install", str(package)],
                   env=env, check=True, text=True, capture_output=True)
    log_path = temp / "kwin.log"
    log = log_path.open("w")
    existing_x_sockets = set(glob.glob("/tmp/.X11-unix/X*"))
    output_count = int(os.environ.get("OWG_TEST_OUTPUT_COUNT", "1"))
    if output_count not in (1, 2):
        raise RuntimeError("OWG_TEST_OUTPUT_COUNT must be 1 or 2")
    if hand_input:
        if not (input_probe_plugins or (native_qml and native_check)) or output_count != 2 or os.environ.get("OWG_TEST_PLANNING") != "1":
            raise RuntimeError("hand consumer test requires native plugin, planning and two outputs")
        parent = pathlib.Path(os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
        if not parent.is_absolute():
            parent = pathlib.Path(os.environ["XDG_RUNTIME_DIR"]) / parent
        backend = ["--wayland-display", str(parent)]
    else:
        backend = ["--virtual"]
    kwin = subprocess.Popen(["kwin_wayland", *backend, "--width", "901" if group_acceptance else "900", "--height", "650",
                             "--scale", "1", "--output-count", str(output_count), "--no-lockscreen",
                             "--xwayland"],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    client = xclient = companion = None
    xclient_started = False
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if kwin.poll() is not None:
                raise RuntimeError("nested KWin exited before readiness")
            ready = subprocess.run(["gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                                    "--object-path", "/Scripting"], env=env,
                                   text=True, capture_output=True, timeout=5)
            if ready.returncode == 0 and "loadDeclarativeScript" in ready.stdout:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("nested KWin scripting interface did not appear")
        if input_probe_plugins and not hand_input:
            effects = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                      "--object-path", "/Effects", "--method",
                                      "org.freedesktop.DBus.Properties.Get", "org.kde.kwin.Effects",
                                      "listOfEffects"], env=env, text=True, capture_output=True, timeout=5)
            if effects.returncode or "libowg_input_probe" not in effects.stdout:
                raise RuntimeError("native input probe was not discovered")
            print("INPUT_EFFECT_DISCOVERY: native probe available")
            def input_snapshot():
                reply = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                        "--object-path", "/org/oledwindowguard/InputProbe", "--method",
                                        "org.oledwindowguard.InputProbe1.Snapshot"], env=env,
                                       text=True, capture_output=True, timeout=5, check=True)
                return json.loads(ast.literal_eval(reply.stdout.strip())[0])

            initial = input_snapshot()
            if initial["schema"] != 2 or initial["sequence"] != 0 or initial["buttonKnown"]:
                raise RuntimeError("input probe did not start in an unknown, empty state")
            (temp / "input-trigger").write_text("sequence\n")
            for _ in range(40):
                observed = input_snapshot()
                if (observed["keyboard"] >= 2 and observed["button"] >= 2 and
                        observed["pointer"] >= output_count):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError("input snapshot did not receive synthetic sequence")
            if (observed["pointer"] < output_count or observed["axis"] < 1 or
                    observed["buttonsDown"] or not observed["buttonKnown"] or
                    observed["sequence"] < 6 or observed["ageMs"] < 0 or
                    not observed["outputIdentityComplete"] or len(observed["outputs"]) != output_count):
                raise RuntimeError("incomplete bounded input snapshot: " + str(observed))
            if output_count == 2 and not all(row["events"] for row in observed["outputs"]):
                raise RuntimeError("both outputs were not represented in the input snapshot")
            print("INPUT_SNAPSHOT: counts, age, output attribution, button release and unknown startup passed")
            origin = input_environment(env)
            if (origin.get("schema") != 1 or origin.get("outputBackend") != "KWin::VirtualBackend" or
                    origin.get("sessionBackend") != "KWin::NoopSession" or
                    origin.get("directMode") is not False or origin.get("injectionEnabled") is not True or
                    any(origin.get("libinput", {}).get(kind) != 0
                        for kind in ("pointer", "button", "axis", "keyboard"))):
                raise RuntimeError("synthetic input was misidentified as direct libinput input: " + str(origin))
            try:
                verify_environment(origin, 1)
            except RuntimeError:
                pass
            else:
                raise RuntimeError("direct input verifier accepted a virtual compositor")
            print("INPUT_ORIGIN: virtual/Noop identified; synthetic events cannot satisfy the direct gate")
            def effect_lifecycle(action):
                return subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                       "--object-path", "/Effects", "--method",
                                       "org.kde.kwin.Effects." + action, "libowg_input_probe"],
                                      env=env, text=True, capture_output=True, timeout=5, check=True)

            effect_lifecycle("unloadEffect")
            gone = subprocess.run(["gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                                   "--object-path", "/org/oledwindowguard/InputProbe"],
                                  env=env, text=True, capture_output=True, timeout=5)
            if gone.returncode == 0 and "InputProbe1" in gone.stdout:
                raise RuntimeError("unloaded input effect left its D-Bus endpoint registered")
            effect_lifecycle("loadEffect")
            fresh = input_snapshot()
            if fresh["epoch"] == initial["epoch"] or fresh["sequence"] != 0 or fresh["buttonKnown"]:
                raise RuntimeError("reloaded input observer retained stale activity state")
            fresh_trace = activity_transitions(env)
            if (fresh_trace["epoch"] != fresh["epoch"] or fresh_trace["transitions"] or
                    fresh_trace["topologyTransitions"]):
                raise RuntimeError("reloaded observer retained stale boundary records")
            print("INPUT_LIFECYCLE: endpoint removed on unload, fresh epoch and unknown buttons on reload")
            (temp / "input-trigger").write_text("press\n")
            for _ in range(40):
                held = input_snapshot()
                if held["button"]:
                    break
                time.sleep(.05)
            if not held["buttonKnown"] or not held["buttonsDown"]:
                raise RuntimeError("held button was not reported in the snapshot")
            (temp / "input-trigger").write_text("release\n")
            for _ in range(40):
                released = input_snapshot()
                if released["button"] > held["button"]:
                    break
                time.sleep(.05)
            if released["buttonsDown"] or released["button"] <= held["button"]:
                raise RuntimeError("button release was not reported in the snapshot")
            print("INPUT_BUTTON_STATE: held and released snapshots passed")
            if output_count == 2:
                before_topology = released
                (temp / "input-trigger").write_text("add-output\n")
                for _ in range(80):
                    added = input_snapshot()
                    if (added["topologyGeneration"] > before_topology["topologyGeneration"] and
                            len(added["outputs"]) == 3):
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError("virtual output addition did not reach the observer")
                if (added["buttonKnown"] or added["ageMs"] != -1 or
                        any(row["events"] for row in added["outputs"])):
                    raise RuntimeError("topology addition kept stale activity facts: " + str(added))
                (temp / "input-trigger").write_text("remove-output\n")
                for _ in range(80):
                    removed = input_snapshot()
                    if (removed["topologyGeneration"] > added["topologyGeneration"] and
                            len(removed["outputs"]) == 2):
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError("virtual output removal did not reach the observer")
                if (removed["buttonKnown"] or removed["ageMs"] != -1 or
                        any(row["events"] for row in removed["outputs"])):
                    raise RuntimeError("topology removal kept stale activity facts: " + str(removed))
                print("INPUT_TOPOLOGY: real virtual-backend add/remove each invalidated activity")
                topology_trace = activity_transitions(env)["topologyTransitions"]
                counts = [len(row["outputGeometries"]) for row in topology_trace]
                if (not 2 <= len(topology_trace) <= TOPOLOGY_TRACE_LIMIT or not counts or counts[0] != 3 or counts[-1] != 2 or
                        counts != sorted(counts, reverse=True) or set(counts) != {2, 3} or
                        any(row["buttonKnown"] or row["ageMs"] != -1 or
                            any(output["events"] for output in row["outputs"]) for row in topology_trace) or
                        [row["topologyGeneration"] for row in topology_trace] != list(range(1, len(topology_trace) + 1)) or
                        topology_trace[-1]["topologyGeneration"] != removed["topologyGeneration"]):
                    raise RuntimeError("Topology callback did not preserve synchronous reset/geometry records: " +
                                       json.dumps(topology_trace))
                print("TOPOLOGY_TRANSITIONS: synchronous reset and output geometry records passed")
                burst_before = removed
                (temp / "input-trigger").write_text("topology-burst\n")
                for _ in range(80):
                    burst = input_snapshot()
                    if burst["topologyGeneration"] >= burst_before["topologyGeneration"] + 10:
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError("Native topology burst did not complete")
                burst_trace = activity_transitions(env)
                burst_records = burst_trace["topologyTransitions"]
                if (burst_trace.get("topologyTraceCapacity") != TOPOLOGY_TRACE_LIMIT or
                        len(burst_records) <= 8 or
                        [row["topologyGeneration"] for row in burst_records] !=
                        list(range(1, burst["topologyGeneration"] + 1)) or
                        any(row["buttonKnown"] or row["buttonsDown"] or row["ageMs"] != -1 or
                            any(output["events"] for output in row["outputs"])
                            for row in burst_records) or len(burst["outputs"]) != 2):
                    raise RuntimeError("Native reset burst lost records or stale-state invalidation")
                print("TOPOLOGY_BURST: more than eight real resets retained contiguously with cleared activity")
                (temp / "input-trigger").write_text("topology-overflow\n")
                for _ in range(80):
                    overflow = input_snapshot()
                    if overflow["topologyGeneration"] >= burst["topologyGeneration"] + 66:
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError("Native topology overflow did not complete")
                overflow_records = activity_transitions(env)["topologyTransitions"]
                if (len(overflow_records) != TOPOLOGY_TRACE_LIMIT or
                        [row["topologyGeneration"] for row in overflow_records] !=
                        list(range(overflow["topologyGeneration"] - TOPOLOGY_TRACE_LIMIT + 1,
                                   overflow["topologyGeneration"] + 1)) or
                        overflow_records[0]["topologyGeneration"] <= burst["topologyGeneration"] + 1 or
                        len(overflow["outputs"]) != 2):
                    raise RuntimeError("Native reset history is unbounded or hides its overflow gap")
                print("TOPOLOGY_OVERFLOW: bounded 64-record history exposes the missing-generation gap")
        client_env = env.copy()
        client_env["WAYLAND_DISPLAY"] = "wayland-0"
        if hand_input or os.environ.get("OWG_TEST_HAND_FIXTURE") == "1":
            client_env["OWG_PLANNING_HAND_INSTRUCTIONS"] = str(temp / "hand-instructions.txt")
            (temp / "hand-instructions.txt").write_text("Wait while the read-only planning test starts.\nKeep the pointer outside both nested displays.")
        client_log = (temp / "client.log").open("w")
        client = subprocess.Popen([sys.executable, str(CLIENT)], env=client_env,
                                  stdout=client_log, stderr=subprocess.STDOUT)
        time.sleep(1.0)
        display = xauthority = None
        if all(shutil.which(tool) for tool in ("Xwayland", "xterm", "xdpyinfo")):
            xenv = env.copy()
            display = None
            xauthority = None
            for _ in range(40):
                try:
                    processes = subprocess.run(["ps", "-eo", "pid=,ppid=,args="],
                                               text=True, capture_output=True, timeout=2,
                                               check=True).stdout.splitlines()
                except (subprocess.SubprocessError, OSError):
                    processes = []
                descendants = {kwin.pid}
                entries = []
                for line in processes:
                    parts = line.strip().split(None, 2)
                    if len(parts) == 3:
                        try:
                            entries.append((int(parts[0]), int(parts[1]), parts[2]))
                        except ValueError:
                            pass
                changed = True
                while changed:
                    changed = False
                    for pid, ppid, args in entries:
                        if ppid in descendants and pid not in descendants:
                            descendants.add(pid)
                            changed = True
                xwayland_args = [args for pid, ppid, args in entries
                                 if pid in descendants and "Xwayland" in args]
                if xwayland_args:
                    match = re.search(r"(?:^|\s)-auth\s+(\S+)", xwayland_args[0])
                    if match:
                        xauthority = match.group(1)
                    display_match = re.search(r"(?:^|\s):([0-9]+)(?:\s|$)", xwayland_args[0])
                    if display_match:
                        candidate = ":" + display_match.group(1)
                        socket = "/tmp/.X11-unix/X" + display_match.group(1)
                        if socket in (set(glob.glob("/tmp/.X11-unix/X*")) - existing_x_sockets):
                            display = candidate
                            break
                # KWin may launch Xwayland outside the direct child tree. A
                # newly created socket is only a candidate: the later test
                # requires its xterm to appear in this private KWin snapshot.
                new_sockets = set(glob.glob("/tmp/.X11-unix/X*")) - existing_x_sockets
                if len(new_sockets) == 1:
                    display = ":" + pathlib.Path(next(iter(new_sockets))).name[1:]
                    break
                time.sleep(.25)
            if display:
                xenv["DISPLAY"] = display
                if xauthority:
                    xenv["XAUTHORITY"] = xauthority
                probe = subprocess.run(["xdpyinfo", "-display", display], env=xenv,
                                       text=True, capture_output=True, timeout=3)
                if probe.returncode != 0:
                    print("XWayland display discovered but xdpyinfo failed; XAUTHORITY/display setup unverified")
                    display = None
            if display:
                xclient = subprocess.Popen(["xterm", "-iconic", "-geometry", "40x5+20+20"],
                                           env=xenv, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                time.sleep(.5)
                if xclient.poll() is None:
                    xclient_started = True
                    print("XWayland disposable xterm: started on " + display + " in iconified state")
                else:
                    xclient = None
                    print("XWayland disposable xterm: launch failed; capability unverified")
            else:
                print("KWin child XWayland display/socket unavailable; XWayland enumeration unverified")

        def call(method, *args):
            return subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                   "--object-path", "/Scripting", "--method",
                                  "org.kde.kwin.Scripting." + method, *args], env=env,
                                  check=True, text=True, capture_output=True, timeout=5).stdout.strip()

        def load():
            output = call("loadDeclarativeScript",
                          str(temp / "data/kwin/scripts/oled-window-guard-adapter/contents/ui/main.qml"),
                          "oled-window-guard-adapter")
            match = re.search(r"\((\d+),\)", output)
            if not match:
                raise RuntimeError("KWin did not return a script ID: " + output)
            script_id = match.group(1)
            subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                            "--object-path", "/Scripting/Script" + script_id,
                           "--method", "org.kde.kwin.Script.run"], env=env,
                           check=True, text=True, capture_output=True, timeout=5)
            if call("isScriptLoaded", "oled-window-guard-adapter") != "(true,)":
                raise RuntimeError("adapter did not remain loaded")

        if hand_input or os.environ.get("OWG_TEST_HAND_FIXTURE") == "1":
            setup_script=ROOT / "tests/integration/hand_planning_setup.qml"
            if os.environ.get("OWG_TEST_INTERACTION_POLICY")=="1":
                original=setup_script.read_text();setup_script=temp/"interaction-setup.qml"
                setup_script.write_text(original.replace("first.y+100","first.y+160"))
            if os.environ.get("OWG_TEST_GUARDED_WRITER")=="1":
                original=setup_script.read_text()
                setup_script=temp/"writer-setup.qml"
                setup_script.write_text(original.replace("first.x+5,first.y+5","second.x+5,second.y+5").replace("first.x+30,first.y+100","first.x+30,first.y+160"))
            if ordered_acceptance:
                original=setup_script.read_text()
                setup_script=temp/"ordered-setup.qml"
                setup_script.write_text(original.replace("else if (w.caption===\"OLED Window Guard disposable probe window\")",
                    "else if (w.caption===\"OLED Window Guard owned ordered candidate\")\n                    target=Qt.rect(first.x+330,first.y+160,220,128)\n                else if (w.caption===\"OLED Window Guard disposable probe window\")").replace("found===3","found===4").replace("matches===3","matches===4"))
            if os.environ.get("OWG_TEST_MOVEMENT_DIMMING")=="1":
                original=setup_script.read_text();setup_script=temp/"movement-dimming-setup.qml"
                setup_script.write_text(original.replace("first.x+5,first.y+5","second.x+5,second.y+5"))
            reply=call("loadDeclarativeScript",str(setup_script),"owg-hand-setup")
            setup_id=re.search(r"\((\d+),\)",reply).group(1)
            subprocess.run(["gdbus","call","--session","--dest","org.kde.KWin",
                "--object-path","/Scripting/Script"+setup_id,"--method","org.kde.kwin.Script.run"],
                env=env,check=True,capture_output=True,timeout=5)
            for _ in range(100):
                if "HAND_PLANNING_SETUP_READY" in log_path.read_text(errors="replace"):
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("owned hand-test windows did not reach safe setup geometry")
            call("unloadScript","owg-hand-setup")

        if os.environ.get("OWG_TEST_PLANNING") == "1":
            companion_env = dict(env, QT_QPA_PLATFORM="offscreen")
            if display and xclient_started:
                companion_env["OWG_PRIVATE_X11_DISPLAY"] = display
                if xauthority:
                    companion_env["OWG_PRIVATE_X11_AUTHORITY"] = xauthority
            companion_script="session_resume_acceptance.py" if os.environ.get("OWG_TEST_SESSION_RESUME")=="1" else "output_retention_acceptance.py" if os.environ.get("OWG_TEST_OUTPUT_RETENTION")=="1" else "guard_controls_acceptance.py" if os.environ.get("OWG_TEST_GUARD_CONTROLS")=="1" else "grouped_swap_acceptance.py" if os.environ.get("OWG_TEST_GROUPED_ARRANGEMENTS")=="1" else "adjacent_rounding_acceptance.py" if os.environ.get("OWG_TEST_ADJACENT_ROUNDING")=="1" else "xwayland_geometry_acceptance.py" if os.environ.get("OWG_TEST_XWAYLAND_GEOMETRY")=="1" else "product_followup_acceptance.py" if os.environ.get("OWG_TEST_PRODUCT_FOLLOWUP")=="1" else "timer_drag_acceptance.py" if os.environ.get("OWG_TEST_TIMER_DRAG")=="1" else "grace_dimming_acceptance.py" if os.environ.get("OWG_TEST_GRACE_DIMMING")=="1" else "movement_dimming_acceptance.py" if os.environ.get("OWG_TEST_MOVEMENT_DIMMING")=="1" else "interaction_acceptance.py" if os.environ.get("OWG_TEST_INTERACTION_POLICY")=="1" else "imported_product_acceptance.py" if product_acceptance else "group_restore_acceptance.py" if group_acceptance else "ordered_writer_acceptance.py" if ordered_acceptance else "writer_acceptance.py" if writer_acceptance else "planning_companion.py"
            companion = subprocess.Popen([sys.executable, str(ROOT / "tests/runtime" / companion_script), str(temp)],
                                         env=companion_env, stdout=log, stderr=subprocess.STDOUT)
        if not product_acceptance:
            load()
        if companion:
            companion.wait(timeout=120 if os.environ.get("OWG_TEST_INTERACTION_POLICY")=="1" or os.environ.get("OWG_TEST_MOVEMENT_DIMMING")=="1" else 370 if hand_input else 230 if product_acceptance else 130 if writer_acceptance else 50 if native_check else 35)
            outcome = json.loads((temp / "planning-result.json").read_text())
            if companion.returncode or outcome["result"] != "PASS":
                raise RuntimeError("production planning exchange failed: " + json.dumps(outcome))
            print("PRODUCTION_PLANNING: " + json.dumps(outcome))
            time.sleep(1.2)
        else:
            time.sleep(2.5)
        def inspect_native():
            response=subprocess.run(["gdbus","call","--session","--dest","org.kde.KWin",
                "--object-path","/org/oledwindowguard/NativeCheck","--method","org.oledwindowguard.NativeCheck1.Inspect"],
                env=env,check=True,capture_output=True,text=True,timeout=5)
            return json.loads(ast.literal_eval(response.stdout.strip())[0])
        original_native=inspect_native() if native_check else None
        if original_native and not original_native['present']:
            raise RuntimeError('production native observer missing before unload: '+str(original_native))
        call("unloadScript", "oled-window-guard-adapter")
        time.sleep(.5)
        if kwin.poll() is not None:
            raise RuntimeError("KWin exited after first adapter unload")
        if call("isScriptLoaded", "oled-window-guard-adapter") != "(false,)":
            raise RuntimeError("adapter did not unload")
        if native_check and inspect_native()['present']:
            raise RuntimeError('script unload retained production native observer')
        load()
        time.sleep(1.5)
        if native_check:
            fresh=inspect_native()
            if not fresh['present'] or fresh['snapshot']['epoch']==original_native['snapshot']['epoch'] or fresh['snapshot']['buttonKnown']:
                raise RuntimeError('native reload retained stale epoch/button facts: '+str(fresh))
            outcome['nativeLifecycle']={'removedOnUnload':True,'freshEpochOnReload':True,'unknownButtonsOnReload':True}
            (temp/'planning-result.json').write_text(json.dumps(outcome,indent=2))
        call("unloadScript", "oled-window-guard-adapter")
        time.sleep(.5)
        if kwin.poll() is not None:
            raise RuntimeError("KWin exited after second adapter unload")
        if call("isScriptLoaded", "oled-window-guard-adapter") != "(false,)":
            raise RuntimeError("reloaded adapter did not unload")
        print("KPackage load/unload/reload: passed")
    finally:
        if companion and companion.poll() is None:
            companion.terminate()
            companion.wait(timeout=3)
        # Failed assertions must also unload declarative scripts before KWin
        # destroys the shared QML worker engine during process termination.
        if kwin.poll() is None:
            for script in ("owg-hand-setup","oled-window-guard-adapter"):
                try:call("unloadScript",script)
                except Exception:pass
        if client:
            client.terminate()
            try:
                client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait(timeout=2)
        if xclient:
            xclient.terminate()
            try:
                xclient.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xclient.kill()
                xclient.wait(timeout=2)
        kwin.terminate()
        try:
            kwin.wait(timeout=8)
        except subprocess.TimeoutExpired:
            kwin.kill()
            kwin.wait(timeout=3)
        log.close()
    text = log_path.read_text(errors="replace")
    if hand_input and "OWG_INPUT injecting" in text:
        raise RuntimeError("hand-input run unexpectedly injected synthetic input")
    if input_probe_plugins and not hand_input:
        probe_lines = [line for line in text.splitlines() if "OWG_INPUT" in line]
        print("INPUT_OBSERVER_LOG:\n" + "\n".join(probe_lines))
        for marker in ("OWG_INPUT installed", "OWG_INPUT pointer", "OWG_INPUT button",
                       "OWG_INPUT held", "OWG_INPUT axis", "OWG_INPUT key"):
            if not any(marker in line for line in probe_lines):
                raise RuntimeError("native input observer did not record " + marker)
        if output_count == 2 and not all(any(f'output "Virtual-{number}"' in line for line in probe_lines)
                                         for number in (0, 1)):
            raise RuntimeError("native input observer did not attribute both virtual outputs")
    rows = []
    for line in text.splitlines():
        marker = "OLED_WINDOW_GUARD_SNAPSHOT "
        if marker in line:
            rows.append(json.loads(line.split(marker, 1)[1]))
    if not rows:
        raise RuntimeError("KWin emitted no adapter snapshots")
    if rows[0]["snapshot"]["outputs"]:
        diagnostic = subprocess.run([
            "node", str(ROOT / "tools/diagnose_snapshot.js"), "--file", str(log_path),
            "--select", rows[0]["snapshot"]["outputs"][0]["id"],
        ], text=True, capture_output=True, check=True, timeout=5)
        if "Eligible:" not in diagnostic.stdout or "Obstacles:" not in diagnostic.stdout:
            raise RuntimeError("read-only diagnostic did not produce counts")
        print("DIAGNOSTIC_COUNTS:\n" + diagnostic.stdout.strip())
    payload = json.dumps({"rows": rows, "expectXwaylandClient": xclient_started, "handInput": hand_input,"allowFocusChanges":hand_input or os.environ.get("OWG_TEST_INTERACTION_POLICY")=="1","guardedWrite":outcome["guardedWriter"][-1]["lastWrite"] if os.environ.get("OWG_TEST_GUARDED_WRITER")=="1" and not writer_acceptance else None,
        "writeAudit":outcome.get("writeAudit",[]) if writer_acceptance or os.environ.get("OWG_TEST_INTERACTION_POLICY")=="1" else []})
    validator = r'''
const fs=require("fs"),vm=require("vm");
const source=fs.readFileSync(process.argv[1],"utf8").replace(/^\.pragma library\s*/m,"");
const c={}; vm.runInNewContext(source+"\nthis.api={createSnapshot,validateSnapshot,normalizeReadbackRectangle};",c);
const payload=JSON.parse(fs.readFileSync(0,"utf8")), rows=payload.rows;
for(const row of rows){const result=c.api.validateSnapshot(c.api.createSnapshot(row.snapshot));
 if(!result.valid) throw Error("snapshot invalid: "+JSON.stringify(result.errors));}
if(rows.length<2) throw Error("expected startup and timer/event snapshots");
const token=value=>value===null?null:String(value).replace(/^[^:]*:[^:]*:/,"");
const focus=token(rows[0].snapshot.focusedIdentity);
if(!payload.allowFocusChanges && rows.some(row=>token(row.snapshot.focusedIdentity)!==focus)) throw Error("focus identity changed");
const frame=r=>{r=c.api.normalizeReadbackRectangle(r);return JSON.stringify([r.x,r.y,r.width,r.height]);};
const initial=new Map(rows[0].snapshot.windows.map(w=>[token(w.identity),frame(w.rect)]));
for(const row of rows) for(const w of row.snapshot.windows)
 if(initial.has(token(w.identity))&&initial.get(token(w.identity))!==frame(w.rect)) {
  const audit=payload.writeAudit.filter(a=>token(a.identity)===token(w.identity));
  if(audit.length && (w.appId==="oled-window-guard-runtime-test" || String(w.appId).toLowerCase().includes("xterm")) &&
     audit.some(a=>frame(w.rect)===frame(a.from)||frame(w.rect)===frame(a.to)||(a.observed && frame(w.rect)===frame(a.observed)))) continue;
  const allowed=payload.guardedWrite;
  if(!allowed || token(w.identity)!==token(allowed.identity) || w.appId!=="oled-window-guard-runtime-test" ||
     initial.get(token(w.identity))!==frame(allowed.from) || frame(w.rect)!==frame(allowed.to))
   throw Error("unapproved window geometry changed");
 }
if(payload.guardedWrite && !rows.some(row=>row.snapshot.windows.some(w=>token(w.identity)===token(payload.guardedWrite.identity) && frame(w.rect)===frame(payload.guardedWrite.to))))
 throw Error("guarded write destination never observed");
const outputs=rows[0].snapshot.outputs, windows=rows[0].snapshot.windows;
const appIds=[...new Set(rows.flatMap(row=>row.snapshot.windows.map(w=>w.appId).filter(Boolean)))];
if(payload.expectXwaylandClient && !appIds.some(id=>String(id).toLowerCase().includes("xterm")))
 throw Error("disposable XWayland xterm did not appear in adapter snapshots");
console.log("snapshots valid="+rows.length+" outputs="+outputs.length+" windows="+windows.length+
 " fractional="+windows.some(w=>Object.values(w.rect).some(n=>n%1!==0))+
 " negative="+windows.some(w=>w.rect.x<0||w.rect.y<0)+
 (payload.writeAudit.length?" approved owned-window write audit verified":payload.guardedWrite?" approved single owned-window write observed":payload.handInput?" window geometry invariant=passed (hand-driven focus permitted)":" focus/window geometry invariants=passed"));
console.log("observed appIds="+appIds.join(","));
console.log("event reasons="+rows.map(row=>row.reason).join(","));
console.log("non-null window fields="+[...new Set(rows.flatMap(row=>row.snapshot.windows.flatMap(w=>Object.keys(w).filter(k=>w[k]!==null))))].sort().join(","));
console.log("non-null output fields="+[...new Set(rows.flatMap(row=>row.snapshot.outputs.flatMap(o=>Object.keys(o).filter(k=>o[k]!==null))))].sort().join(","));
'''
    subprocess.run(["node", "-e", validator, str(ROOT / "core/model.js")],
                   input=payload, text=True, check=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] != "--worker":
        raise RuntimeError("usage: run_kwin_adapter.py")
    temp = pathlib.Path(tempfile.mkdtemp(prefix="owg-kwin-adapter-"))
    command = ["dbus-run-session", "--", sys.executable, __file__, "--worker", str(temp)]
    runner_env = os.environ.copy()
    runner_env["XDG_RUNTIME_DIR"] = str(temp / "runtime")
    pathlib.Path(runner_env["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
    # Preserve the host socket before selecting the private runtime directory.
    if os.environ.get("OWG_TEST_HAND_INPUT") == "1":
        parent = pathlib.Path(os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
        if not parent.is_absolute():
            parent = pathlib.Path(os.environ["XDG_RUNTIME_DIR"]) / parent
        runner_env["WAYLAND_DISPLAY"] = str(parent)
    # Activated private-bus services may inherit stdout and outlive the runner.
    # A regular file avoids waiting for those unrelated pipe holders at EOF.
    runner_log = temp / "runner.log"
    with runner_log.open("w") as output:
        completed = subprocess.run(command, env=runner_env, text=True, stdout=output,
                                   stderr=output, timeout=410 if os.environ.get("OWG_TEST_HAND_INPUT") == "1" else 160 if os.environ.get("OWG_TEST_WRITER_FAILURES")=="1" or os.environ.get("OWG_TEST_ORDERED_WRITER")=="1" else 75)
    print(runner_log.read_text(errors="replace"), end="")
    if completed.returncode:
        print("FAILED_RUN_ARTIFACTS:", temp)
        return completed.returncode
    # The worker's nested compositor log is read before the isolated directory is removed.
    logs = list(temp.rglob("kwin.log"))
    if not logs:
        shutil.rmtree(temp)
        return 0
    if os.environ.get("OWG_TEST_PLANNING") == "1":
        print("PLANNING_ARTIFACTS:", temp)
    else:
        shutil.rmtree(temp)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        try:
            run(pathlib.Path(sys.argv[2]))
            sys.exit(0)
        except Exception as error:
            print("ADAPTER_HARNESS_FAILURE:", error, file=sys.stderr)
            sys.exit(1)
    sys.exit(main())
