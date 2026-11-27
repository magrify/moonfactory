"""The two picker entries the motion skills pass added
(2026-11-26): the product showcase and the logo draw-in.

Pins the whole pattern end to end. The showcase is a sixth
forge3d scene whose photo crosses the STANDARD input gate
at create (the presenter's own-photo exemption is called
by name as not extending here), stamps its verdict, and
rides the claim path's generic source staging; the draw-in
is a composition-only entry drawing the Play Hollow mark
as SVG line art, with no scene bundle and no upload. Both
are $0 templates in the picker with their own field blocks,
and the scene bundle carries the showcase builder: that is
pinned where it is determined, in the bundle's source and
build config, because the bundle itself is a build artifact
(motion/build.mjs writes motion/scene/, gitignored) that
ships as a loose file in the Kaggle toolchain dataset and
is never in the tree. Reading it here made this check fail
on every fresh checkout for a file nothing in the project
produces at that path; the sibling check for the same asset
family (check_forge3d_presenter) pins sources the same way.

Local render proof: scratch/new-templates-proof/.

Run:  modal-venv python scratch/check_new_templates.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))
import app as m  # noqa: E402

APP = (ROOT / "platform" / "app.py").read_text()
SCENE = (ROOT / "motion" / "src" / "forge3d.ts").read_text()
BUILD = (ROOT / "motion" / "build.mjs").read_text()

# The catalog entries: names, lengths, fields, $0 like the
# rest of the wing (template jobs charge nothing, ever).
assert m.HF_TEMPLATES["showcase3d"]["name"] == "Product showcase 3D"
assert m.HF_TEMPLATES["showcase3d"]["duration"] == 8.0
assert m.HF_TEMPLATES["showcase3d"]["fields"] == ("headline", "subline")
assert m.HF_TEMPLATES["logodraw"]["name"] == "Logo draw-in"
assert m.HF_TEMPLATES["logodraw"]["duration"] == 4.0
assert m.HF_TEMPLATES["logodraw"]["fields"] == ("headline",)
assert "showcase3d" in m.FORGE3D_TEMPLATES
assert "logodraw" not in m.FORGE3D_TEMPLATES

# Required fields gate both at create.
for tid in ("showcase3d", "logodraw"):
    try:
        m.hf_clean_fields(tid, {})
        raise SystemExit(f"{tid} accepted empty fields")
    except Exception as e:
        assert "empty" in str(getattr(e, "detail", e)), (tid, e)
clean = m.hf_clean_fields("showcase3d", {"headline": "Hi"})
assert clean["headline"] == "Hi"
clean = m.hf_clean_fields("logodraw", {"headline": "Hi"})
assert clean["headline"] == "Hi"

# The showcase's create path: the standard gate, the
# verdict stamp, and NO presenter exemption anywhere near
# it (the exemption function is called for presenter only).
create = APP[APP.index("async def create_template"):]
create = create[:20000]
assert 'if template_id == "showcase3d":' in create
assert "input_gate_refusal(" in create
assert "classify_input_images(" in create
assert "stamp_input_verdict(job, showcase_verdict)" in create
assert create.count("presenter_input_refusal(") == 1
assert "upload your product photo first" in create

# The claim path rebuilds the showcase composition with
# the staged photo's name and flags the source download.
claim = APP[APP.index("def hf_next_job"):]
claim = claim[:claim.index("def hf_job_source")]
assert 'if job["template"] == "showcase3d":' in claim
assert 'fields["photo"] = Path(job["upload_path"]).name' in claim
assert 'payload["source_name"] = Path(job["upload_path"]).name' in claim

# The scene: the builder exists in the bundle's source and
# is registered in its dispatcher, the photo gate is armed
# only for it, and the parts assemble on the directional
# easings. The built bundle carries the builder by
# construction: build.mjs bundles this one entry wholesale
# (no minify, no subsetting), the dispatcher map that
# registers buildShowcase runs at module load, and the
# outfile below is the forge3d-scene.js the worker stages.
assert "function buildShowcase(" in SCENE
assert "showcase3d: buildShowcase" in SCENE
assert "builders[props.template]" in SCENE
assert 'entryPoints: ["src/forge3d.ts"]' in BUILD
assert 'outfile: "scene/forge3d-scene.js"' in BUILD
assert "photoGateArmed" in SCENE

# The draw-in composition: the mark's two shapes, the brand
# colors, the draw mechanics, and the headline.
html = m.build_hf_composition("logodraw", {"headline": "Acme"}, 0.0)
assert 'id="ld-crescent"' in html and 'id="ld-tri"' in html
assert "#818cf8" in html and "#f5d67a" in html
assert 'pathLength="1"' in html and "strokeDashoffset" in html
assert "Acme" in html and "gsap" in html
# And the showcase composition carries its photo prop.
html = m.build_hf_composition(
    "showcase3d", {"headline": "Hi", "photo": "p.jpg"}, 0.0)
assert '"photo": "p.jpg"' in html and '"template": "showcase3d"' in html

# The picker: both entries, both blocks, both notes, the
# showcase's parked upload flow.
for frag in ("{ id: 'showcase3d', name: 'Product showcase 3D' }",
             "{ id: 'logodraw', name: 'Logo draw-in' }",
             "showcase3d: 'motion-fields-showcase3d'",
             "logodraw: 'motion-fields-logodraw'",
             'id="motion-fields-showcase3d"',
             'id="motion-fields-logodraw"',
             'id="mf-showcase3d-photo"', 'id="mf-logodraw-headline"',
             "needsShowcaseUpload", "uploadShowcasePhoto"):
    assert frag in APP, frag

print("new templates pins OK")
