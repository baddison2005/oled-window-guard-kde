"""Exercise the supported paused preview in a private D-Bus session.

Run with: dbus-run-session -- python3 tests/integration/run_paused_install.py
The script uses temporary XDG roots and never touches the current installation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import install_user


def command(executable: Path, action: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(executable), action], env=env, text=True,
                          capture_output=True, timeout=10, check=False)


def main() -> None:
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("run under dbus-run-session")
    with tempfile.TemporaryDirectory(prefix="owg-acceptance-") as temporary:
        base = Path(temporary)
        data, binary, config = base / "data", base / "bin", base / "config"
        env = dict(os.environ, XDG_DATA_HOME=str(data), XDG_BIN_HOME=str(binary),
                   XDG_CONFIG_HOME=str(config), QT_QPA_PLATFORM="offscreen")
        # The installer checks the session bus in this process too.
        os.environ.update({key: env[key] for key in (
            "XDG_DATA_HOME", "XDG_BIN_HOME", "XDG_CONFIG_HOME", "QT_QPA_PLATFORM")})
        unrelated = data / "window-layouts-preservation-sentinel"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("keep\n")
        native_module=Path(os.environ["OWG_NATIVE_OBSERVER_QML_PATH"]) if os.environ.get("OWG_NATIVE_OBSERVER_QML_PATH") else None
        install_user.install(ROOT, data, binary,native_module=native_module)
        native_binaries=list((data/"kwin/scripts/oled-window-guard-adapter/contents/ui/native").glob("*/org/oledwindowguard/Native/libowg_native_observer.so"))
        assert len(native_binaries)==(1 if native_module else 0)
        assert not (data / install_user.AUTOSTART_REL).exists()
        assert not (config / "oled-window-guard/settings.json").exists()
        launcher = binary / install_user.APP_ID
        host = subprocess.Popen([str(launcher), "open"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if host.poll() is not None:
                    raise AssertionError(f"host exited early: {host.stderr.read()}")
                status = command(launcher, "pause", env)
                if status.returncode == 0:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("session service did not become ready")
            assert json.loads(status.stdout)["status"]["paused"] is True
            assert command(launcher, "open", env).returncode == 0
            blocked = command(launcher, "start", env)
            assert blocked.returncode == 2
            assert json.loads(blocked.stdout)["reason"] == "capability-unavailable"
            assert command(launcher, "restore-brightness", env).returncode == 2
            try:
                install_user.uninstall(data, binary)
            except install_user.InstallError as exc:
                assert "active" in str(exc)
            else:
                raise AssertionError("active host did not block uninstall")
            assert command(launcher, "quit", env).returncode == 0
            host.wait(timeout=10)
            assert host.returncode == 0
        finally:
            if host.poll() is None:
                host.terminate()
                host.wait(timeout=5)
            host.stderr.close()

        settings = config / "oled-window-guard/settings.json"
        assert settings.is_file() and json.loads(settings.read_text())["paused"] is True
        install_user.install(ROOT, data, binary, autostart=True,native_module=native_module)
        assert (data / install_user.AUTOSTART_REL).is_file()
        install_user.install(ROOT, data, binary, autostart=False,native_module=native_module)
        assert not (data / install_user.AUTOSTART_REL).exists()
        install_user.uninstall(data, binary)
        assert not launcher.exists()
        assert all(not native_binary.exists() for native_binary in native_binaries)
        assert unrelated.read_text() == "keep\n"
        assert json.loads(settings.read_text())["paused"] is True
    print("Paused private-session install, lifecycle, and removal: PASS")


if __name__ == "__main__":
    main()
