#!/usr/bin/env python3
"""Actual private-KWin startup checks with an isolated fake logind service."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusVariant, QDBusVirtualObject


class Login(QDBusVirtualObject):
    def __init__(self):
        super().__init__()
        self.value = False
        self.pending = []

    def introspect(self, path):
        return ""

    def handleMessage(self, message, connection):
        if message.interface() != "org.freedesktop.DBus.Properties" or message.member() != "Get":
            return False
        assert message.arguments() == ["org.freedesktop.login1.Manager", "PreparingForSleep"]
        if self.value == "defer":
            self.pending.append((message, connection))
        elif self.value == "error":
            connection.send(message.createErrorReply("org.freedesktop.DBus.Error.Failed", "test refusal"))
        else:
            reply = message.createReply()
            reply.setArguments([QDBusVariant(self.value)])
            connection.send(reply)
        return True


def worker(module, root):
    os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    app = QCoreApplication([])
    bus = QDBusConnection.sessionBus()
    login = Login()
    assert bus.registerVirtualObject("/org/freedesktop/login1", login)
    assert bus.registerService("org.freedesktop.login1")
    env = os.environ.copy()
    for key, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                           ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "runtime")):
        target = root/directory
        target.mkdir(mode=0o700)
        env[key] = str(target)
    env.update(QT_QPA_PLATFORM="wayland", QML_IMPORT_PATH=str(module),
               QT_LOGGING_RULES="qml.debug=true", QT_FORCE_STDERR_LOGGING="1")
    repo=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(repo))
    from tools.build_kwin_package import materialize
    materialize(root/"package",native_module=module)
    # A QML-only update moves an identical native binary into another immutable
    # runtime tree. Exercise both paths within the same retained QML engine.
    source_root=root/"next-source"
    shutil.copytree(repo/"core",source_root/"core")
    shutil.copytree(repo/"packages/kwin-script",source_root/"packages/kwin-script")
    main=source_root/"packages/kwin-script/contents/ui/main.qml"
    main.write_text(main.read_text()+"\n// isolated QML-only upgrade fixture\n")
    materialize(root/"package-next",root=source_root,native_module=module)
    build_id=json.loads((module/"build-info.json").read_text())["pluginSHA256"]
    qml = root/"startup.qml"
    source='''import QtQuick
import "./package/contents/ui/native/BUILD/org/oledwindowguard/Native" as Native
QtObject {
 property Native.NativeObserver observer: Native.NativeObserver {}
 function sample() { console.log("OWG_STARTUP", JSON.stringify(observer.snapshot())) }
 property Connections events: Connections {
  target: observer
  function onInvalidated(reason) { sample() }
 }
 Component.onCompleted: sample()
}'''.replace("BUILD",build_id)
    qml.write_text(source)
    immutable_entries=[]
    for name in ("package","package-next"):
        runtime=json.loads((root/name/"contents/runtime-build.json").read_text())["main"]
        relative=(Path(name)/"contents"/Path(runtime).parent/"native"/build_id).as_posix()
        entry=root/("immutable-first.qml" if name=="package" else "immutable-second.qml")
        entry.write_text(source.replace("package/contents/ui/native/"+build_id,relative))
        immutable_entries.append((name,entry))
    results = []
    with (root/"kwin.log").open("w") as output:
        kwin = subprocess.Popen(["kwin_wayland", "--virtual", "--width", "900", "--height", "650",
                                 "--no-lockscreen"], env=env, stdout=output, stderr=subprocess.STDOUT)

        def wait(predicate, timeout=8):
            deadline = time.monotonic()+timeout
            while time.monotonic()<deadline:
                app.processEvents()
                if predicate():
                    return
                if kwin.poll() is not None:
                    raise RuntimeError((root/"kwin.log").read_text())
                time.sleep(.01)
            raise RuntimeError("startup check timed out: "+(root/"kwin.log").read_text()[-4000:])

        def samples():
            return [json.loads(line.split("OWG_STARTUP ", 1)[1]) for line in
                    (root/"kwin.log").read_text(errors="replace").splitlines() if "OWG_STARTUP {" in line]

        def call(method, *args):
            interface = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
            reply = interface.call(method, *args)
            assert reply.type() != QDBusMessage.MessageType.ErrorMessage, reply.errorMessage()
            return reply.arguments()[0] if reply.arguments() else None

        def load(path=qml):
            before=len(samples())
            index = call("loadDeclarativeScript", str(path), "owg-native-startup-test")
            interface = QDBusInterface("org.kde.KWin", f"/Scripting/Script{index}", "org.kde.kwin.Script", bus)
            reply = interface.call("run")
            assert reply.type() != QDBusMessage.MessageType.ErrorMessage
            wait(lambda: len(samples())>before)

        def check(name, state):
            wait(lambda: bool(samples()) and samples()[-1]["sleepState"] == state)
            sample = samples()[-1]
            assert sample["lockState"] == "unlocked" and not sample["buttonKnown"]
            assert all(row["ageMs"] == -1 for row in sample["outputs"])
            results.append({"case": name, "snapshot": sample})

        try:
            wait(lambda: bus.interface().isServiceRegistered("org.kde.KWin").value())
            old=repo/"docs/evidence/production-native-observer-2026-10-05/qml"
            shutil.copytree(old,root/"old-native")
            old_qml=root/"old-startup.qml"
            old_qml.write_text(source.replace("./package/contents/ui/native/"+build_id,"./old-native"))
            load(old_qml)
            wait(lambda: bool(samples()))
            assert samples()[-1]["lockState"]=="unknown" and samples()[-1]["sleepState"]=="unknown"
            old_epoch=samples()[-1]["epoch"]
            call("unloadScript", "owg-native-startup-test")
            load()
            check("fresh-awake", "awake")
            assert samples()[-1]["epoch"]!=old_epoch
            results.append({"case":"same-compositor-native-upgrade", "oldEpoch":old_epoch,
                            "newEpoch":samples()[-1]["epoch"]})
            for name,entry in immutable_entries:
                previous=samples()[-1]["epoch"]
                call("unloadScript", "owg-native-startup-test")
                load(entry)
                check("immutable-runtime-reload-"+name,"awake")
                assert samples()[-1]["epoch"]!=previous
            assert bus.unregisterService("org.freedesktop.login1")
            check("owner-loss", "unknown")
            login.value = True
            assert bus.registerService("org.freedesktop.login1")
            check("owner-replacement-sleeping", "sleeping")
            call("unloadScript", "owg-native-startup-test")
            login.value = "defer"
            load()
            wait(lambda: bool(login.pending))
            assert bus.unregisterService("org.freedesktop.login1")
            check("deferred-read-owner-loss", "unknown")
            login.value = True
            assert bus.registerService("org.freedesktop.login1")
            check("replacement-before-old-reply", "sleeping")
            for message, connection in login.pending:
                reply = message.createReply()
                reply.setArguments([QDBusVariant(False)])
                connection.send(reply)
            until = time.monotonic()+.3
            while time.monotonic()<until:
                app.processEvents();time.sleep(.01)
            check("stale-awake-reply-rejected", "sleeping")
            call("unloadScript", "owg-native-startup-test")
            login.value = "error"
            load()
            until = time.monotonic()+.3
            while time.monotonic()<until:
                app.processEvents();time.sleep(.01)
            check("read-error-fails-closed", "unknown")
            (root/"results.json").write_text(json.dumps(results, indent=2)+"\n")
            print("PASS", len(results), "startup checks; artifacts:", root)
        finally:
            kwin.terminate()
            kwin.wait(timeout=10)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        module = Path(sys.argv[1]).resolve()
        assert module.is_relative_to(Path("/tmp"))
        root = Path(tempfile.mkdtemp(prefix="owg-native-startup-"))
        raise SystemExit(subprocess.run(["dbus-run-session", "--", sys.executable, __file__,
                                         "--worker", str(module), str(root)], timeout=55).returncode)
