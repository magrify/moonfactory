"""Local checks for profile photos and the Account dialog
redesign (Jack, 2026-11-24).

Rules pinned here:
- An account can set ONE profile photo from the Account
  dialog. The upload is processed on the server: it must
  decode as a real picture, is center cropped to a square,
  downscaled to at most 256 on a side, and stored as a JPEG
  on the data volume keyed by user, a new upload replacing
  the old file.
- Before anything is stored, the picture runs through the
  platform's input classifier (classify_input_images, the
  upload scanning seam). A flagged photo is refused and
  never stored or shown; an unjudgeable photo is refused
  too (fail closed, the regular lane upload rule).
- The classifier judges the PROCESSED photo, the normalized
  256x256 JPEG that is literally what gets stored and
  shown, never the raw upload (phone fix, Jack, 2026-11-24:
  scoring a full size multi-megabyte phone photo inline in
  the async handler hung the request until the webview gave
  up with "Load failed"). The decode, process, and scoring
  run in the threadpool, off the event loop, through
  judge_profile_photo.
- When the upload fetch itself dies with no response at
  all, the dialog shows a plain retry message instead of
  the raw browser error text; a server refusal's own detail
  still shows unchanged.
- Removing the photo deletes the file and clears the
  version stamp, so the account is back to its initial
  letter everywhere.
- The photo shows only where the handle shows: the header
  avatar, the Account dialog, and gallery cards beside the
  handle (public_gallery_item carries avatar_url, and the
  gallery tile renders it). With no photo, every surface
  keeps the initial letter.
- The Account dialog is the redesigned structure: photo at
  top with handle and email as plain muted text (the email
  is never a link), plan and money as labeled stat rows
  (never the old run-on sentence), the handle editor as one
  row group, and the gallery choice as a switch row driving
  the same gallery-optout checkbox semantics.

No Modal calls, no network: the processing and refusal
functions run for real against PIL-made pictures, the
endpoint wiring is pinned in source, and the dialog markup
is pinned in the page source.

Run:  modal-venv python scratch/check_profile_photo.py
"""
import ast
import io
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
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- processing: real pictures in, stored form out -----------
from PIL import Image  # noqa: E402


