"""Local proof for detected-beat scene cuts (2026-11-26).

Runs the production path end to end on this machine: the
synthetic 120 BPM bed beside this file (ground truth beats
every 0.5s from 0.0), four stills, four tone "narration"
lines of known lengths. Detects the bed's beats with
shorts.detect_beat_times, snaps the scene lengths with
shorts.snap_durations_to_beats, assembles a real short with
shorts.assemble_short, then reads the finished file's scene
changes back with ffmpeg and reports every cut against the
detected grid and the ground truth.

Run:  ~/workspace/.venvs/beat/bin/python prove_beat_sync.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "platform"))
import shorts  # noqa: E402

WORK = HERE / "work"
WORK.mkdir(exist_ok=True)
BED = HERE / "bed_120bpm.wav"
TRUE_BEAT = 0.5

# 1. Detection against the ground truth grid.
beats = shorts.detect_beat_times(BED, hint_bpm=120)
assert beats, "no beats detected"
errs = [abs(b - round(b / TRUE_BEAT) * TRUE_BEAT) for b in beats]
print(f"detected {len(beats)} beats; first five: "
      f"{[round(b, 3) for b in beats[:5]]}")
print(f"phase error vs ground truth: mean {sum(errs) / len(errs):.4f}s "
      f"max {max(errs):.4f}s")

# 2. Scene lengths the processor's way: line + gap, last + tail.
line_durations = [2.3, 3.1, 1.8, 2.6]
scene_durations = []
for i, d in enumerate(line_durations):
    pad = shorts.TAIL_SECONDS if i == len(line_durations) - 1 \
        else shorts.LINE_GAP_SECONDS
    scene_durations.append(d + pad)
snapped = shorts.snap_durations_to_beats(scene_durations, beats)
assert snapped
cuts = []
cursor = 0.0
for d in snapped:
    cursor += d
    cuts.append(round(cursor, 3))
print(f"natural scene lengths: {[round(d, 2) for d in scene_durations]}")
print(f"snapped scene lengths: {[round(d, 3) for d in snapped]}")
print(f"cut times: {cuts[:-1]}")
for c in cuts[:-1]:
    nearest = min(beats, key=lambda b: abs(b - c))
    truth = round(c / TRUE_BEAT) * TRUE_BEAT
    print(f"  cut {c:.3f}: {abs(c - nearest):.4f}s from the nearest "
          f"detected beat, {abs(c - truth):.4f}s from the true grid")

# 3. Assemble a real short through the production function.
for i, color in enumerate(("0x10203a", "0xf2e14c", "0x1d4d2b", "0xd94f9e")):
    still = WORK / f"still_{i}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=540x960:d=1", "-frames:v", "1",
         str(still)], check=True)
    wav = WORK / f"line_{i}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={300 + i * 80}:duration={line_durations[i]}",
         "-ar", "24000", "-ac", "1", str(wav)], check=True)
line_wavs = [WORK / f"line_{i}.wav" for i in range(4)]
narration = shorts.build_narration_track(
    line_wavs, line_durations, WORK, scene_durations=snapped)
scene_paths = []
for i in range(4):
    scene = WORK / f"ai_scene_{i}.mp4"
    shorts.animate_image_scene(WORK / f"still_{i}.png", snapped[i], scene, i)
    scene_paths.append(scene)
out = HERE / "proof_short.mp4"
info = shorts.assemble_short(
    scene_paths, snapped, narration, None, out, WORK,
    bed_path=BED, watermark=False)
print(f"assembled proof short: {info['duration']:.2f}s -> {out}")

# 4. Read the finished file's cuts back and compare.
proc = subprocess.run(
    ["ffmpeg", "-i", str(out), "-filter:v",
     "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
    capture_output=True, text=True)
found = []
for line in proc.stderr.splitlines():
    if "pts_time:" in line:
        found.append(round(float(
            line.split("pts_time:")[1].split()[0]), 3))
print(f"scene changes read back at: {found}")
for want, got in zip(cuts[:-1], found):
    print(f"  planned cut {want:.3f}, read back {got:.3f}, "
          f"delta {abs(want - got):.3f}s")
print("PROOF DONE")
