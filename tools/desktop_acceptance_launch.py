#!/usr/bin/env python3
"""Launch physical-test apps as independent, non-restarting user services.

These transient units have journal logs, survive tool subprocess cleanup, and
are never installed or enabled for autostart. No movement intent is sent.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
               "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE", "QT_QPA_PLATFORM")


def launch(role, *, receipt=None, output="DP-1", test="quit-warning",backend="auto",profile="normal"):
    if role == "dashboard":
        installed = Path('/usr/bin/oled-window-guard')
        launcher = installed if installed.is_file() else Path.home()/".local/bin/oled-window-guard"
        command = [str(launcher), "open"]
    elif role in ("windows","gtk-windows","gtk4-windows"):
        if receipt is None:
            raise ValueError("Windows require a unique receipt path")
        if Path(receipt).exists():
            raise ValueError("Fixture receipt already exists")
        script="desktop_gtk_windows.py" if role in ("gtk-windows","gtk4-windows") else "desktop_acceptance_windows.py"
        command = [sys.executable, str(ROOT/"tools"/script), "--receipt", str(Path(receipt).resolve())]
        if role in ("gtk-windows","gtk4-windows") and (backend!="auto" or profile!="normal"):
            raise ValueError("GTK fixtures use Wayland and normal size only")
        if role=="gtk4-windows":command.extend(["--gtk-version","4"])
        if backend not in ("auto","wayland","xwayland"):raise ValueError("Unknown fixture backend")
        if backend!="auto":command.extend(["--backend",backend])
        if profile not in ("normal","fixed-bounded"):raise ValueError("Unknown fixture profile")
        if profile!="normal":command.extend(["--profile",profile])
    elif role == "control" and test == "ui-alert":
        command = [sys.executable, str(ROOT/"tools/desktop_ui_alert_check.py")]
    elif role == "control":
        command = ([sys.executable, str(ROOT/"tools/desktop_dimming_check.py")]+(["--brightness-only"] if test=="dimming-brightness" else []) if test in ("dimming","dimming-brightness") else
                   [sys.executable, str(ROOT/"tools/desktop_acceptance_control.py"), "--output", output, "--test", test])
    else:
        raise ValueError("Unknown test app role")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    unit = "owg-physical-"+role+"-"+stamp+"-"+uuid.uuid4().hex[:8]+".service"
    args = ["systemd-run", "--user", "--unit="+unit, "--property=Type=exec",
            "--property=Restart=no", "--working-directory="+str(ROOT)]
    args.extend("--setenv="+name+"="+os.environ[name] for name in ENVIRONMENT if os.environ.get(name))
    # Explicitly avoid importing unrelated credentials or tool environment.
    args.extend(["--", *command])
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError("Desktop test launch failed: "+result.stderr.strip())
    return {"role":role, "unit":unit, "command":command,
            "logCommand":"journalctl --user -u "+unit+" --no-pager"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("dashboard", "windows", "gtk-windows", "gtk4-windows", "control"))
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--output", choices=("DP-1", "HDMI-A-1"), default="DP-1")
    parser.add_argument("--test", default="quit-warning")
    parser.add_argument("--backend",choices=("auto","wayland","xwayland"),default="auto")
    parser.add_argument("--profile",choices=("normal","fixed-bounded"),default="normal")
    args = parser.parse_args()
    print(json.dumps(launch(args.role,receipt=args.receipt,output=args.output,test=args.test,backend=args.backend,profile=args.profile)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
