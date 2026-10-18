#!/usr/bin/env python3
"""Exercise the production warning surface in a disposable nested KWin session."""
import json
import os
import pathlib
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRODUCTION = ROOT / "packages/kwin-script/contents/ui/WarningSurface.qml"
PROBE = ROOT / "tests/integration/warning_surface_probe.qml"
PACKAGE_ID = "oledwindowguard-warning-surface-integration"


def verify_output_report(output, scale=None, position=None):
    """Verify compositor readback before input, rather than trusting KScreen."""
    if scale is not None:
        scales = [float(match.group(1)) for match in re.finditer(
            r"target-output geometry=.* scale=([0-9.]+)", output)]
        if len(scales) != 1 or abs(scales[0] - float(scale)) > .01:
            raise RuntimeError("target output did not expose configured fractional scale")
    if position is not None:
        positions = [tuple(map(int, match.groups())) for match in re.finditer(
            r"target-output geometry=KWin::Rect\((-?\d+), (-?\d+),", output)]
        if len(positions) != 1 or positions[0] != tuple(map(int, position.split(","))):
            raise RuntimeError("target output did not expose configured position")


def worker(temp):
    physical_mode = os.environ.get("OWG_WARNING_PHYSICAL") == "1"
    windowed_mode = os.environ.get("OWG_WARNING_WINDOWED") == "1"
    pass_through_mode = os.environ.get("OWG_WARNING_PASS_THROUGH") == "1"
    input_pass_through_mode = os.environ.get("OWG_WARNING_INPUT_PASS_THROUGH") == "1"
    auto_pass_through_mode = os.environ.get("OWG_WARNING_AUTO_PASS_THROUGH") == "1"
    force_dismiss_mode = os.environ.get("OWG_WARNING_FORCE_DISMISS") == "1"
    window_backend = os.environ.get("OWG_WARNING_WINDOW_BACKEND", "wayland")
    output_count = int(os.environ.get("OWG_WARNING_OUTPUT_COUNT", "1"))
    target_output = int(os.environ.get("OWG_WARNING_TARGET_OUTPUT", "0"))
    owned_on_target = target_output > 0 and (
        physical_mode or os.environ.get("OWG_WARNING_OWNED_ON_TARGET") == "1")
    configure_scale = os.environ.get("OWG_WARNING_CONFIGURE_SCALE")
    configure_position = os.environ.get("OWG_WARNING_CONFIGURE_POSITION")
    if window_backend not in ("wayland", "x11"):
        raise RuntimeError("invalid private window backend")
    if window_backend == "x11" and not (physical_mode or windowed_mode):
        raise RuntimeError("X11 parent requires a windowed nested probe")
    if window_backend == "x11" and output_count != 1:
        raise RuntimeError("X11-parent warning check currently supports one private output")
    output_prefix = "X11-" if window_backend == "x11" else (
        "WL-" if physical_mode or windowed_mode else "Virtual-")
    if not 1 <= output_count <= 3 or not 0 <= target_output < output_count:
        raise RuntimeError("invalid warning output-count or target-output")
    if physical_mode and output_count not in (1, 2):
        raise RuntimeError("hand-driven warning probe supports one or two nested outputs")
    if configure_scale is not None and not 0.75 <= float(configure_scale) <= 2.25:
        raise RuntimeError("invalid private output scale configuration")
    if configure_position is not None:
        coordinates = re.fullmatch(r"(-?\d{1,4}),(-?\d{1,4})", configure_position)
        if not coordinates or any(abs(int(value)) > 4096 for value in coordinates.groups()):
            raise RuntimeError("invalid private output position configuration")
    if physical_mode and windowed_mode:
        raise RuntimeError("hand-driven and automatic windowed modes are exclusive")
    if pass_through_mode and not physical_mode:
        raise RuntimeError("outside-warning pass-through requires hand-driven physical mode")
    if input_pass_through_mode and not pass_through_mode:
        raise RuntimeError("scroll/key pass-through requires outside-click mode")
    if force_dismiss_mode and (physical_mode or pass_through_mode):
        raise RuntimeError("forced dismissal runs only in the virtual warning probe")
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
    input_probe_plugins = os.environ.get("OWG_INPUT_PROBE_PLUGIN_DIR")
    if auto_pass_through_mode and (physical_mode or not windowed_mode or
                                   force_dismiss_mode or not input_probe_plugins):
        raise RuntimeError("automatic outside-input check requires a private windowed probe")
    if input_probe_plugins:
        env["QT_PLUGIN_PATH"] = input_probe_plugins
        env["OWG_INPUT_PROBE_ISOLATED"] = "1"
        (temp / "config/kwinrc").write_text("[Plugins]\nlibowg_input_probeEnabled=true\n")
        if not physical_mode:
            env["OWG_INPUT_TEST_TRIGGER"] = str(temp / "input-trigger")
            (temp / "input-trigger").write_text("")
        else:
            env.pop("OWG_INPUT_TEST_TRIGGER", None)
    runtime = temp / "runtime"
    runtime.mkdir(mode=0o700)

    package = temp / "package"
    ui = package / "contents/ui"
    ui.mkdir(parents=True)
    code = package / "contents/code"
    code.mkdir(parents=True)
    for module in ("model.js", "registry.js", "planner.js", "safety.js", "transaction.js"):
        shutil.copy2(ROOT / "core" / module, code / module)
    (ui / "core").mkdir()
    shutil.copy2(ROOT / "core/warning_clock.js", ui / "core/warning_clock.js")
    shutil.copy2(PRODUCTION, ui / "WarningSurface.qml")
    probe_source = PROBE.read_text().replace("screens[0]", f"screens[{target_output}]")
    if owned_on_target:
        probe_source = probe_source.replace(
            "property bool positionOwnedOnTarget: false",
            "property bool positionOwnedOnTarget: true")
    if input_probe_plugins or force_dismiss_mode:
        simulated_skip = "if (generation === 17) Qt.callLater(function() { presenter.onSkip() })"
        if probe_source.count(simulated_skip) != 1:
            raise RuntimeError("warning probe no longer has the expected callback-only form")
        if force_dismiss_mode:
            probe_source = probe_source.replace(
                simulated_skip,
                "if (generation === 17) Qt.callLater(function() { presenter.warning.visible = false })")
        else:
            if probe_source.count("pointerSkipDelivered=unverified") != 1:
                raise RuntimeError("warning probe no longer has the expected callback marker")
            probe_source = probe_source.replace(
                simulated_skip,
                "if (generation === 17) { /* wait for compositor pointer click */ }").replace(
                'pointerSkipDelivered=unverified', 'pointerSkipDelivered=observed')
        if physical_mode:
            probe_source = probe_source.replace(
                "presenter.showForOutput(17, probe.outputBounds, 6000)",
                "presenter.showForOutput(17, probe.outputBounds, 45000)")
    (ui / "main.qml").write_text(probe_source)
    metadata = {
        "KPackageStructure": "KWin/Script",
        "KPlugin": {
            "Authors": [{"Name": "OLED Window Guard contributors"}],
            "Category": "Utilities", "Description": "Private warning surface integration probe",
            "EnabledByDefault": False, "Icon": "dialog-information", "Id": PACKAGE_ID,
            "License": "GPL-2.0-or-later", "Name": "OLED Window Guard Warning Surface Probe",
            "Version": "0.1.0",
        },
        "X-Plasma-API": "declarativescript",
        "X-Plasma-API-Minimum-Version": "6.0",
        "X-Plasma-MainScript": "ui/main.qml",
    }
    (package / "metadata.json").write_text(json.dumps(metadata, indent=2))
    installed = subprocess.run(["kpackagetool6", "--type", "KWin/Script", "--install", str(package)],
                               env=env, text=True, capture_output=True, timeout=10)
    if installed.returncode:
        raise RuntimeError("temporary KPackage install failed: " + installed.stderr.strip())
    print("PACKAGE_INSTALL:", installed.stdout.strip(), flush=True)

    log_path = temp / "kwin.log"
    log = log_path.open("w")
    kwin_args = ["kwin_wayland", "--width", "900", "--height", "650",
                 "--scale", "1", "--output-count", str(output_count), "--no-lockscreen"]
    if physical_mode or windowed_mode:
        kwin_args += ["--socket", "owg-warning-0"]
        if window_backend == "x11":
            kwin_args += ["--x11-display", os.environ["OWG_PARENT_X11_DISPLAY"]]
            env.pop("WAYLAND_DISPLAY", None)
        else:
            kwin_args += ["--wayland-display", os.environ["OWG_PARENT_WAYLAND_SOCKET"]]
            env["WAYLAND_DISPLAY"] = os.environ["OWG_PARENT_WAYLAND_SOCKET"]
    else:
        kwin_args.append("--virtual")
    kwin = subprocess.Popen(kwin_args,
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    owned = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if kwin.poll() is not None:
                raise RuntimeError("nested KWin exited before readiness")
            ready = subprocess.run(["gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                                    "--object-path", "/Scripting"], env=env, text=True,
                                   capture_output=True, timeout=5)
            if ready.returncode == 0 and "loadDeclarativeScript" in ready.stdout:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("nested KWin scripting interface did not appear")

        client_env = env.copy()
        client_env["WAYLAND_DISPLAY"] = "owg-warning-0" if physical_mode or windowed_mode else "wayland-0"
        if configure_scale is not None or configure_position is not None:
            doctor = subprocess.run(["kscreen-doctor", "-o"], env=client_env,
                                    text=True, capture_output=True, timeout=15, check=False)
            if doctor.returncode:
                raise RuntimeError("private kscreen-doctor output enumeration failed: " +
                                   doctor.stderr.strip()[:400])
            plain_output = re.sub(r"\x1b\[[0-9;]*m", "", doctor.stdout)
            output_ids = dict((name, output_id) for output_id, name in
                              re.findall(r"Output:\s*(\d+)\s+(\S+)", plain_output))
            print("KSCREEN_OUTPUTS:", output_ids, flush=True)
            target_name = output_prefix + str(target_output)
            if target_name not in output_ids:
                raise RuntimeError("private kscreen-doctor did not enumerate " + target_name)
            for property_name, value in (("scale", configure_scale), ("position", configure_position)):
                if value is None:
                    continue
                change = subprocess.run(
                    ["kscreen-doctor", f"output.{output_ids[target_name]}.{property_name}.{value}"],
                    env=client_env, text=True, capture_output=True, timeout=15, check=False)
                print("KSCREEN_" + property_name.upper() + ":", change.returncode,
                      change.stdout.strip()[:400], change.stderr.strip()[:400], flush=True)
                if change.returncode or "applying config failed" in (change.stdout + change.stderr).lower():
                    raise RuntimeError("private kscreen-doctor " + property_name + " change failed")
            time.sleep(.4)
        if shutil.which("wayland-info"):
            protocols = subprocess.run(["wayland-info"], env=client_env, text=True,
                                       capture_output=True, timeout=5, check=False)
            globals_of_interest = [line.strip() for line in protocols.stdout.splitlines()
                                   if any(name in line.lower() for name in
                                          ("fake_input", "virtual_pointer", "virtual_keyboard", "ei_"))]
            print("INPUT_PROTOCOL_GLOBALS:", globals_of_interest,
                  "wayland-info exit:", protocols.returncode,
                  "stderr:", protocols.stderr.strip()[:240], flush=True)
        # A disposable owned focusable window gives the warning check a concrete
        # activeWindow identity. Its geometry setup is confined to this private
        # nested session; the warning component never calls a window writer.
        client_script = temp / "owned_focus_probe.py"
        client_script.write_text('''from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PySide6.QtCore import QTimer, QObject, QEvent, Qt
app = QApplication([])
QApplication.setDesktopFileName("oled-window-guard-focus-test")
class InputLog(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            print("OWNED_CLICK", type(obj).__name__, flush=True)
        elif event.type() == QEvent.Type.Wheel:
            print("OWNED_WHEEL", flush=True)
        elif event.type() == QEvent.Type.KeyPress:
            print("OWNED_KEY", flush=True)
        return False
input_log = InputLog()
app.installEventFilter(input_log)
window = QWidget()
window.setWindowTitle("OWG owned focus probe")
window.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
layout = QVBoxLayout(window)
layout.addWidget(QLabel("Owned test window: click this dark area below the warning, then wait."))
window.setStyleSheet("background: #334455; color: white; font-size: 18px;")
window.showMaximized()
window.activateWindow()
window.setFocus()
QTimer.singleShot(1500, lambda: print("OWNED_GEOMETRY", window.geometry(), window.frameGeometry(), flush=True))
QTimer.singleShot(30000, app.quit)
app.exec()
''')
        if physical_mode:
            client_script.write_text(client_script.read_text().replace(
                "QTimer.singleShot(30000, app.quit)", "QTimer.singleShot(90000, app.quit)"))
        owned_log = (temp / "owned-window.log").open("w")
        owned = subprocess.Popen([sys.executable, str(client_script)], env=client_env,
                                 stdout=owned_log, stderr=subprocess.STDOUT)
        time.sleep(1.0)
        if owned.poll() is not None:
            raise RuntimeError("owned test windows failed to start")
        def call(method, *args):
            return subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                   "--object-path", "/Scripting", "--method",
                                   "org.kde.kwin.Scripting." + method, *args], env=env,
                                  check=True, text=True, capture_output=True, timeout=5).stdout.strip()

        loaded = call("loadDeclarativeScript",
                      str(temp / "data/kwin/scripts" / PACKAGE_ID / "contents/ui/main.qml"), PACKAGE_ID)
        print("LOAD:", loaded, flush=True)
        match = re.search(r"\((\d+),\)", loaded)
        if not match:
            raise RuntimeError("KWin did not return script ID: " + loaded)
        script_path = "/Scripting/Script" + match.group(1)
        subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", script_path,
                        "--method", "org.kde.kwin.Script.run"], env=env,
                       check=True, text=True, capture_output=True, timeout=5)

        end = time.monotonic() + (60 if physical_mode else 18)
        click_requested = False
        outside_delivered = False
        input_delivered = False
        owned_clicks_at_show = 0
        owned_wheels_at_show = 0
        owned_keys_at_show = 0
        auto_stage = "await-show"
        while time.monotonic() < end:
            if kwin.poll() is not None:
                raise RuntimeError("nested KWin exited during warning test")
            current_log = log_path.read_text(errors="replace")
            if "shown generation=17 count=1 focusUnchanged=true" in current_log:
                verify_output_report(current_log, configure_scale, configure_position)
            if input_probe_plugins and not physical_mode and "shown generation=17 count=1 focusUnchanged=true" in current_log:
                if auto_pass_through_mode and auto_stage == "await-show":
                    owned_events = (temp / "owned-window.log").read_text(errors="replace")
                    owned_clicks_at_show = owned_events.count("OWNED_CLICK")
                    owned_wheels_at_show = owned_events.count("OWNED_WHEEL")
                    owned_keys_at_show = owned_events.count("OWNED_KEY")
                    pathlib.Path(env["OWG_INPUT_TEST_TRIGGER"]).write_text("outside-click\n")
                    auto_stage = "click-requested"
                elif not auto_pass_through_mode and not click_requested:
                    pathlib.Path(env["OWG_INPUT_TEST_TRIGGER"]).write_text("click-warning\n")
                    click_requested = True
            if auto_pass_through_mode and auto_stage == "click-requested":
                owned_events = (temp / "owned-window.log").read_text(errors="replace")
                if owned_events.count("OWNED_CLICK") > owned_clicks_at_show:
                    if "skip callback generation=17" in current_log or "hidden generation=17" in current_log:
                        raise RuntimeError("outside click dismissed warning before Skip")
                    pathlib.Path(env["OWG_INPUT_TEST_TRIGGER"]).write_text("outside-key\n")
                    auto_stage = "key-requested"
            if auto_pass_through_mode and auto_stage == "key-requested":
                owned_events = (temp / "owned-window.log").read_text(errors="replace")
                if owned_events.count("OWNED_KEY") > owned_keys_at_show:
                    if "skip callback generation=17" in current_log or "hidden generation=17" in current_log:
                        raise RuntimeError("outside key dismissed warning before Skip")
                    print("AUTO_OUTSIDE_INPUT: owned client received click and key while warning stayed up",
                          flush=True)
                    pathlib.Path(env["OWG_INPUT_TEST_TRIGGER"]).write_text("click-warning\n")
                    auto_stage = "skip-requested"
                    click_requested = True
            if pass_through_mode and click_requested and not outside_delivered and (
                    (temp / "owned-window.log").read_text(errors="replace").count(
                        "OWNED_CLICK") > owned_clicks_at_show):
                if "skip callback generation=17" in current_log or "hidden generation=17" in current_log:
                    raise RuntimeError("outside click closed or skipped the warning")
                outside_delivered = True
                print("OUTSIDE_CLICK: owned client received click while warning remained visible", flush=True)
                print("READY: scroll and type one key in the dark owned area" if input_pass_through_mode else
                      "READY: now click Skip in the nested warning", flush=True)
            if input_pass_through_mode and outside_delivered and not input_delivered:
                owned_events = (temp / "owned-window.log").read_text(errors="replace")
                if (owned_events.count("OWNED_WHEEL") > owned_wheels_at_show and
                        owned_events.count("OWNED_KEY") > owned_keys_at_show):
                    if "skip callback generation=17" in current_log or "hidden generation=17" in current_log:
                        raise RuntimeError("warning closed before owned scroll/key delivery")
                    input_delivered = True
                    print("OUTSIDE_INPUT: owned client received wheel and key while warning remained visible", flush=True)
                    print("READY: now click Skip in the nested warning", flush=True)
            if physical_mode and not click_requested and "shown generation=17 count=1 focusUnchanged=true" in current_log:
                owned_events = (temp / "owned-window.log").read_text(errors="replace")
                owned_clicks_at_show = owned_events.count("OWNED_CLICK")
                owned_wheels_at_show = owned_events.count("OWNED_WHEEL")
                owned_keys_at_show = owned_events.count("OWNED_KEY")
                print(("READY: click the dark owned client below the warning in the second nested window"
                       if target_output else "READY: click the owned test window below the warning first")
                      if pass_through_mode else
                      "READY: click Skip in the nested OLED Window Guard warning window", flush=True)
                click_requested = True
            if "RESULT PASS" in current_log or "RESULT FAIL" in current_log:
                break
            time.sleep(.2)
        else:
            raise RuntimeError("warning surface did not finish within the allotted time")
        if pass_through_mode and not outside_delivered:
            if "reason=warning-unexpectedly-hidden" in current_log:
                raise RuntimeError("warning disappeared on outside click before delivery to owned client")
            raise RuntimeError("outside-warning click never reached the owned client")
        if input_pass_through_mode and not input_delivered:
            raise RuntimeError("outside-warning wheel/key never reached the owned client")
        if auto_pass_through_mode and auto_stage != "skip-requested":
            raise RuntimeError("automatic outside-input sequence never reached owned client")
        if force_dismiss_mode:
            time.sleep(6.5)  # The original six-second warning expiry must stay cancelled.

        call("unloadScript", PACKAGE_ID)
        time.sleep(.4)
        if kwin.poll() is not None:
            raise RuntimeError("nested KWin exited after script unload")
        if call("isScriptLoaded", PACKAGE_ID) != "(false,)":
            raise RuntimeError("warning surface script did not unload cleanly")
        print("SCRIPT_UNLOAD: passed", flush=True)
    finally:
        if owned:
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
        log.close()
        print("NESTED_KWIN_EXIT:", kwin.returncode, flush=True)
    output = log_path.read_text(errors="replace")
    relevant = [line for line in output.splitlines() if "owg-warning-surface:" in line]
    print("WARNING_SURFACE_LOG:\n" + "\n".join(relevant), flush=True)
    verify_output_report(output, configure_scale, configure_position)
    if force_dismiss_mode:
        if not any("RESULT FAIL generation=17 reason=warning-unexpectedly-hidden" in line
                   for line in relevant):
            raise RuntimeError("unexpected warning dismissal did not fail closed")
        if any("skip callback generation=17" in line or "hidden generation=17" in line or
               "expired generation=17" in line or
               "RESULT PASS" in line for line in relevant):
            raise RuntimeError("unexpected warning dismissal continued normal lifecycle")
        return 0
    if input_probe_plugins:
        input_lines = [line for line in output.splitlines() if "OWG_INPUT" in line]
        if physical_mode:
            counts = {kind: sum("OWG_INPUT " + kind in line for line in input_lines)
                      for kind in ("pointer", "button", "axis", "key")}
            print("INPUT_OBSERVER_COUNTS:", json.dumps(counts, sort_keys=True), flush=True)
            print("INPUT_BUTTON_LOG:\n" + "\n".join(
                line for line in input_lines if "OWG_INPUT button" in line), flush=True)
        else:
            print("INPUT_OBSERVER_LOG:\n" + "\n".join(input_lines), flush=True)
        for marker in (("OWG_INPUT installed", "OWG_INPUT button") if physical_mode else
                       ("OWG_INPUT installed", "OWG_INPUT injecting compositor pointer click", "OWG_INPUT button")):
            if not any(marker in line for line in input_lines):
                raise RuntimeError("missing input observer assertion: " + marker)
        if input_pass_through_mode and not all(
                any("OWG_INPUT " + kind in line for line in input_lines) for kind in ("axis", "key")):
            raise RuntimeError("native observer missed hand-driven wheel or key activity")
        if auto_pass_through_mode and not all(
                any(marker in line for line in input_lines) for marker in
                ("OWG_INPUT injecting outside click", "OWG_INPUT injecting outside key",
                 "OWG_INPUT key")):
            raise RuntimeError("native observer missed automatic outside input")
        if not physical_mode:
            target_name = output_prefix + str(target_output)
            if not any("OWG_INPUT pointer" in line and 'output "' + target_name + '"' in line
                       for line in input_lines):
                raise RuntimeError("pointer Skip was not attributed to " + target_name)
    required = ["shown generation=17 count=1 focusUnchanged=true",
                "skip callback generation=17 pointerSkipDelivered=" +
                ("observed" if input_probe_plugins else "unverified"),
                "hidden generation=17 count=0 focusUnchanged=true",
                "hide-timeout generation=18 retryable=true",
                "hidden generation=18 count=0 focusUnchanged=true", "RESULT PASS"]
    for marker in required:
        if not any(marker in line for line in relevant):
            raise RuntimeError("missing warning surface assertion: " + marker)
    if any("expired generation=17" in line for line in relevant):
        raise RuntimeError("warning expired before the tested Skip")
    if any("RESULT FAIL" in line for line in relevant):
        raise RuntimeError("warning surface probe reported failure")
    return 0


