"""Visible, disposable client for nested and direct input observer checks."""
import os
import time
from pathlib import Path
from PySide6.QtCore import QObject, QEvent, QTimer, Qt
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

app = QApplication([])
if os.environ.get("OWG_INPUT_CLIENT_NO_FOCUS") == "1":
    app.setDesktopFileName("oled-window-guard-unknown-focus-test")
    app.setApplicationName("oled-window-guard-unknown-focus-test")
window = QWidget()
window.setWindowTitle("OLED Window Guard input check — click inside")
layout = QVBoxLayout(window)
label = QLabel("Click here, move the pointer, scroll, type a few keys,\n"
               "then hold the mouse button for about 3 seconds and release.\n"
               "Only event-type counts are recorded. This window closes automatically.")
layout.addWidget(label)
window.resize(610, 220)
if os.environ.get("OWG_INPUT_CLIENT_DIRECT") == "1":
    no_focus = os.environ.get("OWG_INPUT_CLIENT_NO_FOCUS") == "1"
    if no_focus:
        window.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    warning_instructions = os.environ.get("OWG_INPUT_CLIENT_INSTRUCTIONS") or os.environ.get("OWG_INPUT_WARNING_INSTRUCTIONS")
    if warning_instructions:
        window.setWindowTitle("OWG owned focus probe")
    duration = int(os.environ.get("OWG_INPUT_TEST_SECONDS", "45"))
    started = time.monotonic()
    counts = {"pointer": 0, "button": 0, "axis": 0, "keyboard": 0}

    class InputLog(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                print("CLIENT_ABORT", flush=True)
                app.quit()
                return True
            kinds = {QEvent.Type.MouseMove: "pointer", QEvent.Type.MouseButtonPress: "button",
                     QEvent.Type.Wheel: "axis", QEvent.Type.KeyPress: "keyboard"}
            kind = kinds.get(event.type())
            if kind:
                counts[kind] += 1
                print("CLIENT_EVENT " + kind, flush=True)
            return False

    input_log = InputLog()
    app.installEventFilter(input_log)
    window.setMouseTracking(True)
    label.setMouseTracking(True)
    window.setFocusPolicy(Qt.FocusPolicy.NoFocus if no_focus else Qt.FocusPolicy.StrongFocus)
    if no_focus:
        label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    window.setStyleSheet("background: #334455; color: white; font-size: 22px;")

    def refresh():
        remaining = max(0, duration - int(time.monotonic() - started))
        if warning_instructions:
            label.setText(os.environ.get("OWG_INPUT_CLIENT_HEADING", "Direct warning test") + "\n\n" + Path(warning_instructions).read_text() +
                          ("\n\nThis window closes automatically. Only event types are recorded." if no_focus
                           else "\n\nEscape aborts. Only event types are recorded."))
            return
        label.setText("Direct DRM/libinput test — about " + str(remaining) + " seconds remaining\n\n"
                      "Click this area, move, scroll and type a few keys.\n"
                      "Hold the left mouse button for 3 seconds, then release.\n"
                      "Escape aborts. The test closes automatically.\n\n"
                      "Only event types are recorded: " + str(counts))

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(500)
    refresh()
    if os.environ.get("OWG_INPUT_CLIENT_WINDOWED") == "1":
        label.setWordWrap(True)
        window.resize(660, 380)
        window.show()
    else:
        window.showMaximized()
    if not no_focus:
        window.activateWindow()
        window.setFocus()
    QTimer.singleShot(500, lambda: print("CLIENT_READY", flush=True))
else:
    window.show()
app.exec()
