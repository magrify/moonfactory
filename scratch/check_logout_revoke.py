"""Logout must revoke server side (Jack, 2026-11-21: the Log out
button was not signing him out on his phone).

Sessions are stateless signed tokens, so clearing the cookie is
the browser's half of the job. The account now carries a session
epoch: login mints tokens under the current epoch, logout stamps
a fresh epoch, and current_user rejects any token whose epoch is
stale. This check pins that wiring and round-trips the token
functions the way the proof apps use them.
"""
import re
import sys

sys.path.insert(0, "platform")

# _session_secret fails closed when SESSION_SECRET is
# unconfigured (security hardening, 2026-11-27); the harness
# supplies a check-only value so the token round-trip can run.
import os

os.environ.setdefault("SESSION_SECRET", "check-only-secret")
SRC = open("platform/app.py").read()

fails = []


def need(cond, label):
    if not cond:
        fails.append(label)


# Token shape: epoch rides in the signed payload.
need('def _make_session(user_id: str, epoch: str = "")' in SRC,
     "_make_session takes an epoch")
need('"ep": epoch' in SRC, "token payload carries the epoch")
need("return payload" in SRC, "_read_session returns the payload")

# Enforcement lives in current_user.
cu = SRC.split("def current_user")[1].split("def require_user")[0]
need('user.get("session_epoch")' in cu, "current_user reads the epoch")
need('payload.get("ep") != epoch' in cu,
     "current_user rejects a stale epoch")

# Login mints under the account's current epoch.
need('_make_session(user["id"],\n                                        user.get("session_epoch", ""))' in SRC
     or 'user.get("session_epoch", "")' in SRC,
     "login mints with the account epoch")

# Logout bumps the epoch and clears both cookie paths.
lo = SRC.split('"/api/logout"')[1].split('"/api/me"')[0]
need('user["session_epoch"] = uuid.uuid4().hex' in lo,
     "logout stamps a fresh epoch")
need('resp.delete_cookie("mf_session")' in lo,
     "logout deletes the root cookie")
need("Path=/app" in lo, "logout also expires the /app cookie")

# The button reloads even if the request itself fails.
need("try { await api('/api/logout', { method: 'POST' }); } catch (e) {} location.reload();" in SRC,
     "logout click always reloads")

# Functional round-trip of the token functions.
import app as platform_app  # noqa: E402

tok = platform_app._make_session("user-1", "epoch-a")
payload = platform_app._read_session(tok)
need(payload is not None and payload.get("uid") == "user-1"
     and payload.get("ep") == "epoch-a",
     "token round-trips uid and epoch")
old = platform_app._make_session("user-1")
need(platform_app._read_session(old) is not None,
     "pre-epoch token still parses (epoch enforced per account)")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS check_logout_revoke")
