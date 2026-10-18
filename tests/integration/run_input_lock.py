#!/usr/bin/env python3
"""Check KWin lock lifecycle inside a disposable virtual session."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from run_input_live import snapshot
from direct_vt import transitions as activity_transitions, boundary


def worker(directory, mode):
    transition = mode != "startup"
    env = os.environ.copy()
    env.update({"XDG_RUNTIME_DIR": str(directory / "runtime"),
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_DATA_HOME": str(directory / "data"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "QT_QPA_PLATFORM": "wayland", "KDE_SESSION_VERSION": "6",
                "QT_PLUGIN_PATH": os.environ["OWG_INPUT_PROBE_PLUGIN_DIR"],
                "OWG_INPUT_PROBE_ISOLATED": "1"})
    env.pop("OWG_INPUT_TEST_TRIGGER", None)
    for name in ("runtime", "config", "data", "cache"):
        (directory / name).mkdir(mode=0o700)
    (directory / "config/kwinrc").write_text("[Plugins]\nlibowg_input_probeEnabled=true\n")
    if mode == "unlock":
        # This setting belongs only to the private XDG config root. It permits
        # the disposable greeter to exit without using the host's credentials.
        (directory / "config/kscreenlockerrc").write_text(
            "[Daemon]\nRequirePassword=false\n")
    if transition:
        env["OWG_INPUT_TEST_TRIGGER"] = str(directory / "input-trigger")
        (directory / "input-trigger").write_text("")
    log_path = directory / "kwin.log"
    log = log_path.open("w")
    output_count = int(os.environ.get("OWG_TEST_OUTPUT_COUNT", "1"))
    if output_count not in (1, 2):
        raise RuntimeError("OWG_TEST_OUTPUT_COUNT must be 1 or 2")
    kwin_args = ["kwin_wayland", "--virtual", "--width", "900", "--height", "650",
                 "--output-count", str(output_count)]
    if not transition:
        kwin_args.append("--lockscreen")
    kwin = subprocess.Popen(kwin_args, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 20
        current = None
        while time.monotonic() < deadline:
            if kwin.poll() is not None:
                raise RuntimeError("nested KWin exited before lock observation")
            try:
                current = snapshot(env)
                if transition or current["lockState"] == "locked":
                    break
            except subprocess.SubprocessError:
                pass
            time.sleep(.25)
        if not current:
            raise RuntimeError("nested KWin observer endpoint did not appear")
        if not current['outputIdentityComplete'] or len(current['outputs']) != output_count:
            raise RuntimeError('Private session did not establish the requested output count')
        if mode == "signals":
            if current["schema"] != 2 or current["sleepState"] != "unknown" or \
                    current["sessionActivity"] != "unknown":
                raise RuntimeError("private session did not start with unknown facts: " + str(current))

            def trigger(command):
                path = directory / "input-trigger"
                path.write_text(command + "\n")
                for _ in range(80):
                    if not path.read_text():
                        return snapshot(env)
                    time.sleep(.05)
                raise RuntimeError("test-only trigger was not consumed: " + command)

            seeded = trigger("keyboard-only")
            if any(row['events'] <= 0 for row in seeded['outputs']):
                raise RuntimeError('Initial keyboard input did not mark every private output')
            held = trigger("press")
            if not held["buttonKnown"] or not held["buttonsDown"]:
                raise RuntimeError("test-only held button was not observed")
            sleeping = trigger("sleep")
            if (sleeping["sleepState"] != "sleeping" or
                    sleeping["sessionGeneration"] <= held["sessionGeneration"] or
                    sleeping["buttonKnown"] or sleeping["ageMs"] != -1):
                raise RuntimeError("sleep signal did not invalidate activity: " + str(sleeping))
            ignored_sleep = trigger("keyboard-only")
            if ignored_sleep["keyboard"] != sleeping["keyboard"] or \
                    ignored_sleep["sequence"] != sleeping["sequence"]:
                raise RuntimeError("activity was recorded during synthetic sleep")
            awake = trigger("wake")
            if (awake["sleepState"] != "awake" or
                    awake["sessionGeneration"] <= sleeping["sessionGeneration"] or
                    awake["buttonKnown"]):
                raise RuntimeError("wake signal did not reset activity: " + str(awake))
            from direct_suspend import validate_trace as validate_suspend_trace
            sleep_history = activity_transitions(env)
            checked = validate_suspend_trace(held, awake, sleep_history, sleep_history['outputGeometries'])
            if checked is None or [r['reason'] for r in checked[0]] != ['sleeping', 'awake']:
                raise RuntimeError('Synthetic sleep/wake did not satisfy suspend reset validator')
            print('SUSPEND_RESET_VALIDATOR: native synchronous sleep/wake trace passed (synthetic only)', flush=True)
            awake_input = trigger('keyboard-only')
            if any(row['events'] <= 0 for row in awake_input['outputs']):
                raise RuntimeError('Fresh awake input did not mark every private output')
            inactive = trigger("inactive")
            if (inactive["sessionActivity"] != "inactive" or
                    inactive["sessionGeneration"] <= awake["sessionGeneration"] or
                    inactive["buttonKnown"]):
                raise RuntimeError("inactive signal did not reset activity: " + str(inactive))
            ignored_inactive = trigger("keyboard-only")
            if ignored_inactive["keyboard"] != inactive["keyboard"] or \
                    ignored_inactive["sequence"] != inactive["sequence"]:
                raise RuntimeError("activity was recorded while session inactive")
            active = trigger("active")
            if (active["sessionActivity"] != "active" or
                    active["sessionGeneration"] <= inactive["sessionGeneration"] or
                    active["buttonKnown"]):
                raise RuntimeError("active signal did not reset activity: " + str(active))
            for state in (sleeping, awake, inactive, active):
                if len(state['outputs']) != output_count or any(row['events'] for row in state['outputs']):
                    raise RuntimeError('Session boundary retained activity on a private output')
            resumed_input = trigger("keyboard-only")
            if resumed_input["keyboard"] != active["keyboard"] + 2:
                raise RuntimeError("activity did not resume after active signal: " + str(resumed_input))
            if any(row['events'] != 2 for row in resumed_input['outputs']):
                raise RuntimeError('Fresh resumed input did not mark every private output')
            trace = activity_transitions(env)
            if trace.get("schema") != 1 or trace.get("epoch") != current["epoch"] or len(trace.get("transitions", [])) != 2:
                raise RuntimeError("Missing bounded activity-transition endpoint")
            boundary(awake, trace["transitions"][0], False, inactive["sessionGeneration"])
            boundary(awake, trace["transitions"][1], True, active["sessionGeneration"])
            for _ in range(5):
                trigger("inactive")
                latest = trigger("active")
            bounded = activity_transitions(env)["transitions"]
            if (len(bounded) != 8 or bounded[-1]["sessionGeneration"] != latest["sessionGeneration"] or
                    any(row["buttonKnown"] or row["ageMs"] != -1 for row in bounded) or
                    any(bounded[i + 1]["sessionGeneration"] != bounded[i]["sessionGeneration"] + 1
                        for i in range(7))):
                raise RuntimeError("Activity-transition record was not bounded and ordered")
            print("ACTIVITY_TRANSITIONS: synchronous inactive/active reset snapshots and eight-record bound passed (synthetic NoopSession only)", flush=True)
            current = resumed_input
            print("SESSION_SIGNALS: sleep/wake/inactive/active invalidation and input gating passed",
                  flush=True)
            print('SESSION_OUTPUTS:', output_count, 'all cleared at boundaries and marked by fresh input', flush=True)
        elif transition:
            if current["lockState"] == "locked":
                raise RuntimeError("transition test unexpectedly started locked")
            (directory / "input-trigger").write_text("press\n")
            for _ in range(40):
                current = snapshot(env)
                if current["buttonKnown"] and current["buttonsDown"]:
                    break
                time.sleep(.05)
            else:
                raise RuntimeError("test-only held-button setup failed")
            lock_baseline = current
            baseline_generation = current["sessionGeneration"]
            subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver",
                            "--object-path", "/ScreenSaver", "--method",
                            "org.freedesktop.ScreenSaver.Lock"], env=env, text=True,
                           capture_output=True, check=True, timeout=5)
            for _ in range(80):
                current = snapshot(env)
                if current["lockState"] == "locked":
                    break
                time.sleep(.05)
            if (current["lockState"] != "locked" or
                    current["sessionGeneration"] <= baseline_generation or
                    current["buttonKnown"]):
                raise RuntimeError("lock transition did not invalidate held-button state: " + str(current))
            print("LOCK_TRANSITION: unlocked to locked invalidated held-button state", flush=True)
            if mode == "unlock":
                locked_generation = current["sessionGeneration"]
                (directory / "input-trigger").write_text("motion-unlock\n")
                for _ in range(100):
                    current = snapshot(env)
                    if current["lockState"] == "unlocked":
                        break
                    time.sleep(.05)
                if (current["lockState"] != "unlocked" or
                        current["sessionGeneration"] <= locked_generation or
                        current["buttonKnown"]):
                    raise RuntimeError("unlock transition retained activity or was not observed: " + str(current))
                inactive = subprocess.run(
                    ["gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver",
                     "--object-path", "/ScreenSaver", "--method",
                     "org.freedesktop.ScreenSaver.GetActive"],
                    env=env, text=True, capture_output=True, check=True, timeout=5)
                if inactive.stdout.strip() != "(false,)":
                    raise RuntimeError("private greeter still reports active: " + inactive.stdout.strip())
                from direct_lock import validate_trace
                lifecycle = activity_transitions(env)
                print("LOCK_TRACE_RECORDS:", [(r["sessionGeneration"],r["reason"],r["lockState"]) for r in lifecycle["lifecycleTransitions"]], flush=True)
                records = validate_trace(lock_baseline, current, lifecycle, lifecycle["outputGeometries"])
                if records is None or [r["reason"] for r in records][-2:] != ["locked", "unlocked"]:
                    raise RuntimeError("Incomplete synchronous lock lifecycle trace")
                print("LOCK_LIFECYCLE_TRACE: complete cleared locking/locked/unlocked boundaries on all outputs (password-free virtual preparation only)", flush=True)
                print("UNLOCK_TRANSITION: private password-free greeter exited; activity unknown", flush=True)
        else:
            if current["lockState"] != "locked":
                raise RuntimeError("nested --lockscreen did not report locked; state=" +
                                   str(current.get("lockState")))
            print("LOCKED_STARTUP: observed locked state via KWin EffectsHandler", flush=True)
        print("LOCK_SNAPSHOT:", "generation=" + str(current["sessionGeneration"]),
              "buttonKnown=" + str(current["buttonKnown"]), flush=True)
    finally:
        kwin.terminate()
        try:
            kwin.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kwin.kill()
            kwin.wait(timeout=3)
        log.close()
        if not current or (mode not in ("unlock", "signals") and current["lockState"] != "locked"):
            print("NESTED_KWIN_LOG_TAIL:", log_path.read_text(errors="replace")[-1800:], flush=True)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), sys.argv[3])
        return
    mode = "startup" if len(sys.argv) == 1 else (
        {"--transition": "transition", "--unlock": "unlock",
         "--session-signals": "signals"}.get(sys.argv[1])
        if len(sys.argv) == 2 else None)
    if mode is None:
        raise SystemExit("usage: run_input_lock.py [--transition|--unlock|--session-signals]")
    plugin_dir = Path(os.environ.get("OWG_INPUT_PROBE_PLUGIN_DIR", "")).resolve()
    if not plugin_dir.is_relative_to(Path("/tmp")) or not (
            plugin_dir / "kwin/effects/plugins/libowg_input_probe.so").is_file():
        raise SystemExit("OWG_INPUT_PROBE_PLUGIN_DIR must point to a private /tmp build")
    directory = Path(tempfile.mkdtemp(prefix="owg-input-lock-"))
    try:
        subprocess.run(["dbus-run-session", "--", sys.executable, "-u", __file__,
                        "--worker", str(directory), mode],
                       check=True, timeout=60)
    finally:
        shutil.rmtree(directory)


if __name__ == "__main__":
    main()
