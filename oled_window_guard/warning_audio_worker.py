"""Persistent, windowless warning audio; stdin EOF ends it with its controller."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QSocketNotifier
from oled_window_guard.warning_audio import SoundPlayer


def main():
    app = QCoreApplication([])
    player = SoundPlayer()
    player.message.connect(lambda message: print('sent' if message.startswith('Chime sent') else 'unavailable', flush=True))
    buffer = bytearray()

    def receive(*_):
        data = os.read(0, 1024)
        if not data:
            app.quit()
            return
        buffer.extend(data)
        if len(buffer) > 1024:
            app.quit()
            return
        while b'\n' in buffer:
            line, _, rest = buffer.partition(b'\n')
            buffer[:] = rest
            if line == b'play':
                player.play()

    notifier = QSocketNotifier(0, QSocketNotifier.Type.Read)
    notifier.activated.connect(receive)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
