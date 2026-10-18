#!/usr/bin/env python3
"""Run the passive KWin probe in a private D-Bus + virtual Wayland session."""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

ROOT = pathlib.Path(__file__).resolve().parents[2]


class CompanionMock(dbus.service.Object):
    def __init__(self, allow_owned_test_move=False):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.name = dbus.service.BusName("org.oledwindowguard.ProbeCompanion", self.bus)
        super().__init__(self.bus, "/org/oledwindowguard/ProbeCompanion")
        self.count = 0
        self.requests = []
        self.allow_owned_test_move = allow_owned_test_move
        self.loop = GLib.MainLoop()
        threading.Thread(target=self.loop.run, daemon=True).start()

    @dbus.service.method("org.oledwindowguard.ProbeCompanion", in_signature="sii", out_signature="s",
                          async_callbacks=("reply", "error"))
    def Exchange(self, epoch, request_id, revision, reply, error):
        self.count += 1
        self.requests.append((str(epoch), int(request_id), int(revision)))
        number = self.count
        print(f"MOCK_EXCHANGE count={number} request={request_id}", flush=True)
        if number == 2:
            GLib.timeout_add(2500, lambda: (reply("delayed-reply"), GLib.SOURCE_REMOVE)[1])
        elif number == 3:
            # A companion that never answers must not retain its QML call forever.
            pass
        elif number == 4:
            error(dbus.exceptions.DBusException("org.oledwindowguard.ProbeCompanion.Failed",
                                                "intentional probe failure"))
        else:
            reply("move-owned-test" if self.allow_owned_test_move else "probe-ack")


