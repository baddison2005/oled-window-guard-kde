"""Read-only diagnostics for the one Fedora/KWin matrix exercised so far."""
from __future__ import annotations

import shutil
import subprocess
from typing import Callable, Sequence

from PySide6.QtCore import qVersion

TESTED_VERSIONS = {"kwin": "6.7.5", "plasma-workspace": "6.7.5",
                   "python3-pyside6": "6.11.2"}
REQUIRED_COMMANDS = ("kpackagetool6", "kreadconfig6", "rpm")


def _run(argv: Sequence[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=2,
                          check=True).stdout.strip()


def check_dependencies(*, runner: Callable[[Sequence[str]], str] = _run,
                       which: Callable[[str], str | None] = shutil.which,
                       qt_version: str | None = None) -> dict:
    issues = []
    versions = {}
    for command in REQUIRED_COMMANDS:
        if which(command) is None:
            issues.append(f"missing command: {command}")
    for package, expected in TESTED_VERSIONS.items():
        try:
            observed = runner(["rpm", "-q", "--qf", "%{VERSION}", package])
        except (OSError, subprocess.SubprocessError):
            observed = None
        versions[package] = observed
        if observed != expected:
            issues.append(f"{package} {observed or 'unavailable'}; tested {expected}")
    observed_qt = qt_version if qt_version is not None else qVersion()
    versions["Qt"] = observed_qt
    if observed_qt != "6.11.2":
        issues.append(f"Qt {observed_qt}; tested 6.11.2")
    return {"supportedMatrix": not issues, "versions": versions, "issues": issues}
