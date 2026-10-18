#!/usr/bin/env python3
"""Check that loading the native module outside KWin cannot install a spy."""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

app=QGuiApplication([])
# Even matching name/version do not turn a companion process into KWin.
app.setApplicationName('kwin');app.setApplicationVersion('6.7.5')
engine=QQmlEngine();engine.addImportPath(str(Path(sys.argv[1]).resolve()))
component=QQmlComponent(engine)
component.setData(b'''import QtQml
import org.oledwindowguard.Native 1.0
QtObject {
    property NativeObserver observer: NativeObserver {}
    property bool refused: !observer.available
    property bool rejectedToken: !observer.isCurrent(observer.epoch,0)
    property string facts: JSON.stringify(observer.snapshot())
}''',QUrl())
root=component.create()
if not root: raise RuntimeError('\n'.join(error.toString() for error in component.errors()))
facts=json.loads(root.property('facts'))
assert root.property('refused') and root.property('rejectedToken')
assert not facts['immediateCancellation'] and not facts['outputIdentityComplete'] and not facts['buttonKnown']
print(json.dumps({'result':'PASS','nonKWinRejected':True,'spoofedNameVersionRejected':True,'tokenRejected':True}))
