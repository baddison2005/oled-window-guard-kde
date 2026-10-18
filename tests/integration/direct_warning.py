"""One-surface warning fixture and ordered checks shared by direct/nested tests."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import time
from direct_recording import event

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ID = "oledwindowguard-direct-warning-test"
PREFIX = "OWG_DIRECT_WARNING "
SHOWN = PREFIX + "SHOWN focusUnchanged=true count=1"
SKIP = PREFIX + "SKIP focusUnchanged=true"
HIDDEN = PREFIX + "HIDDEN focusUnchanged=true count=0"
KINDS = ("pointer", "button", "axis", "keyboard")


def publish_instruction(directory, message):
    path = directory / "instructions.txt"
    if (directory / 'recording.json').is_file():
        if path.is_file() and path.read_text() == message:
            return
        # Persist the imminent cable prompt before the user can act on it.
        event(directory, 'instruction', message=message)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(message)
    temporary.replace(path)


def scripting(env, method, *args, path="/Scripting", interface="org.kde.kwin.Scripting"):
    result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                             "--object-path", path, "--method", interface + "." + method, *args],
                            env=env, check=True, text=True, capture_output=True, timeout=5)
    reply = result.stdout.strip()
    if reply in ("(true,)", "(false,)"):
        return reply == "(true,)"
    return ast.literal_eval(reply)[0]


def install_fixture(directory, env):
    package = directory / "warning-package"
    ui = package / "contents/ui"
    ui.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "packages/kwin-script/contents/ui/WarningSurface.qml", ui / "WarningSurface.qml")
    shutil.copy2(ROOT / "tests/integration/warning_direct_probe.qml", ui / "main.qml")
    (package / "metadata.json").write_text(json.dumps({
        "KPackageStructure": "KWin/Script",
        "KPlugin": {"Id": PACKAGE_ID, "Name": "Private direct warning test", "Version": "0.1.0",
                    "License": "GPL-2.0-or-later", "EnabledByDefault": False},
        "X-Plasma-API": "declarativescript", "X-Plasma-API-Minimum-Version": "6.0",
        "X-Plasma-MainScript": "ui/main.qml"}))
    installed = Path(env["XDG_DATA_HOME"]) / "kwin/scripts" / PACKAGE_ID / "contents/ui/main.qml"
    subprocess.run(["kpackagetool6", "--type", "KWin/Script",
                    "--upgrade" if installed.exists() else "--install", str(package)],
                   env=env, check=True, text=True, capture_output=True, timeout=10)
    return installed


def check_log(log):
    if PREFIX + "FAIL " in log:
        raise RuntimeError("Warning fixture failed: " + next(
            line for line in log.splitlines() if PREFIX + "FAIL " in line))
    for marker in (SHOWN, SKIP, HIDDEN):
        if log.count(marker) > 1:
            raise RuntimeError("Duplicate warning lifecycle callback")
    if SKIP in log and (SHOWN not in log or log.index(SKIP) < log.index(SHOWN)):
        raise RuntimeError("Warning Skip preceded show")
    if HIDDEN in log and (SKIP not in log or log.index(HIDDEN) < log.index(SKIP)):
        raise RuntimeError("Warning hid before Skip")


def unload_fixture(env, log):
    check_log(log)
    for marker in (SHOWN, SKIP, HIDDEN):
        if log.count(marker) != 1:
            raise RuntimeError("Incomplete warning lifecycle")
    if scripting(env, "unloadScript", PACKAGE_ID) is not True:
        raise RuntimeError("Warning script refused unload")
    if scripting(env, "isScriptLoaded", PACKAGE_ID) is not False:
        raise RuntimeError("Warning script remained loaded")


class WarningCheck:
    def __init__(self, directory, env, log_start=0, after_skip="The test closes automatically afterward."):
        self.directory = directory
        self.env = env
        self.armed = False
        self.finished = False
        self.skip_origin = None
        self.log_start = log_start
        self.after_skip = after_skip
        self.instruction("Preparing the warning. Wait before interacting.")

    def instruction(self, message):
        publish_instruction(self.directory, message)

    def log(self):
        return (self.directory / "kwin.log").read_text(errors="replace")[self.log_start:]

    def start(self, kwin, client):
        source = install_fixture(self.directory, self.env)
        script_id = scripting(self.env, "loadDeclarativeScript", str(source), PACKAGE_ID)
        if type(script_id) is not int or script_id < 0:
            raise RuntimeError("Warning script did not load")
        # Script.run returns no value; use the subprocess directly.
        subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin",
                        "--object-path", "/Scripting/Script" + str(script_id),
                        "--method", "org.kde.kwin.Script.run"], env=self.env,
                       check=True, text=True, capture_output=True, timeout=5)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if kwin.poll() is not None or client.poll() is not None:
                raise RuntimeError("Private warning compositor/client exited before show")
            log = self.log()
            check_log(log)
            if SKIP in log or HIDDEN in log:
                raise RuntimeError("Warning dismissed before input baseline")
            if SHOWN in log:
                return
            time.sleep(.1)
        raise RuntimeError("Warning did not show within the bounded startup time")

    def ready(self):
        self.instruction("Click this dark area BELOW the warning, move the pointer, scroll and type a key.\n"
                         "Hold the left button for 3 seconds, then release.\n"
                         "WAIT for this text to say NOW CLICK SKIP.")

    def advance(self, client_counts, held, released, current, origin, origin_before):
        log = self.log()
        check_log(log)
        if SHOWN not in log:
            raise RuntimeError("Missing confirmed warning show")
        if not self.armed:
            if SKIP in log or HIDDEN in log:
                raise RuntimeError("Skip was clicked before outside-input confirmation; repeat the check")
            if (all(client_counts.get(kind, 0) > 0 for kind in KINDS) and held and released and
                    current.get("buttonKnown") is True and current.get("buttonsDown") is False and
                    all(origin["libinput"][kind] > origin_before["libinput"][kind] for kind in KINDS)):
                self.armed = True
                self.skip_origin = origin["libinput"]["button"]
                self.instruction("NOW CLICK SKIP in the warning above.\n"
                                 + self.after_skip + " Escape aborts.")
                print("DIRECT_WARNING_OUTSIDE_INPUT: confirmed; now click Skip", flush=True)
        elif HIDDEN in log:
            if (origin["libinput"]["button"] >= self.skip_origin + 2 and
                    current.get("buttonKnown") is True and current.get("buttonsDown") is False):
                self.finished = True
                self.instruction("Skip passed. Checking teardown; please wait.")

    def finish(self):
        if not self.finished:
            raise RuntimeError("Direct outside-input/Skip sequence did not complete")
        time.sleep(.4)
        unload_fixture(self.env, self.log())
        return {"outsideInputDelivered": True, "pointerSkipDelivered": True,
                "focusUnchanged": True, "hiddenSurfaceCount": 0, "scriptUnloaded": True,
                "warningCount": 1}
