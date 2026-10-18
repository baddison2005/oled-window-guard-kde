#!/usr/bin/env python3
"""Create the exact source archive consumed by the RPM spec."""
from __future__ import annotations

import gzip
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path.cwd() / "oled-window-guard-1.0.0.tar.gz"
INCLUDE = ("native/observer", "core", "tools", "oled_window_guard", "schema", "packages/desktop",
           "packages/kwin-script", "packaging/rpm", "packaging/licenses", "interfaces", "assets", "tests",
           "LICENSE", "NOTICE", "SOURCE.md", "README.md", "CHANGELOG.md", "docs/RELEASE_REVIEW_v1.0.0.md")

with OUTPUT.open("wb") as stream, gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed:
    with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative in INCLUDE:
            source = ROOT / relative
            for path in ([source] if source.is_file() else sorted(source.rglob("*"))):
                if path.is_symlink():
                    raise ValueError(f"Source symlink is not permitted: {path}")
                if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                info = archive.gettarinfo(str(path), arcname=f"oled-window-guard-1.0.0/{path.relative_to(ROOT)}")
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                with path.open("rb") as member:
                    archive.addfile(info, member)
print(OUTPUT)
