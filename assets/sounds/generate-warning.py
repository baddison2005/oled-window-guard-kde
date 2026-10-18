"""Generate the original OWG two-note warning chime (no external samples)."""
from pathlib import Path
import math
import struct
import wave

RATE = 44100
samples = []
for frequency in (660, 880):
    duration = .20
    for index in range(round(RATE * duration)):
        t = index / RATE
        envelope = min(1, t / .015, (duration - t) / .045)
        samples.append(round(15000 * envelope * math.sin(2 * math.pi * frequency * t)))
    samples.extend([0] * round(RATE * .06))
with wave.open(str(Path(__file__).with_name('warning.wav')), 'wb') as output:
    output.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
    output.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
