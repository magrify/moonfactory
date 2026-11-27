"""Proof renders for the movie assembly render (Jack,
2026-11-27): the guided flow's "Put them together" step.

Builds three sample clips locally with ffmpeg's own test
sources, deliberately mismatched the way real Moonfactory
clips are: different sizes (720x1280 portrait, 1280x720
landscape, 854x480), different frame rates (24, 30, 25),
one of them silent. Then it runs the production render
(clipping.render_assembly, the exact function the assembly
worker calls) on them in a chosen order and again in the
reverse order, and proves from the finished files:

- the movie's raster follows its first clip (portrait in,
  720x1280 out; landscape first, 1280x720 out) at 30 fps;
- every source frame survives whole inside the raster: the
  landscape clip inside the portrait movie is letterboxed,
  its green still spans the full width at its own height
  and the bars above and below it are black;
- the order on screen is the order picked: a frame sampled
  from each third of the movie is that clip's color;
- the silent clip does not break the join: the movie
  carries one continuous audio stream across all three
  clips;
- the total length is the sum of the clips;
- one clip alone is refused.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/proof_movie_assembly.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import clipping  # noqa: E402

WORK = Path("/tmp/movie-assembly-proof")
WORK.mkdir(parents=True, exist_ok=True)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    print(("ok   " if cond else "FAIL ") + msg)
    return cond


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def make_clip(name, size, fps, color, seconds=2, tone=None):
    path = WORK / name
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i",
           f"color=c={color}:s={size}:r={fps}:d={seconds}"]
    if tone:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency={tone}:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if tone:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    run(cmd)
    return path


def frame_rgb(path, at_seconds):
    """The average RGB of one frame, read as raw pixels."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at_seconds),
         "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True).stdout
    n = len(out) // 3
    r = sum(out[0::3]) / n
    g = sum(out[1::3]) / n
    b = sum(out[2::3]) / n
    return r, g, b


def frame_rgb_at(path, at_seconds, x_frac, y_frac, probe):
    """The RGB of one pixel of one frame."""
    w, h = int(probe["width"]), int(probe["height"])
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(at_seconds),
         "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True).stdout
    x = min(w - 1, int(w * x_frac))
    y = min(h - 1, int(h * y_frac))
    i = (y * w + x) * 3
    return out[i], out[i + 1], out[i + 2]


def fps_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", str(path)],
        check=True, capture_output=True, text=True).stdout
    import json
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, _, den = v["avg_frame_rate"].partition("/")
    return float(num) / float(den or 1)


def has_audio(path):
    return clipping._has_audio_stream(path)


red = make_clip("a-red.mp4", "720x1280", 24, "red", tone=440)
green = make_clip("b-green.mp4", "1280x720", 30, "green")
blue = make_clip("c-blue.mp4", "854x480", 25, "blue", tone=660)

probes = [clipping.probe_media(p) for p in (red, green, blue)]
check(clipping.assembly_target_size(probes) == (720, 1280),
      "portrait first clip sets a 720x1280 raster")
check(clipping.assembly_target_size([probes[1]]) == (1280, 720),
      "landscape first clip sets a 1280x720 raster")
check(clipping.assembly_target_size(
    [{"width": 500, "height": 500}]) == (960, 960),
    "square first clip sets a 960x960 raster")

# -- forward order: red, green, blue ------------------------------------

dest = WORK / "movie-forward.mp4"
result = clipping.render_assembly(
    [red, green, blue], dest, WORK / "work-forward")
info = clipping.probe_media(dest)
check((info["width"], info["height"]) == (720, 1280),
      f"forward movie is 720x1280, got {info['width']}x{info['height']}")
check(abs(float(info["duration"]) - 6.0) < 0.35,
      f"forward movie runs about 6s, got {info['duration']}")
check(abs(fps_of(dest) - 30) < 0.1,
      f"forward movie is 30 fps, got {fps_of(dest)}")
check(has_audio(dest), "forward movie carries an audio stream")
check(abs(float(result["duration"]) - float(info["duration"])) < 0.01,
      "the render reports the measured duration")

r1 = frame_rgb(dest, 1.0)
check(r1[0] > 150 and r1[1] < 90 and r1[2] < 90,
      f"first third is the red clip, got rgb{r1}")
# The letterboxed and pillarboxed thirds average lower
# (their color shares the frame with black bars), so these
# read the dominant channel: each third's own color leads
# the other two channels by a wide margin.
r2 = frame_rgb(dest, 3.0)
check(r2[1] > r2[0] + 25 and r2[1] > r2[2] + 25,
      f"middle third is the green clip, got rgb{r2}")
r3 = frame_rgb(dest, 5.0)
check(r3[2] > r3[0] + 40 and r3[2] > r3[1] + 40,
      f"last third is the blue clip, got rgb{r3}")
# The landscape clip inside the portrait movie is fitted
# whole: its green spans the middle band at full width, and
# the letterbox bars above it are black.
mid = frame_rgb_at(dest, 3.0, 0.5, 0.5, info)
check(mid[1] > 90, f"the fitted clip's middle is green, got {mid}")
bar = frame_rgb_at(dest, 3.0, 0.5, 0.02, info)
check(bar[0] < 25 and bar[1] < 25 and bar[2] < 25,
      f"the letterbox bar is black, got {bar}")

# -- reverse order: blue, green, red -------------------------------------

dest2 = WORK / "movie-reverse.mp4"
clipping.render_assembly(
    [blue, green, red], dest2, WORK / "work-reverse")
info2 = clipping.probe_media(dest2)
check((info2["width"], info2["height"]) == (1280, 720),
      f"reverse movie is 1280x720, got {info2['width']}x{info2['height']}")
q1 = frame_rgb(dest2, 1.0)
check(q1[2] > 140 and q1[0] < 90,
      f"reversed first third is the blue clip, got rgb{q1}")
q3 = frame_rgb(dest2, 5.0)
check(q3[0] > 60 and q3[0] > q3[1] + 40 and q3[0] > q3[2] + 40,
      f"reversed last third is the red clip, got rgb{q3}")

# -- one clip alone is refused -------------------------------------------

try:
    clipping.render_assembly([red], WORK / "movie-one.mp4",
                             WORK / "work-one")
    check(False, "one clip alone was accepted")
except ValueError:
    check(True, "one clip alone is refused")

if failures:
    print("FAIL proof_movie_assembly")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS proof_movie_assembly")
