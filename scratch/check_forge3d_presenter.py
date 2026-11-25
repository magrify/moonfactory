"""The 3D motion templates and the photo presenter (2026-11-25).

Pins the MotionForge lift: the five 3D scenes and the photo
presenter are individual entries on the free template lane,
their compositions carry the right bundles and props, the
presenter waits for its voiceover at claim time and rebuilds
at the voice's real length, the photo crosses the input gate
at create (with the own-photo exemption on the flag: a
flagged photo passes on this entry only, an unjudged one
still fails closed, the verdict still stamps), and the
worker stages every new asset and fails clearly when one is
missing. Runs against the source tree
without importing the platform (no Modal SDK in the venv),
like the other lane checks.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "platform" / "app.py").read_text()
KERNEL = (ROOT / "worker-kaggle" / "hf_worker_kernel.py").read_text()

spec = importlib.util.spec_from_loader("hf_forge3d", loader=None)
mod = importlib.util.module_from_spec(spec)
mod.__dict__["re"] = re
code = APP[APP.index("HF_TEMPLATES = {"):APP.index("def hf_kaggle_push")]
exec(compile(code, "hf_forge3d_tables", "exec"), mod.__dict__)
HF_TEMPLATES = mod.HF_TEMPLATES
FORGE3D_TEMPLATES = mod.FORGE3D_TEMPLATES
build_hf_composition = mod.build_hf_composition
hf_clean_fields = mod.hf_clean_fields

# The five scenes and the presenter, each under its own name.
assert FORGE3D_TEMPLATES == ("turntable3d", "globe3d", "citypin3d",
                             "house3d", "bars3d")
for tid in FORGE3D_TEMPLATES:
    assert HF_TEMPLATES[tid]["duration"] in (6.0, 8.0, 9.0), tid
assert HF_TEMPLATES["bars3d"]["fields"] == ("title", "data")
assert HF_TEMPLATES["presenter"]["fields"] == ("script",)
assert HF_TEMPLATES["presenter"]["duration"] is None

# Field cleaning: the headline scenes, the bars data shape,
# and the presenter's mouth position with its defaults and
# its refusal outside the photo.
out = hf_clean_fields("turntable3d",
                      {"headline": "Mug", "subline": "Five colors"})
assert out["headline"] == "Mug" and out["color"] == "#6366f1"
out = hf_clean_fields("bars3d", {"title": "Sales",
                                 "data": "Jan, 10\nFeb, 20"})
assert out["points"] == [["Jan", 10.0], ["Feb", 20.0]]
out = hf_clean_fields("presenter", {"script": "Hello there"})
assert out["mouth_x"] == 50.0 and out["mouth_y"] == 68.0
out = hf_clean_fields("presenter", {"script": "Hi",
                                    "mouth_x": "40", "mouth_y": 55})
assert out["mouth_x"] == 40.0 and out["mouth_y"] == 55.0
for bad in ({"script": "Hi", "mouth_x": 2},
            {"script": "Hi", "mouth_y": "soon"}):
    try:
        hf_clean_fields("presenter", bad)
        raise AssertionError("a bad mouth position slipped through")
    except ValueError:
        pass

# Compositions: every scene loads the forge3d bundle with its
# own template id in the props; bars carries its points; the
# presenter loads its bundle, the mouth curve and the voice,
# with the photo name and mouth position in the props.
for tid in FORGE3D_TEMPLATES:
    body = ({"headline": "H", "subline": "S"} if tid != "bars3d"
            else {"title": "T", "data": "A, 1\nB, 2"})
    html = build_hf_composition(tid, hf_clean_fields(tid, body), 0.0)
    assert 'src="forge3d-scene.js"' in html, tid
    assert f'"template": "{tid}"' in html, tid
    assert 'data-fps="24"' in html, tid
html = build_hf_composition(
    "bars3d", hf_clean_fields("bars3d", {"title": "T",
                                         "data": "A, 1\nB, 2"}), 0.0)
props = json.loads(re.search(r"window\.__motion = (\{.*?\});", html).group(1))
assert props["points"][1] == ["B", 2.0]
fields = hf_clean_fields("presenter", {"script": "Hello"})
fields["duration"] = 12.5
fields["photo"] = "abc123.jpg"
html = build_hf_composition("presenter", fields, 0.0)
assert 'src="presenter-scene.js"' in html
assert 'src="mouth-curve.js"' in html
assert 'src="voice.wav"' in html
assert 'data-duration="12.5"' in html
props = json.loads(re.search(r"window\.__motion = (\{.*?\});", html).group(1))
assert props["photo"] == "abc123.jpg"
assert props["mouthX"] == 50.0 and props["mouthY"] == 68.0

# The platform seams, read off the source: the forge3d and
# presenter claim flags, the voice wait at claim time, the
# voice endpoint, the photo gate at create, the voice child
# queue, and the image branch of the upload route.
for pin in ('payload["forge3d"] = True',
            'payload["presenter"] = True',
            'payload["has_voice"] = True',
            'payload["source_name"]',
            '"/api/worker/hf/jobs/{job_id}/voice"',
            '"voice_child_id"',
            'voice_child = _queue_audio_child(',
            '(voice_child or {}).get("id")',
            'classify_input_images(',
            'presenter_input_refusal(presenter_verdict)',
            'stamp_input_verdict(job, presenter_verdict)',
            '"kind": "image"',
            'rec.get("kind") != "image"'):
    assert pin in APP, pin
# Audio children live in the jobs Dict and the queue helper
# takes the parent job itself; pin both so a rename anywhere
# breaks here instead of at claim time.
assert "audio_jobs" not in APP
claim = APP[APP.index("def hf_next_job"):APP.index("def hf_job_source")]
assert "child = jobs.get(child_id)" in claim
# The claim path skips a presenter whose wav is not on disk
# and fails one whose voice child errored.
claim = APP[APP.index("def hf_next_job"):APP.index("def hf_job_source")]
assert 'child.get("status") == "error"' in claim
assert "not Path(voice_path).exists()" in claim
assert "clipping.probe(voice_path)" in claim

# The studio: each entry is its own picker row with its own
# field block, and the presenter uploads through the template
# upload route like the captions clip.
for tid in FORGE3D_TEMPLATES + ("presenter",):
    assert f"{{ id: '{tid}'" in APP, tid
    assert f"id=\"motion-fields-{tid}\"" in APP, tid
    assert f"{tid}: 'motion-fields-{tid}'" in APP, tid
assert "uploadPresenterPhoto" in APP
assert "needsPresenterUpload" in APP
assert 'id="mf-presenter-photo"' in APP
assert 'id="mf-presenter-script"' in APP
for tid in ("turntable3d", "globe3d", "citypin3d", "house3d"):
    assert f"id=\"mf-{tid}-headline\"" in APP, tid
assert 'id="mf-bars3d-data"' in APP

# The worker: the new assets stage from the toolchain dataset,
# the forge3d flag stages its bundle on the motion bound, and
# the presenter branch pulls the voice and runs the curve.
for pin in ('"forge3d-scene.js", "presenter-scene.js"',
            '"mouth_curve.py"',
            'job.get("forge3d")',
            'job.get("presenter")',
            '}/voice"',
            '"mouth-curve.js"',
            'source_name = job.get("source_name") or "source.mp4"',
            "3d scene assets missing",
            "presenter assets missing"):
    assert pin in KERNEL, pin

# The bundles exist as sources, and the mouth curve tool is
# standard library only, like the rest of the worker kit.
assert (ROOT / "motion" / "src" / "forge3d.ts").exists()
assert (ROOT / "motion" / "src" / "presenter.ts").exists()
curve = (ROOT / "motion" / "presenter" / "mouth_curve.py").read_text()
for banned in ("import numpy", "import scipy", "import torch"):
    assert banned not in curve

print("forge3d + presenter pins OK")
