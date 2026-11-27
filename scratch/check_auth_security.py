"""Local checks for the auth security fixes (2026-11-27).

Audit findings pinned here:
  * pre-account-takeover-unverified-password-merge (HIGH):
    a social sign-in merging into an unverified password
    account clears the pre-registered password, password
    sign-in refuses an unverified account, and the owner has
    real eviction paths (signed-in password change with
    epoch rotation, and an emailed reset that verifies the
    account as it sets the password).
  * oauth-park-unbound-pending-create-autofire (HIGH): the
    park is bound to the parking browser by a nonce cookie
    whose hash is all the server stores, a parked creation
    never overwrites the account's own in-session creation,
    it lands flagged needs_confirm, and the claim hands it
    over for firing only on an explicit confirm; an illegal
    confirmed payload is dropped without a refusal record.
  * oauth-state-not-browser-bound-login-csrf (MEDIUM): both
    redirect flows bind their state to the starting browser
    the same way and the callbacks refuse a foreign browser.

Source pins plus behavior legs for the pure helpers, the
way the house checks prove their rules. No Modal calls, no
network.

Run:  modal-venv python scratch/check_auth_security.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

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


# --- pre-account takeover ---------------------------------------------------
google_cb = func_src("google_oauth_callback")
check('user["pw"] = ""' in google_cb
      and "pw_cleared_by_social_merge" in google_cb,
      "the Google merge into an unverified account clears "
      "the pre-registered password")
login = func_src("login")
check('user.get("verified") is False' in login,
      "password sign-in refuses an unverified account")
check(bool(func_src("set_my_password")),
      "the signed-in password change endpoint exists")
check("session_epoch" in func_src("set_my_password"),
      "the password change rotates the session epoch")
check(bool(func_src("password_reset_request"))
      and bool(func_src("password_reset_confirm")),
      "the emailed reset flow exists")
check('"verified"] = True' in func_src("password_reset_confirm")
      or 'user["verified"] = True' in func_src("password_reset_confirm"),
      "the reset verifies the account as it sets the password")
check("has_password" in func_src("public_user"),
      "the studio can tell whether the account has a password")

# --- park binding + confirm ---------------------------------------------------
park = func_src("oauth_park")
check("bind_hash" in park and "mf_park_bind" in park,
      "the park stores only the hash of a browser nonce cookie")
land = func_src("_land_parked_create")
check("mf_park_bind" in land and "bind_hash" in land,
      "landing a park requires the parking browser")
check('user.get("pending_create")' in land,
      "a park never overwrites the account's own pending creation")
check('"needs_confirm"' in land or "needs_confirm" in land,
      "a parked creation lands flagged needs_confirm")
claim = func_src("claim_pending_create")
check('body.get("confirm")' in claim,
      "the claim requires an explicit confirm for parked creations")
check("prompt_is_illegal" in claim and "record_refusal" not in claim,
      "an illegal parked payload is dropped at claim with no "
      "refusal recorded against the account")
check("fireRestoredIfReady" in SRC and "needsConfirm" in SRC
      and "showRestoreConfirm" in SRC,
      "the studio shows a parked creation for an explicit start "
      "instead of firing it at sign-in")

# --- oauth state browser binding ----------------------------------------------
for start, cb in (("google_oauth_start", "google_oauth_callback"),
                  ("x_oauth_start", "x_oauth_callback")):
    check("mf_oauth_bind" in func_src(start),
          f"{start} sets the browser binding cookie")
    check("_oauth_bind_ok" in func_src(cb),
          f"{cb} requires the starting browser")

# --- behavior: the landing helper against fakes --------------------------------
import hashlib
import hmac
import time

meta = {}
users_store = {}


class FakeRequest:
    def __init__(self, cookies):
        self.cookies = cookies


def save_user(user):
    users_store[user["id"]] = user


ns = {"meta": meta, "save_user": save_user, "_now": lambda: time.time(),
      "hashlib": hashlib, "hmac": hmac, "Request": object, "dict": dict}
exec(func_src("_land_parked_create"), ns)
land_fn = ns["_land_parked_create"]

nonce = "browser-nonce"
meta["oauth-park:p1"] = {
    "pending_create": {"kind": "video", "body": {"prompt": "x"}},
    "expires": time.time() + 600,
    "bind_hash": hashlib.sha256(nonce.encode()).hexdigest()}
record = {"park": "p1"}

victim = {"id": "v1"}
land_fn(victim, record, FakeRequest({}))
check("pending_create" not in victim,
      "a park id alone, in a foreign browser, claims nothing")

owner = {"id": "v2"}
land_fn(owner, record, FakeRequest({"mf_park_bind": nonce}))
check(owner.get("pending_create", {}).get("needs_confirm") is True,
      "the parking browser's sign-in lands the creation flagged "
      "needs_confirm")
check(meta["oauth-park:p1"]["pending_create"] is None,
      "a landed park is spent")

meta["oauth-park:p2"] = {
    "pending_create": {"kind": "video", "body": {"prompt": "y"}},
    "expires": time.time() + 600,
    "bind_hash": hashlib.sha256(nonce.encode()).hexdigest()}
busy = {"id": "v3", "pending_create": {"kind": "video", "body": {}}}
land_fn(busy, {"park": "p2"}, FakeRequest({"mf_park_bind": nonce}))
check(busy["pending_create"].get("body") == {},
      "a park never overwrites an in-session pending creation")

# --- behavior: pending gate texts ----------------------------------------------
gns = {}
exec(func_src("_pending_gate_texts"), gns)
texts = gns["_pending_gate_texts"](
    {"body": {"prompt": "a cat", "lines": ["one", "two"],
              "nested": {"script": "words"}}})
check("a cat" in texts and "one" in texts and "words" in texts,
      "the gate text walk reaches nested authored strings")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_auth_security: all checks passed")
