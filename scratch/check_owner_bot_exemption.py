"""Local checks for the owner bot check exemption (Jack,
2026-11-21): once the owner account is signed in, the human
check wall (Cloudflare Turnstile token or proof of work) is
off for that account only, on every gated creation endpoint.
Everyone else, signed in or signed out, passes the exact
same check as before, and signup keeps the check for all.

The decision keys on the is_owner flag on the server-resolved
session record, the same source of truth as the owner pricing
exemption. An email string alone never grants it, and nothing
the client sends can claim it.

No Modal, no network: execs the extracted is_owner /
bot_check_ok defs from app.py against plain dicts with the
real verifier stubbed, and pins the guard placement in each
endpoint's source plus the page wiring.

Run:  modal-venv python scratch/check_owner_bot_exemption.py
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
            return ast.get_source_segment(SRC, node)
    return None


# --- behavior: the extracted defs against plain dicts -----------------------

calls = []
verdict = {"ok": False}


def stub_verify(req_body, remote_ip):
    calls.append((req_body, remote_ip))
    return verdict["ok"]


ns = {"_verify_bot_check": stub_verify}
exec(func_src("is_owner"), ns)
exec(func_src("bot_check_ok"), ns)
bot_check_ok = ns["bot_check_ok"]
is_owner = ns["is_owner"]

OWNER = {"id": "own1", "email": "support@magrify.com", "is_owner": True}
PLAIN = {"id": "usr1", "email": "someone@example.com"}
EMAIL_ONLY = {"id": "usr2", "email": "support@magrify.com"}

# The owner passes with no token and no proof of work, and the
# real verifier is never even consulted.
calls.clear()
verdict["ok"] = False
check(bot_check_ok({}, "1.2.3.4", OWNER) is True,
      "owner session passes the bot check with no token")
check(calls == [], "the verifier is not consulted for the owner")

# A non-owner still lives and dies by the real verifier.
calls.clear()
verdict["ok"] = False
check(bot_check_ok({}, "1.2.3.4", PLAIN) is False,
      "non-owner with no token still fails the bot check")
check(len(calls) == 1, "the verifier runs for a non-owner")
verdict["ok"] = True
check(bot_check_ok({"turnstile_token": "t"}, "1.2.3.4", PLAIN) is True,
      "non-owner with a passing check still passes")

# Signed out (no session user at all) still fails.
verdict["ok"] = False
check(bot_check_ok({}, "1.2.3.4", None) is False,
      "signed-out call with no token still fails the bot check")
check(bot_check_ok({}, "1.2.3.4") is False,
      "a call with no user argument still fails the bot check")

# The owner email string without the flag earns nothing: the
# exemption reads the account record's flag, never the email.
verdict["ok"] = False
check(is_owner(EMAIL_ONLY) is False,
      "the owner email without the is_owner flag is not the owner")
check(bot_check_ok({}, "1.2.3.4", EMAIL_ONLY) is False,
      "the owner email alone does not skip the bot check")
check(is_owner(OWNER) is True and is_owner(PLAIN) is False
      and is_owner(None) is False and is_owner({}) is False,
      "is_owner reads only the account record flag")

# --- source pins: every gated endpoint routes through the guard -------------

GATED = ("create_job", "create_image", "create_clip", "create_template",
         "create_short", "create_drama", "retake_drama_shot")
for name in GATED:
    src = func_src(name) or ""
    check("bot_check_ok(body, client_ip(request), user)" in src,
          f"{name} routes its bot check through the owner guard")
    check("_verify_bot_check(body, client_ip(request))" not in src,
          f"{name} no longer calls the raw verifier")

signup_src = func_src("signup") or ""
check("_verify_bot_check(body, ip)" in signup_src,
      "signup keeps the raw bot check")
check("bot_check_ok" not in signup_src,
      "signup has no owner exemption (there is no session yet)")

me_src = func_src("public_user") or ""
check('"is_owner": is_owner(user)' in me_src,
      "/api/me carries is_owner from the account record")

# --- source pins: the page ---------------------------------------------------

check("let botExempt = false;" in SRC,
      "the page carries the exemption flag, defaulted off")
check("if (botExempt) return {};" in SRC,
      "botFields asks for nothing once the owner is signed in")
check("if (botExempt) return;\n  if (cfg.turnstile_site_key && window.turnstile)" in SRC,
      "renderBotChecks renders no widget for the owner")
check("if (u.is_owner) dropBotChecks();" in SRC,
      "afterSignIn drops the wall from the /api/me owner signal")
drop_src = SRC.split("function dropBotChecks()")[1].split("async function api(")[0] \
    if "function dropBotChecks()" in SRC else ""
check("botExempt = true;" in drop_src and "turnstile.remove" in drop_src,
      "dropBotChecks sets the flag and removes rendered widgets")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS: owner bot check exemption")
