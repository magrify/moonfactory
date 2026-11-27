"""Local checks for the CSRF origin guard (security fix,
2026-11-27; audit: csrf-lax-only-cookie-auth-no-origin-or-
token-check, MEDIUM).

The app is served from modal.run, a public suffix, so
SameSite=Lax cannot separate it from a neighbour app. The
guard refuses a state changing request that rides the
session cookie unless its Origin (or Referer) is this app's
own host. The middleware is extracted from app.py and driven
with fake requests. No Modal calls, no network.

Run:  modal-venv python scratch/check_csrf_guard.py
"""
import ast
import asyncio
import sys
import urllib.parse
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


class Resp:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code


def JSONResponse(body, status_code=200):
    return Resp(body, status_code)


import os

ns = {"JSONResponse": JSONResponse, "os": os,
      "urllib": urllib, "Request": object}
exec(func_src("csrf_origin_guard"), ns)
guard = ns["csrf_origin_guard"]


class Req:
    def __init__(self, method="POST", cookies=None, headers=None):
        self.method = method
        self.cookies = cookies or {}
        self.headers = headers or {}


async def call_next(request):
    return Resp({"ok": True}, 200)


def run(req):
    return asyncio.get_event_loop().run_until_complete(guard(req, call_next))


HOST = {"host": "magrify--moonfactory-api.us-east.modal.run"}
COOKIE = {"mf_session": "abc"}

r = run(Req(cookies=COOKIE, headers={**HOST, "origin":
    "https://magrify--moonfactory-api.us-east.modal.run"}))
check(r.status_code == 200, "same-origin cookie POST passes")

r = run(Req(cookies=COOKIE, headers={**HOST, "origin":
    "https://attacker--evil.us-east.modal.run"}))
check(r.status_code == 403,
      "a neighbour app on the same public suffix is refused")

r = run(Req(cookies=COOKIE, headers=dict(HOST)))
check(r.status_code == 403,
      "a cookie POST with no Origin at all is refused")

r = run(Req(cookies=COOKIE, headers={**HOST, "referer":
    "https://magrify--moonfactory-api.us-east.modal.run/app"}))
check(r.status_code == 200, "a same-origin Referer passes")

r = run(Req(headers={**HOST, "origin": "https://evil.example",
                     "authorization": "Bearer mf_live_x"}))
check(r.status_code == 200,
      "API key requests (no cookie) are untouched")

r = run(Req(method="GET", cookies=COOKIE,
            headers={**HOST, "origin": "https://evil.example"}))
check(r.status_code == 200, "safe methods are untouched")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_csrf_guard: all checks passed")
