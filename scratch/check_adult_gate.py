"""Local checks for the 18+ gate on the uncensored scope and
the acceptance log (Jack, 2026-11-24).

Rules pinned here:
- The gate's scope is the platform's one standing definition,
  job_is_uncensored (the same scope the upload scanning uses):
  the house NSFW lane, the uncensored catalog video models
  (the house Wan 2.2 default included), and the image lanes
  whose pricing entry carries the uncensored flag. A regular
  lane never asks, ever.
- An account without the stamp is refused on an in-scope
  creation with the one plain message, and the refusal sits in
  every create seam before any quote, hold, charge, free
  count, or dispatch (the video create, the shared per-second
  create the drama retake also enters, and the image create).
- The stamp is written once: confirming again keeps the
  original time. /api/me carries the stamp as adult_confirmed
  so the studio can ask in place at the Generate moment.
- The confirmation endpoint takes only an explicit true;
  anything else is refused and records nothing.
- Every real acceptance appends one event to the append only
  acceptance log (the moonfactory-acceptances Dict, keyed by
  user id): the sign-up legal acceptance with its legal
  version and the account email, and the 18+ confirmation.
  Events append in order and never overwrite; a refusal
  writes nothing.
- The dialog copy is the exact confirmation line, the panel
  starts hidden, and the client pre-flight runs inside the
  video and image submit paths only.

No Modal calls, no network: the gate, stamp, and log defs run
for real, extracted from app.py against a fake log store, and
the endpoint and page wiring is pinned in source.

Run:  modal-venv python scratch/check_adult_gate.py
"""
import ast
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

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
            return ast.get_source_segment(SRC, node)
    return None


# --- behavior: the extracted defs against plain dicts -----------------------


