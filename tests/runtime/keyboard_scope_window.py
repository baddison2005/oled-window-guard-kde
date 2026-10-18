"""Disposable focusable client for the private two-output keyboard-scope test."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication([])
QApplication.setDesktopFileName("oled-window-guard-keyboard-scope-test")
window = QWidget()
window.setWindowTitle("OWG owned keyboard scope probe")
window.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
window.resize(320, 240)
window.show()
window.activateWindow()
window.setFocus()
QTimer.singleShot(30000, app.quit)
app.exec()
