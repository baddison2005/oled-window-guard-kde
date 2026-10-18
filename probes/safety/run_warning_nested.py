#!/usr/bin/env python3
"""Run only the default-off warning probe in a private virtual KWin session."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "probes/safety/warning"


def run_worker(root):
    env = os.environ.copy()
    env.update({
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_RUNTIME_DIR": str(root / "runtime"),
        "QT_QPA_PLATFORM": "wayland",
        "KDE_SESSION_VERSION": "6",
        "QT_LOGGING_TO_CONSOLE": "1",
        "QT_LOGGING_RULES": "kwin_scripting.debug=true;qml.debug=true",
        "QSG_USE_SIMPLE_ANIMATION_DRIVER": "1",
    })
    for directory in ("config", "data", "cache"):
        (root / directory).mkdir()
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)

    installed = subprocess.run(["kpackagetool6", "--type", "KWin/Script", "--install", str(PACKAGE)],
                               env=env, text=True, capture_output=True)
    if installed.returncode:
        raise RuntimeError("KPackage install failed: " + installed.stderr.strip())
    print("PACKAGE_INSTALL:", installed.stdout.strip(), flush=True)
    kwin_log_path = root / "kwin.log"
    kwin_log = kwin_log_path.open("w")
    kwin = subprocess.Popen([
        "kwin_wayland", "--virtual", "--width", "900", "--height", "650",
        "--scale", "1", "--output-count", "1", "--no-lockscreen",
    ], env=env, stdout=kwin_log, stderr=subprocess.STDOUT)
    owned = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if kwin.poll() is not None:
                raise RuntimeError(f"nested KWin exited with status {kwin.returncode}")
            introspection = subprocess.run([
                "gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                "--object-path", "/Scripting",
            ], env=env, text=True, capture_output=True)
            if introspection.returncode == 0 and "loadDeclarativeScript" in introspection.stdout:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("nested KWin scripting D-Bus object did not appear")

        client_env = env.copy()
        client_env["WAYLAND_DISPLAY"] = "wayland-0"
        owned_log = (root / "owned-window.log").open("w")
        owned = subprocess.Popen([sys.executable, str(ROOT / "tests/runtime/owned_window.py")],
                                 env=client_env, stdout=owned_log, stderr=subprocess.STDOUT)
        time.sleep(1.5)
        if owned.poll() is not None:
            raise RuntimeError("owned QQuickWindow failed to start")

        main_script = (root / "data/kwin/scripts/oledwindowguard-safety-warning-probe/contents/ui/main.qml")
        loaded = subprocess.run([
            "gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Scripting",
            "--method", "org.kde.kwin.Scripting.loadDeclarativeScript", str(main_script),
            "oledwindowguard-safety-warning-probe",
        ], env=env, text=True, capture_output=True, check=True)
        print("LOAD:", loaded.stdout.strip(), flush=True)
        match = re.search(r"\((\d+),\)", loaded.stdout)
        if not match:
            raise RuntimeError(f"unable to parse KWin script id: {loaded.stdout}")
        script_path = "/Scripting/Script" + match.group(1)
        run_script = subprocess.run([
            "gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", script_path,
            "--method", "org.kde.kwin.Script.run",
        ], env=env, text=True, capture_output=True, check=True)
        print("SCRIPT_RUN:", run_script.stdout.strip(), flush=True)
        state = subprocess.run([
            "gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Scripting",
            "--method", "org.kde.kwin.Scripting.isScriptLoaded", "oledwindowguard-safety-warning-probe",
        ], env=env, text=True, capture_output=True, check=True)
        print("SCRIPT_LOADED:", state.stdout.strip(), flush=True)
        time.sleep(3.0)
        subprocess.run([
            "gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Scripting",
            "--method", "org.kde.kwin.Scripting.unloadScript", "oledwindowguard-safety-warning-probe",
        ], env=env, text=True, capture_output=True, check=True)
        time.sleep(.5)
        if kwin.poll() is not None:
            raise RuntimeError(f"KWin exited after warning script unload: {kwin.returncode}")

        kwin.terminate()
        kwin.wait(timeout=8)
        kwin_log.close()
        log = kwin_log_path.read_text(errors="replace")
        relevant = [line for line in log.splitlines() if "owg-warning-probe:" in line]
        print("WARNING_PROBE_LOG:\n" + "\n".join(relevant), flush=True)
        if not any("RESULT PASS" in line for line in relevant):
            raise RuntimeError("warning probe did not report RESULT PASS")
        if not any("shownManagedWarningWindows=1" in line for line in relevant):
            raise RuntimeError("warning probe did not confirm one managed warning window")
        if not any("pointerSkipDelivered=unverified" in line for line in relevant):
            raise RuntimeError("warning probe did not report the input limitation")
        return 0
    finally:
        if owned is not None:
            owned.terminate()
            try:
                owned.wait(timeout=3)
            except subprocess.TimeoutExpired:
                owned.kill()
                owned.wait(timeout=2)
        kwin.terminate()
        try:
            kwin.wait(timeout=8)
        except subprocess.TimeoutExpired:
            kwin.kill()
            kwin.wait(timeout=3)
        kwin_log.close()
        print("NESTED_KWIN_EXIT:", kwin.returncode, flush=True)
        if kwin_log_path.exists():
            tail = kwin_log_path.read_text(errors="replace").splitlines()[-50:]
            print("KWIN_LOG_TAIL:\n" + "\n".join(tail), flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix="owg-warning-probe-") as directory:
        return subprocess.run(["dbus-run-session", "--", sys.executable, __file__, "--worker", directory],
                              text=True).returncode


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        sys.exit(run_worker(pathlib.Path(sys.argv[2])))
    sys.exit(main())