class FakeAcceptances:
    """A Dict stand-in: the log def reads and writes whole
    per-user lists, exactly the shape the real Dict holds."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def __setitem__(self, key, value):
        self.store[key] = value


fake_log = FakeAcceptances()
ns = {
    "pricing": pricing,
    "UNCENSORED_VIDEO_MODELS": None,  # filled from the module below
    "DEFAULT_MODEL": None,
    "_now": time.time,
    "acceptances": fake_log,
}
import app as app_module  # noqa: E402

ns["UNCENSORED_VIDEO_MODELS"] = app_module.UNCENSORED_VIDEO_MODELS
ns["DEFAULT_MODEL"] = app_module.DEFAULT_MODEL
ns["ADULT_CONFIRMATION_REQUIRED"] = app_module.ADULT_CONFIRMATION_REQUIRED
exec(func_src("job_is_uncensored"), ns)
exec(func_src("adult_gate_refusal"), ns)
exec(func_src("stamp_adult_confirmation"), ns)
exec(func_src("record_acceptance"), ns)
adult_gate_refusal = ns["adult_gate_refusal"]
stamp_adult_confirmation = ns["stamp_adult_confirmation"]
record_acceptance = ns["record_acceptance"]
MESSAGE = app_module.ADULT_CONFIRMATION_REQUIRED

UNSTAMPED = {"id": "usr1", "email": "someone@example.com"}
STAMPED = {"id": "usr2", "email": "adult@example.com",
           "adult_confirmed_at": 1700000000.0}

REGULAR_VIDEO = {"model": "kling3", "lane": "kling3"}
REGULAR_IMAGE = {"image_model": "house", "lane": "image",
                 "model": "house"}
UNC_VIDEO_MODEL = {"model": "wan22_uncut", "lane": "draft"}
UNC_DEFAULT_MODEL = {"model": app_module.DEFAULT_MODEL,
                     "lane": "draft"}
UNC_NSF_LANE = {"model": "wan2.2", "lane": "nsfw"}
UNC_IMAGE = {"image_model": "krea2_verite", "lane": "image",
             "model": "krea2_verite"}

# A regular lane never asks, stamped or not.
check(adult_gate_refusal(UNSTAMPED, REGULAR_VIDEO) is None,
      "a regular video lane never asks an unstamped account")
check(adult_gate_refusal(UNSTAMPED, REGULAR_IMAGE) is None,
      "a regular image lane never asks an unstamped account")
check(adult_gate_refusal(UNSTAMPED, {}) is None,
      "an empty probe is out of scope and never asks")
check(adult_gate_refusal(UNSTAMPED, None) is None,
      "a missing probe is out of scope and never asks")

# Every in-scope shape refuses an unstamped account, with the
# one plain message.
for probe, name in ((UNC_VIDEO_MODEL, "an uncensored video model"),
                    (UNC_DEFAULT_MODEL, "the house default model"),
                    (UNC_NSF_LANE, "the house NSFW lane"),
                    (UNC_IMAGE, "an uncensored image lane")):
    check(adult_gate_refusal(UNSTAMPED, probe) == MESSAGE,
          f"{name} refuses an unstamped account")

# A stamped account passes everywhere.
for probe, name in ((UNC_VIDEO_MODEL, "an uncensored video model"),
                    (UNC_DEFAULT_MODEL, "the house default model"),
                    (UNC_NSF_LANE, "the house NSFW lane"),
                    (UNC_IMAGE, "an uncensored image lane")):
    check(adult_gate_refusal(STAMPED, probe) is None,
          f"{name} lets a stamped account through")

# The refusal message follows the customer copy rules.
check(not any(c in MESSAGE for c in ":;"),
      "the refusal message carries no colon or semicolon")
check("-" not in MESSAGE and "—" not in MESSAGE,
      "the refusal message carries no dash")

# The stamp is written once.
fresh = {"id": "usr3", "email": "fresh@example.com"}
check(stamp_adult_confirmation(fresh) is True,
      "the first confirmation writes the stamp")
first_at = fresh.get("adult_confirmed_at")
check(isinstance(first_at, float) and first_at > 0,
      "the stamp carries a server timestamp")
check(stamp_adult_confirmation(fresh) is False,
      "a second confirmation does not restamp")
check(fresh.get("adult_confirmed_at") == first_at,
      "the original stamp time survives a second confirmation")
check(adult_gate_refusal(fresh, UNC_VIDEO_MODEL) is None,
      "a freshly stamped account passes the gate")

# --- behavior: the acceptance log -------------------------------------------

SIGNUP_KIND = app_module.ACCEPTANCE_SIGNUP_LEGAL
ADULT_KIND = app_module.ACCEPTANCE_ADULT_18_PLUS
check(SIGNUP_KIND == "signup_legal" and ADULT_KIND == "adult_18_plus",
      "the log's event kinds are the signup and 18+ kinds")

acct = {"id": "usr9", "email": "new@example.com"}
record_acceptance(acct, SIGNUP_KIND,
                  legal_version=app_module.LEGAL_VERSION)
events = fake_log.store.get("usr9") or []
check(len(events) == 1,
      "a signup appends exactly one acceptance event")
ev = events[0] if events else {}
check(ev.get("kind") == SIGNUP_KIND,
      "the signup event carries the signup kind")
check(ev.get("legal_version") == app_module.LEGAL_VERSION,
      "the signup event carries the legal version accepted")
check(ev.get("user_id") == "usr9"
      and ev.get("email") == "new@example.com",
      "the signup event carries the user id and account email")
check(isinstance(ev.get("at"), float) and ev.get("at") > 0,
      "the signup event carries a server timestamp")

record_acceptance(acct, ADULT_KIND)
events = fake_log.store.get("usr9") or []
check(len(events) == 2,
      "the 18+ confirmation appends its own event")
check(events[0].get("kind") == SIGNUP_KIND
      and events[1].get("kind") == ADULT_KIND,
      "events stay in order and the first is never overwritten")
check(events[1].get("legal_version") is None,
      "the 18+ event carries no legal version (none applies)")

record_acceptance(acct, ADULT_KIND)
events = fake_log.store.get("usr9") or []
check(len(events) == 3 and events[2].get("kind") == ADULT_KIND,
      "a repeated acceptance appends again rather than overwriting")

# A refusal writes nothing: the gate def holds no store, and
# running refusals leaves every list untouched.
before = {k: len(v) for k, v in fake_log.store.items()}
adult_gate_refusal(UNSTAMPED, UNC_VIDEO_MODEL)
adult_gate_refusal(UNSTAMPED, UNC_IMAGE)
after = {k: len(v) for k, v in fake_log.store.items()}
check(before == after, "a refused attempt logs nothing")

# --- source pins: the server -------------------------------------------------

check('acceptances = modal.Dict.from_name("moonfactory-acceptances"'
      in SRC,
      "the acceptance log is its own dedicated Dict")

create_job_src = func_src("create_job_core") or ""
check('adult_gate_refusal(\n            user, {"model": model, "lane": lane})'
      in create_job_src,
      "the video create runs the 18+ gate on the creation probe")
check(create_job_src.index("adult_gate_refusal")
      < create_job_src.index("return create_api_job("),
      "the video create gate lands before the per-second branch "
      "and every charge below it")

api_job_src = func_src("create_api_job") or ""
check('adult_gate_refusal(\n            user, {"model": model, "lane": model})'
      in api_job_src,
      "the shared per-second create runs the 18+ gate")
check(api_job_src.index("adult_gate_refusal")
      < api_job_src.index("api_lane_state(model)"),
      "the per-second gate lands before the quote, hold, and submit")

image_src = func_src("create_image_core") or ""
check('{"image_model": lane_id, "lane": "image",' in image_src
      and "adult_gate_refusal(" in image_src,
      "the image create runs the 18+ gate on the shelf probe")
check(image_src.index("adult_gate_refusal")
      < image_src.index('user["balance_cents"] = int('),
      "the image gate lands before the charge")

confirm_src = func_src("adult_confirm") or ""
check('body.get("adult_confirm") is not True' in confirm_src,
      "the confirmation endpoint takes only an explicit true")
check("stamp_adult_confirmation(user)" in confirm_src,
      "the confirmation endpoint stamps through the write-once def")
check("record_acceptance(user, ACCEPTANCE_ADULT_18_PLUS)"
      in confirm_src,
      "the confirmation endpoint appends its acceptance event")
check('@web.post("/api/me/adult-confirm")' in SRC,
      "the confirmation endpoint is routed")

signup_src = func_src("signup") or ""
check("record_acceptance(user, ACCEPTANCE_SIGNUP_LEGAL,"
      in signup_src,
      "the signup endpoint appends its acceptance event")
check('legal_version=acceptance["legal_version"]' in signup_src,
      "the signup event carries the accepted legal version")

me_src = func_src("public_user") or ""
check('"adult_confirmed": bool(user.get("adult_confirmed_at"))'
      in me_src,
      "/api/me carries the stamp as adult_confirmed")

# --- source pins: the page ---------------------------------------------------

check('id="adult-gate" class="card" style="display:none'
      in SRC,
      "the 18+ panel starts hidden")
check('<input type="checkbox" id="adult-confirm"' in SRC,
      "the panel carries the confirmation checkbox")
check("I confirm I am 18 years old or older." in SRC,
      "the checkbox reads with the exact confirmation copy")
check("async function ensureAdultConfirmed(inScope)" in SRC,
      "the studio carries the in-place confirmation flow")
check("modelIsUncensored(d.body.model)" in SRC,
      "the video submit pre-flights the picked model's scope")
check("imageModelIsUncensored(d.body.image_model)" in SRC,
      "the image submit pre-flights the picked lane's scope")
check("'/api/me/adult-confirm'" in SRC,
      "the panel confirms through the stamping endpoint")
check("if (currentUser.adult_confirmed) return true;" in SRC,
      "a stamped account is never asked again")
check("if (!currentUser) return true;" in SRC,
      "a signed out build parks at sign in first and the gate "
      "runs when the creation fires signed in")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_adult_gate")
