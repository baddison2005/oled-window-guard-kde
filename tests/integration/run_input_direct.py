#!/usr/bin/env python3
"""Bounded DRM/libinput observer test, launched from an authenticated local VT.

Default mode only checks prerequisites. --run takes the private test compositor
onto the already active VT. No input injection or production guard is enabled.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from run_input_live import CLIENT, ROOT, snapshot

KINDS = ("pointer", "button", "axis", "keyboard")


def validate_session(properties, terminal, uid):
    if uid == 0:
        raise RuntimeError("Run as the logged-in user, without sudo")
    if (properties.get("Type") != "tty" or properties.get("Class") != "user" or
            properties.get("Active") != "yes" or properties.get("Remote") != "no" or
            not properties.get("Seat") or properties.get("User") != str(uid)):
        raise RuntimeError("An active, local, seated tty login for this user is required")
    vt = properties.get("VTNr", "")
    if not vt.isdigit() or int(vt) <= 0 or terminal != "/dev/tty" + str(int(vt)):
        raise RuntimeError("The input terminal must match the active logind virtual terminal")
    return int(vt)


def verify_probe(plugin_dir, running):
    folder = plugin_dir.resolve()
    plugin = folder / "kwin/effects/plugins/libowg_input_probe.so"
    if (folder == Path("/tmp") or not folder.is_relative_to(Path("/tmp")) or
            not plugin.is_file() or not plugin.resolve().is_relative_to(folder)):
        raise RuntimeError("Use the private probe build under /tmp")
    info = json.loads((folder / "build-info.json").read_text())
    if (info.get("schema") != 1 or running != "kwin " + info.get("kwinVersion", "") or
            info.get("pluginSHA256") != hashlib.sha256(plugin.read_bytes()).hexdigest() or
            info.get("sourceSHA256") != hashlib.sha256(
                (ROOT / "probes/native_input/observer.cpp").read_bytes()).hexdigest()):
        raise RuntimeError("Probe build is stale or mismatched; rebuild against this KWin")
    return folder


def preflight(plugin_dir):
    try:
        terminal = os.ttyname(sys.stdin.fileno())
    except OSError as exc:
        raise RuntimeError("Launch from a logged-in virtual terminal, not a desktop terminal or agent") from exc
    result = subprocess.run(["loginctl", "show-session", "self", "--no-pager",
                             "-p", "Type", "-p", "Class", "-p", "Active", "-p", "Remote",
                             "-p", "Seat", "-p", "VTNr", "-p", "User"],
                            check=True, text=True, capture_output=True, timeout=5)
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    vt = validate_session(props, terminal, os.getuid())
    running = subprocess.run(["kwin_wayland", "--version"], check=True, text=True,
                             capture_output=True, timeout=5).stdout.strip()
    return verify_probe(plugin_dir, running), vt


def environment(env):
    result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                             "--object-path", "/org/oledwindowguard/InputProbe", "--method",
                             "org.oledwindowguard.InputProbe1.Environment"],
                            env=env, check=True, text=True, capture_output=True, timeout=3)
    return json.loads(ast.literal_eval(result.stdout.strip())[0])


def verify_environment(facts, vt, active=True):
    if (facts.get("schema") != 1 or facts.get("outputBackend") != "KWin::DrmBackend" or
            facts.get("sessionBackend") != "KWin::LogindSession" or
            facts.get("sessionActive") is not active or facts.get("sessionTerminal") != vt or
            facts.get("directMode") is not True or facts.get("injectionEnabled") is not False):
        raise RuntimeError("Private DRM/logind, VT, activity or injection state does not match the expected session")


def verify_continuity(baseline, current):
    fields = ("epoch", "topologyGeneration", "sessionGeneration")
    if any(current.get(key) != baseline.get(key) for key in fields):
        raise RuntimeError("Observer epoch, topology or session changed during the input check")
    if (current.get("schema") != 2 or current.get("outputIdentityComplete") is not True or
            not current.get("outputs") or current.get("lockState") in ("locking", "locked") or
            current.get("sleepState") == "sleeping" or current.get("sessionActivity") == "inactive"):
        raise RuntimeError("Observer safety facts became incomplete or suspended")


def verify_result(baseline, final, origin_before, origin_after, client_counts, held, released):
    verify_continuity(baseline, final)
    for counters in (baseline, final, origin_before["libinput"], origin_after["libinput"], client_counts):
        if any(type(counters.get(kind)) is not int or counters[kind] < 0 for kind in KINDS):
            raise RuntimeError("Input counts must be nonnegative integers")
    counts = {kind: final[kind] - baseline[kind] for kind in KINDS}
    direct = {kind: origin_after["libinput"][kind] - origin_before["libinput"][kind] for kind in KINDS}
    if (any(value <= 0 for value in counts.values()) or any(value <= 0 for value in direct.values()) or
            any(client_counts.get(kind, 0) <= 0 for kind in KINDS) or
            held is not True or released is not True or final.get("buttonKnown") is not True or
            final.get("buttonsDown") is not False):
        raise RuntimeError("Incomplete libinput, client-delivery or held/released input matrix")
    return {"observer": counts, "libinput": direct, "client": client_counts,
            "heldObserved": held, "releaseObserved": released}


def terminate(process, seconds):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
        raise RuntimeError("Private test process required forced termination")


def run_private_session(command, env, timeout, recorder=None):
    log = (recorder.directory / 'runner.log').open('wb', buffering=0) if recorder else None
    try:
        process = subprocess.Popen(command, env=env, process_group=0,
                                   **({'stdout': log, 'stderr': subprocess.STDOUT} if log else {}))
    except BaseException:
        if log:
            log.close()
        raise
    try:
        deadline = time.monotonic() + timeout
        while True:
            if recorder:
                recorder.check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                return process.wait(timeout=min(.25, remaining))
            except subprocess.TimeoutExpired:
                continue
    except (subprocess.TimeoutExpired, KeyboardInterrupt, RuntimeError) as exc:
        if recorder:
            from direct_recording import event
            try:
                event(recorder.directory, 'private-session-aborting', reason=type(exc).__name__,
                      detail=str(exc))
            except OSError:
                pass  # A failed disk must not prevent stopping the DRM worker.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)
        return 124
    finally:
        if log:
            os.fsync(log.fileno())
            log.close()


def private_environment(directory, plugin_dir, warning=False, runtime_dir=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("OWG_", "KWIN_"))}
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "SESSION_MANAGER"):
        env.pop(key, None)
    runtime = runtime_dir if runtime_dir is not None else directory / 'runtime'
    if len(os.fsencode(runtime / 'owg-direct-0')) + 1 > 108:
        raise RuntimeError('Private Wayland socket path exceeds 108 bytes; use --record-dir for a short separate runtime')
    env.update({"XDG_RUNTIME_DIR": str(runtime),
                "XDG_CONFIG_HOME": str(directory / "config"), "XDG_DATA_HOME": str(directory / "data"),
                "XDG_CACHE_HOME": str(directory / "cache"), "QT_PLUGIN_PATH": str(plugin_dir),
                "QT_QPA_PLATFORM": "wayland", "OWG_INPUT_PROBE_ISOLATED": "1",
                "OWG_INPUT_PROBE_DIRECT": "1", "KDE_SESSION_VERSION": "6"})
    if warning:
        env.update({"QT_ASSUME_STDERR_HAS_CONSOLE": "1", "QT_FORCE_STDERR_LOGGING": "1",
                    "QT_LOGGING_RULES": "kwin_scripting.debug=true;qml.debug=true"})
    if runtime_dir is None:
        runtime.mkdir(mode=0o700)
    for name in ("config", "data", "cache"):
        (directory / name).mkdir(mode=0o700)
    (directory / "config/kwinrc").write_text("[Plugins]\nlibowg_input_probeEnabled=true\n")
    return env


def worker(directory, plugin_dir, seconds, warning=False, vt_cycle=False, output_scope=False,
           warning_both=False, hotplug=False, unknown_focus=False, hotplug_connector='HDMI',
           runtime_dir=None, lock_cycle=False, suspend_cycle=False):
    from direct_recording import atomic_json, event
    plugin_dir, vt = preflight(plugin_dir)
    if (vt_cycle or lock_cycle or suspend_cycle) and vt == 2:
        raise RuntimeError("Lifecycle cycle requires a spare terminal distinct from the KDE desktop on VT 2")
    env = private_environment(directory, plugin_dir, warning or output_scope or warning_both or hotplug or lock_cycle or suspend_cycle,
                              runtime_dir)
    recording = (directory / 'recording.json').is_file()
    if recording:
        env['QT_LOGGING_RULES'] = 'kwin_wayland_drm.debug=true;kwin_core.debug=true;kwin_scripting.debug=true;qml.debug=true'
        if lock_cycle:
            env['QT_LOGGING_RULES'] = 'kwin_wayland_drm.debug=true;kwin_core.debug=true;kwin_scripting.debug=true;qt.qml.debug=false;qml.debug=false'
        env['QT_MESSAGE_PATTERN'] = '[%{time yyyy-MM-ddTHH:mm:ss.zzz} %{pid} %{category}] %{message}'
    event(directory, 'worker-starting', terminal=vt, runtime=str(runtime_dir))
    if unknown_focus:
        from direct_unknown_focus import prepare_fixture
        prepare_fixture(directory)
    report = {"status": "fail", "scope": "direct warning outside-input/Skip" if warning else
              "direct VT inactive/active recovery" if vt_cycle else
              "direct physical two-output keyboard attribution" if output_scope else
              "direct physical warning outside-input/Skip on both outputs" if warning_both else
              "direct physical " + hotplug_connector + " hotplug and two-output recovery" if hotplug else
              "direct physical unknown-focus keyboard fallback on both outputs" if unknown_focus else
              "direct authenticated two-output lock/unlock recovery" if lock_cycle else
              "direct physical two-output suspend/resume recovery" if suspend_cycle else
              "direct observer/client input only", "terminal": vt}
    report["probeBuild"] = json.loads((plugin_dir / "build-info.json").read_text())
    if recording:
        atomic_json(directory / 'result.json', dict(report, status='in-progress'))
    kwin = client = None
    with (directory / "kwin.log").open("w") as log, (directory / "client.log").open("w") as client_log:
        try:
            kwin_args = ["kwin_wayland", "--drm", "--socket", "owg-direct-0", "--no-kactivities"]
            if not lock_cycle:
                kwin_args.append("--no-lockscreen")
            else:
                from direct_lock import prepare_authentication
                prepare_authentication(directory)
            kwin = subprocess.Popen(kwin_args,
                                    env=env, stdout=log, stderr=subprocess.STDOUT)
            event(directory, 'private-kwin-launched', pid=kwin.pid)
            deadline = time.monotonic() + 25
            while True:
                if kwin.poll() is not None:
                    raise RuntimeError("Private DRM compositor exited before readiness; see kwin.log")
                try:
                    facts = environment(env)
                    break
                except subprocess.CalledProcessError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Private input endpoint did not appear")
                    time.sleep(.2)
            verify_environment(facts, vt)
            event(directory, 'private-kwin-ready', environment=facts)
            client_env = dict(env, WAYLAND_DISPLAY="owg-direct-0", OWG_INPUT_CLIENT_DIRECT="1",
                              OWG_INPUT_TEST_SECONDS=str(seconds))
            warning_check = None
            if warning:
                from direct_warning import WarningCheck
                warning_check = WarningCheck(directory, env)
                client_env["OWG_INPUT_WARNING_INSTRUCTIONS"] = str(directory / "instructions.txt")
            if vt_cycle:
                (directory / "instructions.txt").write_text("Preparing the VT-cycle test; please wait.")
                client_env["OWG_INPUT_CLIENT_INSTRUCTIONS"] = str(directory / "instructions.txt")
                client_env["OWG_INPUT_CLIENT_HEADING"] = "Direct VT-cycle test"
            if output_scope:
                (directory / "instructions.txt").write_text("Preparing two-monitor keyboard check; keep the mouse still.")
                client_env.update(OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory / "instructions.txt"),
                                  OWG_INPUT_CLIENT_HEADING="Physical two-monitor keyboard check",
                                  OWG_INPUT_CLIENT_WINDOWED="1")
            if warning_both:
                (directory / "instructions.txt").write_text("Preparing the warning on the first monitor. Please wait.")
                client_env.update(OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory / "instructions.txt"),
                                  OWG_INPUT_CLIENT_HEADING="Physical two-monitor warning check",
                                  OWG_INPUT_CLIENT_WINDOWED="1")
            if hotplug:
                (directory / "instructions.txt").write_text("Preparing the physical " + hotplug_connector + " hotplug check. Please wait.")
                client_env.update(OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory / "instructions.txt"),
                                  OWG_INPUT_CLIENT_HEADING="Physical connector hotplug check",
                                  OWG_INPUT_CLIENT_WINDOWED="1")
            if lock_cycle:
                (directory / "instructions.txt").write_text("Preparing authenticated lock/unlock. Please wait.")
                client_env.update(OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory / "instructions.txt"),
                                  OWG_INPUT_CLIENT_HEADING="Authenticated two-monitor lock/unlock",
                                  OWG_INPUT_CLIENT_WINDOWED="1")
            if suspend_cycle:
                (directory / "instructions.txt").write_text("Preparing real system suspend/resume. Please wait.")
                client_env.update(OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory / "instructions.txt"),
                                  OWG_INPUT_CLIENT_HEADING="Physical two-monitor suspend/resume",
                                  OWG_INPUT_CLIENT_WINDOWED="1")
            if unknown_focus:
                (directory / "instructions.txt").write_text("Preparing unknown-focus keyboard check. Keep the mouse still.")
                client_env.update(OWG_INPUT_CLIENT_INSTRUCTIONS=str(directory / "instructions.txt"),
                                  OWG_INPUT_CLIENT_HEADING="Physical unknown-focus keyboard check",
                                  OWG_INPUT_CLIENT_WINDOWED="1", OWG_INPUT_CLIENT_NO_FOCUS="1")
            client = subprocess.Popen([sys.executable, "-u", str(CLIENT)], env=client_env,
                                      stdout=client_log, stderr=subprocess.STDOUT)
            event(directory, 'client-launched', pid=client.pid)
            deadline = time.monotonic() + 10
            while "CLIENT_READY" not in (directory / "client.log").read_text(errors="replace"):
                if client.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Disposable Wayland input client did not become ready")
                time.sleep(.1)
            if suspend_cycle:
                from direct_suspend import observe
                report.update(observe(directory, env, kwin, client, seconds, vt))
            elif lock_cycle:
                from direct_lock import observe
                report.update(observe(directory, env, kwin, client, seconds, vt))
            elif unknown_focus:
                from direct_unknown_focus import observe
                report.update(observe(directory, env, kwin, client, seconds, vt))
            elif hotplug:
                from direct_hotplug import observe
                report.update(observe(directory, env, kwin, client, seconds, vt, hotplug_connector))
            elif warning_both:
                from direct_warning_outputs import observe
                report.update(observe(directory, env, kwin, client, seconds, vt))
            elif output_scope:
                from direct_output_scope import observe
                report.update(observe(directory, env, kwin, client, seconds, vt))
            elif vt_cycle:
                from direct_vt import observe
                report.update(observe(directory, env, kwin, client, seconds, vt))
            else:
                if warning_check:
                    warning_check.start(kwin, client)
                baseline = snapshot(env)
                verify_continuity(baseline, baseline)
                origin_before = environment(env)
                verify_environment(origin_before, vt)
                initial_log = (directory / "client.log").read_text(errors="replace")
                before = {kind: initial_log.count("CLIENT_EVENT " + kind) for kind in KINDS}
                if warning_check:
                    warning_check.ready()
                print("DIRECT_READY: use the disposable window for", seconds, "seconds", flush=True)
                held = released = False
                final = baseline
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    if kwin.poll() is not None or client.poll() is not None:
                        raise RuntimeError("Test aborted or private compositor/client exited")
                    final = snapshot(env)
                    verify_continuity(baseline, final)
                    held |= final["buttonKnown"] and final["buttonsDown"] and final["button"] > baseline["button"]
                    released |= held and final["buttonKnown"] and not final["buttonsDown"]
                    if warning_check:
                        events = (directory / "client.log").read_text(errors="replace")
                        counts = {kind: events.count("CLIENT_EVENT " + kind) - before[kind] for kind in KINDS}
                        origin = environment(env)
                        verify_environment(origin, vt)
                        warning_check.advance(counts, held, released, final, origin, origin_before)
                        if warning_check.finished:
                            break
                    time.sleep(.08)
                if warning_check:
                    report["warning"] = warning_check.finish()
                    final = snapshot(env)
                origin_after = environment(env)
                verify_environment(origin_after, vt)
                events = (directory / "client.log").read_text(errors="replace")
                counts = {kind: events.count("CLIENT_EVENT " + kind) - before[kind] for kind in KINDS}
                report.update(verify_result(baseline, final, origin_before, origin_after, counts, held, released))
                report["environment"] = origin_after
        except Exception as exc:
            report["error"] = str(exc)
            try:
                event(directory, 'worker-error', error=str(exc))
            except OSError:
                pass
            raise
        finally:
            try:
                event(directory, 'cleanup-starting')
            except OSError:
                pass
            try:
                try:
                    terminate(client, 3)
                finally:
                    terminate(kwin, 8)
            except Exception as exc:
                report["error"] = str(exc)
                raise
            finally:
                atomic_json(directory / 'result.json', report)
                event(directory, 'cleanup-finished', report=report)
    report["status"] = "pass"
    atomic_json(directory / 'result.json', report)
    event(directory, 'worker-pass')
    print("DIRECT_RESULT: PASS (" + report["scope"] + ")", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preflight", action="store_true", help="check only; this is the default")
    modes.add_argument("--run", action="store_true", help="start private DRM compositor on the active login VT")
    scenario = parser.add_mutually_exclusive_group()
    scenario.add_argument("--warning", action="store_true", help="also check outside input and physical warning Skip")
    scenario.add_argument("--vt-cycle", action="store_true", help="check held-input invalidation and recovery across a hand-driven VT switch")
    scenario.add_argument("--output-scope", action="store_true", help="check physical keyboard attribution on each of two outputs and a spanning owned window")
    scenario.add_argument("--warning-both", action="store_true", help="check outside input and physical warning Skip in sequence on both outputs")
    scenario.add_argument("--hotplug", action="store_true", help="check physical connector disconnect/reconnect invalidation and fresh two-output input")
    parser.add_argument("--hotplug-connector", choices=('HDMI', 'DP'),
                        help="connector to disconnect during --hotplug (default: HDMI)")
    scenario.add_argument("--unknown-focus", action="store_true", help="check physical keyboard fallback to both outputs without application focus")
    scenario.add_argument("--lock-cycle", action="store_true", help="check password-authenticated lock/unlock and fresh input on both outputs")
    scenario.add_argument("--suspend-cycle", action="store_true",
                          help="suspend the whole system after held input, then check two-output recovery")
    parser.add_argument("--seconds", type=int, default=45, choices=range(15, 121), metavar="15..120")
    parser.add_argument("--plugin-dir", type=Path, default=Path(os.environ.get(
        "OWG_INPUT_PROBE_PLUGIN_DIR", "/tmp/owg-kwin-devel/probe_plugins")))
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--runtime-dir', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--record-dir', type=Path,
                        help='permanent parent directory for flushed diagnostics; runtime socket stays under /tmp')
    args = parser.parse_args()
    if (args.lock_cycle or args.suspend_cycle) and args.worker is None and not args.record_dir:
        parser.error("--lock-cycle/--suspend-cycle requires --record-dir for durable lifecycle evidence")
    if args.hotplug_connector and not args.hotplug:
        parser.error('--hotplug-connector requires --hotplug')
    if args.worker is not None:
        worker(args.worker, args.plugin_dir, args.seconds, args.warning, args.vt_cycle,
               args.output_scope, args.warning_both, args.hotplug, args.unknown_focus,
               args.hotplug_connector or 'HDMI', args.runtime_dir, args.lock_cycle, args.suspend_cycle)
        return 0
    folder, vt = preflight(args.plugin_dir)
    if (args.vt_cycle or args.lock_cycle or args.suspend_cycle) and vt == 2:
        raise RuntimeError("Lifecycle cycle requires a spare terminal distinct from the KDE desktop on VT 2")
    if (args.lock_cycle or args.suspend_cycle) and json.loads((folder / "build-info.json").read_text()).get("lifecycleTraceCapacity") != 16:
        raise RuntimeError("Lifecycle cycle requires the rebuilt lifecycle-trace probe")
    if args.suspend_cycle:
        from direct_suspend import suspend_preflight
        suspend_preflight()
    print("DIRECT_PREFLIGHT: PASS; active login VT", vt, "and matching private probe", flush=True)
    if args.record_dir:
        from direct_recording import recording_preflight
        recording_preflight()
    if not args.run:
        return 0
    if args.record_dir:
        from direct_recording import create_directory
        directory = create_directory(args.record_dir)
    else:
        directory = Path(tempfile.mkdtemp(prefix="owg-input-direct-"))
    print('DIRECT_ARTIFACTS:', directory, flush=True)
    env = os.environ.copy()
    for name in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SESSION_BUS_PID"):
        env.pop(name, None)
    command = ["dbus-run-session", "--", sys.executable, "-u", __file__,
               "--worker", str(directory), "--plugin-dir", str(folder), "--seconds", str(args.seconds)]
    if args.warning:
        command.append("--warning")
    if args.vt_cycle:
        command.append("--vt-cycle")
    if args.output_scope:
        command.append("--output-scope")
    if args.warning_both:
        command.append("--warning-both")
    if args.hotplug:
        command.append("--hotplug")
        command.extend(['--hotplug-connector', args.hotplug_connector or 'HDMI'])
    if args.unknown_focus:
        command.append("--unknown-focus")
    if args.lock_cycle:
        command.append("--lock-cycle")
    if args.suspend_cycle:
        command.append("--suspend-cycle")
    try:
        if args.record_dir:
            from direct_recording import Recorder, event
            metadata = {'bootID': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                        'kernel': os.uname().release, 'terminal': vt,
                        'nvidiaVersion': (Path('/proc/driver/nvidia/version').read_text()
                                          if Path('/proc/driver/nvidia/version').exists() else None),
                        'probeBuild': json.loads((folder / 'build-info.json').read_text()),
                        'scenario': 'suspend-cycle' if args.suspend_cycle else 'lock-cycle' if args.lock_cycle else 'hotplug' if args.hotplug else 'other',
                        'connector': None if (args.lock_cycle or args.suspend_cycle) else args.hotplug_connector or 'HDMI',
                        'seconds': args.seconds,
                        'sources': {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                    for name in ('run_input_direct.py', 'direct_hotplug.py',
                                                 'direct_recording.py', 'direct_warning.py', 'direct_lock.py', 'direct_suspend.py',
                                                 'direct_output_scope.py', 'direct_vt.py',
                                                 'direct_output_scope_stage.qml')}}
            # No symlink and no long socket: only durable artifacts use the project path.
            with tempfile.TemporaryDirectory(prefix='owg-dr-', dir='/tmp') as runtime:
                command.extend(['--runtime-dir', runtime])
                with Recorder(directory, metadata) as recorder:
                    result = run_private_session(command, env, args.seconds + 60, recorder)
                    event(directory, 'private-session-exited', exitCode=result)
                    for line in (directory / 'runner.log').read_text(errors='replace').splitlines()[-8:]:
                        print(line, flush=True)
                    recorder.check()
                    return result
        return run_private_session(command, env, args.seconds + 60)
    finally:
        print("DIRECT_ARTIFACTS:", directory, flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("DIRECT_REFUSED_OR_FAILED:", exc, file=sys.stderr)
        sys.exit(1)
