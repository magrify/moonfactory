"""The animation studio pass (2026-11-27): three picker
entries from the open source animation list, plus GIF
export for finished template jobs.

Pins the whole pattern end to end. The diagram explainer
(Motion Canvas's shape), the particle burst (Proton's
shape) and the vector scene (Two.js's shape) are original
composition only entries on the free lane: catalog rows,
field gates, deterministic builders with their signature
mechanics, and the studio picker wiring. The GIF option
rides the stored fields to the claim payload, the worker
kernel converts with ffmpeg's palette pipeline and posts
the GIF back while the job runs, the platform stores it
beside the MP4 without touching the job's status, and the
owner can download it from the library card.

Local proof: scratch/animation-studio-proof/.

Run:  modal-venv python scratch/check_animation_studio_pass.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))
import app as m  # noqa: E402

APP = (ROOT / "platform" / "app.py").read_text()
KERNEL = (ROOT / "worker-kaggle" / "hf_worker_kernel.py").read_text()

# The catalog entries: names, lengths, fields, $0 like the
# rest of the wing, and no scene bundle for any of them.
assert m.HF_TEMPLATES["diagram"]["name"] == "Diagram explainer"
assert m.HF_TEMPLATES["diagram"]["duration"] == 9.0
assert m.HF_TEMPLATES["diagram"]["fields"] == ("headline", "points")
assert m.HF_TEMPLATES["particles"]["name"] == "Particle burst"
assert m.HF_TEMPLATES["particles"]["duration"] == 6.0
assert m.HF_TEMPLATES["particles"]["fields"] == ("headline", "subline")
assert m.HF_TEMPLATES["vectorscene"]["name"] == "Vector scene"
assert m.HF_TEMPLATES["vectorscene"]["duration"] == 8.0
assert m.HF_TEMPLATES["vectorscene"]["fields"] == ("headline", "subline")
for tid in ("diagram", "particles", "vectorscene"):
    assert tid not in m.FORGE3D_TEMPLATES, tid

# Required fields gate all three at create.
for tid, body in (("diagram", {"points": "a\nb"}),
                  ("particles", {}),
                  ("vectorscene", {"subline": "x"})):
    try:
        m.hf_clean_fields(tid, body)
        raise SystemExit(f"{tid} accepted missing fields")
    except ValueError as e:
        assert "empty" in str(e), (tid, e)
clean = m.hf_clean_fields(
    "diagram", {"headline": "How booking works",
                "points": "Guest finds you\nGuest books direct"})
assert clean["points"].count("\n") == 1
assert clean["gif"] is False
clean = m.hf_clean_fields(
    "particles", {"headline": "Ten thousand guests", "gif": True})
assert clean["gif"] is True
clean = m.hf_clean_fields("vectorscene", {"headline": "Morning"})
assert clean["headline"] == "Morning"

# The diagram composition: a card per step, a connector and
# a signal pulse between cards, the words in place.
html = m.build_hf_composition("diagram", m.hf_clean_fields(
    "diagram", {"headline": "How booking works",
                "points": "Guest finds you\nGuest books direct\nYou keep the fee",
                "color": "#ff8800"}))
for frag in ("dg-node-0", "dg-node-2", "dg-link-1", "dg-link-2",
             "dg-pulse-1", "dg-dot-2", "STEP 3", "strokeDashoffset",
             "How booking works", "You keep the fee", "#ff8800",
             'data-duration="9"'):
    assert frag in html, frag
evil = m.build_hf_composition("diagram", m.hf_clean_fields(
    "diagram", {"headline": "<b>H</b>", "points": "a <script>x</script>\nb"}))
assert "<script>x</script>" not in evil.replace(
    '<script src="gsap.min.js"></script>', "")
assert "&lt;script&gt;" in evil

# The particle composition: the burst, the echo burst and
# the embers are all in the page, and the seeded generator
# makes the page byte for byte deterministic.
fields = m.hf_clean_fields(
    "particles", {"headline": "Ten thousand guests",
                  "subline": "thank you", "color": "#ff8800"})
html = m.build_hf_composition("particles", fields)
for frag in ("pt-head", "pt-sub", "pt-glow", "pt-a-0", "pt-a-75",
             "pt-b-0", "pt-b-29", "pt-e-0", "pt-e-19",
             "Ten thousand guests", 'data-duration="6"'):
    assert frag in html, frag
assert m.build_hf_composition("particles", fields) == html

# The vector scene composition: sun, hills, the drawn path,
# clouds and birds, at eight seconds.
html = m.build_hf_composition("vectorscene", m.hf_clean_fields(
    "vectorscene", {"headline": "Morning at the lake",
                    "subline": "coffee at seven", "color": "#ff8800"}))
for frag in ("vs-sun", "vs-glow", "vs-stars", "vs-hill-far",
             "vs-hill-mid", "vs-hill-near", "vs-river",
             "vs-cloud-0", "vs-bird-1", "vs-head", "vs-sub",
             "Morning at the lake", 'data-duration="8"'):
    assert frag in html, frag

# The GIF option end to end in the platform source: the
# claim flags it, the worker posts it while the job runs,
# the owner downloads it, and the job view carries it.
assert 'payload["gif"] = True' in APP
assert '@web.post("/api/worker/hf/jobs/{job_id}/gif")' in APP
assert '@web.get("/api/jobs/{job_id}/gif")' in APP
assert '"has_gif": bool(job.get("gif"))' in APP
assert 'f"{job_id}.gif"' in APP
assert "GIF89a" in APP

# The worker kernel: the palette pipeline, and the upload
# ordered before the MP4 post that finishes the job.
assert "def write_gif(" in KERNEL
assert "palettegen=stats_mode=diff" in KERNEL
assert "paletteuse=dither=bayer:bayer_scale=4" in KERNEL
assert "job.get(\"gif\")" in KERNEL
assert KERNEL.index('out_path.with_suffix(".gif")') \
    < KERNEL.index("uploading result")

# The picker: entries, blocks, notes, the field blocks, the
# payload branches and the shared GIF checkbox.
for frag in ("{ id: 'diagram', name: 'Diagram explainer' }",
             "{ id: 'particles', name: 'Particle burst' }",
             "{ id: 'vectorscene', name: 'Vector scene' }",
             "diagram: 'motion-fields-explainer'",
             "particles: 'motion-fields-particles'",
             "vectorscene: 'motion-fields-vectorscene'",
             'id="motion-fields-particles"',
             'id="motion-fields-vectorscene"',
             'id="mf-particles-headline"', 'id="mf-particles-subline"',
             'id="mf-vectorscene-headline"', 'id="mf-vectorscene-subline"',
             "model === 'diagram'", "'particles', 'vectorscene'",
             'id="mf-gif"', "payload.gif = true",
             "diagram: 'Your points become steps in a diagram",
             "particles: 'A burst of sparks",
             "vectorscene: 'A flat illustrated landscape",
             "j.has_gif", "/gif\" download>Download GIF</a>"):
    assert frag in APP, frag

print("animation studio pass pins OK")
