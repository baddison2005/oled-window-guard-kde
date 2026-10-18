#!/usr/bin/env python3
"""Reversible user-local development installer for OLED Window Guard."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping

try:
    from .build_kwin_package import package_files
except ImportError:
    from build_kwin_package import package_files

APP_ID = "oled-window-guard"
MANIFEST_REL = Path("oled-window-guard/install-manifest.json")
AUTOSTART_REL = Path("autostart/oled-window-guard.desktop")


class InstallError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def xdg_paths(env: Mapping[str, str] = os.environ) -> tuple[Path, Path]:
    home = Path(env.get("HOME", str(Path.home()))).expanduser()
    data = Path(env.get("XDG_DATA_HOME", str(home / ".local/share"))).expanduser()
    # XDG_BIN_HOME is a documented installer override; default keeps commands local.
    bindir = Path(env.get("XDG_BIN_HOME", str(home / ".local/bin"))).expanduser()
    for value, label in ((data, "data"), (bindir, "bin")):
        if not value.is_absolute():
            raise InstallError(f"XDG {label} path must be absolute: {value}")
    return data, bindir


def companion_active() -> bool:
    """Return true only when the exact session service is registered."""
    busctl = shutil.which("busctl")
    if not busctl:
        raise InstallError("cannot check the session service: busctl is unavailable")
    try:
        result = subprocess.run([busctl, "--user", "--no-legend", "list"],
                                capture_output=True, text=True, check=False, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError("cannot determine whether OLED Window Guard is active") from exc
    if result.returncode:
        raise InstallError("cannot determine whether OLED Window Guard is active")
    return any(line.split() and line.split()[0] == "org.oledwindowguard.Controller1"
               for line in result.stdout.splitlines())


def _source_payload(root: Path, data_home: Path, bin_home: Path,
                    autostart: bool, native_module: Path | None = None) -> dict[Path, tuple[bytes, int]]:
    app_dest = data_home / APP_ID / "app"
    payload: dict[Path, tuple[bytes, int]] = {}
    for dirname in ("core", "oled_window_guard", "schema", "interfaces", "packages/kwin-script", "assets", "packaging/licenses"):
        for source in sorted((root / dirname).rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            rel = source.relative_to(root)
            payload[app_dest / rel] = (source.read_bytes(), 0o644)
    for name in ("LICENSE", "NOTICE", "SOURCE.md"):
        payload[app_dest / name] = ((root / name).read_bytes(), 0o644)
    executable = ("#!/usr/bin/env python3\n"
                  "import sys\n"
                  f"sys.path.insert(0, {str(app_dest)!r})\n"
                  "from oled_window_guard.__main__ import main\n"
                  "raise SystemExit(main())\n").encode()
    payload[bin_home / APP_ID] = (executable, 0o755)
    icon = root / "assets/icons/oled-window-guard.png"
    payload[data_home / "icons/hicolor/256x256/apps/oled-window-guard.png"] = (icon.read_bytes(), 0o644)

    desktop = (root / "packages/desktop/oled-window-guard.desktop").read_text(encoding="utf-8")
    command = str(bin_home / APP_ID)
    desktop = desktop.replace("Exec=oled-window-guard", f"Exec={command}")
    desktop = desktop.replace("Icon=oled-window-guard", "Icon=oled-window-guard")
    payload[data_home / "applications/oled-window-guard.desktop"] = (desktop.encode(), 0o644)
    if autostart:
        payload[data_home / AUTOSTART_REL] = (
            ("[Desktop Entry]\nType=Application\nName=OLED Window Guard\n"
             f"Exec={command} open\nIcon=oled-window-guard\nTerminal=false\n"
             "X-GNOME-Autostart-enabled=true\n").encode(), 0o644)

    # KPackage is installed as data only; its metadata keeps the isolated ID and
    # default-off state. Never invoke kpackagetool or enable this package here.
    for rel, content in package_files(root,native_module).items():
        payload[data_home / "kwin/scripts/oled-window-guard-adapter" / rel] = (content, 0o644)
        payload[app_dest / "packages/kwin-script" / rel] = (content, 0o644)
    return payload


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("format") != 1 or not isinstance(value.get("files"), dict):
            raise ValueError("unsupported manifest")
        return value
    except FileNotFoundError:
        return {"format": 1, "files": {}}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid install ownership manifest: {exc}") from exc


def _check_prior_ownership(manifest: dict) -> None:
    for name, entry in manifest["files"].items():
        path = Path(name)
        try:
            actual = digest(path.read_bytes())
        except FileNotFoundError:
            continue
        if actual != entry.get("sha256"):
            raise InstallError(f"owned file was changed; refusing to overwrite: {path}")


def _validate_manifest_targets(manifest: dict, data_home: Path, bin_home: Path) -> None:
    data = data_home.resolve()
    bindir = bin_home.resolve()
    allowed_exact = {
        data / "icons/hicolor/256x256/apps/oled-window-guard.png",
        data / "applications/oled-window-guard.desktop",
        data / AUTOSTART_REL,
        bindir / APP_ID,
    }
    allowed_trees = [data / APP_ID / "app",
                     data / "kwin/scripts/oled-window-guard-adapter"]
    for name, entry in manifest["files"].items():
        path = Path(name)
        if not path.is_absolute():
            raise InstallError("ownership manifest contains a non-absolute path")
        # Resolve symlinks before allowing filesystem actions. Installer-owned
        # destinations never intentionally follow a symlink out of their roots.
        resolved = path.resolve(strict=False)
        if resolved != path:
            raise InstallError(f"ownership manifest path traverses a symlink or alias: {path}")
        if (resolved not in allowed_exact and
                not any(resolved != tree and resolved.is_relative_to(tree) for tree in allowed_trees)):
            raise InstallError(f"ownership manifest contains an out-of-scope path: {path}")
        checksum = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise InstallError(f"ownership manifest has an invalid hash for: {path}")


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def install(root: Path, data_home: Path, bin_home: Path, autostart: bool = False, native_module: Path | None = None) -> None:
    check_dependencies()
    data_home = data_home.expanduser().resolve()
    bin_home = bin_home.expanduser().resolve()
    if companion_active():
        raise InstallError("OLED Window Guard is active; pause and quit it before upgrading")
    manifest_path = data_home / MANIFEST_REL
    if manifest_path.resolve(strict=False) != manifest_path:
        raise InstallError("ownership manifest path traverses a symlink or alias")
    prior = _read_manifest(manifest_path)
    _validate_manifest_targets(prior, data_home, bin_home)
    _check_prior_ownership(prior)
    payload = _source_payload(root, data_home, bin_home, autostart, native_module)
    for path in payload:
        if path.resolve(strict=False) != path:
            raise InstallError(f"destination path traverses a symlink or alias: {path}")
    prior_files = prior["files"]
    # Do not claim or replace files that are not already recorded as ours.
    for path in payload:
        if path.exists() and str(path) not in prior_files:
            raise InstallError(f"destination exists and is not owned by this installer: {path}")
    old = {Path(name): (Path(name).read_bytes(), Path(name).stat().st_mode & 0o777)
           for name in prior_files if Path(name).is_file()}
    # Drop a previously-owned optional autostart file when the user opts out.
    desired = set(payload)
    stale = [Path(name) for name in prior_files if Path(name) not in desired]
    for path in stale:
        if path.exists() and digest(path.read_bytes()) != prior_files[str(path)].get("sha256"):
            raise InstallError(f"owned file was changed; refusing to remove: {path}")
    new_entries = {str(path): {"sha256": digest(data), "mode": mode}
                   for path, (data, mode) in payload.items()}
    manifest_data = json.dumps({"format": 1, "files": new_entries}, sort_keys=True,
                               indent=2).encode() + b"\n"
    try:
        for path in stale:
            path.unlink(missing_ok=True)
        for path, (data, mode) in payload.items():
            _atomic_write(path, data, mode)
        _atomic_write(manifest_path, manifest_data, 0o600)
    except Exception as exc:
        # Restore all pre-existing files and remove new files written so far.
        for path in payload:
            if path in old:
                data, mode = old[path]
                _atomic_write(path, data, mode)
            else:
                path.unlink(missing_ok=True)
        for path in stale:
            if path in old:
                data, mode = old[path]
                _atomic_write(path, data, mode)
        raise InstallError(f"installation failed and was rolled back: {exc}") from exc


def uninstall(data_home: Path, bin_home: Path) -> list[str]:
    if companion_active():
        raise InstallError("OLED Window Guard is active; pause and quit it before uninstalling")
    data_home = data_home.expanduser().resolve()
    bin_home = bin_home.expanduser().resolve()
    manifest_path = data_home / MANIFEST_REL
    if manifest_path.resolve(strict=False) != manifest_path:
        raise InstallError("ownership manifest path traverses a symlink or alias")
    manifest = _read_manifest(manifest_path)
    _validate_manifest_targets(manifest, data_home, bin_home)
    removed: list[str] = []
    for name, entry in manifest["files"].items():
        path = Path(name)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            continue
        if digest(data) == entry.get("sha256"):
            path.unlink()
            removed.append(name)
    manifest_path.unlink(missing_ok=True)
    # Prune only empty directories, never user data or settings.
    for name in sorted(manifest["files"], key=lambda item: len(Path(item).parts), reverse=True):
        parent = Path(name).parent
        while parent != data_home and parent.is_relative_to(data_home):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    return removed


def check_dependencies() -> None:
    if sys.version_info < (3, 10):
        raise InstallError("Python 3.10 or newer is required")
    if importlib.util.find_spec("PySide6") is None:
        raise InstallError("PySide6 is required; install the distribution's python3-pyside6 package")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--uninstall", action="store_true")
    parser.add_argument("--enable-autostart", action="store_true", help="opt in to paused session startup")
    parser.add_argument("--data-home", type=Path)
    parser.add_argument("--bin-home", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--native-module",type=Path,help="bundle a verified version-matched native observer build")
    args = parser.parse_args(argv)
    data, bindir = xdg_paths()
    data = args.data_home or data
    bindir = args.bin_home or bindir
    try:
        if args.uninstall:
            removed = uninstall(data, bindir)
            print(f"Removed {len(removed)} unchanged installer-owned files; user settings were preserved.")
        else:
            install(args.source_root.resolve(), data, bindir, args.enable_autostart, args.native_module)
            print("Installed OLED Window Guard for this user. It starts paused; autostart is "
                  + ("enabled." if args.enable_autostart else "disabled."))
        return 0
    except (InstallError,ValueError,OSError) as exc:
        print(f"OLED Window Guard installer: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