def worker(temp, host_wayland=None, allow_owned_test_move=False):
    env = os.environ.copy()
    env.update({
        "XDG_CONFIG_HOME": str(temp / "config"),
        "XDG_DATA_HOME": str(temp / "data"),
        "XDG_CACHE_HOME": str(temp / "cache"),
        "XDG_RUNTIME_DIR": str(temp / "runtime"),
        "QT_QPA_PLATFORM": "wayland",
        "KDE_SESSION_VERSION": "6",
        "QT_ASSUME_STDERR_HAS_CONSOLE": "1",
        "QT_FORCE_STDERR_LOGGING": "1",
        "QT_LOGGING_RULES": "kwin_scripting.debug=true;qml.debug=true",
        "QSG_USE_SIMPLE_ANIMATION_DRIVER": "1",
    })
    runtime = pathlib.Path(env["XDG_RUNTIME_DIR"])
    runtime.mkdir(mode=0o700)
    (temp / "config").mkdir()
    (temp / "data").mkdir()
    (temp / "cache").mkdir()
    companion = CompanionMock(allow_owned_test_move)
    print("MOCK_COMPANION: org.oledwindowguard.ProbeCompanion ready")
    install = subprocess.run(
        ["kpackagetool6", "--type", "KWin/Script", "--install", str(ROOT / "probes/runtime")],
        env=env, text=True, capture_output=True, check=True,
    )
    print("PACKAGE_INSTALL:", install.stdout.strip())
    kwin_log = (temp / "kwin.log").open("w")
    kwin_command = ["kwin_wayland"]
    if host_wayland:
        kwin_command.extend(["--wayland-display", host_wayland, "--socket", "wayland-0"])
    else:
        kwin_command.append("--virtual")
    kwin_command.extend(["--width", "900", "--height", "650", "--scale", "1",
                         "--output-count", "1", "--no-lockscreen"])
    kwin = subprocess.Popen(kwin_command, env=env, stdout=kwin_log, stderr=subprocess.STDOUT)
    owned_client = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if kwin.poll() is not None:
                raise RuntimeError(f"kwin_wayland exited {kwin.returncode}")
            probe = subprocess.run([
                "gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                "--object-path", "/Scripting",
            ], env=env, text=True, capture_output=True)
            if probe.returncode == 0 and "loadScript" in probe.stdout:
                print("SCRIPTING_INTROSPECTION:\n" + probe.stdout.strip())
                break
            time.sleep(.25)
        else:
            raise RuntimeError("KWin scripting D-Bus object did not appear")
        client_env = env.copy()
        client_env["WAYLAND_DISPLAY"] = "wayland-0"
        client_log = (temp / "owned-window.log").open("w")
        owned_client = subprocess.Popen([sys.executable, str(ROOT / "tests/runtime/owned_window.py")],
                                        env=client_env, stdout=client_log, stderr=subprocess.STDOUT)
        time.sleep(1)
        if owned_client.poll() is not None:
            raise RuntimeError("owned QQuickWindow did not start in nested Wayland session")
        script = str(temp / "data/kwin/scripts/oledwindowguard-runtime-probe/contents/ui/main.qml")

        def call(*args):
            result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                     "--object-path", "/Scripting", "--method",
                                     "org.kde.kwin.Scripting." + args[0], *args[1:]],
                                    env=env, text=True, capture_output=True, check=True)
            print("DBUS", args[0] + ":", result.stdout.strip())
            return result.stdout.strip()

        loaded_id = call("loadDeclarativeScript", script, "oledwindowguard-runtime-probe")
        script_id = re.search(r"\((\d+),\)", loaded_id).group(1)
        call_script = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                      "--object-path", "/Scripting/Script" + script_id,
                                      "--method", "org.kde.kwin.Script.run"], env=env,
                                     text=True, capture_output=True, check=True)
        print("DBUS Script.run:", call_script.stdout.strip())
        loaded = call("isScriptLoaded", "oledwindowguard-runtime-probe")
        if loaded != "(true,)":
            raise RuntimeError("declarative probe failed to stay loaded after initialization")
        time.sleep(11.2)
        if companion.count < 5:
            raise RuntimeError(f"expected success/delayed/failure exchanges, got {companion.count}")
        print("FIRST_INSTANCE_EXCHANGES:", companion.requests)
        call("unloadScript", "oledwindowguard-runtime-probe")
        time.sleep(.5)
        if kwin.poll() is not None:
            raise RuntimeError(f"KWin exited after first script unload: {kwin.returncode}")
        loaded = call("isScriptLoaded", "oledwindowguard-runtime-probe")
        if loaded != "(false,)":
            raise RuntimeError("probe remained loaded after unload")
        old_count = companion.count
        time.sleep(1.4)
        if companion.count != old_count:
            raise RuntimeError("unloaded script continued making companion exchanges")
        loaded_id = call("loadDeclarativeScript", script, "oledwindowguard-runtime-probe")
        script_id = re.search(r"\((\d+),\)", loaded_id).group(1)
        call_script = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                      "--object-path", "/Scripting/Script" + script_id,
                                      "--method", "org.kde.kwin.Script.run"], env=env,
                                     text=True, capture_output=True, check=True)
        print("DBUS Script.run:", call_script.stdout.strip())
        loaded = call("isScriptLoaded", "oledwindowguard-runtime-probe")
        if loaded != "(true,)":
            raise RuntimeError("probe failed to reload")
        before_reload_exchange = companion.count
        time.sleep(1.4)
        if companion.count != before_reload_exchange + 1:
            raise RuntimeError("reload did not create exactly one exchange timer")
        call("unloadScript", "oledwindowguard-runtime-probe")
        time.sleep(.5)
        if kwin.poll() is not None:
            raise RuntimeError(f"KWin exited after final script unload: {kwin.returncode}")
        if call("isScriptLoaded", "oledwindowguard-runtime-probe") != "(false,)":
            raise RuntimeError("probe remained loaded after final unload")
        companion.loop.quit()
        return 0
    finally:
        if owned_client is not None:
            owned_client.terminate()
            try:
                owned_client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                owned_client.kill()
                owned_client.wait(timeout=2)
        kwin.terminate()
        try:
            kwin.wait(timeout=8)
        except subprocess.TimeoutExpired:
            kwin.kill()
            kwin.wait(timeout=3)
        kwin_log.close()
        print("KWIN_EXIT:", kwin.returncode)
        log = (temp / "kwin.log").read_text(errors="replace")
        relevant = [line for line in log.splitlines()
                    if re.search(r"probe|qml.*(error|warn)|(?:error|warning).*qml|script.*(?:error|warn)", line, re.I)]
        print("KWIN_RELEVANT_LOG_LINES:\n" + "\n".join(relevant))
        expected_log_markers = (
            "started epoch=",
            "exchange sent request=",
            '"accepted":true,"ok":true',
            "exchange timeout request=",
            "exchange stale reply request=2",
            "expired exchange released request=3",
            '"accepted":true,"ok":false,"reason":"failed"',
            "shortcut registration unavailable on declarative KWin wrapper",
        )
        missing = [marker for marker in expected_log_markers if marker not in log]
        if not missing:
            print("RUNTIME_LOG_ASSERTIONS: success, timeout, failure, shortcut-unavailable passed")
        else:
            raise RuntimeError("missing KWin runtime log outcomes: " + ", ".join(missing))
        if allow_owned_test_move:
            move_markers = (
                "owned move requested identity=",
                "owned move readback accepted",
                "owned resize requested identity=",
                "owned resize readback accepted",
                "owned resize restore requested identity=",
                "owned restore readback accepted",
                "focusInvariant=passed desktopInvariant=passed",
            )
            missing_move = [marker for marker in move_markers if marker not in log]
            if missing_move:
                raise RuntimeError("owned geometry test did not pass safely: " + ", ".join(missing_move))
            print("OWNED_GEOMETRY_ASSERTIONS: exact owned-window move+resize/readback/restore and invariants passed")
        print("KWIN_LOG_TAIL:\n" + "\n".join(log.splitlines()[-25:]))
        if (temp / "owned-window.log").exists():
            print("OWNED_WINDOW_LOG:\n" + (temp / "owned-window.log").read_text(errors="replace"))


