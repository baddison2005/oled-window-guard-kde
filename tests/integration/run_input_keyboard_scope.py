#!/usr/bin/env python3
"""Prove keyboard output scope in a disposable two-output virtual KWin session."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "tests/integration/keyboard_scope_stage.qml"
CLIENT = ROOT / "tests/runtime/keyboard_scope_window.py"


def worker(temp):
    env = os.environ.copy()
    env.update({
        "XDG_CONFIG_HOME": str(temp / "config"), "XDG_DATA_HOME": str(temp / "data"),
        "XDG_CACHE_HOME": str(temp / "cache"), "XDG_RUNTIME_DIR": str(temp / "runtime"),
        "QT_QPA_PLATFORM": "wayland", "KDE_SESSION_VERSION": "6",
        "QT_ASSUME_STDERR_HAS_CONSOLE": "1", "QT_FORCE_STDERR_LOGGING": "1",
        "QT_LOGGING_RULES": "kwin_scripting.debug=true;qml.debug=true",
        "QSG_USE_SIMPLE_ANIMATION_DRIVER": "1",
        "QT_PLUGIN_PATH": os.environ["OWG_INPUT_PROBE_PLUGIN_DIR"],
        "OWG_INPUT_PROBE_ISOLATED": "1", "OWG_INPUT_TEST_TRIGGER": str(temp / "trigger"),
    })
    for directory in ("config", "data", "cache", "runtime"):
        (temp / directory).mkdir(exist_ok=True)
    (temp / "runtime").chmod(0o700)
    (temp / "config/kwinrc").write_text("[Plugins]\nlibowg_input_probeEnabled=true\n")
    from direct_unknown_focus import prepare_fixture
    prepare_fixture(temp)
    (temp / "trigger").write_text("")
    installed_scripts = {}
    for stage in ("first", "second", "spanning"):
        for mode in ("move", "readback"):
            package_id = f"owg-keyboard-{stage}-{mode}"
            package = temp / f"package-{stage}-{mode}"
            (package / "contents/ui").mkdir(parents=True)
            (package / "metadata.json").write_text(json.dumps({
                "KPackageStructure": "KWin/Script",
                "KPlugin": {"Id": package_id, "Name": "OWG private keyboard scope probe",
                            "Description": "Disposable keyboard attribution check", "Version": "0.1.0",
                            "License": "GPL-2.0-or-later", "EnabledByDefault": False},
                "X-Plasma-API": "declarativescript", "X-Plasma-API-Minimum-Version": "6.0",
                "X-Plasma-MainScript": "ui/main.qml",
            }))
            (package / "contents/ui/main.qml").write_text(
                STAGE.read_text().replace("__STAGE__", stage).replace("__MODE__", mode))
            subprocess.run(["kpackagetool6", "--type", "KWin/Script", "--install", str(package)],
                           env=env, text=True, capture_output=True, check=True, timeout=10)
            installed_scripts[(stage, mode)] = (
                temp / f"data/kwin/scripts/{package_id}/contents/ui/main.qml")
    log_path = temp / "kwin.log"
    with log_path.open("w") as log:
        kwin = subprocess.Popen(["kwin_wayland", "--virtual", "--width", "900", "--height", "650",
                                 "--scale", "1", "--output-count", "2", "--no-lockscreen"],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        client = None
        try:
            def dbus(path, method, *args):
                return subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                                       "--object-path", path, "--method", method, *args],
                                      env=env, text=True, capture_output=True, check=True,
                                      timeout=5).stdout.strip()

            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if kwin.poll() is not None:
                    raise RuntimeError("private KWin exited before readiness")
                check = subprocess.run(["gdbus", "introspect", "--session", "--dest", "org.kde.KWin",
                                        "--object-path", "/Scripting"], env=env, text=True,
                                       capture_output=True, timeout=5)
                if check.returncode == 0 and "loadDeclarativeScript" in check.stdout:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError("private KWin scripting did not start")

            def snapshot():
                reply = dbus("/org/oledwindowguard/InputProbe", "org.oledwindowguard.InputProbe1.Snapshot")
                return json.loads(ast.literal_eval(reply)[0])

            def output_counts(state):
                if not state["outputIdentityComplete"] or len(state["outputs"]) != 2:
                    raise RuntimeError("invalid two-output snapshot: " + str(state))
                return {row["id"]: row["events"] for row in state["outputs"]}

            def key_delta(expected, focus_known=False):
                before = snapshot()
                if before.get('focusKnown') is not focus_known:
                    raise RuntimeError('Keyboard stage did not establish its expected focus state')
                old = output_counts(before)
                (temp / "trigger").write_text("keyboard-only\n")
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    after = snapshot()
                    if after["keyboard"] >= before["keyboard"] + 2:
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError("keyboard-only trigger was not observed")
                if after["keyboard"] != before["keyboard"] + 2:
                    raise RuntimeError("unexpected keyboard event count")
                if (after.get('focusKnown') is not focus_known or
                        after['focusGeneration'] != before['focusGeneration'] or
                        after['unknownFocusKeyboard'] - before['unknownFocusKeyboard'] !=
                        (0 if focus_known else 2)):
                    raise RuntimeError('Keyboard focus changed or callback focus classification disagreed')
                new = output_counts(after)
                delta = {name: new[name] - old[name] for name in old}
                if delta != expected:
                    raise RuntimeError(f"keyboard output scope {delta} != {expected}")
                return delta

            print("UNKNOWN_FOCUS:", key_delta({"Virtual-0": 2, "Virtual-1": 2}), flush=True)
            client_env = env.copy()
            client_env["WAYLAND_DISPLAY"] = "wayland-0"
            instruction = temp / 'instructions.txt'
            instruction.write_text('Private non-focusing keyboard preflight.')
            no_focus_env = dict(client_env, OWG_INPUT_CLIENT_DIRECT='1',
                                OWG_INPUT_CLIENT_NO_FOCUS='1', OWG_INPUT_CLIENT_WINDOWED='1',
                                OWG_INPUT_TEST_SECONDS='45', OWG_INPUT_CLIENT_INSTRUCTIONS=str(instruction))
            no_focus_log = temp / 'non-focusing-client.log'
            with no_focus_log.open('w') as output:
                client = subprocess.Popen([sys.executable, '-u', str(ROOT / 'tests/runtime/input_window.py')],
                                          env=no_focus_env, stdout=output, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 5
                while 'CLIENT_READY' not in no_focus_log.read_text(errors='replace'):
                    if client.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Non-focusing instruction client did not become ready')
                    time.sleep(.05)
                before = snapshot()
                if before.get('focusKnown') is not False:
                    raise RuntimeError('Instruction client took application focus')
                delta = key_delta({'Virtual-0': 2, 'Virtual-1': 2})
                after = snapshot()
                if (after.get('focusKnown') is not False or
                        after['focusGeneration'] != before['focusGeneration'] or
                        after['unknownFocusKeyboard'] - before['unknownFocusKeyboard'] != 2):
                    raise RuntimeError('Non-focusing window did not retain unknown-focus keyboard evidence')
                time.sleep(.2)
                if 'CLIENT_EVENT keyboard' in no_focus_log.read_text(errors='replace'):
                    raise RuntimeError('Non-focusing instruction client received a key')
                from direct_unknown_focus import keyboard_result
                from run_input_direct import environment
                try:
                    physical_result = keyboard_result(before, after, environment(env), environment(env), 0)
                except RuntimeError:
                    pass
                else:
                    if physical_result is not None:
                        raise RuntimeError('Synthetic keyboard preflight was accepted as physical evidence')
                print('VISIBLE_UNKNOWN_FOCUS:', delta, 'no activation or client keyboard delivery; synthetic origin refused', flush=True)
                client.terminate()
                client.wait(timeout=3)
                client = None
            client = subprocess.Popen([sys.executable, str(CLIENT)], env=client_env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)
            for stage, expected in (("first", {"Virtual-0": 2, "Virtual-1": 0}),
                                    ("second", {"Virtual-0": 0, "Virtual-1": 2}),
                                    ("spanning", {"Virtual-0": 2, "Virtual-1": 2})):
                for mode, marker in (("move", f"OWG_KEYBOARD_SCOPE_MOVED {stage} "),
                                     ("readback", f"OWG_KEYBOARD_SCOPE_READY {stage} ")):
                    script_id = f"owg-keyboard-{stage}-{mode}"
                    reply = dbus("/Scripting", "org.kde.kwin.Scripting.loadDeclarativeScript",
                                 str(installed_scripts[(stage, mode)]), script_id)
                    match = re.fullmatch(r"\((\d+),\)", reply)
                    if not match:
                        raise RuntimeError("KWin did not load stage script: " + reply)
                    dbus("/Scripting/Script" + match.group(1), "org.kde.kwin.Script.run")
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline:
                        transcript = log_path.read_text(errors="replace")
                        if f"OWG_KEYBOARD_SCOPE_FAIL {stage} {mode}" in transcript:
                            raise RuntimeError("stage failed: " + transcript.splitlines()[-10:].__repr__())
                        if marker in transcript:
                            break
                        time.sleep(.1)
                    else:
                        raise RuntimeError("stage did not reach " + marker)
                    if mode == "move":
                        time.sleep(.5)
                print(stage.upper() + "_FOCUS:", key_delta(expected, focus_known=True), flush=True)
            print("KEYBOARD_SCOPE: PASS", flush=True)
        finally:
            if client:
                client.terminate()
                try:
                    client.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    client.kill()
                    client.wait(timeout=2)
            kwin.terminate()
            try:
                kwin.wait(timeout=8)
            except subprocess.TimeoutExpired:
                kwin.kill()
                kwin.wait(timeout=3)
    print("KWIN_SCOPE_LOG:", "\n".join(line for line in log_path.read_text(errors="replace").splitlines()
                                      if "OWG_KEYBOARD_SCOPE" in line), flush=True)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]))
        return
    if len(sys.argv) != 1:
        raise RuntimeError("usage: run_input_keyboard_scope.py")
    directory = tempfile.mkdtemp(prefix="owg-keyboard-scope-")
    command = ["dbus-run-session", "--", sys.executable, "-u", __file__, "--worker", directory]
    with (Path(directory) / "runner.out").open("w") as stdout, \
            (Path(directory) / "runner.err").open("w") as stderr:
        completed = subprocess.run(command, env=os.environ.copy(), stdout=stdout, stderr=stderr,
                                   timeout=55)
    print((Path(directory) / "runner.out").read_text(errors="replace"), end="")
    if completed.returncode:
        print((Path(directory) / "runner.err").read_text(errors="replace")[-4000:], file=sys.stderr)
        print((Path(directory) / "kwin.log").read_text(errors="replace")[-4000:], file=sys.stderr)
        raise SystemExit(completed.returncode)
    shutil.rmtree(directory)


if __name__ == "__main__":
    main()
