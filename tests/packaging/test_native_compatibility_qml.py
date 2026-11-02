"""Compile the real loader with a fake D-Bus transport, without running KWin."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlComponent, QQmlExpression
from PySide6.QtCore import QUrl
from tools.build_kwin_package import materialize


class NativeCompatibilityQmlTests(unittest.TestCase):
    def test_new_compositor_does_not_import_old_native_module(self):
        app = QGuiApplication.instance() or QGuiApplication([])
        with tempfile.TemporaryDirectory(prefix="owg-qml-gate-") as tmp:
            module = Path(tmp) / "org/kde/kwin"
            module.mkdir(parents=True)
            (module / "qmldir").write_text("module org.kde.kwin\nDBusCall 1.0 DBusCall.qml\n")
            (module / "DBusCall.qml").write_text('''import QtQml
QtObject {
    property string service; property string path
    property string dbusInterface; property string method
    signal finished(var values); signal failed()
    function call() {}
}''')
            package = Path(tmp) / "package"
            materialize(package)
            engine = QQmlEngine()
            engine.addImportPath(tmp)
            component = QQmlComponent(engine, QUrl.fromLocalFile(str(package / "contents/ui/NativeObservation.qml")))
            obj = component.create()
            self.assertIsNotNone(obj, "\n".join(e.toString() for e in component.errors()))
            obj.setProperty("expectedKwin", "6.7.5")
            obj.setProperty("expectedQt", "6.11.2")
            expression = QQmlExpression(engine.rootContext(), obj,
                'compatibilityCall.finished(["KWin version: 6.8.2\\nQt Version: 6.11.2\\n"])')
            expression.evaluate()
            self.assertFalse(expression.hasError(), expression.error().toString())
            self.assertIsNone(obj.property("localObserver"))
            self.assertIn("compatible OWG build", obj.property("loadError"))
            self.assertFalse(obj.property("immediateCancellation"))
            obj.deleteLater()


if __name__ == "__main__":
    unittest.main()
