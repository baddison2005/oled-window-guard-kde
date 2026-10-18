"""Read-only Fedora package update information; no archive installer."""
from __future__ import annotations

import subprocess
from typing import Callable, Sequence

PACKAGE = "oled-window-guard"


def _run(argv: Sequence[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True,
                          check=True, timeout=2).stdout.strip()


def package_update_status(*, runner: Callable[[Sequence[str]], str] = _run) -> dict:
    try:
        installed = runner(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", PACKAGE])
    except (OSError, subprocess.SubprocessError):
        installed = ""
    if installed:
        return {"installed": True, "version": installed,
                "message": f"Installed RPM: {installed}. Download updates from github.com/baddison2005/oled-window-guard-kde/releases and install with DNF."}
    return {"installed": False, "version": None,
            "message": "Development checkout: no installed RPM. Fedora package updates are unavailable here."}
