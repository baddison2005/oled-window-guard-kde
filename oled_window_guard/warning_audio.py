"""Play the bundled warning without opening a window or invoking the system bell."""
from pathlib import Path
import sys
from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, QUrl, Signal


class WarningAudio(QObject):
    """Keep multimedia initialization and playback outside the controller loop."""
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.setProgram(sys.executable)
        self._process.setArguments([str(Path(__file__).with_name('warning_audio_worker.py'))])
        self._process.started.connect(self._send)
        self._process.readyReadStandardOutput.connect(self._receive)
        self._process.errorOccurred.connect(self._failed)
        self._process.finished.connect(self._finished)
        self._pending = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(5000)
        self._timeout.timeout.connect(self._failed)
        if QCoreApplication.instance() is not None:
            QCoreApplication.instance().aboutToQuit.connect(self.stop)

    def play(self):
        self._pending = True
        self._timeout.start()
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._process.start()
        elif self._process.state() == QProcess.ProcessState.Running:
            self._send()

    def _send(self):
        if self._pending:
            self._process.write(b'play\n')

    def _receive(self):
        while self._process.canReadLine():
            reply = bytes(self._process.readLine()).strip()
            if not self._pending:
                continue
            if reply in (b'sent', b'unavailable'):
                self._pending = False
                self._timeout.stop()
                self.message.emit('Chime sent to the default audio output. Check its volume if inaudible.'
                                  if reply == b'sent' else
                                  'Warning audio unavailable. Check your default audio output and volume.')

    def _finished(self, *_):
        self._failed()

    def stop(self):
        self._pending = False
        self._timeout.stop()
        self._process.closeWriteChannel()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()

    def _failed(self, *_):
        was_pending = self._pending
        self._pending = False
        self._timeout.stop()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
        if was_pending:
            self.message.emit('Warning audio unavailable. Check your default audio output and volume.')


class SoundPlayer(QObject):
    """Multimedia backend, constructed only by the audio subprocess."""
    message = Signal(str)

    def __init__(self, parent=None, *, effect_factory=None):
        super().__init__(parent)
        self._factory = effect_factory
        self._effect = None
        self._pending = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(3000)
        self._timeout.timeout.connect(self._timed_out)

    def play(self):
        try:
            if self._effect is None:
                if self._factory is None:
                    from PySide6.QtMultimedia import QSoundEffect
                    self._factory = QSoundEffect
                self._effect = self._factory(self)
                self._effect.setVolume(.65)
                self._effect.setLoopCount(1)
                self._effect.statusChanged.connect(self._loaded)
                path = Path(__file__).resolve().parents[1] / 'assets/sounds/warning.wav'
                self._effect.setSource(QUrl.fromLocalFile(str(path)))
            self._pending = True
            self._timeout.start()
            self._loaded()
        except Exception:
            self._pending = False
            self._timeout.stop()
            self.message.emit('Warning audio unavailable. Check your default audio output and volume.')

    def _loaded(self):
        if not self._pending:
            return
        status = self._effect.status()
        if status == self._effect.Status.Ready:
            self._pending = False
            self._timeout.stop()
            self._effect.play()
            self.message.emit('Chime sent to the default audio output. Check its volume if inaudible.')
        elif status == self._effect.Status.Error:
            self._timed_out()

    def _timed_out(self):
        self._pending = False
        self._timeout.stop()
        self.message.emit('Warning audio unavailable. Check your default audio output and volume.')