def main():
    directory = tempfile.mkdtemp(prefix="owg-runtime-probe-")
    worker_args = ["--worker", directory]
    options = set(sys.argv[1:])
    if options - {"--windowed", "--allow-owned-test-move"}:
        raise RuntimeError("unknown options: " + ", ".join(sorted(options - {"--windowed", "--allow-owned-test-move"})))
    allow_move = "--allow-owned-test-move" in options
    if allow_move:
        worker_args.append("--allow-owned-test-move")
    if "--windowed" in options:
        host_runtime = os.environ.get("XDG_RUNTIME_DIR")
        host_display = os.environ.get("WAYLAND_DISPLAY")
        if not host_runtime or not host_display:
            raise RuntimeError("host Wayland session is unavailable")
        host_socket = pathlib.Path(host_display)
        if not host_socket.is_absolute():
            host_socket = pathlib.Path(host_runtime) / host_socket
        if not host_socket.is_socket():
            raise RuntimeError(f"host Wayland socket not found: {host_socket}")
        worker_args.extend(["--host-wayland", str(host_socket)])
    result = subprocess.run(["dbus-run-session", "--", sys.executable, __file__, *worker_args],
                            text=True)
    if result.returncode == 0:
        shutil.rmtree(directory)
    else:
        print("FAILED_RUN_ARTIFACTS:", directory)
    return result.returncode


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        remaining = sys.argv[3:]
        host_wayland = None
        allow_move = False
        if "--host-wayland" in remaining:
            index = remaining.index("--host-wayland")
            host_wayland = remaining[index + 1]
        if "--allow-owned-test-move" in remaining:
            allow_move = True
        sys.exit(worker(pathlib.Path(sys.argv[2]), host_wayland, allow_move))
    sys.exit(main())
