"""Local checks for the Clone tab (Jack, 2026-11-26; a
top-level tab since 2026-11-27).

Pins the tab's shape so a later edit cannot quietly break it:

- the tab is titled Clone and stands as a top-level tab
  beside Create and Clip (Jack, 2026-11-27: Create / Clip /
  Clone): the tab bar carries exactly those three in that
  order, the Clone card lives in its own clone pane and no
  longer inside Create, and the served studio HTML carries
  the Clone card with its link, upload and library inputs,
  the hook and character controls, the character picker, the
  model picker and the clone button;
- the flow stands as four numbered steps visible from the
  first paint (2026-11-27 redesign): 1 Add a video,
  2 Choose your character, 3 Trim the hook, 4 Model and
  price, in that order, with nothing hidden behind the old
  ec-source container: the character picker stands in
  step 2 from the start, the two controls that need the
  source (hook, clone the character) start disabled with a
  note saying what they wait for, no message line carries
  text in the served HTML and the pick-one-first error
  exists only inside the Add click handler, both file
  picks wear the site's .drop zone over hidden inputs, the
  model options carry each model's own $/s rate from the
  same catalog figure Create's picker reads, the quote
  speaks Create's exact price words with the video's
  length in it, the Clone button carries the price, and
  the free steps and the rights line read in one short
  line each where they belong;
- the clone prompt is prefilled with Jack's template word for
  word: the served textarea, the CLONE_PROMPT_TEMPLATE
  constant and this file's own copy must all three agree;
- the library source is picked through one quiet Pick from
  your library row and a thumbnail panel headed Your
  Moonfactory library (Jack, 2026-11-27), never a dropdown
  of full prompts and never a giant pill competing with the
  step's primary; the picked job id rides the hidden
  ec-library input into the same Add flow;
- step 1 is polished (Jack, 2026-11-27): the library pick
  is that single quiet row with the word library appearing
  once in the block, Add this video starts disabled with a
  waiting note and wakes only when a link is typed, a file
  is picked, or a library video is chosen, and Upload a
  video file is said once, as the drop zone's own heading,
  not again as a label above it;
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
- the four house lanes (Wan 2.2, Wan 2.2 Uncut, LTX-2.5
  Uncensored, HunyuanVideo 1.5) are in the picker too: the
  config serves their clone adaptation (start image), the
  clone body rides the house job path (character picture
  as the start image, the source's length in house
  segments, Production named for Wan 2.2, one segment for
  LTX-2.5 Uncensored), and the quote reads the lane's own
  segment accounting (job price times segments, or the
  included rail at the house unit), never the per-second
  machinery;
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

# -- the tab: titled Clone, top-level beside Create and Clip --------------

html = app.APP_HTML
check('id="create-pane"' in html and 'id="clip-pane"' in html
      and 'id="clone-pane"' in html,
      "a studio pane is missing")
check("<h2>Clone</h2>" in html,
      "the Clone card does not carry its Clone title")
check("Edit / Clone" not in html,
      "the tab still shows its old Edit / Clone name")
# The tab bar carries exactly Create, Clip, Clone, in that order.
check(html.count('id="tab-') == 3,
      "the tab bar does not carry exactly three tabs")
check('<button class="btn" id="tab-create">Create</button>' in html
      and '<button class="btn secondary" id="tab-clip">Clip</button>'
      in html
      and '<button class="btn secondary" id="tab-clone">Clone</button>'
      in html,
      "the tab bar is not Create, Clip, Clone")
check(html.index('id="tab-create"') < html.index('id="tab-clip"')
      < html.index('id="tab-clone"'),
      "the tab bar order is not Create, Clip, Clone")
check("$('tab-clone').onclick = () => showTab('clone');" in html,
      "the Clone tab does not route to the clone pane")
check("$('clone-pane').style.display" in html,
      "showTab does not toggle the clone pane")
create_block = html[html.index('id="create-pane"'):
                    html.index('id="upgrade-pane"')]
check("<h2>Clone</h2>" not in create_block
      and 'id="ec-' not in create_block,
      "Clone markup still sits inside Create")
check(html.index('id="clone-pane"') < html.index("<h2>Clone</h2>")
      and html.index('id="clone-pane"') < html.index('id="ec-clone"'),
      "the Clone card does not live inside the clone pane")
for el in ("ec-url", "ec-file", "ec-library", "ec-add", "ec-hook",
           "ec-char-name", "ec-clone-char", "ec-char-files",
           "ec-make-char", "ec-character", "ec-model", "ec-feed",
           "ec-clone", "botcheck4"):
    check(f'id="{el}"' in html,
          f"the Clone control {el} is missing")

# -- the four steps stand visible from the first paint -------------------
# (2026-11-27 redesign: the whole journey on the page, in
# order, nothing appearing out of nowhere later.)
for step in ("ec-step1", "ec-step2", "ec-step3", "ec-step4"):
    check(f'id="{step}"' in html, f"the Clone step {step} is missing")
check(html.index('id="ec-step1"') < html.index('id="ec-step2"')
      < html.index('id="ec-step3"') < html.index('id="ec-step4"')
      < html.index('id="ec-clone"'),
      "the Clone steps are not 1, 2, 3, 4 in order before the button")
for title in ("Add a video", "Choose your character",
              "Trim the hook", "Model and price"):
    check(f"<h3>{title}</h3>" in html,
          f"the Clone step title {title!r} is missing")
check('id="ec-source"' not in html,
      "the steps still hide inside the old ec-source container")
check(html.index('id="ec-step2"') < html.index('id="ec-character"')
      < html.index('id="ec-step3"'),
      "the character picker does not stand in step 2 from the start")
check("function ecUpdateSteps" in html
      and "$('ec-hook').disabled" in html
      and "$('ec-clone-char').disabled" in html,
      "the step state machinery is missing")
check('id="ec-hook" type="button" disabled' in html
      and 'id="ec-clone-char" type="button" disabled' in html,
      "a source-bound control does not start disabled")
for note in ("That reads the person out of your video",
             "That cuts your video from step 1"):
    check(note in html, f"a waiting note is missing: {note!r}")

# -- no error before an action, messages beside their step ----------------

for el in ("ec-msg", "ec-char-msg", "ec-hook-msg", "ec-clone-msg"):
    check(f'<p id="{el}"></p>' in html,
          f"the message line {el} carries text before any action")
pick_first = "Pick one first: paste a link, upload a file"
check(html.count(pick_first) == 1
      and html.index(pick_first) > html.index("$('ec-add').onclick"),
      "the pick-one-first error lives outside the Add handler")
check("Paste a link, upload a file, or pick a video from your "
      "library first." not in html,
      "the old pre-action error text still renders")
check("msg('ec-char-msg'" in html and "msg('ec-hook-msg'" in html,
      "a step's messages do not land beside that step")

# -- the file picks wear the site's dress ---------------------------------

check('class="drop" id="ec-file-drop"' in html
      and 'class="drop" id="ec-char-drop"' in html,
      "a file pick is not a styled drop zone")
check('<input id="ec-file" type="file" accept="video/*" '
      'style="display:none">' in html
      and '<input id="ec-char-files" type="file" '
      'accept="image/png,image/jpeg" multiple '
      'style="display:none">' in html,
      "a raw browser file widget still shows")
clone_card = html[html.index('id="clone-pane"'):
                  html.index("<h2>Make a movie</h2>")]
check("Or upload a video file" not in clone_card,
      "the old raw upload label still renders")
check("ecWireDrop('ec-file-drop', 'ec-file', ecRenderFileDrop)"
      in html
      and "ecWireDrop('ec-char-drop', 'ec-char-files', "
      "ecRenderCharDrop)" in html,
      "a drop zone is not wired to its hidden input")

# -- costs like Create: rates in the step, exact total, priced button -----

check("ecRateText(m.rate_cents_per_second)" in html,
      "the model options do not carry each model's $/s rate")
check("'Clone the video · $'" in html,
      "the Clone button does not carry its price like Create's")
for words in ("'The exact price for your '",
              "' of your included budget'",
              "' from your prepaid balance.'",
              "Nothing is charged to your balance."):
    check(words in html, f"the quote lost Create's words: {words!r}")
check("This clone uses $" not in html
      and "This clone costs $" not in html,
      "the quote still speaks its old one-off wording")

# -- the short lines, each where it belongs --------------------------------

check("Only clone videos you own or have permission to use."
      in html,
      "the rights line is missing from the source step")
check("Adding a video, trimming the hook and making a "
      "character are free." in html,
      "the price honesty line is missing from the create step")
check("Adding a video is free." in html
      and "Making a character is free." in html,
      "a free step is not labeled free")
check("Only edit and clone content you own" not in html,
      "the old wall of text still renders")

# -- the library picker: a button and thumbnail panel, no select --------
# (Jack, 2026-11-27: the option stays, the dropdown of full
# prompts goes.) The picked job id rides the hidden ec-library
# input, the same value the old select handed the Add flow.
check('<select id="ec-library">' not in html,
      "the library source is still a native select")
check("Choose a video you already have" not in html,
      "the old select heading still renders")
check('id="ec-library-btn"' in html
      and "Pick from your library" in html,
      "the Pick from your library row is missing")
check("Choose from your Moonfactory library" not in html,
      "the giant library pill still renders")
check("<label>Pick from your library</label>" not in html,
      "the library pick still wears its old stacked label")
check("<h2>Your Moonfactory library</h2>" in html,
      "the library picker panel does not name the Moonfactory library")
check('<input type="hidden" id="ec-library" value="">' in html,
      "the hidden input carrying the picked job id is missing")
check('id="panel-ec-library"' in html and 'id="ec-library-grid"' in html,
      "the library picker panel is missing")
check('id="ec-library-empty"' in html
      and "No finished videos in your library yet." in html,
      "the picker has no plain empty state")
for el in ("ec-library-picked", "ec-library-picked-thumb",
           "ec-library-picked-label", "ec-library-change",
           "ec-library-clear"):
    check(f'id="{el}"' in html,
          f"the picked-source display is missing {el}")
check("j.kind !== 'image' && j.status === 'done'" in html,
      "the picker no longer offers the select's done-video set")
check("'/api/jobs/' + j.id + '/thumb'" in html,
      "the picker cards do not carry the job thumbnails")
check("function ecShortLabel" in html and "slice(0, 64)" in html,
      "the picker labels are not truncated short labels")
check("card.dataset.libpick = j.id" in html
      and "ecPickLibrary(card.dataset.libpick)" in html,
      "tapping a card does not pick that job")
check("$('ec-library').value = id" in html,
      "the pick does not land in the ec-library value")
check("const libId = $('ec-library').value" in html
      and "ecJobsCache.find(j => j.id === libId)" in html
      and "ecSetSource(job)" in html,
      "the Add flow no longer resolves the picked id to the source job")

# -- step 1 polish: quiet library row, Add earns its place,
# one upload label (Jack, 2026-11-27) -----------------------------------
# The library pick is one understated row in the quiet text
# dress, not a label stacked over a full-width pill; the
# word library is said once in the block. Add this video
# starts disabled with its waiting note and wakes only when
# one of the three routes holds something. Upload a video
# file is the drop zone's own heading, never a label above
# it as well (the count of two is that heading plus the
# zone's aria-label).

step1 = html[html.index('id="ec-step1"'):html.index('id="ec-step2"')]
check('class="librow" id="ec-library-btn"' in step1,
      "the library pick is not one quiet row")
check(step1.count("Pick from your library") == 1,
      "the library pick is not said exactly once in step 1")
check('id="ec-add" type="button" disabled' in step1,
      "Add this video does not start disabled over an empty step")
check('id="ec-add-note"' in step1
      and "It wakes up as soon as you give it a video above."
      in step1,
      "the Add button carries no waiting note")
check("function ecUpdateAdd" in html
      and "$('ec-add').disabled = !ready" in html
      and "$('ec-add-note').style.display" in html,
      "nothing re-derives the Add button's state from the inputs")
check("$('ec-url').addEventListener('input', ecUpdateAdd)" in html,
      "typing a link does not wake the Add button")
file_drop_fn = html[html.index("function ecRenderFileDrop"):
                    html.index("function ecRenderCharDrop")]
check("ecUpdateAdd();" in file_drop_fn,
      "picking or removing a file does not update the Add button")
picked_fn = html[html.index("function ecRenderLibraryPicked"):
                 html.index("function ecOpenLibrary")]
check(picked_fn.count("ecUpdateAdd();") == 2,
      "picking or clearing a library video does not update "
      "the Add button on both of the picked render's paths")
check("finally { btnDone(btn); ecUpdateAdd(); }" in html,
      "a finished Add attempt does not re-derive the button state")
check("<label>Upload a video file</label>" not in html
      and '<div class="big">Upload a video file</div>' in step1
      and step1.count("Upload a video file") == 2,
      "Upload a video file is still said twice in step 1")

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

# The sandbox resolver answers every name with the egress
# proxy's 198.18/15 addresses, which ipaddress does not
# count as global; the guard's contract is about what DNS
# returns for a name, so the check stands in a public
# answer and tests the guard's logic, not the sandbox's
# resolver. Production resolvers return the real records.
import socket as _socket
clipping.socket.getaddrinfo = lambda host, port, *a, **k: [
    (_socket.AF_INET, _socket.SOCK_STREAM, 6, "",
     ("93.184.216.34", 0))]

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
# The download itself moved into the small fetch function the
# worker fans out to (2026-11-27 link ingest fix); the intent
# is unchanged: ingest downloads through the any-site fetch.
check("download_any_video" in func_src("editclone_fetch")
      and "_editclone_fetch_any" in worker,
      "the ingest worker does not download through the any-site fetch")
check("trim_opening" in worker,
      "the hook step does not cut through clipping.trim_opening")
check("grab_frames" in worker,
      "the character step does not grab frames from the source")
check("stamp_content_hash" in worker and "classify_job_output" in worker,
      "finished Clone files skip the hash or the classifier")
check("data_volume.reload" in func_src("_editclone_source_job"),
      "the source check does not refresh the volume before "
      "calling a freshly landed file gone")
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
# Kling 4.0 left this list 2026-11-27: its UnificAlly
# route takes no reference video, so it clones from frames.
# Seedance 2.0 Fast joined it the same day: its UnificAlly
# route takes the full omni reference set.
for model in ("seedance2_mini", "seedance", "magi2",
              "seedance25_flash", "wan3", "seedance2_fast"):
    check(adapt(model) == "both",
          f"{model} should clone with both references, got {adapt(model)}")
for model in ("veo3", "veo3_fast", "grok", "grok15",
              "vidu_q4", "kling_o1", "kling4"):
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
check(app.provider_for("seedance2_mini", "480p") == "unifically",
      "seedance2_mini no longer routes to UnificAlly "
      "(it moved off Kie in the 2026-11-27 catalog audit; "
      "the Kie submit below stays wired as the spare)")
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

# -- the house lanes clone too ---------------------------------------------

HOUSE_CLONE_LANES = {
    "wan2.2": "production",
    "wan22_uncut": "nsfw",
    "ltx25_unc": "ltx_unc",
    "hunyuan15": "hunyuan15",
}
for model, lane in HOUSE_CLONE_LANES.items():
    check(app.HOUSE_MODEL_PRICING_LANE.get(model) == lane,
          f"{model} no longer settles on its house lane {lane}")
    check(model in app.MODEL_LABELS,
          f"{model} is missing from the picker catalog data")
    check(adapt(model) == "start",
          f"{model} should clone from a start image, got {adapt(model)}")
check(all(m["id"] in app.PER_SECOND_MODELS
          or m["id"] in app.HOUSE_MODEL_PRICING_LANE
          for m in app.MODELS),
      "a catalog model takes no clone: the picker must cover every model")
check("HOUSE_MODEL_PRICING_LANE" in func_src("entry_for"),
      "the config does not serve a clone adaptation for the house lanes")
check("HOUSE_MODEL_PRICING_LANE" in body_fn,
      "the clone body does not route house picks to the house path")
check('"start_image_png_b64": char_b64' in body_fn,
      "the house clone does not carry Image1 as its start image")
check('"segments": segments' in body_fn
      and "SEGMENT_SECONDS" in body_fn
      and "math.ceil" in body_fn,
      "the house clone does not take its length in house segments")
check('house_body["lane"] = "production"' in body_fn,
      "the Wan 2.2 clone does not name its Production lane")
check('"ltx25_unc"' in body_fn and "segments = 1" in body_fn,
      "the LTX-2.5 Uncensored clone is not held to one segment")
# The length math the house branch runs, on the constants it
# reads: a 12 second source is 3 house segments, a short one
# is 1, a long one caps at MAX_SEGMENTS.
import math as _math
for seconds, want in ((5.0, 1), (12.0, 3), (200.0, app.MAX_SEGMENTS)):
    got = min(app.MAX_SEGMENTS,
              max(1, _math.ceil(seconds / app.SEGMENT_SECONDS)))
    check(got == want,
          f"a {seconds}s source should clone in {want} segments, got {got}")
quote_fn = func_src("quote_editclone_clone")
check("HOUSE_MODEL_PRICING_LANE" in quote_fn
      and "job_price_cents" in quote_fn,
      "the house clone quote does not read the lane's own job price")
check("included_budget_remaining" in quote_fn
      and 'MEASURED_COST_CENTS["production"]' in quote_fn,
      "the house clone quote does not mirror the included rail")
check("lane_paused" in quote_fn,
      "the house clone quote does not report a paused lane")
# The pricing relationship the quote and the create share:
# a house clone of N segments costs a normal house job of
# N segments on the same lane, owner at raw cost.
import pricing as _pricing
for lane in HOUSE_CLONE_LANES.values():
    for segments in (1, 3):
        check(_pricing.job_price_cents(lane, owner=False) * segments > 0,
              f"house lane {lane} has no job price to quote a clone from")
check(_pricing.job_price_cents("production", owner=True)
      == _pricing.MEASURED_COST_CENTS["production"],
      "the owner's Wan 2.2 clone price is not the lane's raw cost")

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