def png_bytes(size, color=(180, 40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


out = app.process_profile_photo(png_bytes((600, 400)))
with Image.open(io.BytesIO(out)) as im:
    check(im.format == "JPEG", "stored photo is not a JPEG")
    check(im.size == (256, 256),
          f"wide photo not cropped and capped at 256: {im.size}")

out = app.process_profile_photo(png_bytes((400, 600)))
with Image.open(io.BytesIO(out)) as im:
    check(im.size == (256, 256),
          f"tall photo not center cropped to square: {im.size}")

out = app.process_profile_photo(png_bytes((120, 90)))
with Image.open(io.BytesIO(out)) as im:
    check(im.size == (90, 90),
          f"small photo was upscaled or not squared: {im.size}")

try:
    app.process_profile_photo(b"this is not a picture at all")
    check(False, "a non picture upload was accepted")
except ValueError:
    pass

check(app.profile_photo_path("user42").name == "user42.jpg"
      and app.profile_photo_path("user42").parent.name == "avatars",
      "profile photo path is not one JPEG per user under avatars/")

# -- refusal: the classifier gate's decision ------------------
check(app.profile_photo_refusal({"score": 0.99, "flagged": True})
      == app.PROFILE_PHOTO_FLAGGED,
      "a flagged verdict does not refuse with the flagged message")
check(app.profile_photo_refusal({"score": None, "error": "boom"})
      == app.PROFILE_PHOTO_UNJUDGED,
      "an unjudged verdict does not refuse (fail closed)")
check(app.profile_photo_refusal(None) == app.PROFILE_PHOTO_UNJUDGED,
      "a missing verdict does not refuse (fail closed)")
check(app.profile_photo_refusal({"score": 0.01, "flagged": False})
      is None,
      "a clean verdict refuses a photo it should keep")

for msg in (app.PROFILE_PHOTO_FLAGGED, app.PROFILE_PHOTO_UNJUDGED,
            app.PROFILE_PHOTO_NOT_IMAGE, app.PROFILE_PHOTO_TOO_LARGE):
    check(":" not in msg and ";" not in msg,
          f"photo refusal copy breaks the copy rules: {msg!r}")

# -- the judging seam: processed bytes in, and a full size
# phone photo end to end (the "Load failed" fix) --------------
import time  # noqa: E402

real_session = app._output_classifier
real_bytes_score = app._classifier_bytes_score
seen = {}
try:
    app._output_classifier = lambda: object()  # noqa: E731

    def recording_score(data, session=None):
        seen["bytes"] = data
        return 0.01

    app._classifier_bytes_score = recording_score

    raw = png_bytes((800, 600))
    jpeg, verdict = app.judge_profile_photo(raw)
    check(seen.get("bytes") == jpeg,
          "the classifier did not receive the processed JPEG bytes")
    check(seen.get("bytes") != raw,
          "the classifier received the raw upload bytes")
    check(verdict == {"score": 0.01, "flagged": False},
          f"clean judged photo verdict wrong: {verdict}")
    check(app.profile_photo_refusal(verdict) is None,
          "a clean judged photo is refused")

    # A 4000x3000 noise JPEG, the shape of a real phone photo:
    # several megabytes in, judged as its small stored form,
    # and filed by the same store step the endpoint runs.
    import numpy as np

    noise = np.random.default_rng(7).integers(
        0, 256, size=(3000, 4000, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(noise).save(buf, "JPEG", quality=85)
    big = buf.getvalue()
    check(len(big) > 3 * 1024 * 1024,
          f"the synthetic phone photo is not several MB: {len(big)}")
    check(len(big) < app.PROFILE_PHOTO_MAX_BYTES,
          "the synthetic phone photo would hit the size cap "
          f"itself: {len(big)}")
    start = time.time()
    jpeg, verdict = app.judge_profile_photo(big)
    elapsed = time.time() - start
    check(seen.get("bytes") == jpeg,
          "the big photo was not judged as its stored form")
    check(len(jpeg) < 200 * 1024,
          f"the judged form of the big photo is not small: {len(jpeg)}")
    check(app.profile_photo_refusal(verdict) is None,
          "the clean big photo is refused")
    import tempfile

    real_files_dir = app.FILES_DIR
    try:
        with tempfile.TemporaryDirectory() as td:
            app.FILES_DIR = Path(td)
            dest = app.save_profile_photo_file("biguser", jpeg)
            stored = dest.read_bytes()
    finally:
        app.FILES_DIR = real_files_dir
    check(stored == jpeg, "the stored photo is not the judged JPEG")
    with Image.open(io.BytesIO(stored)) as im:
        check(im.format == "JPEG" and im.size == (256, 256),
              f"the stored big photo is wrong: {im.format} {im.size}")
    print(f"big photo: {len(big) / 1e6:.1f} MB in, "
          f"{len(jpeg) / 1e3:.0f} KB stored, "
          f"{elapsed:.1f}s judged end to end")
finally:
    app._output_classifier = real_session
    app._classifier_bytes_score = real_bytes_score

# -- endpoint wiring, pinned in source -------------------------
up = func_src("set_profile_photo")
check(bool(up), "set_profile_photo endpoint not found")
check('@web.post("/api/me/photo")' in SRC,
      "the photo upload route is not registered")
check("require_user" in up, "the upload route does not require sign in")
judge = func_src("judge_profile_photo")
check(bool(judge), "judge_profile_photo helper not found")
check(judge.index("process_profile_photo")
      < judge.index("classify_input_images"),
      "the photo is not processed before it is judged")
check("b64encode(jpeg)" in judge,
      "the classifier is not judging the processed JPEG bytes")
check("b64encode(data)" not in judge
      and "b64encode(data)" not in up,
      "the classifier is still being fed the raw upload bytes")
check("run_in_threadpool" in up and "judge_profile_photo" in up,
      "the upload does not run its heavy work in the threadpool")
check(up.index("judge_profile_photo")
      < up.index("profile_photo_refusal")
      < up.index("save_profile_photo_file"),
      "the upload does not judge, decide, and only then store")
check("profile_photo_refusal" in up,
      "the upload does not run the refusal decision")
check('user["avatar_version"]' in up,
      "the upload does not stamp the new photo version")
check("data_volume.commit()" in up,
      "the upload does not commit the stored photo")

rm = func_src("remove_profile_photo")
check(bool(rm), "remove_profile_photo endpoint not found")
check('@web.delete("/api/me/photo")' in SRC,
      "the photo remove route is not registered")
check("unlink" in rm and 'pop("avatar_version"' in rm,
      "remove does not delete the file and clear the version")

serve = func_src("profile_photo")
check(bool(serve), "profile_photo serve route not found")
check('@web.get("/api/avatars/{user_id}")' in SRC,
      "the photo serve route is not registered")
check("require_user" not in serve,
      "the public photo route must not require sign in")
check("max-age=300" in serve,
      "the photo route does not carry its short public cache")
check('media_type="image/jpeg"' in serve,
      "the photo route does not serve the stored JPEG")

me = func_src("public_user")
check('"avatar_url": avatar_url(user)' in me,
      "/api/me does not carry the account avatar_url")
item = func_src("public_gallery_item")
check('"avatar_url": avatar_url(owner)' in item,
      "gallery items do not carry the creator avatar_url")
helper = func_src("avatar_url")
check("avatar_version" in helper and "?v=" in helper,
      "avatar_url is not versioned by the upload stamp")

# -- dialog structure, pinned in the page source ---------------
a = SRC.index('id="panel-account"')
b = SRC.index('id="panel-billing"', a)
panel = SRC[a:b]
for needle, what in (
        ('id="account-photo"', "the dialog photo element"),
        ('id="account-handle"', "the dialog handle line"),
        ('id="account-email"', "the dialog email line"),
        ('id="photo-change"', "the change photo button"),
        ('id="photo-remove"', "the remove photo button"),
        ('id="photo-file"', "the photo file input"),
        ('id="stat-plan"', "the plan stat row"),
        ('id="stat-mid"', "the middle stat row"),
        ('id="stat-balance"', "the balance stat row"),
        ('class="handle-row"', "the handle editor row group"),
        ('class="switch"', "the gallery switch"),
        ('id="gallery-optout"', "the gallery opt out control"),
        ('id="save-handle"', "the save handle button")):
    check(needle in panel, f"the Account dialog is missing {what}")
check('<a id="account-email"' not in panel
      and '<a href' not in panel.split('id="account-email"')[0][-200:],
      "the dialog email is presented as a link")
check("acct-email" in panel,
      "the dialog email is not the plain muted text element")
check("'Your plan is ' + u.plan" not in SRC,
      "the run-on account sentence is still being built")
check("stat-plan" in SRC and "stat-balance" in SRC
      and "Production time left" in SRC,
      "the stat rows are not filled by the studio script")
check("renderAvatar" in SRC and "u.avatar_url" in SRC,
      "the header avatar does not render the photo state")
check("item.avatar_url" in SRC and "gavatar" in SRC,
      "gallery cards do not render the creator photo")
check("Hide my clips from the public gallery" in panel,
      "the gallery toggle lost its wording")
check("That upload did not go through. Check your connection "
      "and try again." in SRC,
      "a network level upload failure does not show the plain "
      "retry message")
check("catch (netErr)" in SRC,
      "the upload fetch is not guarded for a network failure")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK: profile photo pipeline and Account dialog checks pass")
