"""Detected-beat sync for faceless shorts (2026-11-26).

Pins the second half of the beat sync playbook: the shorts
assembly hears the chosen bed's beats with librosa and snaps
the scene cuts to the heard grid, falling back to the
nominal-tempo snap whenever there is no bed or no grid.
Detection and the grid snap live in platform/shorts.py as
pure seams (the snap is exercised here on fabricated grids;
librosa itself is exercised by the local proof in
scratch/beat-sync-proof/, since this venv carries no
librosa), the processor wiring is pinned by source shape,
and the clip image carries the library.

Run:  modal-venv python scratch/check_beat_detect.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))
import shorts  # noqa: E402

APP = (ROOT / "platform" / "app.py").read_text()

# The grid snap: cuts accumulate on the heard beats, scenes
# only ever grow, and running out of beats leaves the rest
# of the lengths untouched.
beats = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
out = shorts.snap_durations_to_beats([1.2, 1.2, 1.2], beats)
assert out == [1.5, 1.5, 1.5], out
# And when the grid runs out mid-short, the rest pass through.
out = shorts.snap_durations_to_beats([1.2, 1.2, 1.2], beats[:8])
assert out == [1.5, 1.5, 1.2], out
out = shorts.snap_durations_to_beats([3.8, 1.2], beats)
assert out == [4.0, 1.2], out
out = shorts.snap_durations_to_beats([0.4], beats)
assert out == [0.5], out
assert shorts.snap_durations_to_beats([1.0], []) is None
assert shorts.snap_durations_to_beats([1.0], None) is None
assert shorts.snap_durations_to_beats([1.0], [0.0, -1.0]) is None

# Detection refuses cleanly where there is nothing to hear:
# a missing file is a fallback, never an exception.
assert shorts.detect_beat_times("/nonexistent/bed.wav") is None
assert shorts.detect_beat_times(None) is None

# The processor wiring: detected first, tempo fallback, the
# job record says which grid the cuts followed, and the bed
# is fetched through the same cached route the assembly
# mixes.
assert "def _snap_scenes_to_bed_beats(" in APP
assert "shorts.detect_beat_times(bed_path, hint_bpm=bpm)" in APP
assert "shorts.snap_durations_to_beats(" in APP
assert 'job["short_beat_source"] = "detected"' in APP
assert 'job["short_beat_source"] = "tempo"' in APP
assert "scene_durations = _snap_scenes_to_bed_beats(" in APP
# The old direct nominal snap is gone from the processor.
proc = APP[APP.index("def process_short_job"):APP.index("def process_short_job") + 20000]
assert "scene_durations = shorts.snap_durations_to_beat(\n" not in proc

# The library rides the image the shorts processor runs on.
img = APP[APP.index("clip_image = ("):APP.index("clip_image = (") + 1500]
assert '"librosa==1.0.1"' in img

print("beat detect pins OK")
