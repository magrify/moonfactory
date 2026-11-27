"""Local checks for the Clone section (Jack, 2026-11-26).

Pins the section's shape so a later edit cannot quietly break it:

- the section is titled Clone and lives as its own section
  inside Create, alongside the composer (not inside the Clip
  pane, and no new tab): the served studio HTML carries the
  Clone card with its link, upload and library inputs, the
  hook and character controls, the character picker, the
  model picker and the clone button;
- the clone prompt is prefilled with Jack's template word for
  word: the served textarea, the CLONE_PROMPT_TEMPLATE
  constant and this file's own copy must all three agree;
- ingest takes any public video site through the yt-dlp guard
  (TikTok, YouTube, X, Instagram pass; non-http schemes,
  localhost and private address literals are refused), with
  the duration and size caps named as constants;
- the hook is the opening seconds under one named constant,
  cut by clipping.trim_opening;
- characters come from the existing store: frames are grabbed
  from the source and appended through the same record shape
  create_character writes, and the custom path posts to the
  existing /api/characters endpoint;
- the clone dispatches through the normal creation path
  (create_job) on the model the user picks in the section's
  own picker, which is built from the same config catalog as
  the Create picker and defaults to seedance2_mini; the
  platform adapts the inputs to the picked lane from the
  lane capability data (editclone_clone_adaptation): both
  references where the lane takes them, Image1 plus source
  frames where it takes reference images only, Image1 as
  the start image on start-image lanes, subject plus
  driving video on Wan Motion, the prompt alone where a
  lane takes no visual input;
- the worker finishes its files the platform way (content
  hash, output classification, gallery kept private), the
  mirror fails a stuck Clone job clean, the file route
  serves its files untouched, and retry refuses these kinds
  plainly instead of rebuilding them as generations.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_editclone_section.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import clipping  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


TEMPLATE = (
    "Recreate Video1, replace only the original character with "
    "Image1. Strictly preserve 100% of Image1's face, hair, body, "
    "clothing and details. Match Video1's movements, expressions, "
    "camera angles, framing, lighting, background, timing and "
    "pacing. Seamless, photorealistic character replacement. "
    "No other changes."
)

# -- the template, word for word, in all three places -------------------

check(app.CLONE_PROMPT_TEMPLATE == TEMPLATE,
      "CLONE_PROMPT_TEMPLATE drifted from Jack's words")
check(TEMPLATE in app.APP_HTML,
      "the served clone prompt is not prefilled with the template")
check('id="ec-prompt"' in app.APP_HTML,
      "the clone prompt field is missing from the studio page")

# -- the section: titled Clone, its own section inside Create -------------

html = app.APP_HTML
check('id="create-pane"' in html and 'id="clip-pane"' in html,
      "a studio pane is missing")
check("<h2>Clone</h2>" in html,
      "the Clone card does not carry its Clone title")
check("Edit / Clone" not in html,
      "the section still shows its old Edit / Clone name")
check(html.count('id="tab-') == 2,
      "a new tab appeared: the section must live inside Create")
check(html.index('id="create-pane"') < html.index("<h2>Clone</h2>")
      < html.index('id="clip-pane"'),
      "the Clone card is not a section inside Create")
check(html.index('id="ec-clone"') < html.index('id="clip-pane"'),
      "the Clone card still sits inside the Clip pane")
for el in ("ec-url", "ec-file", "ec-library", "ec-add", "ec-hook",
           "ec-char-name", "ec-clone-char", "ec-char-files",
           "ec-make-char", "ec-character", "ec-model", "ec-feed",
           "ec-clone", "botcheck4"):
    check(f'id="{el}"' in html,
          f"the Clone control {el} is missing")

# -- the model picker: same catalog as Create, feed line ------------------

check("buildEditCloneModels" in html,
      "the Clone model picker is not built from the config catalog")
check("clone_adaptation" in html and "clone_default_model" in html,
      "the Clone picker does not read the served adaptation data")
check('"clone_adaptation"' in SRC and '"clone_default_model"' in SRC,
      "the config payload does not serve the clone adaptation")
check(html.count("model: $('ec-model').value") == 2,
      "the quote or the create does not send the picked model")
for line in ("Sends your video and your character picture to ",
             "Sends frames from your video plus your character picture to ",
             "Starts from your character picture and follows your prompt on ",
             "motion guide on ",
             "Sends your prompt only to "):
    check(line in html, f"the feed line for a branch is missing: {line!r}")

# -- ingest: any public site, capped, guarded ----------------------------

for good in ("https://www.tiktok.com/@someone/video/7300000000000000000",
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "https://youtu.be/dQw4w9WgXcQ",
             "https://x.com/someone/status/1700000000000000000",
             "https://www.instagram.com/reel/C0000000000/",
             "https://vimeo.com/123456789"):
    try:
        clipping.validate_ingest_url(good)
    except ValueError:
        check(False, f"ingest guard refused a public link: {good}")
for bad in ("ftp://example.com/v.mp4", "file:///etc/passwd",
            "http://localhost:8000/v", "http://127.0.0.1/v",
            "https://10.1.2.3/v", "https://192.168.0.9/v",
            "https://[::1]/v", "", "notaurl"):
    try:
        clipping.validate_ingest_url(bad)
        check(False, f"ingest guard accepted a bad link: {bad!r}")
    except ValueError:
        pass
check(callable(clipping.fetch_any_info)
      and callable(clipping.download_any_video),
      "the any-site fetch functions are missing from clipping.py")
check("yt_dlp" in Path(ROOT / "platform" / "clipping.py").read_text(),
      "ingest does not run through yt-dlp")
check(app.EDITCLONE_MAX_SOURCE_SECONDS == 600,
      "the ingest duration cap moved")
check(app.EDITCLONE_MAX_UPLOAD_BYTES == 1024 ** 3,
      "the ingest size cap moved")
check("yt-dlp" in (ROOT / "THIRD-PARTY-NOTICES.md").read_text(),
      "yt-dlp is missing from THIRD-PARTY-NOTICES.md")

# -- the hook ------------------------------------------------------------

check(app.EDITCLONE_HOOK_SECONDS == 5,
      "the hook length moved from its named constant")
check(callable(clipping.trim_opening),
      "clipping.trim_opening is missing")
check(callable(clipping.grab_frames),
      "clipping.grab_frames is missing")
check(callable(clipping.probe_media),
      "clipping.probe_media is missing")

# -- endpoints and the worker ---------------------------------------------

for route in ("/api/editclone/upload", "/api/editclone/ingest",
              "/api/editclone/hook", "/api/editclone/character",
              "/api/editclone/clone", "/api/editclone/clone/quote"):
    check(f'"{route}"' in SRC, f"endpoint {route} is missing")

worker = func_src("process_editclone_job")
check("download_any_video" in worker,
      "the ingest worker does not download through the any-site fetch")
check("trim_opening" in worker,
      "the hook step does not cut through clipping.trim_opening")
check("grab_frames" in worker,
      "the character step does not grab frames from the source")
check("stamp_content_hash" in worker and "classify_job_output" in worker,
      "finished Clone files skip the hash or the classifier")
check("_validate_image_b64" in worker,
      "character frames skip the image validation")
check('"gallery_public": False' in func_src("_editclone_new_job"),
      "Clone working material is not kept out of the gallery")

ingest = func_src("create_editclone_source")
check("enforce_prompt_gate" in ingest,
      "ingest skips the illegal-content gate on the source title")
check("validate_ingest_url" in ingest,
      "ingest does not run the any-site guard")
check("is_paid(user)" in ingest,
      "ingest is not a paid feature like the rest of the surface")

# -- characters ride the existing store -----------------------------------

check("user[\"characters\"]" in worker,
      "the frame-built character does not land in the character store")
check('"train_status": "none"' in worker and '"lora_url": None' in worker,
      "the frame-built character record shape drifted from create_character")
check("await api('/api/characters'" in app.APP_HTML,
      "the custom character path does not use the existing endpoint")

# -- the clone: any picked model, inputs adapted by capability ------------

check(app.EDITCLONE_CLONE_MODEL == "seedance2_mini",
      "the clone picker default moved off seedance2_mini")
check(app.EDITCLONE_CLONE_MODEL in app.PER_SECOND_MODELS,
      "the clone picker default is not a per-second model")
check(all(m in app.MODEL_LABELS for m in app.PER_SECOND_MODELS),
      "a per-second model is missing from the picker catalog data")

adapt = app.editclone_clone_adaptation
for model in ("seedance2_mini", "seedance", "kling4", "magi2",
              "seedance25_flash", "wan3"):
    check(adapt(model) == "both",
          f"{model} should clone with both references, got {adapt(model)}")
for model in ("veo3", "veo3_fast", "grok", "grok15",
              "seedance2_fast", "vidu_q4", "kling_o1"):
    check(adapt(model) == "frames",
          f"{model} should clone from frames, got {adapt(model)}")
for model in ("veo3_lite", "grok15_lite", "kling3", "wan22_unc",
              "hailuo23_fast", "ltx25"):
    check(adapt(model) == "start",
          f"{model} should clone from a start image, got {adapt(model)}")
check(adapt("wan_motion") == "motion",
      "Wan Motion should clone as subject plus driving video")
# The text branch has no lane today: a zero-cap entry outside
# the mode sets is the data shape that lands there.
app.MODEL_REF_CAPS["__clone_text_probe__"] = (0, 0, 0)
try:
    check(adapt("__clone_text_probe__") == "text",
          "a no-visual-input lane should clone from the prompt alone")
finally:
    del app.MODEL_REF_CAPS["__clone_text_probe__"]
branches = {"both", "frames", "start", "motion", "text"}
check(all(adapt(m) in branches for m in app.PER_SECOND_MODELS),
      "a per-second model maps to no adaptation branch")

caps = app.ref_caps_for("seedance2_mini")
check(caps[0] >= 1 and caps[1] >= 1,
      "the default lane no longer takes a reference image and video")
check(app.provider_for("seedance2_mini", "480p") == "kie",
      "seedance2_mini no longer routes to Kie")
kie = func_src("submit_kie_job")
check("video_urls" in kie and "image_urls" in kie,
      "the Kie submit no longer forwards reference videos and images")

clone_fn = func_src("create_editclone_clone")
check("create_job" in clone_fn,
      "the clone does not dispatch through the normal creation path")
body_fn = func_src("_editclone_clone_body")
check("editclone_clone_adaptation" in body_fn,
      "the clone body does not adapt its inputs to the picked lane")
check("PER_SECOND_MODELS" in body_fn,
      "the clone body does not refuse a non per-second pick")
check("reference_video_urls" in body_fn
      and "reference_image_urls" in body_fn,
      "the clone body does not carry Video1 and Image1 as references")
check("start_image_png_b64" in body_fn,
      "the clone body never carries Image1 as a start image")
check("MODEL_RESOLUTIONS" in body_fn,
      "the clone body does not pick the lane's own sold resolution")
check("CLONE_PROMPT_TEMPLATE" in body_fn,
      "the clone body does not fall back to the template prompt")
check("MODEL_REF_VIDEO_SECONDS_CAP" in body_fn,
      "the clone body does not cap the reference seconds by the lane table")
check("_find_character" in body_fn,
      "the clone body does not resolve the character from the store")
frames_fn = func_src("_editclone_source_frame_urls")
check("grab_frames" in frames_fn and "EDITCLONE_FRAME_FRACTIONS" in frames_fn,
      "the frames branch does not grab the source frames it sends")
check("_fal_upload_bytes" in frames_fn,
      "the frames branch does not host the frames it sends")

# -- platform fit: mirror bound, file route, retry -------------------------

mirror = func_src("mirror_status")
check('"editclone"' in mirror and "EDITCLONE_POLL_LIMIT_SECONDS" in mirror,
      "a stuck Clone job has no mirror bound")
ensure = func_src("ensure_processed")
check('"editclone"' in ensure,
      "the file route would run Clone files through the finishing chain")
retry = func_src("retry_job")
check('"hook_clip"' in retry and '"ec_character"' in retry,
      "retry would rebuild a Clone step as a generation")
pub = func_src("public_job")
for label in ('"Source video"', '"Hook"', '"Character"'):
    check(label in pub, f"public_job has no {label} label")

if failures:
    print("FAIL check_editclone_section")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_editclone_section")
