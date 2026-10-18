#!/usr/bin/env python3
"""Open disposable windows for supervised normal-desktop acceptance.

Run on an otherwise empty virtual desktop, with Window Guard paused. This tool
does not arm the writer, change settings, inject input or control other windows.
"""
import argparse
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--backend",choices=("auto","wayland","xwayland"),default="auto")
    parser.add_argument("--profile",choices=("normal","fixed-bounded"),default="normal")
    args = parser.parse_args()
    if args.backend=="xwayland":os.environ["QT_QPA_PLATFORM"]="xcb"
    if args.backend=="wayland":os.environ["QT_QPA_PLATFORM"]="wayland"
    app = QApplication([])
    if args.backend=="xwayland" and app.platformName()!="xcb":
        raise RuntimeError("XWayland fixtures require the xcb platform")
    app.setApplicationName("oled-window-guard-physical-fixture")
    QGuiApplication.setDesktopFileName("oled-window-guard-physical-fixture")
    windows = []
    for screen in app.screens():
        for number in (1, 2):
            window = QWidget()
            window.setWindowTitle(f"OWG disposable {screen.name()} — {number}"+(" (XWayland)" if args.backend=="xwayland" else ""))
            layout = QVBoxLayout(window)
            label = QLabel("Disposable movement / Restore target.\n"
                           "Keep your working applications on another desktop.\n"
                           "Close these windows when testing finishes.")
            layout.addWidget(label)
            window.resize(420, 180)
            if args.profile=="fixed-bounded":
                label.setText(("Fixed size: 420 × 180" if number==1 else "Bounded size: 320 × 140 to 560 × 260")+"\nDisposable constraint test target.")
                window.setWindowTitle(window.windowTitle()+(" [fixed]" if number==1 else " [bounded]"))
                if number==1:window.setFixedSize(420,180)
                else:
                    window.setMinimumSize(320,140)
                    window.setMaximumSize(560,260)
            window.winId()
            window.windowHandle().setScreen(screen)
            window.show()
            windows.append(window)

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    with args.receipt.open("x", encoding="utf-8") as receipt:
        previous = None

        def sample():
            nonlocal previous
            rows = []
            for window in windows:
                rect = window.frameGeometry()
                rows.append({"title": window.windowTitle(), "visible": window.isVisible(),
                             "screen": window.screen().name(),
                             "clientSize":[window.width(),window.height()],
                             "minimumSize":[window.minimumWidth(),window.minimumHeight()],
                             "maximumSize":[window.maximumWidth(),window.maximumHeight()],
                             "frame": [rect.x(), rect.y(), rect.width(), rect.height()]})
            if rows != previous:
                receipt.write(json.dumps({"monotonic": time.monotonic(), "platform":app.platformName(),"profile":args.profile,"pid":os.getpid(), "windows": rows}) + "\n")
                receipt.flush()
                previous = rows

        def close_requested_output():
            command=args.receipt.with_suffix(".close-output")
            if not command.exists():return
            try:
                request=json.loads(command.read_text())
                selected=[w for w in windows if w.isVisible() and w.screen().name()==request.get("output")]
                if request.get("output") not in ("DP-1","HDMI-A-1") or len(selected)!=2:
                    result={"requestId":request.get("requestId"),"ok":False,"reason":"Expected exactly two visible fixtures on output"}
                else:
                    app.setQuitOnLastWindowClosed(False)
                    for window in selected:window.close()
                    sample()
                    result={"requestId":request.get("requestId"),"ok":True,"closed":2,"output":request["output"]}
                temporary=args.receipt.with_suffix(".close-ack.tmp")
                temporary.write_text(json.dumps(result))
                temporary.replace(args.receipt.with_suffix(".close-ack"))
            finally:
                command.unlink(missing_ok=True)

        timer = QTimer()
        timer.setInterval(100)
        timer.timeout.connect(close_requested_output)
        timer.timeout.connect(sample)
        timer.start()
        app.aboutToQuit.connect(sample)
        sample()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
