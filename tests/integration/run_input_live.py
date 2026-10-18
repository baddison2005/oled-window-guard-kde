#!/usr/bin/env python3
"""Opt-in hardware-path check in a visible, disposable nested Wayland KWin.

The helper never sends synthetic input or logs typed content. The user must
interact with its nested window. It does not alter host display configuration.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "tests/runtime/input_window.py"


def snapshot(env):
    result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                             "--object-path", "/org/oledwindowguard/InputProbe", "--method",
                             "org.oledwindowguard.InputProbe1.Snapshot"], env=env, text=True,
                            capture_output=True, check=True, timeout=3)
    return json.loads(ast.literal_eval(result.stdout.strip())[0])


def worker(directory):
    host_socket = os.environ["OWG_PARENT_WAYLAND_SOCKET"]
    plugin_dir = os.environ["OWG_INPUT_PROBE_PLUGIN_DIR"]
    env = os.environ.copy()
    env.update({"XDG_RUNTIME_DIR": str(directory / "runtime"),
                "XDG_CONFIG_HOME": str(directory / "config"),
                "XDG_DATA_HOME": str(directory / "data"),
                "XDG_CACHE_HOME": str(directory / "cache"),
                "WAYLAND_DISPLAY": host_socket,
                "QT_QPA_PLATFORM": "wayland", "QT_PLUGIN_PATH": plugin_dir,
                "OWG_INPUT_PROBE_ISOLATED": "1", "KDE_SESSION_VERSION": "6"})
    env.pop("OWG_INPUT_TEST_TRIGGER", None)
    for name in ("runtime", "config", "data", "cache"):
        (directory / name).mkdir(mode=0o700)
    (directory / "config/kwinrc").write_text("[Plugins]\nlibowg_input_probeEnabled=true\n")
    kwin_log = (directory / "kwin.log").open("w")
    kwin = subprocess.Popen(["kwin_wayland", "--wayland-display", host_socket,
                             "--socket", "owg-live-0", "--width", "700", "--height", "480",
                             "--output-count", "1", "--no-lockscreen"],
                            env=env, stdout=kwin_log, stderr=subprocess.STDOUT)
    client = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if kwin.poll() is not None:
                raise RuntimeError("nested KWin exited: " + (directory / "kwin.log").read_text()[-2500:])
            try:
                first = snapshot(env)
                break
            except subprocess.SubprocessError:
                time.sleep(.25)
        else:
            raise RuntimeError("nested KWin input endpoint did not appear")
        client_env = env.copy()
        client_env["WAYLAND_DISPLAY"] = "owg-live-0"
        client = subprocess.Popen([sys.executable, str(CLIENT)], env=client_env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(.5)
        if client.poll() is not None:
            raise RuntimeError("nested input client failed to open")
        baseline = snapshot(env)
        print("READY: interact with the OLED Window Guard input check window now for 30 seconds", flush=True)
        seen_held = False
        seen_released_after_hold = False
        final = baseline
        end = time.monotonic() + 30
        while time.monotonic() < end and kwin.poll() is None:
            current = snapshot(env)
            if current["buttonKnown"] and current["buttonsDown"]:
                seen_held = True
            if seen_held and current["buttonKnown"] and not current["buttonsDown"]:
                seen_released_after_hold = True
            final = current
            time.sleep(.07)
        if kwin.poll() is not None:
            raise RuntimeError("nested KWin exited during physical-input observation")
        deltas = {kind: final[kind] - baseline[kind]
                  for kind in ("pointer", "axis", "button", "keyboard")}
        print("HARDWARE_PATH_COUNTS:", json.dumps(deltas, sort_keys=True), flush=True)
        print("HELD_OBSERVED:", seen_held, "RELEASE_OBSERVED:", seen_released_after_hold,
              "OUTPUTS:", len(final["outputs"]), flush=True)
        if any(value <= 0 for value in deltas.values()) or not seen_released_after_hold:
            raise RuntimeError("manual input matrix incomplete; see counts above")
        print("HARDWARE_PATH_RESULT: PASS (host-delivered events in nested KWin)", flush=True)
    finally:
        if client and client.poll() is None:
            client.terminate()
            try:
                client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                client.kill()
        kwin.terminate()
        try:
            kwin.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kwin.kill()
            kwin.wait(timeout=3)
        kwin_log.close()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]))
        return
    if len(sys.argv) != 1:
        raise SystemExit("usage: run_input_live.py")
    plugin_dir = Path(os.environ.get("OWG_INPUT_PROBE_PLUGIN_DIR", "")).resolve()
    if not plugin_dir.is_relative_to(Path("/tmp")) or not (
            plugin_dir / "kwin/effects/plugins/libowg_input_probe.so").is_file():
        raise SystemExit("OWG_INPUT_PROBE_PLUGIN_DIR must point to the private /tmp probe build")
    host_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    host_socket = Path(os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
    if not host_socket.is_absolute():
        host_socket = host_runtime / host_socket
    if not host_socket.is_socket():
        raise SystemExit("host Wayland socket is unavailable")
    directory = Path(tempfile.mkdtemp(prefix="owg-input-live-"))
    env = os.environ.copy()
    env["OWG_PARENT_WAYLAND_SOCKET"] = str(host_socket)
    try:
        subprocess.run(["dbus-run-session", "--", sys.executable, "-u", __file__,
                        "--worker", str(directory)], env=env, check=True, timeout=70)
    finally:
        shutil.rmtree(directory)


if __name__ == "__main__":
    main()
