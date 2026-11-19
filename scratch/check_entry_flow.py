"""Local checks for the entry flow rework (2026-11-19).
No Modal calls, no network: imports the platform module and pins
the new flow at source level. The site opens straight onto the
Create studio for every visitor, signed out included. There is
no landing page, no sign-in wall in front of the studio, and no
Open Create interstitial. A signed out visitor builds the whole
creation, and only a create button opens the sign-in form, in
place on the same page. Signing in fires the captured creation
once with its exact payload; signing up stores it on the
account, and the verified return claims it, restores the form,
and fires it. File payloads cannot survive the sign-up round
trip: they become file_missing names, and the restored form
asks for the file again before firing.

Run:  modal-venv python scratch/check_entry_flow.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
HTML = app.APP_HTML

# --- the old gate screens are gone --------------------------------------
check("INDEX_HTML = (" not in SRC and "INDEX_HTML.replace" not in SRC,
      "the landing page constant is gone")
check("_LANDING_TOP" not in SRC, "the landing top blocks are gone")
check("hero-go" not in SRC, "the Try It hero button is gone")
check("Open Create" not in HTML, "no Open Create interstitial remains")
landing = SRC.split('def landing(', 1)[1].split('@web.get("/app"', 1)[0]
check("return HTMLResponse(APP_HTML)" in landing,
      "the root route serves the studio page to everyone")
studio_route = SRC.split('def studio():', 1)[1].split("def terms_page", 1)[0]
check("return HTMLResponse(APP_HTML)" in studio_route,
      "/app serves the same studio page")

# --- the studio renders signed out; the auth form is a panel -------------
check('<div id="studio">' in HTML,
      "the studio is not hidden behind the sign-in wall")
check('<div id="studio" style="display:none">' not in HTML,
      "the studio carries no hidden default")
check('id="auth" class="card" style="display:none' in HTML,
      "the sign-in form starts hidden, as an in-page panel")
check('id="auth-intro"' in HTML, "the panel carries the pending line")
check('id="auth-back"' in HTML, "the panel can be dismissed back to the form")
check('id="nav-signin"' in HTML, "the header offers sign in to a visitor")
check("function showGuestStudio()" in SRC,
      "a signed out visit opens the studio in guest shape")
check("catch (e) { showGuestStudio(); }" in SRC,
      "a failed /api/me lands on the guest studio, not a wall")
gates = SRC.split("function applyModelGates()", 1)[1].split(
    "function updateHousePrice()", 1)[0]
check("if (!currentUser) return;" in gates,
      "the model gates do not narrow a signed out visitor's picker")

# --- generate while signed out captures, never submits -------------------
create_btn = SRC.split("$('create').onclick", 1)[1].split(
    "function costLine", 1)[0]
check("if (!currentUser) { pendingCreate = d; openAuthPanel(); return; }"
      in create_btn,
      "the Create button captures the build and opens sign in, in place")
gen_btn = SRC.split("$('gen-image').onclick", 1)[1].split(
    "function buildSegmentsSeg", 1)[0] if "function buildSegmentsSeg" in SRC \
    else SRC.split("$('gen-image').onclick", 1)[1][:800]
check("pendingCreate = d; openAuthPanel(); return;" in gen_btn,
      "the first frame button captures and opens sign in the same way")
check("function buildCreate()" in SRC and "function buildShort()" in SRC
      and "function buildTitleCard()" in SRC
      and "function buildImage()" in SRC
      and "function buildFirstFrame()" in SRC,
      "every lane builds its payload before anything fires")
check("async function fireCreate(d)" in SRC,
      "one fire path submits a built creation")

# --- sign in fires the captured creation once, exactly as built ----------
after = SRC.split("async function afterSignIn(u)", 1)[1].split(
    "async function claimAndRestore()", 1)[0]
check(after.index("await fireCreate(d);") < after.index("showStudio(u);"),
      "sign in fires the pending creation before the studio repaints")
check("pendingCreate = null;" in after,
      "the pending creation is consumed before it fires (at most once)")
check("payload.pending_create = serializePending(pendingCreate)" in SRC,
      "sign up carries the pending creation to the server")
check("/api/me/pending-create'" in after,
      "an unverified sign in parks the creation on the account")

# --- the server stores, cleans, and claims pending creations -------------
check("def _clean_pending_create(value):" in SRC,
      "the server validates a pending creation before storing it")
check("PENDING_FILE_FIELDS" in SRC
      and '"start_image_png_b64": "start_image"' in SRC
      and '"upload_id": "captions_clip"' in SRC,
      "file fields become file_missing names, never stored payloads")
check("PENDING_BOT_FIELDS" in SRC,
      "bot check fields never enter the stored creation")
signup = SRC.split('async def signup(', 1)[1].split(
    '@web.get("/api/verify")', 1)[0]
check('user["pending_create"] = pending' in signup,
      "sign up stores the cleaned creation on the new account")
check('@web.post("/api/me/pending-create")' in SRC,
      "the park endpoint exists")
check('@web.post("/api/me/pending-create/claim")' in SRC
      and 'user.pop("pending_create", None)' in SRC,
      "the claim hands the creation over and clears it in one step")

# --- the verified return restores the form and fires ---------------------
check("async function claimAndRestore()" in SRC
      and "restoreCreateForm(d);" in SRC
      and "fireRestoredIfReady();" in SRC,
      "the verified return claims, restores, and fires")
check("function restoreCreateForm(d)" in SRC,
      "the form is refilled from the stored creation")
for hook in ("restoredFileArrived('start_image_png_b64'",
             "restoredFileArrived('end_image_png_b64'",
             "restoredFileArrived('source_image'",
             "restoredFileArrived('upload_id'"):
    check(hook in SRC, f"a file pick can complete a restored creation: {hook}")
check("could not be carried over. Pick it again and your video starts."
      in SRC, "the restored form names the missing file plainly")
verified = SRC.split("get('verified')", 1)[1][:600]
check("openAuthPanel();" in verified,
      "the verify link lands on the studio with sign in open in place")

# --- the save control on every finished result (Jack, 2026-11-19) -------
# One share icon button on any generated video or photo shown to
# its owner. It opens a two option menu. Save to Photos rides
# the share sheet, Save to Files downloads under the proper
# name. The plain Download anchor is gone everywhere.
check('/file" download>Download</a>' not in SRC,
      "the plain download anchor is gone from the studio")
check("function saveControlHtml(jobId, kind)" in SRC,
      "the save control builder exists")
check('class="menu save-menu"' in SRC
      and 'data-save="' in SRC and 'aria-expanded="false"' in SRC,
      "the button carries the share icon menu, closed by default")
check(">Save to Photos</button>" in SRC and ">Save to Files</button>" in SRC,
      "the menu holds exactly the two save options")
check("M12 14.5V4" in SRC and "M7.8 7.6L12 3.4l4.2 4.2" in SRC,
      "the button icon is the share glyph, arrow up out of a box")
check("navigator.canShare" in SRC and "navigator.share({ files: [file] })" in SRC,
      "Save to Photos goes through the file share sheet")
check("This browser cannot send files to your photos." in SRC,
      "an unsupported browser hears so plainly, in one line")
check("navigator.canShare({ files: [file] })" in SRC,
      "the support gate is asked about the exact file being shared")
check("const saveFileCache = {}" in SRC
      and "function prefetchSaveFile(jobId, kind)" in SRC,
      "the file is fetched ahead of the share tap and cached")
check("prefetchSaveFile(saveBtn.dataset.save, saveBtn.dataset.kind)" in SRC,
      "opening the save menu starts the file fetch")
check("function saveToPhotos(btn)" in SRC
      and "async function saveToPhotos" not in SRC,
      "the Photos tap shares the cached file without awaiting first")
check("saveFileCache[btn.dataset.savePhotos]" in SRC,
      "the Photos tap reads the cached entry as it stands")
check("That file could not be loaded. Try again in a moment." in SRC,
      "a file that cannot be fetched hears so plainly, in its own line")
check("That did not work." not in SRC,
      "the vague failure line is gone")
check("shareSaveFile(btn, entry.file, true)" in SRC,
      "a tap that lands before the fetch finishes fires the share when the file lands")
check('filename=f"moonfactory-{job_id}.mp4"' in SRC
      and 'media_type="video/mp4"' in SRC
      and 'filename=f"moonfactory-{job_id}.png"' in SRC
      and 'media_type="image/png"' in SRC,
      "the file route serves each kind under its own type and name")
check("content-disposition" in SRC and "a.download = file.name" in SRC,
      "Save to Files downloads under the file's proper name")
check("closeSaveMenus()" in SRC and "if (!e.target.closest('.save-wrap'))" in SRC,
      "a tap outside the menu closes it")
library = SRC.split("async function loadJobs()", 1)[1].split(
    "function renderClipResults", 1)[0]
check("saveControlHtml(j.id, 'image')" in library
      and "saveControlHtml(j.id, 'video')" in library,
      "library rows carry the control for images and videos alike")
clips = SRC.split("function renderClipResults(jobs)", 1)[1].split(
    "$('jobs').addEventListener", 1)[0]
check("saveControlHtml(j.id, 'video')" in clips,
      "clip results carry the control")
check("saveControlHtml(imageJobId, 'image')" in SRC,
      "the first frame preview carries the control the moment it lands")
check('id="image-result-save"' in HTML
      and "saveControlHtml(r.job_id, 'image')" in SRC,
      "the image mode result carries the control")

# --- working visuals: every wait shows motion (Jack, 2026-11-19) --------
jobs = SRC.split("async function loadJobs()", 1)[1].split(
    "function renderClipResults", 1)[0]
login = SRC.split("$('login').onclick", 1)[1].split(
    "// ---- Entry flow", 1)[0]
# One shared spinner, one button working state, one progress
# bar treatment, reused everywhere the studio waits. A pushed
# button enters its working state, stays disabled in flight,
# and is restored when the request settles, error included.
# A running job pulses beside its status word, carries the
# bar and a ticking elapsed clock, and shows the real stage
# words its record carries. No percentage is ever invented:
# the bar is determinate only from reported segments.
check(".spin{" in SRC and "@keyframes mf-spin" in SRC,
      "one shared spinner style exists")
check(".pulse-dot{" in SRC and "@keyframes mf-pulse" in SRC,
      "one shared pulsing mark exists")
check(".workbar{" in SRC and "@keyframes mf-slide" in SRC,
      "one shared progress bar treatment exists")
check(".workbar-fill{animation:none;transform:none}" in SRC,
      "reduced motion rests the working visuals in a calm static state")
check("const SPIN = " in SRC and "function btnBusy(btn, label)" in SRC
      and "function btnDone(btn)" in SRC
      and "function btnLabel(btn, label)" in SRC,
      "one shared button working state exists")
check("function spinMsg(id, text)" in SRC,
      "a message line can carry the spinner while its flow runs")
fire = SRC.split("async function fireCreate(d)", 1)[1].split(
    "async function afterSignIn", 1)[0]
check("btnBusy(btn, 'Starting...')" in fire
      and "btnLabel(btn, 'Working on the bot check...')" in fire
      and "finally { btnDone(btn); }" in fire,
      "a Create submit spins its button from Starting, follows the flow, and always restores it")
check("btnBusy(btn, 'Starting...')" in SRC.split(
      "async function fireImage(d)", 1)[1].split(
      "$('image-use-start')", 1)[0],
      "an image submit works its button until the job exists")
frame = SRC.split("async function fireFirstFrame(d)", 1)[1].split(
    "$('gen-image').onclick", 1)[0]
check("btnBusy(btn, 'Checking the price...')" in frame
      and "btnDone(btn);" in frame,
      "the first frame button works until the frame lands or fails")
check("if (b.dataset.holds) return;" in SRC,
      "a quote refresh never stomps a working button label")
clips_btn = SRC.split("$('make-clips').onclick", 1)[1].split(
    "// ---- Title cards", 1)[0]
check("btnBusy(btn, 'Starting...')" in clips_btn
      and "finally { btnDone(btn); }" in clips_btn,
      "the clip lane button works while its submit is in flight")
check("workBarHtml(j)" in jobs and "function workBarHtml(j)" in SRC,
      "a running job carries the progress bar")
check("j.segments_total > 1" in SRC and "workbar-fill set" in SRC,
      "the bar is only ever as full as the segments the pipeline reports")
check("pulse-dot" in jobs and "elapsedHtml(j)" in jobs
      and "j.stage && activeJob" in jobs,
      "a running job pulses beside its status, shows its stage words, and carries its clock")
check("function fmtElapsed(total)" in SRC
      and "document.querySelectorAll('[data-since]')" in SRC
      and "}, 1000);" in SRC,
      "the elapsed clock ticks between job list renders, on no second poller")
check("spinMsg('auth-msg', 'Starting your video...')" in login,
      "the sign in panel shows the captured creation starting, not a frozen form")
check("btnBusy(btn, 'Signing in...')" in login
      and "btnBusy(btn, 'Creating your account...')" in SRC,
      "the auth buttons work while sign in and sign up are in flight")
check("function saveLineBusy(btn, label)" in SRC
      and "saveLineBusy(btn, 'Loading...')" in SRC
      and "saveLineBusy(btn, 'Saving...')" in SRC,
      "the save menu lines spin while their save is in flight")
check("note.innerHTML = SPIN + 'Loading your file...'" in SRC,
      "opening the save menu shows the file on its way")
check("Your file is ready. Tap Save to Photos once more." in SRC,
      "a deferred share that finds its tap spent says so plainly")
check("spinMsg('clip-msg', 'Uploading your file...')" in SRC
      and "$('clip-file').disabled = true;" in SRC,
      "the clip lane file pick shows its upload until it lands")
check("spinMsg('tpl-msg', 'Uploading your clip...')" in SRC
      and "$('tpl-clip-file').disabled = true;" in SRC,
      "the captions clip pick shows its upload until it lands")
check("function renderDropLoading()" in SRC
      and "Reading your image..." in SRC,
      "the start image drop shows a reading state while the file loads")

# --- picker groups: Free, Paid Models, Uncensored Models -----------------
# Both pickers group by a class that comes from data (the same
# flags the gallery filter reads), never from label text, and
# each group sorts by customer cost inside itself (2026-11-19).
for m in app.MODELS:
    want = ("uncensored" if m["id"] in app.UNCENSORED_VIDEO_MODELS
            else "paid")
    check(app.model_picker_class(m["id"]) == want,
          f"video model {m['id']} class is not {want}")
check(app.model_picker_class("wan2.2") == "uncensored",
      "the house Wan 2.2 groups as uncensored, the flag the gallery reads")
check(app.model_picker_class("wan22_uncut") == "uncensored",
      "the Uncut lane groups as uncensored")
check(app.model_picker_class("wan3") == "paid",
      "a moderated lane groups as paid")
check(app.image_picker_class("house") == "paid",
      "the house image lane (Z-Image Turbo) groups as paid, "
      "it carries a per image price")
for lane_id in ("seedream55_unc", "seedream5pro_unc", "zimage_unc",
                "zimage_pro_unc", "qwen_edit_unc"):
    check(app.image_picker_class(lane_id) == "uncensored",
          f"image lane {lane_id} groups as uncensored")
    check(pricing.IMAGE_LANES[lane_id].get("uncensored") is True,
          f"image lane {lane_id} carries its uncensored flag in pricing")
for lane_id in ("nano_banana_21", "nano_banana_pro", "nano_banana_2",
                "gpt_image_26"):
    check(app.image_picker_class(lane_id) == "paid",
          f"image lane {lane_id} groups as paid")
for lane_id, lane in pricing.IMAGE_LANES.items():
    check("· Free" not in lane["label"]
          and "· Uncensored" not in lane["label"],
          f"image lane {lane_id} label carries no bullet class word")

# Image tier marks (Jack, 2026-11-19): every label that carries
# $ marks sets them behind a bullet separator, "Name · $$", in
# both pickers. Every image lane, paid and uncensored alike,
# takes its marks from the image bands (Jack, 2026-11-19),
# read on the customer price at the smallest size: $ up to 3c,
# $$ above 3c up to 4.5c, $$$ above 4.5c up to 10c, $$$$ above
# 10c. The video per second bands are separate and unchanged.
def _image_tier_marks(debit):
    if debit <= 3.0:
        return "$"
    if debit <= 4.5:
        return "$$"
    if debit <= 10.0:
        return "$$$"
    return "$$$$"

for lane_id, base in (("nano_banana_21", "Nano Banana 2.1"),
                      ("nano_banana_2", "Nano Banana 2"),
                      ("nano_banana_pro", "Nano Banana Pro"),
                      ("gpt_image_26", "GPT Image 2.6"),
                      ("seedream55_unc", "Seedream 5.5"),
                      ("seedream5pro_unc", "Seedream 5.0 Pro"),
                      ("zimage_unc", "Z-Image"),
                      ("zimage_pro_unc", "Z-Image Pro"),
                      ("qwen_edit_unc", "Qwen Image Edit")):
    want = (base + " · "
            + _image_tier_marks(app.image_picker_price_cents(lane_id)))
    check(pricing.IMAGE_LANES[lane_id]["label"] == want,
          f"image lane {lane_id} label carries its banded tier mark")
check('HOUSE_IMAGE_LABEL = "Z-Image Turbo · '
      + _image_tier_marks(float(pricing.IMAGE_PRICE_CENTS)) + '"' in SRC,
      "the house image label is its real model name, carrying "
      "its banded tier mark behind the bullet")
check(app.HOUSE_IMAGE_LABEL == "Z-Image Turbo · "
      + _image_tier_marks(float(pricing.IMAGE_PRICE_CENTS)),
      "the served house image label recomputes from its price")
check('"label": HOUSE_IMAGE_NAME' in SRC,
      "the config serves the house image plain name to the picker")
check("or HOUSE_IMAGE_LABEL" in SRC,
      "the gallery reads the house image name from the same constant")
check("Moonfactory Image" not in SRC
      and "own image maker" not in SRC,
      "no second hard coded copy of the house image name survives")

# Tier sub separators (Jack, 2026-11-19): both pickers dropped
# the "Name · $$" option format. Options carry plain names
# only; inside each Paid and Uncensored group a disabled
# separator option carrying one tier's $ marks alone heads
# that tier's models, and the tier rides the config as data.
check("' · ' + '$'.repeat(m.price_tier)" not in SRC,
      "the video picker no longer appends tier marks to option names")
check("o.textContent = m.label + (m.available ? '' : ' (unavailable)');"
      in SRC,
      "video picker options carry the plain model name only")
check("sep.dataset.tierSep = '1'" in SRC
      and "sep.textContent = '$'.repeat(t)" in SRC,
      "both pickers build disabled tier separator options")
check("!o.dataset.tierSep" in SRC,
      "the model gates never enable a tier separator")
check('"tier": image_lane_tier(mid)' in SRC
      and '"tier": image_lane_tier("house")' in SRC,
      "the config serves every image lane's tier as data")
check(app.image_lane_name("nano_banana_21") == "Nano Banana 2.1",
      "the config's image label is the plain model name")
for lane_id, tier in (("house", 4), ("zimage_unc", 1),
                      ("zimage_pro_unc", 1), ("seedream55_unc", 2),
                      ("seedream5pro_unc", 2), ("qwen_edit_unc", 3),
                      ("nano_banana_pro", 3), ("gpt_image_26", 3),
                      ("nano_banana_21", 1), ("nano_banana_2", 1)):
    check(app.image_lane_tier(lane_id) == tier,
          f"image lane {lane_id} serves tier {tier} as data")

# The Qwen edit instruction box (Jack, 2026-11-19): the edit
# lane reads its own textarea inside the edit form.
check('id="image-edit-prompt"' in SRC,
      "the edit form carries its own instruction box")
check("const promptText = lane.edit" in SRC
      and "$('image-edit-prompt').value : $('image-gen-prompt').value;" in SRC,
      "the image submit and quote read the edit box for edit lanes "
      "and the image form's own prompt box for generation lanes")
check("$('image-edit-prompt').value = b.prompt || '';" in SRC,
      "a restored edit creation refills the edit box")

# The image form's own prompt box (Jack, 2026-11-19): the
# text to image lanes used to read the shared video prompt,
# which the form swaps hide in the default flow (the picker
# opens on Faceless shorts), so generation jobs built with
# an empty or stale prompt. The image form now carries its
# own box beside the model and size, shown for generation
# lanes and hidden for the edit lane, which keeps its own
# instruction box; the two boxes are never merged.
_img_opts = SRC.split('id="image-opts"', 1)[1].split('id="image-result-wrap"', 1)[0]
check('id="image-gen-prompt"' in _img_opts
      and 'id="image-gen-prompt-wrap"' in _img_opts,
      "the image form carries its own prompt box with the model and size")
check("$('image-gen-prompt-wrap').style.display = isEdit ? 'none' : '';" in SRC,
      "the generation prompt box hides for the edit lane and shows otherwise")
check("$('image-gen-prompt').value = b.prompt || '';" in SRC,
      "a restored image creation refills the image form's prompt box")
check("if (on) $('image-gen-prompt').placeholder = 'Describe the image" in SRC,
      "the image mode swap writes its placeholder on the image form's box")
check("$('image-edit-prompt').value : $('prompt').value;" not in SRC,
      "no image path reads the shared video prompt anymore")

# The video cost readout (Jack, 2026-11-19): the quote line
# and the house price line are the same elements the quote
# paths have always written, moved above the Create button
# so the price is in view before Generate is pressed; the
# house line's writer clears a stale button price when its
# data is not ready, and the account aware pricing payload
# is re-fetched when a studio session starts so a signed in
# owner is quoted his raw costs, not the anonymous copy.
_create_card = SRC.split('id="create-pane"', 1)[1].split('id="upgrade-pane"', 1)[0]
check(_create_card.index('id="house-price"') < _create_card.index('id="create"')
      and _create_card.index('id="quote"') < _create_card.index('id="create"'),
      "the price lines render above the Create button")
check("if (isHouseModel($('model').value)) genLabel(null);" in SRC,
      "a house pick never leaves a stale price on the button")
check("function loadPricing()" in SRC and "loadPricing();" in SRC
      and "currentUser = u;\n  loadPricing();" in SRC,
      "the pricing payload reloads when a studio session starts")

# Image aspect shapes (provider research, 2026-11-19): the
# tier priced lanes whose providers publish an aspect ratio
# priced by the tier alone offer every published shape at its
# tier's raw price; the lanes whose providers publish no such
# price keep their size lists exactly as they were.
check(pricing.IMAGE_LANES["nano_banana_21"]["raw_cents"]["1K-16:9"]
      == pricing.IMAGE_LANES["nano_banana_21"]["raw_cents"]["1K"],
      "a shaped size charges its tier's raw price")
check("1K-4:1" in pricing.IMAGE_LANES["nano_banana_21"]["raw_cents"]
      and "1K-4:1" not in pricing.IMAGE_LANES["nano_banana_pro"]["raw_cents"],
      "the Pro drops the banner ratios its provider refuses")
check(pricing.IMAGE_LANES["seedream5pro_unc"]["raw_cents"]["2K-9:21"] == 7.2,
      "Seedream 5.0 Pro shapes ride its published tiers")
check(list(pricing.IMAGE_LANES["seedream55_unc"]["raw_cents"])
      == ["1K", "1.5K", "2K"],
      "the Wiro lane keeps its sizes (no published shape prices)")
check(list(pricing.IMAGE_LANES["gpt_image_26"]["raw_cents"])
      == ["1024x1024"],
      "the fal lane stays square (token pricing, no per size price)")
check("1536x864" in pricing.IMAGE_LANES["zimage_unc"]["raw_cents"],
      "the flat priced Z-Image lane offers a true 16:9 size")
check('beat_body["aspect_ratio"] = aspect' in SRC
      and 'image_input["aspect_ratio"] = aspect' in SRC,
      "the BeatAPI and SpicyAPI submits send the picked shape")
check(app.image_smallest_size(pricing.IMAGE_LANES["nano_banana_21"]) == "1K",
      "a shaped lane's smallest size is still its lowest tier square")

# The video Size picker (Jack, 2026-11-19): the config serves
# each model's offered resolutions (priced and accepted), the
# form's Size select sits in the model row labeled Size, and
# the aspect picker rides beside it for every video model.
check('"resolutions": (' in SRC and "offered_resolutions" in SRC,
      "the config serves offered resolutions per model")
check(app.offered_resolutions(
    "seedance2", {"seedance2": {"480p": 1, "720p": 1, "1080p": 1, "4K": 1}})
    == ["480p", "720p", "1080p", "4K"],
    "a fully carried model offers every priced and accepted size")
check(app.offered_resolutions(
    "kling3", {"kling3": {"480p": 1, "720p": 1, "1080p": 1, "4K": 1}})
    == ["4K"],
    "kling3 sells only the tier its submit transmits")
check(app.offered_resolutions(
    "kling4", {"kling4": {"720p": 1, "1080p": 1}}) == ["720p"],
    "a lane whose submit sends no size sells its cheapest tier only")
check(app.offered_resolutions("hailuo23", {"hailuo23": {"720p": 1}})
      == ["720p"],
    "a single size model offers its one size")
check('<label class="flabel" for="resolution">Size</label>' in SRC,
      "the resolution select is labeled Size in the main form")
check('"4k" if params.get("resolution") == "4K"' in SRC
      and 'inp["resolution"] = params["resolution"]' in SRC,
      "the Kie omni and Grok submits send every tier they sell")
check('"resolution": params["resolution"]}' in SRC,
      "the PiAPI Veo submit sends the picked resolution")
check("$('aspect-wrap').style.display = '';" in SRC,
      "the aspect picker shows for every video model")

# Prompt audit (2026-11-19): Wan Motion is the one pick whose
# provider call treats the prompt as optional, so it is the
# one pick the create path does not demand a prompt for.
check('(not prompt and model != "wan_motion")' in SRC,
      "only Wan Motion may create with an empty prompt")
check(app.image_smallest_size(pricing.IMAGE_LANES["seedream55_unc"]) == "1K",
      "a tiered lane's smallest size is its lowest tier")
check(app.image_smallest_size(pricing.IMAGE_LANES["zimage_pro_unc"])
      == "1024x1024",
      "a pixel lane's smallest size is its smallest area")
check(app.image_smallest_size(pricing.IMAGE_LANES["qwen_edit_unc"]) == "match",
      "the edit lane's only size is match")
check(app.image_picker_price_cents("house")
      == float(pricing.IMAGE_PRICE_CENTS),
      "the house image sort price is its flat per image price")
check(abs(app.image_picker_price_cents("zimage_unc") - 1.235 * 1.25) < 1e-9,
      "an uncensored lane sorts by its customer price at its smallest size")
check(abs(app.image_picker_price_cents("nano_banana_21") - 1.6 * 1.25) < 1e-9,
      "a paid lane sorts by its customer price at its smallest size")

catalog = SRC.split("function buildCatalog()", 1)[1].split(
    "function applyModelGates()", 1)[0]
check("[['paid', 'Paid Models']," in catalog
      and "['uncensored', 'Uncensored Models']]" in catalog,
      "the video picker builds the Paid and Uncensored groups in order")
check("rate_cents_per_second" in catalog and "picker_class" in catalog,
      "the video picker groups and sorts from the config class and rate")
check("· Uncensored" not in catalog and "· Free" not in catalog,
      "no bullet class tags survive in the video picker labels")
check("· Free" not in HTML and "· Uncensored" not in HTML,
      "no bullet class tags survive anywhere in the served studio page")
image_builder = SRC.split("function buildImageModels()", 1)[1].split(
    "function buildImageSizes()", 1)[0]
check("[['free', 'Free'], ['paid', 'Paid Models']," in image_builder
      and "['uncensored', 'Uncensored Models']]" in image_builder,
      "the image picker builds the same three groups in the same order")
check("picker_class" in image_builder and "price_cents" in image_builder,
      "the image picker groups and sorts from the config class and price")
check("if (!members.length) continue;" in image_builder,
      "an empty image group is left out of the picker")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_entry_flow")
