"""Clone conditioning checks (2026-11-28).

The failure this pins against: two end-to-end clone "proofs"
(2026-11-27 job 1d266678d6be9010, 2026-11-28 job
b6b3cea548f24875) were declared passes on delivery alone —
served, playable, settled — on the house Wan 2.2 lane, whose
dispatch carries no reference channel at all: no frame of the
source is ever sent, so the output was a prompt-invented video
of a woman who was not the owner's character, in a scene that
was not the source. The full diagnosis is
~/workspace/sinclip/clone-diagnosis-2026-11-28.md. Jack's call
(2026-11-28, option C): Kling motion now, house Animate next —
the Clone picker's default is a motion lane.

Pinned here, per lane the Clone picker offers:

- every lane's clone adaptation (editclone_clone_adaptation)
  is one of the five known classes, and each class is
  classified by whether the source actually reaches the lane:
  "motion" and "both" send the source video itself, "frames"
  sends still frames grabbed from the source, "start" and
  "text" send nothing of the source;
- the three MOTION_MODELS resolve to "motion";
- the picker default (EDITCLONE_CLONE_MODEL) resolves to
  "motion": the suite fails if the default ever lands on a
  lane that does not send the source video;
- the composed clone body, driven for real below with the
  builder extracted from app.py, carries the hosted source in
  reference_video_urls plus the character as the start image
  on the motion lanes (the default included), carries the
  source as a reference on a "both" lane, and carries NO
  reference video on the no-source lanes, which stay in the
  picker only under their feed-line disclosure;
- the picker's feed lines disclose the no-source classes
  ("Your video itself is not sent." for start, "Neither your
  video nor your character picture is sent." for text), keyed
  on the adaptation class;
- the two motion submit builders carry the subject image and
  the driving video together: fal's wan_motion payload
  (image_url + video_url) and UnificAlly's kling_motion input
  (image_url + video_url).

Run:  modal-venv python scratch/check_clone_conditioning.py
"""
import ast
import math
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)

# -- the classes, classified by whether the source is sent ----------------

SENDS_VIDEO = {"motion", "both"}
SENDS_FRAMES = {"frames"}
SENDS_NOTHING = {"start", "text"}
KNOWN = SENDS_VIDEO | SENDS_FRAMES | SENDS_NOTHING

adapt = app.editclone_clone_adaptation
picker_models = set(app.PER_SECOND_MODELS) | set(app.HOUSE_MODEL_PRICING_LANE)
check(len(picker_models) > 50,
      "the clone picker universe collapsed; the classification "
      "below would prove nothing")
for model in sorted(picker_models):
    cls = adapt(model)
    check(cls in KNOWN,
          f"{model}: clone adaptation {cls!r} is not one of the "
          f"five known classes; classify it here before it ships")

for model in app.MOTION_MODELS:
    check(adapt(model) == "motion",
          f"{model} is in MOTION_MODELS but resolves to "
          f"{adapt(model)!r}, not 'motion'")

# -- the default must be a true-clone lane --------------------------------

check(app.EDITCLONE_CLONE_MODEL in picker_models,
      "the clone picker default is not a model the picker offers")
check(adapt(app.EDITCLONE_CLONE_MODEL) == "motion",
      f"the clone picker default ({app.EDITCLONE_CLONE_MODEL}) "
      f"resolves to {adapt(app.EDITCLONE_CLONE_MODEL)!r}: a "
      "customer who changes nothing must get a lane that sends "
      "the source as its driving video (Jack, 2026-11-28)")

# -- the feed lines disclose the no-source classes -------------------------

check("Your video itself is not sent." in SRC
      and "o.dataset.adaptation === 'start'" in SRC,
      "the picker no longer discloses, on the start class, that "
      "the video itself is not sent")
check("Neither your video nor your character picture is sent." in SRC
      and "o.dataset.adaptation === 'text'" in SRC,
      "the picker no longer discloses, on the text class, that "
      "neither the video nor the character picture is sent")
check("Uses your character picture as the subject and your "
      "video as the motion guide" in SRC,
      "the motion feed line no longer states the true shape "
      "(subject image plus driving video)")

# -- drive the real clone body builder ------------------------------------
# _editclone_clone_body is a closure inside the web layer; the
# suite extracts its source and drives it against fixture
# lookups, the same shape the endpoint composes at runtime.

def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{name} not found in app.py")