def main():
    directory = tempfile.mkdtemp(prefix="owg-warning-surface-")
    temp = pathlib.Path(directory)
    runner_env = os.environ.copy()
    if runner_env.get("OWG_WARNING_PASS_THROUGH") == "1" and runner_env.get("OWG_WARNING_PHYSICAL") != "1":
        raise RuntimeError("outside-warning test requires OWG_WARNING_PHYSICAL=1")
    if runner_env.get("OWG_WARNING_PHYSICAL") == "1" or runner_env.get("OWG_WARNING_WINDOWED") == "1":
        plugin_dir = pathlib.Path(runner_env.get("OWG_INPUT_PROBE_PLUGIN_DIR", "")).resolve()
        if not plugin_dir.is_relative_to(pathlib.Path("/tmp")) or not (
                plugin_dir / "kwin/effects/plugins/libowg_input_probe.so").is_file():
            raise RuntimeError("physical warning test requires the private /tmp input probe build")
        if runner_env.get("OWG_WARNING_WINDOW_BACKEND", "wayland") == "x11":
            host_display = runner_env.get("DISPLAY", "")
            if not re.fullmatch(r":\d+(?:\.\d+)?", host_display):
                raise RuntimeError("local host XWayland display unavailable for windowed warning test")
            runner_env["OWG_PARENT_X11_DISPLAY"] = host_display
        else:
            host_socket = pathlib.Path(runner_env.get("WAYLAND_DISPLAY", "wayland-0"))
            if not host_socket.is_absolute():
                host_socket = pathlib.Path(runner_env["XDG_RUNTIME_DIR"]) / host_socket
            if not host_socket.is_socket():
                raise RuntimeError("host Wayland socket unavailable for physical warning test")
            runner_env["OWG_PARENT_WAYLAND_SOCKET"] = str(host_socket)
    runtime = temp / "outer-runtime"
    runtime.mkdir(mode=0o700)
    runner_env["XDG_RUNTIME_DIR"] = str(runtime)
    command = ["dbus-run-session", "--", sys.executable, __file__, "--worker", directory]
    process = subprocess.Popen(command, env=runner_env, start_new_session=True)
    try:
        code = process.wait(timeout=100)
    except subprocess.TimeoutExpired:
        print("WARNING_SURFACE_TIMEOUT: terminating isolated process group", flush=True)
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
        print("FAILED_RUN_ARTIFACTS:", directory, flush=True)
        return 124
    if code:
        print("FAILED_RUN_ARTIFACTS:", directory, flush=True)
        return code
    shutil.rmtree(directory, ignore_errors=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        try:
            sys.exit(worker(pathlib.Path(sys.argv[2])))
        except Exception as error:
            print("WARNING_SURFACE_HARNESS_FAILURE:", error, file=sys.stderr)
            sys.exit(1)
    sys.exit(main())
