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

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_entry_flow")
