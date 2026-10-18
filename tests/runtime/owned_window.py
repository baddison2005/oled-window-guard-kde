"""Owned nested-session QQuickWindow; never run on the user's desktop."""
import os
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtQml import QQmlEngine, QQmlComponent
from shiboken6 import isValid
from PySide6.QtGui import QGuiApplication, QColor
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

app = QApplication([])
QGuiApplication.setDesktopFileName("oled-window-guard-runtime-test")

# This window supplies Qt Quick's animation clock for the QML Timer test.
clock = QQuickWindow()
clock.setTitle("OLED Window Guard timer clock probe")
clock.setFlags(Qt.WindowType.Window | Qt.WindowType.WindowDoesNotAcceptFocus)
clock.setColor(QColor("#334455"))
clock.setGeometry(5, 5, 2, 2)
clock.show()

# This is the only managed-window candidate. The harness owns its lifetime and
# the flag prevents activation/focus changes in the nested session.
window = QWidget()
window.setWindowTitle("OLED Window Guard disposable probe window")
window.setWindowFlags(Qt.WindowType.Window if os.environ.get("OWG_TEST_HAND_INPUT")=="1" and os.environ.get("OWG_NATIVE_OBSERVER_QML_PATH") else Qt.WindowType.Window | Qt.WindowType.WindowDoesNotAcceptFocus)
layout = QVBoxLayout(window)
label = QLabel("Disposable OLED Window Guard runtime test")
layout.addWidget(label)
window.setGeometry(80, 90, 320, 240)
instructions = os.environ.get("OWG_PLANNING_HAND_INSTRUCTIONS")
if instructions:
    candidate = QWidget()
    candidate.setWindowTitle("OLED Window Guard owned preview candidate")
    candidate_layout = QVBoxLayout(candidate)
    candidate_layout.addWidget(QLabel("Read-only preview candidate"))
    candidate.resize(220, 100)
    candidate.show()
    if os.environ.get("OWG_TEST_ORDERED_WRITER")=="1":
        second_candidate=QWidget()
        second_candidate.setWindowTitle("OLED Window Guard owned ordered candidate")
        second_layout=QVBoxLayout(second_candidate)
        second_layout.addWidget(QLabel("Ordered writer candidate"))
        second_candidate.resize(220,100)
        second_candidate.show()
    if os.environ.get("OWG_TEST_GROUPED_ARRANGEMENTS")=="1":
        grouped_candidates=[]
        for name in ("tall","writer"):
            grouped=QWidget()
            grouped.setWindowTitle("OLED Window Guard owned grouped "+name+" candidate")
            grouped_layout=QVBoxLayout(grouped)
            grouped_layout.addWidget(QLabel("Grouped "+name+" candidate"))
            grouped.resize(220,100);grouped.show();grouped_candidates.append(grouped)
    trigger=os.environ.get("OWG_WRITER_CLIENT_TRIGGER")
    if trigger:
        overlay_engine=QQmlEngine()
        overlay_window=None
        overlay_component=None
        def client_action():
            global overlay_window,overlay_component
            try:
                action=Path(trigger).read_text().strip()
            except FileNotFoundError:
                return
            if not action:return
            Path(trigger).write_text("")
            if action=="grow":
                candidate.setMinimumSize(candidate.width()+40,candidate.height()+10)
                candidate.resize(candidate.minimumSize())
            elif action=="destroy":
                candidate.close()
                candidate.deleteLater()
                client_timer.stop()
            elif action=="limit-shrink" and os.environ.get('OWG_TEST_ADJACENT_ROUNDING')=='1':
                # Set the Wayland toplevel hint directly: QWidget's width can
                # exclude client-side margins and is not the compositor frame.
                second_candidate.windowHandle().setMinimumWidth(451)
                second_candidate.windowHandle().requestUpdate()
                second_candidate.update()
            elif action=="clear-size-limit" and os.environ.get('OWG_TEST_ADJACENT_ROUNDING')=='1':
                second_candidate.windowHandle().setMinimumWidth(0)
            elif action=="overlay-hide":
                if overlay_window and isValid(overlay_window):
                    overlay_window.setProperty('visible',False)
                    if isValid(overlay_window):overlay_window.deleteLater()
                overlay_window=None;overlay_component=None
            elif action in ('Notification','CriticalNotification','OnScreenDisplay'):
                if overlay_window and isValid(overlay_window):overlay_window.deleteLater()
                overlay_component=QQmlComponent(overlay_engine)
                overlay_component.setData(('import QtQuick; import org.kde.plasma.core as PlasmaCore; PlasmaCore.Dialog { title:"OWG notification fixture"; type:PlasmaCore.Dialog.'+action+
                    '; flags:Qt.Window | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus; x:330; y:160; visible:true; mainItem:Rectangle {width:260;height:100;color:"#456789"} }').encode(),QUrl('file:///tmp/owg-notification-'+action+'.qml'))
                overlay_window=overlay_component.create()
                if overlay_window:QQmlEngine.setObjectOwnership(overlay_window,QQmlEngine.ObjectOwnership.CppOwnership)
                else:print('OVERLAY_ERROR',overlay_component.errors(),flush=True)
        client_timer=QTimer();client_timer.setInterval(5);client_timer.timeout.connect(client_action);client_timer.start()
    label.setWordWrap(True)
    # Keep prompt changes from altering the frame without making the managed
    # window non-resizable (the registry excludes that state).
    label.setFixedSize(450, 190)
    window.resize(480, 240)
    def update_instructions():
        label.setText(Path(instructions).read_text())
    prompt_timer = QTimer()
    prompt_timer.setInterval(100)
    prompt_timer.timeout.connect(update_instructions)
    prompt_timer.start()
window.show()
print("OWNED_QWINDOWS", "clock-visible=", clock.isVisible(), "clock-exposed=", clock.isExposed(),
      "test-visible=", window.isVisible(), "test-exposed=", window.isVisible(), flush=True)
QTimer.singleShot(390000 if instructions else 45000, app.quit)
app.exec()
