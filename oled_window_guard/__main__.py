"""Development entry point; packaging will install the matching command."""
from __future__ import annotations

import argparse
import json
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from .session import SessionHost, call_host, register_host


COMMANDS = {"open": "Open", "start": "Resume", "pause": "Pause",
            "restore-brightness": "RestoreBrightness", "move-now": "MoveNow",
            "skip": "Skip", "restore": "Restore", "quit": "Quit"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oled-window-guard")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS), default="open")
    args = parser.parse_args(argv)
    if args.command == "open":
        from .dashboard import create_application

        app = create_application([])
        existing = call_host("Open")
        if existing is not None:
            return 0 if existing.get("ok") else 2
        from .adapter import AdapterLifetime, installed_adapter
        main = installed_adapter()
        host = SessionHost(app, expected_adapter_source=main.as_uri() if main else 'installed-adapter-unavailable')
        if not register_host(host):
            print("OLED Window Guard: session service unavailable or already running", file=sys.stderr)
            return 2
        adapter = AdapterLifetime()
        app.aboutToQuit.connect(adapter.stop)
        def start_adapter():
            if host.status["dependencies"]["supportedMatrix"]:
                reason = adapter.start()
                if reason:
                    host.status["suspendedReasons"] = [reason]
                    host.dashboard.update_status(host.status)
        QTimer.singleShot(0, start_adapter)
        adapter_watch = QTimer(app)
        adapter_watch.setInterval(2000)
        def recover_adapter():
            reason = adapter.recover(lambda: host.submit("Pause"))
            if reason:
                host.status["suspendedReasons"] = [reason]
                host.dashboard.update_status(host.status)
        adapter_watch.timeout.connect(recover_adapter)
        adapter_watch.start()
        host.show_startup()
        return app.exec()
    app = QCoreApplication.instance() or QCoreApplication([])
    response = call_host(COMMANDS[args.command])
    if response is None:
        print("OLED Window Guard is not running in this session", file=sys.stderr)
        return 2
    print(json.dumps(response, separators=(",", ":")))
    return 0 if response.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