class _HTTPException(Exception):
    """The builder's refusal shape (fastapi's HTTPException is
    imported inside the web layer and is not installed in this
    venv; the fixture drives below never raise it)."""

    def __init__(self, status_code=None, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


ns = {
    "math": math,
    "HTTPException": _HTTPException,
    "EDITCLONE_CLONE_MODEL": app.EDITCLONE_CLONE_MODEL,
    "CLONE_PROMPT_TEMPLATE": app.CLONE_PROMPT_TEMPLATE,
    "HOUSE_MODEL_PRICING_LANE": app.HOUSE_MODEL_PRICING_LANE,
    "PER_SECOND_MODELS": app.PER_SECOND_MODELS,
    "MODEL_REF_VIDEO_SECONDS_CAP": app.MODEL_REF_VIDEO_SECONDS_CAP,
    "MAX_SEGMENTS": app.MAX_SEGMENTS,
    "SEGMENT_SECONDS": app.SEGMENT_SECONDS,
    "editclone_clone_adaptation": app.editclone_clone_adaptation,
    "native_lengths": app.native_lengths,
    "ref_caps_for": app.ref_caps_for,
    "pricing": app.pricing,
    "_editclone_source_job": lambda user, sid: {
        "id": "pinsrc", "duration_seconds": 10.0,
        "source_width": 720, "source_height": 1280},
    "_find_character": lambda user, cid: {
        "id": "pinchar", "images": [{"b64": "QUJD"}]},
    "_editclone_hosted_source": lambda src: "https://files.example/source.mp4",
    "_editclone_char_image_url": lambda character: "https://files.example/char.png",
    "_editclone_source_frame_urls": lambda src, dur: [
        "https://files.example/f1.jpg", "https://files.example/f2.jpg"],
}
exec(textwrap.dedent(func_src("_editclone_out_seconds")), ns)
exec(textwrap.dedent(func_src("_editclone_clone_body")), ns)
clone_body = ns["_editclone_clone_body"]
USER = {"id": "pinuser"}

# The default, unnamed: exactly what a customer who changes
# nothing in the picker sends.
body = clone_body(USER, {"source_job_id": "pinsrc",
                         "character_id": "pinchar"})
check(body.get("model") == app.EDITCLONE_CLONE_MODEL,
      "the composed default clone body is not on the picker default")
check(body.get("reference_video_urls") == ["https://files.example/source.mp4"],
      "the default clone body does not carry the hosted source "
      "as its driving video")
check(body.get("start_image_png_b64") == "QUJD",
      "the default clone body does not carry the character picture")
check(2 <= float(body.get("duration_seconds") or 0) <= 30,
      "the default clone body's length is outside the motion "
      "contract's 2-30s driving window")

# Every motion lane composes the same true-clone shape.
for model in app.MOTION_MODELS:
    body = clone_body(USER, {"source_job_id": "pinsrc",
                             "character_id": "pinchar",
                             "model": model})
    check(body.get("reference_video_urls") == ["https://files.example/source.mp4"]
          and body.get("start_image_png_b64") == "QUJD",
          f"{model}: the composed clone body does not carry the "
          "driving video plus the character subject together")

# A "both" lane carries the source as a reference video.
body = clone_body(USER, {"source_job_id": "pinsrc",
                         "character_id": "pinchar",
                         "model": "seedance2_mini"})
check(body.get("reference_video_urls") == ["https://files.example/source.mp4"]
      and body.get("reference_image_urls") == ["https://files.example/char.png"],
      "seedance2_mini (both): the composed clone body lost the "
      "source reference video or the character reference image")

# A "frames" lane carries frames of the source, never the video.
body = clone_body(USER, {"source_job_id": "pinsrc",
                         "character_id": "pinchar",
                         "model": "veo3"})
check(not body.get("reference_video_urls")
      and (body.get("reference_image_urls") or [None])[0]
      == "https://files.example/char.png"
      and len(body.get("reference_image_urls") or []) > 1,
      "veo3 (frames): the composed clone body should carry the "
      "character plus source frames and no reference video")

# The no-source lanes are explicitly no-source in the composed
# body: that is the shape the feed lines disclose, and the
# shape that must never again wear the default.
for model in ("wan2.2", "kling3"):
    body = clone_body(USER, {"source_job_id": "pinsrc",
                             "character_id": "pinchar",
                             "model": model})
    check(not body.get("reference_video_urls")
          and not body.get("reference_image_urls"),
          f"{model} ({adapt(model)}): the composed clone body "
          "carries source material its class says it never sends; "
          "reclassify it above")

# -- the motion submits carry subject image + driving video ----------------

def dict_keys_under(test_needle, fn_name):
    """The string keys of every dict literal built under an
    if/elif whose test mentions test_needle, inside fn_name."""
    keys = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.If) \
                        and test_needle in ast.unparse(sub.test):
                    for inner in ast.walk(sub):
                        if isinstance(inner, ast.Dict):
                            for k in inner.keys:
                                if isinstance(k, ast.Constant) \
                                        and isinstance(k.value, str):
                                    keys.add(k.value)
    return keys


fal_keys = dict_keys_under("wan_motion", "submit_fal_job")
check({"image_url", "video_url"} <= fal_keys,
      "fal's wan_motion payload no longer carries the subject "
      f"image and the driving video together (keys: {sorted(fal_keys)})")
uni_keys = dict_keys_under("kling_motion", "submit_unifically_job")
check({"image_url", "video_url"} <= uni_keys,
      "UnificAlly's kling_motion input no longer carries the "
      f"subject image and the driving video together (keys: {sorted(uni_keys)})")

if failures:
    print("check_clone_conditioning: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_clone_conditioning: PASS")
