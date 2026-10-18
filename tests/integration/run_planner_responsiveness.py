#!/usr/bin/env python3
"""Measure synchronous pure planners inside an isolated virtual KWin.

Retain raw timings; successful checks are not a responsiveness acceptance claim.
Never bind the production adapter, inject input, or write window geometry.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
NAME = "oledwindowguard-planner-probe"


def instrument(runtime):
    """Instrument private copies only; preserve production source hashes."""
    changes = {
        "swap.js": [
            ("var finalCheck = Planner.validateSwapFinalPlan(snapshot, registryState, plan);",
             'var profileStart = Date.now(); var finalCheck = Planner.validateSwapFinalPlan(snapshot, registryState, plan); console.warn("OWG_PLANNER_PROFILE " + JSON.stringify({stage:"swap-final",durationMs:Date.now()-profileStart}));'),
            ("var ordered = Steps.orderSwapSteps(snapshot, registryState, plan, { maxEvaluations", "profileStart = Date.now(); var ordered = Steps.orderSwapSteps(snapshot, registryState, plan, { maxEvaluations"),
            ("if (!ordered.steps.length)", 'console.warn("OWG_PLANNER_PROFILE " + JSON.stringify({stage:"swap-order",durationMs:Date.now()-profileStart})); if (!ordered.steps.length)'),
        ],
        "swap_steps.js": [
            ("function stepCandidates(from, area, current, finalRects, shouldStop) {", "function stepCandidates(from, area, current, finalRects, shouldStop) { var profileStart = Date.now();"),
            ("    return result;\n}\nfunction validateSwapSteps", '    console.warn("OWG_PLANNER_PROFILE " + JSON.stringify({stage:"stage-candidates",durationMs:Date.now()-profileStart})); return result;\n}\nfunction validateSwapSteps'),
            ("if (shouldStop && shouldStop()) return [];", 'if (shouldStop && shouldStop()) { console.warn("OWG_PLANNER_PROFILE " + JSON.stringify({stage:"stage-candidates",durationMs:Date.now()-profileStart,stopped:true})); return []; }'),
            ("var checked = finalGeometryCheck(snapshot, registryState, plan, options);",
             'var profileStart = Date.now(); var checked = finalGeometryCheck(snapshot, registryState, plan, options); console.warn("OWG_PLANNER_PROFILE " + JSON.stringify({stage:"order-final",durationMs:Date.now()-profileStart}));'),
        ],
    }
    for name, replacements in changes.items():
        path = runtime / "core" / name
        source = path.read_text()
        for old, new in replacements:
            if source.count(old) != 1:
                raise RuntimeError("profiling marker is not unique: " + old)
            source = source.replace(old, new)
        path.write_text(source)


def worker(runtime, artifacts, profile=False, threaded=False):
    env = os.environ.copy()
    env.update({"XDG_CONFIG_HOME": str(runtime / "config"), "XDG_DATA_HOME": str(runtime / "data"),
                "XDG_CACHE_HOME": str(runtime / "cache"), "XDG_RUNTIME_DIR": str(runtime / "runtime"),
                "QT_QPA_PLATFORM": "wayland", "KDE_SESSION_VERSION": "6",
                "QT_ASSUME_STDERR_HAS_CONSOLE": "1", "QT_FORCE_STDERR_LOGGING": "1",
                "QT_LOGGING_RULES": "kwin_scripting.debug=true;qml.debug=true",
                "QSG_USE_SIMPLE_ANIMATION_DRIVER": "1"})
    for name in ("config", "data", "cache", "runtime"):
        (runtime / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / "core", runtime / "core")
    core_source_hashes = {"core/" + p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((runtime / "core").glob("*.js"))}
    if profile:
        instrument(runtime)
    probe_qml = ROOT / "probes/planner" / ("worker_main.qml" if threaded else "main.qml")
    shutil.copy2(probe_qml, runtime / "main.qml")
    if threaded:
        sys.path.insert(0, str(ROOT / "tools"))
        from build_planning_worker import build
        (runtime / "worker.js").write_text(build(runtime / "core", probe=True))
        shutil.copy2(runtime / "worker.js", artifacts / "worker.js")
        shutil.copy2(ROOT / "probes/planner/worker_lifetime.qml", runtime / "worker_lifetime.qml")
    log_path = artifacts / "kwin.log"
    processes = []
    result = None
    with log_path.open("w") as log, (artifacts / "clock-client.log").open("w") as client_log:
        kwin = subprocess.Popen(["kwin_wayland", "--virtual", "--width", "900", "--height", "650",
                                 "--scale", "1", "--output-count", "1", "--no-lockscreen"],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(kwin)
        def call(path, method, *args):
            return subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                   "--object-path", path, "--method", method, *args],
                                  env=env, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if kwin.poll() is not None:
                    raise RuntimeError("private KWin exited before readiness")
                try:
                    call("/Scripting", "org.kde.kwin.Scripting.isScriptLoaded", NAME)
                    break
                except subprocess.CalledProcessError:
                    time.sleep(.25)
            else:
                raise RuntimeError("private scripting interface did not appear")
            client_env = dict(env, WAYLAND_DISPLAY="wayland-0")
            client = subprocess.Popen([sys.executable, str(ROOT / "tests/runtime/owned_window.py")],
                                      env=client_env, stdout=client_log, stderr=subprocess.STDOUT)
            processes.append(client)
            time.sleep(1)
            if client.poll() is not None:
                raise RuntimeError("private clock client failed")
            reply = call("/Scripting", "org.kde.kwin.Scripting.loadDeclarativeScript", str(runtime / "main.qml"), NAME)
            script_id = ast.literal_eval(reply)[0]
            call("/Scripting/Script" + str(script_id), "org.kde.kwin.Script.run")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if kwin.poll() is not None or client.poll() is not None:
                    raise RuntimeError("private compositor or clock client exited during measurement")
                lines = log_path.read_text(errors="replace").splitlines()
                if any("OWG_PLANNER_FAIL " in line for line in lines):
                    raise RuntimeError(next(line for line in lines if "OWG_PLANNER_FAIL " in line))
                matches = [line.split("OWG_PLANNER_RESULT ", 1)[1] for line in lines if "OWG_PLANNER_RESULT " in line]
                if matches:
                    result = json.loads(matches[-1])
                    break
                time.sleep(.1)
            else:
                raise RuntimeError("planner measurement deadline expired")
            if len(result["samples"]) != 120 or result["controls"] != (25 if threaded else 6):
                raise RuntimeError("incomplete planner measurement")
            call("/Scripting", "org.kde.kwin.Scripting.unloadScript", NAME)
            if call("/Scripting", "org.kde.kwin.Scripting.isScriptLoaded", NAME) != "(false,)":
                raise RuntimeError("planner probe failed to unload")
            before = log_path.read_text().count("OWG_PLANNER_RESULT ")
            time.sleep(.3)
            if kwin.poll() is not None or log_path.read_text().count("OWG_PLANNER_RESULT ") != before:
                raise RuntimeError("private compositor failed or probe continued after unload")
            if threaded:
                lifetime_name = NAME + "-lifetime"
                def lifetime_lines(marker):
                    return [json.loads(line.split(marker, 1)[1]) for line in log_path.read_text().splitlines() if marker in line]
                def load_lifetime():
                    script = ast.literal_eval(call("/Scripting", "org.kde.kwin.Scripting.loadDeclarativeScript",
                        str(runtime / "worker_lifetime.qml"), lifetime_name))[0]
                    call("/Scripting/Script" + str(script), "org.kde.kwin.Script.run")
                def wait_lifetime(marker, count):
                    limit = time.monotonic() + 4
                    while time.monotonic() < limit:
                        if kwin.poll() is not None:
                            raise RuntimeError("private KWin exited during worker lifetime test")
                        records = lifetime_lines(marker)
                        if len(records) >= count:
                            return records[-1]
                        time.sleep(.005)
                    raise RuntimeError("missing worker lifetime marker: " + marker)
                load_lifetime()
                first = wait_lifetime("OWG_WORKER_LIFETIME_START ", 1)
                if lifetime_lines("OWG_WORKER_LIFETIME_REPLY "):
                    raise RuntimeError("worker completed before pending-unload test")
                start_unload = time.monotonic()
                call("/Scripting", "org.kde.kwin.Scripting.unloadScript", lifetime_name)
                elapsed_unload = (time.monotonic() - start_unload) * 1000
                if elapsed_unload > 64:
                    raise RuntimeError("pending worker unload blocked KWin: " + str(elapsed_unload) + " ms")
                time.sleep(.65)
                if lifetime_lines("OWG_WORKER_LIFETIME_REPLY "):
                    raise RuntimeError("unloaded worker delivered a reply to its deleted owner")
                load_lifetime()
                second = wait_lifetime("OWG_WORKER_LIFETIME_START ", 2)
                reply = wait_lifetime("OWG_WORKER_LIFETIME_REPLY ", 1)
                if first["epoch"] == second["epoch"] or reply["epoch"] != second["epoch"]:
                    raise RuntimeError("worker reload reused or accepted a stale epoch")
                call("/Scripting", "org.kde.kwin.Scripting.unloadScript", lifetime_name)
                if call("/Scripting", "org.kde.kwin.Scripting.isScriptLoaded", lifetime_name) != "(false,)":
                    raise RuntimeError("worker lifetime script did not unload")
                result["workerLifetime"] = {"pendingUnloadMs": elapsed_unload, "pendingUnloadBudgetMs": 64,
                    "discardedEpoch": first["epoch"], "freshEpoch": second["epoch"],
                    "oldReplyDropped": True, "reloadedReplyMatched": True}
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            if result is not None:
                result["cleanup"] = {"unloaded": True, "kwinExit": kwin.returncode,
                                     "clientExit": client.returncode, "shutdownRequested": "SIGTERM",
                                     "allChildrenReaped": all(p.poll() is not None for p in processes)}
    (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    # KWin may handle the requested SIGTERM by returning its signal number.
    # Preserve the status; reject crashes and forced-kill cleanup.
    if result["cleanup"]["kwinExit"] not in (0, signal.SIGTERM, -signal.SIGTERM):
        raise RuntimeError("private KWin did not shut down cleanly: " + str(result["cleanup"]))
    result["versions"] = {name: subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
                          for name, command in {"kwin": ["kwin_wayland", "--version"], "python": [sys.executable, "--version"]}.items()}
    result["sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [*sorted((ROOT / "core").glob("*.js")), ROOT / "probes/planner/main.qml",
                                  Path(__file__).resolve()]}
    result["sha256"].update(core_source_hashes)
    result["sha256"][str(probe_qml.relative_to(ROOT))] = hashlib.sha256(probe_qml.read_bytes()).hexdigest()
    result["threaded"] = threaded
    if threaded:
        result["sha256"]["tools/build_planning_worker.py"] = hashlib.sha256((ROOT / "tools/build_planning_worker.py").read_bytes()).hexdigest()
        result["workerBundleSha256"] = hashlib.sha256((runtime / "worker.js").read_bytes()).hexdigest()
        result["sha256"]["probes/planner/worker_lifetime.qml"] = hashlib.sha256((ROOT / "probes/planner/worker_lifetime.qml").read_bytes()).hexdigest()
    result["profiled"] = profile
    result["runtimeCoreSha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((runtime / "core").glob("*.js"))}
    result["profileSamples"] = [json.loads(line.split("OWG_PLANNER_PROFILE ", 1)[1])
                                for line in log_path.read_text().splitlines()
                                if "OWG_PLANNER_PROFILE " in line]
    (artifacts / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    for mode in ("shift", "swap"):
        for count in (4, 12):
            samples = [s for s in result["samples"] if s["mode"] == mode and s["candidates"] == count]
            durations = sorted(s["durationMs"] for s in samples)
            gaps = sorted(s["followingTimerGapMs"] for s in samples)
            print(f"{mode} candidates={count}: median={durations[15]}ms p95={durations[28]}ms "
                  f"maxTimerGap={gaps[-1]}ms complete={sum(s['complete'] for s in samples)}/30", flush=True)
    print("PLANNER_CHECKS: PASS; timings retained, responsiveness gate remains open", flush=True)
    if threaded:
        print("PLANNER_WORKER_CHECKS: PASS; 25 controls, workload timer gaps <= 32ms, pending unload/reload passed", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, default=ROOT / "docs/evidence/planner-runs")
    parser.add_argument("--worker", nargs=2, metavar=("RUNTIME", "ARTIFACTS"))
    parser.add_argument("--profile", action="store_true", help="Instrument private core copies to log stage timings")
    parser.add_argument("--threaded", action="store_true", help="Test Qt WorkerScript transport and compositor timer responsiveness")
    args = parser.parse_args()
    if args.threaded and args.profile:
        parser.error("--threaded and --profile must be run separately")
    if args.worker:
        return worker(*(Path(p) for p in args.worker), profile=args.profile, threaded=args.threaded)
    args.record_dir.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(prefix="planner-", dir=args.record_dir.resolve()))
    runtime = Path(tempfile.mkdtemp(prefix="owg-plan-"))
    print("PLANNER_ARTIFACTS:", artifacts, flush=True)
    try:
        return subprocess.run(["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()),
                               "--worker", str(runtime), str(artifacts), *(["--profile"] if args.profile else []),
                               *(["--threaded"] if args.threaded else [])], timeout=90).returncode
    finally:
        shutil.rmtree(runtime)


if __name__ == "__main__":
    sys.exit(main())
