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

# -- endpoint wiring, pinned in source -------------------------
up = func_src("set_profile_photo")
check(bool(up), "set_profile_photo endpoint not found")
check('@web.post("/api/me/photo")' in SRC,
      "the photo upload route is not registered")
check("require_user" in up, "the upload route does not require sign in")
check(up.index("process_profile_photo")
      < up.index("classify_input_images")
      < up.index("tmp.write_bytes"),
      "the upload does not judge the photo before storing it")
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

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("OK: profile photo pipeline and Account dialog checks pass")
