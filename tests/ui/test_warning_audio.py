import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import tempfile
import time
import wave
from pathlib import Path
from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtWidgets import QApplication
from oled_window_guard.warning_audio import SoundPlayer, WarningAudio


class Effect(QObject):
    statusChanged = Signal()
    class Status:
        Loading, Ready, Error = range(3)
    def __init__(self, parent):
        super().__init__(parent)
        self.current = self.Status.Loading
        self.plays = 0
    def setVolume(self, value): self.volume = value
    def setLoopCount(self, value): self.loops = value
    def setSource(self, value): self.source = value
    def status(self): return self.current
    def play(self): self.plays += 1
    def change(self, value):
        self.current = value
        self.statusChanged.emit()


class WarningAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_async_load_plays_once_and_reuses_effect(self):
        audio = SoundPlayer(effect_factory=Effect)
        audio.play()
        effect = audio._effect
        self.assertEqual(effect.plays, 0)
        effect.change(Effect.Status.Ready)
        effect.statusChanged.emit()
        self.assertEqual(effect.plays, 1)
        audio.play()
        self.assertIs(audio._effect, effect)
        self.assertEqual(effect.plays, 2)
        self.assertFalse(audio._timeout.isActive())
        self.assertEqual(effect.loops, 1)
        self.assertTrue(effect.source.isLocalFile())

    def test_error_and_timeout_do_not_play_late(self):
        for error in (True, False):
            audio = SoundPlayer(effect_factory=Effect)
            messages = []
            audio.message.connect(messages.append)
            audio.play()
            if error: audio._effect.change(Effect.Status.Error)
            else: audio._timed_out()
            audio._effect.change(Effect.Status.Ready)
            self.assertEqual(audio._effect.plays, 0)
            self.assertIn('unavailable', messages[-1])

    def test_bundled_chime_is_short_non_silent_pcm(self):
        asset = Path(__file__).resolve().parents[2] / 'assets/sounds/warning.wav'
        with wave.open(str(asset)) as sound:
            self.assertEqual((sound.getnchannels(), sound.getsampwidth()), (1, 2))
            self.assertLess(sound.getnframes() / sound.getframerate(), 1)
            self.assertTrue(any(sound.readframes(sound.getnframes())))

    def test_slow_audio_process_keeps_controller_event_loop_responsive(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'audio.py'
            script.write_text('import sys,time\nfor line in sys.stdin:\n time.sleep(.7)\n print("sent",flush=True)\n')
            audio = WarningAudio()
            audio._process.setArguments([str(script)])
            messages, ticks = [], []
            audio.message.connect(messages.append)
            timer = QTimer()
            timer.setInterval(25)
            timer.timeout.connect(lambda: ticks.append(time.monotonic()))
            timer.start()
            try:
                started = time.monotonic()
                audio.play()
                self.assertLess(time.monotonic()-started, .1)
                deadline = started+3
                while not messages and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertEqual(len(messages), 1)
                self.assertIn('Chime sent', messages[0])
                self.assertGreaterEqual(len(ticks), 15)
                self.assertLess(ticks[0]-started, .2)
                pid = audio._process.processId()
                audio.play()
                while len(messages) < 2 and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertEqual(len(messages), 2)
                self.assertEqual(audio._process.processId(), pid)
                self.assertFalse(audio._timeout.isActive())
            finally:
                timer.stop()
                audio._process.closeWriteChannel()
                self.assertTrue(audio._process.waitForFinished(1000))

    def test_audio_worker_failure_reports_once_and_allows_retry(self):
        audio = WarningAudio()
        messages = []
        audio.message.connect(messages.append)
        audio._process.setProgram('/no/such/owg-audio-worker')
        audio.play()
        deadline = time.monotonic()+1
        while not messages and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertEqual(len(messages), 1)
        self.assertIn('unavailable', messages[0])
        self.assertFalse(audio._pending)
        self.assertFalse(audio._timeout.isActive())
        self.assertEqual(audio._process.state(), QProcess.ProcessState.NotRunning)
