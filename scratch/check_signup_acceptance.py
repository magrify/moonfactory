"""Local checks for legal acceptance at sign-up (Jack, 2026-11-24).

Rules pinned here:
- Creating an account requires accepting the Terms of
  Service, the Privacy Policy, and the Acceptable Use Policy,
  and confirming the age floor. The sign-in dialog carries an
  acceptance checkbox that starts unchecked, never pre-checked,
  and its legal links open in a new tab so the dialog state and
  a parked creation are never lost.
- The checkbox gates Create account only. Sign in works
  exactly as before and never looks at the box.
- The sign-up endpoint itself refuses any create that does
  not carry the acceptance, with the same plain message the
  form shows, whatever the client sent. The refusal lands
  before the bot check and before anything is stored, so an
  unchecked attempt parks nothing server side.
- A successful sign-up stamps the account with the acceptance
  time and the legal version it accepted. LEGAL_VERSION names
  the pack by the date the Terms page itself carries.
- Existing accounts are untouched: no stamp backfill anywhere.

No Modal calls, no network: the stamp function runs for real,
the endpoint and form wiring is pinned in source.

Run:  modal-venv python scratch/check_signup_acceptance.py
"""
import ast
import sys
import time
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
HTML = app.APP_HTML


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the version names the pack the Terms page shows ---------
check(app.LEGAL_VERSION == "2026-11-23",
      "LEGAL_VERSION names the current legal pack")
check("Last updated: November 23, 2026" in app.TERMS_HTML,
      "the Terms page carries the date LEGAL_VERSION names")
check(":" not in app.LEGAL_ACCEPTANCE_REQUIRED
      and ";" not in app.LEGAL_ACCEPTANCE_REQUIRED
      and "-" not in app.LEGAL_ACCEPTANCE_REQUIRED,
      "the refusal message follows the plain copy rule")

# -- the stamp function, run for real -------------------------
check(app.legal_acceptance_stamp({}) is None,
      "a sign-up with no acceptance gets no stamp")
check(app.legal_acceptance_stamp({"accept_terms": False}) is None,
      "an unchecked box gets no stamp")
check(app.legal_acceptance_stamp({"accept_terms": "true"}) is None,
      "only a true acceptance counts, not a string")
check(app.legal_acceptance_stamp(None) is None,
      "a missing body gets no stamp")
stamp = app.legal_acceptance_stamp({"accept_terms": True})
check(isinstance(stamp, dict)
      and stamp.get("legal_version") == app.LEGAL_VERSION,
      "an accepted sign-up stamps the legal version")
check(isinstance(stamp, dict)
      and abs(stamp.get("accepted_at", 0) - time.time()) < 60,
      "an accepted sign-up stamps the acceptance time")

# -- the endpoint enforces it, in order -----------------------
signup = func_src("signup")
check("acceptance = legal_acceptance_stamp(body)" in signup,
      "the sign-up endpoint reads the acceptance from the body")
check("detail=LEGAL_ACCEPTANCE_REQUIRED" in signup,
      "the endpoint refuses with the plain message")
gate = signup.find("legal_acceptance_stamp(body)")
bot = signup.find("_verify_bot_check(body, ip)")
saved = signup.find("save_user(user)")
check(-1 < gate < bot,
      "the acceptance refusal lands before the bot check")
check(-1 < gate < saved,
      "the acceptance refusal lands before anything is stored")
check('"terms_accepted_at": acceptance["accepted_at"]' in signup,
      "the new account record stamps the acceptance time")
check('"legal_version": acceptance["legal_version"]' in signup,
      "the new account record stamps the legal version")
check('user["pending_create"] = pending' in signup
      and gate < signup.find('user["pending_create"] = pending'),
      "a refused sign-up stores no parked creation; an accepted one still does")

# -- sign in never looks at the box ---------------------------
login = func_src("login")
check("accept_terms" not in login and "acceptance" not in login,
      "sign in works without the acceptance, exactly as before")

# -- the dialog carries the box, unchecked --------------------
box_at = HTML.find('id="accept-terms"')
check(box_at != -1, "the sign-in dialog carries the acceptance checkbox")
tag_start = HTML.rfind("<input", 0, box_at)
tag_end = HTML.find(">", box_at)
tag = HTML[tag_start:tag_end]
check('type="checkbox"' in tag and "checked" not in tag,
      "the box starts unchecked, never pre-checked")
label = HTML[tag_start:HTML.find("</label>", box_at)]
check("I agree to the" in label, "the label opens with the agreement")
for href, name in (("/terms", "Terms of Service"),
                   ("/privacy", "Privacy Policy"),
                   ("/acceptable-use", "Acceptable Use Policy")):
    check(f'href="{href}" target="_blank"' in label and name in label,
          f"the label links the {name} in a new tab, so nothing is lost")
check("and I confirm I am at least 18 years old." in label,
      "the label ends with the age confirmation")

# -- the client stops an unchecked create and sends the box ---
onclick = SRC.split("$('signup').onclick", 1)[1].split(
    "$('login').onclick", 1)[0]
check("if (!$('accept-terms').checked)" in onclick,
      "Create account stops when the box is unchecked")
check(app.LEGAL_ACCEPTANCE_REQUIRED in onclick,
      "the form shows the same plain message the endpoint refuses with")
check(onclick.find("accept-terms') .checked") == -1
      and onclick.find("if (!$('accept-terms').checked)")
      < onclick.find("api('/api/signup'"),
      "the stop lands before any sign-up request is sent")
check("accept_terms: $('accept-terms').checked" in onclick,
      "the create payload carries the acceptance")
check("payload.pending_create = serializePending(pendingCreate)" in onclick,
      "the parked creation still rides the same sign-up payload")

if failures:
    print("FAIL check_signup_acceptance")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_signup_acceptance")
