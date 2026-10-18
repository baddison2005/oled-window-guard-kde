#!/usr/bin/env python3
"""Build the isolated, test-only KWin effect into an explicit temporary path.

Requires matching KWin/Qt/KF6 development headers. No install step exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sysroot", type=Path, default=Path("/usr"),
                        help="prefix containing matching development headers and libraries")
    parser.add_argument("--output", type=Path, required=True,
                        help="private Qt plugin path; must not be an active system/user plugin path")
    args = parser.parse_args()
    prefix = args.sysroot.resolve()
    output = args.output.resolve()
    if output == Path("/tmp") or not output.is_relative_to(Path("/tmp")):
        parser.error("output must be a private subdirectory of /tmp")
    moc = prefix / "lib64/qt6/libexec/moc"
    if not moc.is_file():
        parser.error(f"matching Qt moc not found: {moc}")
    include_root = prefix / "include"
    if not (include_root / "kwin/input_event_spy.h").is_file():
        parser.error("matching kwin-devel headers are required")
    header = (include_root / "kwin/config-kwin.h").read_text()
    match = re.search(r'#define KWIN_PLUGIN_VERSION_STRING "([^"]+)"', header)
    running = subprocess.run(["kwin_wayland", "--version"], check=True, capture_output=True,
                             text=True, timeout=10).stdout.strip()
    if not match or running != "kwin " + match.group(1):
        parser.error("development headers must match the installed KWin version exactly")
    includes = [include_root, include_root / "kwin", include_root / "qt6"]
    includes += [path for parent in (include_root / "qt6", include_root / "KF6")
                 for path in sorted(parent.iterdir()) if path.is_dir()]
    include_flags = [f"-I{path}" for path in includes]
    target = output / "kwin/effects/plugins/libowg_input_probe.so"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="owg-input-build-") as temporary:
        generated = Path(temporary)
        subprocess.run([str(moc), *include_flags, str(SOURCE / "observer.cpp"),
                        "-o", str(generated / "observer.moc")], check=True)
        subprocess.run(["g++", "-std=c++23", "-fPIC", "-shared", str(SOURCE / "observer.cpp"),
                        "-o", str(target), f"-I{generated}", *include_flags,
                        f"-L{prefix / 'lib64'}", "-lkwin", "-lQt6Core", "-lQt6Gui", "-lQt6DBus",
                        "-lKF6CoreAddons"], check=True)
    (output / "build-info.json").write_text(json.dumps({
        "schema": 1, "kwinVersion": match.group(1), "lifecycleTraceCapacity": 16,
        "sourceSHA256": hashlib.sha256((SOURCE / "observer.cpp").read_bytes()).hexdigest(),
        "pluginSHA256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }, indent=2) + "\n")
    print(target)


if __name__ == "__main__":
    main()
