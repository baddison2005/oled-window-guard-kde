"""Load the installed default-off observer for this companion's lifetime."""
from pathlib import Path
import json
import os
import re
import time

from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

PLUGIN = "oled-window-guard-adapter"
# KWin reconfigure unloads installed packages marked disabled. This runtime
# belongs to the companion, not the KCM package toggle; keep those identities
# separate so another application's config save cannot tear down our guards.
RUNTIME_PLUGIN = "oled-window-guard-runtime"

def installed_adapter() -> Path | None:
    home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home()/".local/share")))
    roots = [home] + [Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p]
    for root in roots:
        if not root.is_absolute():
            continue
        package = root/"kwin/scripts"/PLUGIN
        try:
            metadata = json.loads((package/"metadata.json").read_text())
            main = package/"contents/ui/main.qml"
            if metadata.get("KPlugin", {}).get("Id") != PLUGIN or metadata.get("X-Plasma-MainScript") != "ui/main.qml":
                continue
            runtime=package/"contents/runtime-build.json"
            if runtime.exists():
                build=json.loads(runtime.read_text())
                if not isinstance(build,dict) or build.get("schema")!=1 or not re.fullmatch(r"build_[0-9a-f]{64}/ui/main\.qml",str(build.get("main",""))):
                    continue
                main=package/"contents"/build["main"]
            if main.is_file() and (main.parent/"planning_worker.js").is_file():
                return main
        except (OSError, ValueError):
            continue
    return None

class AdapterLifetime:
    def __init__(self):
        self.owned = False
        self.requested = False
        self.started_at = 0.0
        self.retry_at = 0.0

    @staticmethod
    def interface(path, name):
        interface = QDBusInterface("org.kde.KWin", path, name, QDBusConnection.sessionBus())
        interface.setTimeout(1000)
        return interface

    @staticmethod
    def value(message):
        if message.type()==QDBusMessage.MessageType.ErrorMessage or not message.arguments():
            raise RuntimeError("KWin scripting unavailable")
        return message.arguments()[0]

    def start(self):
        self.requested = True
        self.started_at = time.monotonic()
        self.retry_at = self.started_at + 5
        main = installed_adapter()
        if not main:
            return "Installed compositor adapter unavailable"
        scripting = self.interface("/Scripting", "org.kde.kwin.Scripting")
        try:
            for name in (PLUGIN, RUNTIME_PLUGIN):
                if self.value(scripting.call("isScriptLoaded", name)) is not True:
                    continue
                # The single-instance controller endpoint is already owned by
                # this host before start(). A leftover script may come from a
                # crashed host or an older RPM; adopting it silently runs stale
                # QML/native code and leaves Quit unable to unload it.
                if self.value(scripting.call("unloadScript", name)) is not True:
                    raise RuntimeError("KWin refused stale compositor adapter removal")
                if self.value(scripting.call("isScriptLoaded", name)) is not False:
                    raise RuntimeError("Stale compositor adapter still loaded")
            script_id = self.value(scripting.call("loadDeclarativeScript", str(main), RUNTIME_PLUGIN))
            if type(script_id) is not int or script_id<0:
                raise RuntimeError("KWin refused compositor adapter")
            self.owned = True
            # KWin 6.7.5 derives IDs from the current list length. After a
            # removal, /ScriptN can still belong to another script. The global
            # entry point runs the registered objects directly, without that
            # ambiguous per-ID D-Bus path. Our non-package identity survives
            # the accompanying enabled-package scan.
            reply = scripting.call("start")
            if reply.type()==QDBusMessage.MessageType.ErrorMessage:
                raise RuntimeError("KWin refused adapter startup")
            return None
        except RuntimeError as exc:
            self.stop()
            return str(exc)

    def stop(self):
        if self.owned:
            self.interface("/Scripting", "org.kde.kwin.Scripting").call("unloadScript", RUNTIME_PLUGIN)
            self.owned = False

    def recover(self, pause, *, heartbeat_healthy=None):
        """Recover an owned script removed by KWin reconfiguration, paused.

        A failed bus probe is not evidence of unloading. Never reload or send
        old product commands until pausing has persisted successfully.
        """
        if not self.owned and not self.requested:
            return None
        if self.owned and heartbeat_healthy is True:
            return None  # Avoid synchronous bus probes on the active GUI loop.
        now = time.monotonic()
        if now < self.retry_at:
            return None
        try:
            scripting = self.interface("/Scripting", "org.kde.kwin.Scripting")
            loaded = self.value(scripting.call("isScriptLoaded", RUNTIME_PLUGIN))
            if loaded is True and heartbeat_healthy is not False:
                return None
            if loaded is not True and loaded is not False:
                raise RuntimeError("KWin scripting unavailable")
        except RuntimeError as exc:
            return str(exc)
        self.retry_at = now + 5
        if not pause().get("ok"):
            return "Cannot pause before compositor adapter recovery"
        return self.start()
