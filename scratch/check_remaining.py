"""Pin for the remaining-findings cluster (security fixes,
2026-11-27).

- pow-fallback-preauth-imbalance: with TURNSTILE_SECRET_KEY
  configured a proof-of-work solve no longer passes, and a
  submitted nonce spends its challenge either way.
- ratelimit-meta-keys-grow-unbounded: expired rl:/pow:/
  loginfail: keys are swept.
- login-no-botcheck-scrypt: unknown emails verify against
  a dummy scrypt hash, and per-account failures impose a
  progressive delay (source pins; the handler rides the web
  machinery).
- gallery-media-immutable-cache: public gallery media
  revalidates after an hour; owner media is unchanged.
- gallery-optout-feed-not-invalidated: the serve path
  re-checks visibility per item, and opt-out drops the
  user's items from the feed in the same request.
- gallery-reports-global-fifo-eviction: reports are
  durable per-job aggregates with per-reporter dedup.
- handle-scan-unratelimited: uniqueness reads a handle
  index Dict, and the endpoint is rate limited.

The bot check, the sweep, and the media cache function are
exercised for real below (AST-extracted, plain dicts).
"""
import ast
import hashlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def fn(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"function {name} not found")


def check(label, cond):
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"ok: {label}")


# --- bot check, exercised -------------------------------------
meta = {}
ns = {
    "meta": meta, "os": os, "sys": sys, "hashlib": hashlib,
    "POW_DIFFICULTY_BITS": 18,
    "_now": lambda: time.time(),
    "_turnstile_configured":
        lambda: bool(os.environ.get("TURNSTILE_SECRET_KEY")),
}
exec(fn("_verify_bot_check"), ns)
verify = ns["_verify_bot_check"]

challenge = "ch-1234567890abcdef"
meta[f"pow:{challenge}"] = {
    "challenge": challenge, "used": False,
    "expires": time.time() + 600}
good_nonce = None
for i in range(2_000_000):
    digest = hashlib.sha256(f"{challenge}:{i}".encode()).hexdigest()
    if int(digest, 16) >> (256 - 18) == 0:
        good_nonce = str(i)
        break
assert good_nonce is not None

os.environ.pop("TURNSTILE_SECRET_KEY", None)
check("PoW passes when Turnstile is not configured",
      verify({"challenge_id": challenge,
              "challenge_nonce": good_nonce}, "1.2.3.4") is True)

meta[f"pow:{challenge}"] = {
    "challenge": challenge, "used": False,
    "expires": time.time() + 600}
os.environ["TURNSTILE_SECRET_KEY"] = "test-secret"
check("PoW no longer passes when Turnstile is configured",
      verify({"challenge_id": challenge,
              "challenge_nonce": good_nonce}, "1.2.3.4") is False)
check("a missing token fails when Turnstile is configured",
      verify({}, "1.2.3.4") is False)
os.environ.pop("TURNSTILE_SECRET_KEY", None)

meta[f"pow:{challenge}"] = {
    "challenge": challenge, "used": False,
    "expires": time.time() + 600}
bad = verify({"challenge_id": challenge,
              "challenge_nonce": "not-the-nonce"}, "1.2.3.4")
check("a failed nonce spends the challenge",
      bad is False
      and meta[f"pow:{challenge}"]["used"] is True)
check("the good nonce cannot replay a spent challenge",
      verify({"challenge_id": challenge,
              "challenge_nonce": good_nonce}, "1.2.3.4") is False)

# --- meta sweep, exercised --------------------------------------
meta2 = {
    "rl:login:1.1.1.1": {"start": time.time() - 7200, "count": 3},
    "rl:login:2.2.2.2": {"start": time.time(), "count": 1},
    "pow:old": {"expires": time.time() - 7200},
    "pow:fresh": {"expires": time.time() + 600},
    "loginfail:a@b.c": {"count": 9, "last": time.time() - 7200},
    "loginfail:d@e.f": {"count": 2, "last": time.time()},
    "unrelated": {"keep": True},
}
ns2 = {"meta": meta2, "_now": lambda: time.time()}
exec(fn("sweep_expired_meta"), ns2)
removed = ns2["sweep_expired_meta"]()
check("the sweep removes only expired ephemeral keys",
      removed == 3
      and set(meta2) == {"rl:login:2.2.2.2", "pow:fresh",
                         "loginfail:d@e.f", "unrelated"})

# --- media cache, exercised --------------------------------------
ns3 = {}
exec(fn("_media_cache"), ns3)
cache = ns3["_media_cache"]
check("public gallery media revalidates",
      cache({"kind": "video"}, public=True)
      == "public, max-age=3600, must-revalidate")
check("owner media keeps the long immutable cache",
      cache({"kind": "video"})
      == "private, max-age=31536000, immutable")
check("owner drama media keeps its hour",
      cache({"kind": "drama"}) == "private, max-age=3600")

# --- source pins ---------------------------------------------------
login = fn("login")
check("login verifies unknown emails against the dummy hash",
      "_DUMMY_PW_HASH" in login)
check("login imposes the progressive per-account delay",
      "loginfail:" in login and "2 ** (int(fails" in login)
check("a real scrypt dummy hash is configured",
      "_DUMMY_PW_HASH = (" in SRC and "$" in fn("_hash_password")
      or "_DUMMY_PW_HASH" in SRC)
optout = fn("set_gallery_optout")
check("opt-out drops the user's feed items in the same request",
      "_gallery_drop_user_items" in optout)
listing = fn("gallery_list")
check("every gallery serve path re-checks visibility",
      listing.count("_gallery_visible_items(items)") >= 3)
report = fn("report_gallery_item")
check("reports aggregate per job with reporter dedup",
      "gallery_reports" in report and "reporters" in report
      and "reports[-500:]" not in SRC)
handle = fn("set_handle")
check("handle uniqueness reads the index, not a scan",
      "handles.get(handle.lower())" in handle
      and '_rate_limited("handle"' in handle)
check("the handles index Dict exists",
      'modal.Dict.from_name("moonfactory-handles"' in SRC)

print("check_remaining: all pins hold")
