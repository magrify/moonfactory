"""The photo presenter's mouth curve (2026-11-25).

Reads the finished voiceover wav the platform's Kokoro stage
made and writes mouth-curve.js beside the composition: one
mouth openness value per video frame, window.__mouth, which
the presenter scene script indexes by frame as HyperFrames
seeks. The curve is the speech envelope: per-frame RMS,
normalized against the track's own loud passages, lightly
smoothed so the mouth moves like speech instead of jitter,
with a small floor so a resting mouth still reads as a mouth.

Standard library only, like the score kit: the HF worker
kernel runs this with its own Python beside the render.

Usage: mouth_curve.py VOICE.wav FPS OUT.js
Lifted in shape from the MotionForge mouth curve tool
(github.com/aykutucesai/motionforge, MIT); the envelope math
here is this platform's own.
"""

import json
import math
import struct
import sys
import wave


def read_samples(path):
    with wave.open(path, "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM wav, got sample width {width}")
    values = struct.unpack(f"<{len(frames) // 2}h", frames)
    if channels > 1:
        values = values[::channels]
    return values, rate


def main() -> int:
    wav_path, fps, out_path = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    samples, rate = read_samples(wav_path)
    per_frame = max(1, int(rate / fps))
    count = max(1, math.ceil(len(samples) / per_frame))
    rms = []
    for i in range(count):
        chunk = samples[i * per_frame:(i + 1) * per_frame]
        if not chunk:
            rms.append(0.0)
            continue
        mean_sq = sum(v * v for v in chunk) / len(chunk)
        rms.append(math.sqrt(mean_sq) / 32768.0)
    loud = sorted(rms)[int(len(rms) * 0.95)] if rms else 0.0
    scale = loud if loud > 1e-4 else 1.0
    curve = [min(1.0, v / scale) for v in rms]
    smoothed = []
    for i, v in enumerate(curve):
        window = curve[max(0, i - 1):i + 2]
        smoothed.append(sum(window) / len(window))
    curve = [round(0.06 + 0.94 * v, 3) for v in smoothed]
    with open(out_path, "w") as fh:
        fh.write("window.__mouth = ")
        json.dump(curve, fh)
        fh.write(";\n")
    print(f"mouth curve: {len(curve)} frames at {fps:g} fps "
          f"from {len(samples) / rate:.2f}s of voice")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
