"""Local checks for the Edit/Clone section (Jack, 2026-11-26).

Pins the section's shape so a later edit cannot quietly break it:

- the section lives inside the Clip surface (no new tab): the
  served studio HTML carries the Edit / Clone card with its
  link, upload and library inputs, the hook and character
  controls, the character picker, and the clone button;
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
  (create_job) on seedance2_mini, the cheapest lane whose
  wired route forwards a reference video AND a reference
  image together; its body carries Video1 as the source's
  hosted URL and Image1 as the character's first picture, with
  the reference seconds capped by the lane's own cap table;
- the worker finishes its files the platform way (content
  hash, output classification, gallery kept private), the
  mirror fails a stuck Edit/Clone job clean, the file route
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
import pricing  # noqa: E402

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

# -- the section inside the Clip surface ---------------------------------

check('id="clip-pane"' in app.APP_HTML, "clip pane missing")
check("Edit / Clone" in app.APP_HTML,
      "the Edit / Clone card is missing from the Clip surface")
for el in ("ec-url", "ec-file", "ec-library", "ec-add", "ec-hook",
           "ec-char-name", "ec-clone-char", "ec-char-files",
           "ec-make-char", "ec-character", "ec-clone", "botcheck4"):
    check(f'id="{el}"' in app.APP_HTML,
          f"the Edit / Clone control {el} is missing")
check(app.APP_HTML.count('id="tab-') == 2,
      "a new tab appeared: the section must live inside Clip")
check("Seedance 2.0 Mini" in app.APP_HTML,
      "the clone card does not name its model under its own name")

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
      "finished Edit/Clone files skip the hash or the classifier")
check("_validate_image_b64" in worker,
      "character frames skip the image validation")
check('"gallery_public": False' in func_src("_editclone_new_job"),
      "Edit/Clone working material is not kept out of the gallery")

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

# -- the clone: normal path, cheapest eligible lane ------------------------

check(app.EDITCLONE_CLONE_MODEL == "seedance2_mini",
      "the clone lane moved off seedance2_mini")
check(app.EDITCLONE_CLONE_RESOLUTION == "480p",
      "the clone resolution moved off the lane's sold tier")
caps = app.ref_caps_for("seedance2_mini")
check(caps[0] >= 1 and caps[1] >= 1,
      "the clone lane no longer takes a reference image and video")
check(app.provider_for("seedance2_mini", "480p") == "kie",
      "seedance2_mini no longer routes to Kie")
kie = func_src("submit_kie_job")
check("video_urls" in kie and "image_urls" in kie,
      "the Kie submit no longer forwards reference videos and images")
comet = func_src("submit_cometapi_job")
check("reference_video" not in comet,
      "the CometAPI submit grew a reference video path; recheck the lane pick")
# Cheapest among the lanes whose declared caps take both inputs.
# Eligibility is the explicit MODEL_REF_CAPS entry (no default):
# grok declares none and its Kie task map has no reference mode,
# grok15 declares (7, 0, 0), seedance10pro_fast declares none and
# its VidLab spec carries no video_input, and vidu_q3's Novita
# submit never sends a video; all four only LOOK cheaper.
eligible = []
for model, c in app.MODEL_REF_CAPS.items():
    rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(model)
    if rates and c[0] >= 1 and c[1] >= 1:
        eligible.append((min(rates.values()), model))
eligible.sort()
check(eligible and eligible[0][1] == "seedance2_mini",
      f"seedance2_mini is not the cheapest both-inputs lane: {eligible[:3]}")

clone_fn = func_src("create_editclone_clone")
check("create_job" in clone_fn,
      "the clone does not dispatch through the normal creation path")
body_fn = func_src("_editclone_clone_body")
check("reference_video_urls" in body_fn
      and "reference_image_urls" in body_fn,
      "the clone body does not carry Video1 and Image1")
check("CLONE_PROMPT_TEMPLATE" in body_fn,
      "the clone body does not fall back to the template prompt")
check("MODEL_REF_VIDEO_SECONDS_CAP" in body_fn,
      "the clone body does not cap the reference seconds by the lane table")
check("_find_character" in body_fn,
      "the clone body does not resolve the character from the store")

# -- platform fit: mirror bound, file route, retry -------------------------

mirror = func_src("mirror_status")
check('"editclone"' in mirror and "EDITCLONE_POLL_LIMIT_SECONDS" in mirror,
      "a stuck Edit/Clone job has no mirror bound")
ensure = func_src("ensure_processed")
check('"editclone"' in ensure,
      "the file route would run Edit/Clone files through the finishing chain")
retry = func_src("retry_job")
check('"hook_clip"' in retry and '"ec_character"' in retry,
      "retry would rebuild an Edit/Clone step as a generation")
pub = func_src("public_job")
for label in ('"Source video"', '"Hook"', '"Character"'):
    check(label in pub, f"public_job has no {label} label")

if failures:
    print("FAIL check_editclone_section")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_editclone_section")
