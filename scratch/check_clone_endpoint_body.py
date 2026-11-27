"""Local checks for the composed-body creation paths (2026-11-27).

The bug this pins against: an endpoint that composes a job
body server side (the Clone tab's create and quote, job
regenerate, and every retry branch) used to hand the body to
the creation wrapper by assigning request._body and calling
the wrapper. That substitution never arrives. The endpoint
first awaits request.json(), and Starlette (1.10.0, the
version the app pins through fastapi[standard]==0.165.0)
caches the parsed body on the request: json() answers from
the cache on every later call, so the wrapper's own
request.json() returns the ORIGINAL request body and the
composed body is silently dropped. Proven live by the clone
picker proof (~/workspace/sinclip/clone-picker-proof-2026-11-27.md):
a clone create sent without a prompt field was refused 422
"a prompt of up to 2000 characters is required" even though
the builder had composed the template prompt, and a user
interface house-lane clone was created as a plain draft-lane
text job with no character picture, because the composed
start image, segments, production lane and aspect never
reached create_job_core.

The fix, pinned here: every composing endpoint passes its
composed body to the creation core directly
(create_job_core, quote_job_core, create_image_core, and the
newly extracted create_short_core, create_clip_core and
create_template_core), running the wrapper's own checks
(pace limit, verified, past due, bot check) against the
composed body first, in the wrapper's order. No
request._body assignment remains anywhere in app.py.

Behavior is proven the way this repo proves behavior
locally: the Starlette cache mechanism is reproduced below
with the pinned version's own body()/json() logic, and the
endpoint wiring is pinned by source. No Modal calls, no
network, no generation.

Run:  modal-venv python scratch/check_clone_endpoint_body.py
"""
import ast
import asyncio
import json
import re
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


# -- the mechanism, reproduced with the pinned Starlette's logic --------
#
# Starlette 1.10.0 (pinned by fastapi[standard]==0.165.0 in the
# app image) implements Request.body() and Request.json() as:
#
#     async def body(self) -> bytes:
#         if not hasattr(self, "_body"):
#             chunks = []
#             async for chunk in self.stream():
#                 chunks.append(chunk)
#             self._body = b"".join(chunks)
#         return self._body
#
#     async def json(self) -> typing.Any:
#         if not hasattr(self, "_json"):
#             body = await self.body()
#             self._json = json.loads(body)
#         return self._json
#
# The miniature below is those two methods verbatim in
# substance, over a scripted stream. It demonstrates the exact
# failure the proof run hit live: once json() has been
# awaited, assigning _body changes nothing json() returns.

check("fastapi[standard]==0.165.0" in SRC,
      "the app image no longer pins fastapi 0.165.0, so the "
      "Starlette version this repro cites must be rechecked")


class PinnedRequest:
    """Starlette 1.10.0's body/json caching, in miniature."""

    def __init__(self, raw: bytes):
        self._raw = raw

    async def stream(self):
        yield self._raw

    async def body(self) -> bytes:
        if not hasattr(self, "_body"):
            chunks = []
            async for chunk in self.stream():
                chunks.append(chunk)
            self._body = b"".join(chunks)
        return self._body

    async def json(self):
        if not hasattr(self, "_json"):
            body = await self.body()
            self._json = json.loads(body)
        return self._json


async def _repro():
    original = {"source_job_id": "s1", "character_id": "c1",
                "model": "wan2.2"}
    composed = {"model": "wan2.2", "prompt": app.CLONE_PROMPT_TEMPLATE,
                "aspect_ratio": "9:16", "start_image_png_b64": "QUJD",
                "segments": 1, "lane": "production"}
    req = PinnedRequest(json.dumps(original).encode())
    first = await req.json()          # the endpoint's own parse
    req._body = json.dumps(composed).encode()   # the old substitution
    second = await req.json()         # the wrapper's parse
    return first, second, composed


first, second, composed = asyncio.run(_repro())
check(first == second,
      "the repro no longer shows the cache answering the second parse")
check(second != composed and "prompt" not in second,
      "the repro no longer shows the composed body being dropped")
check(second.get("model") == "wan2.2" and "lane" not in second,
      "the repro's surviving body is not the raw request")

# -- no body substitution remains anywhere ------------------------------

check(not re.search(r"request\._body\s*=", SRC),
      "a request._body assignment is back in app.py: a composed "
      "body handed over that way never reaches a creation core")

# -- the clone builder composes the fields the core must receive --------

builder = func_src("_editclone_clone_body")
check(builder, "_editclone_clone_body is missing")
check('"prompt": prompt' in builder
      and 'body.get("prompt") or CLONE_PROMPT_TEMPLATE' in builder,
      "the clone builder no longer composes the template prompt")
check('"start_image_png_b64": char_b64' in builder,
      "the clone builder no longer composes the character picture "
      "as the start image")
check('"segments": segments' in builder,
      "the clone builder no longer composes the segment count")
check('house_body["lane"] = "production"' in builder,
      "the clone builder no longer composes the production lane "
      "for Wan 2.2")

# -- the clone create passes the composed body to the core --------------

clone_create = func_src("create_editclone_clone")
check(clone_create, "create_editclone_clone is missing")
check("create_job_core(" in clone_create
      and "user, new_body, base_url(request), client_ip(request)"
      in clone_create,
      "the clone create no longer passes its composed body to "
      "create_job_core directly")
check("await create_job(" not in clone_create,
      "the clone create delegates to the create_job wrapper again")
check("bot_check_ok(new_body, client_ip(request), user)"
      in clone_create,
      "the clone create no longer runs the bot check against the "
      "composed body")
check(clone_create.index("new_body[key] = body[key]")
      < clone_create.index("create_job_core("),
      "the clone create no longer copies the challenge fields "
      "into the composed body before the core call")
check('created_job["editclone_source_id"]' in clone_create,
      "the clone create no longer stamps its source on the "
      "created job")

# -- the clone quote passes the composed body to the core ---------------

clone_quote = func_src("quote_editclone_clone")
check(clone_quote, "quote_editclone_clone is missing")
check("return quote_job_core(user, new_body)" in clone_quote,
      "the clone quote no longer passes its composed body to "
      "quote_job_core directly")
check("await quote_job(" not in clone_quote,
      "the clone quote delegates to the quote_job wrapper again")

# -- regenerate passes its composed body to the core ---------------------

regen = func_src("regenerate")
check(regen, "regenerate is missing")
check("create_job_core(" in regen
      and "user, new_body, base_url(request), client_ip(request)"
      in regen,
      "regenerate no longer passes its composed body to "
      "create_job_core directly")
check("await create_job(" not in regen,
      "regenerate delegates to the create_job wrapper again")
check("bot_check_ok(new_body, client_ip(request), user)" in regen,
      "regenerate no longer runs the bot check against the "
      "composed body")

# -- the retry branches pass their composed bodies to the cores ----------

retry = func_src("retry_job")
check(retry, "retry_job is missing")
for core, rate_key in (
        ("create_short_core", "shortcreate"),
        ("create_image_core", "imagecreate"),
        ("create_clip_core", "clipcreate"),
        ("create_template_core", "templatecreate")):
    check(f"{core}(" in retry and "user, new_body" in retry,
          f"retry_job no longer passes a composed body to {core}")
    check(f'_rate_limited("{rate_key}", user["id"]' in retry,
          f"retry_job no longer runs the {rate_key} pace check "
          "before a composed creation")
check("bot_check_ok(new_body, client_ip(request), user)" in retry,
      "retry_job no longer runs the bot check against its "
      "composed bodies")
for wrapper in ("create_short(", "create_image(", "create_clip(",
                "create_template("):
    check(f"await {wrapper}" not in retry,
          f"retry_job delegates to the {wrapper} wrapper again")
check("return await regenerate(job_id, request, mf_session)"
      in retry,
      "the retry fallthrough no longer rides regenerate")

# -- the extracted cores exist and the wrappers delegate to them ---------

for wrapper, core in (("create_short", "create_short_core"),
                      ("create_clip", "create_clip_core"),
                      ("create_template", "create_template_core"),
                      ("create_image", "create_image_core")):
    check(func_src(core), f"{core} is missing")
    check(f"return {core}(user, body, client_ip(request))"
          in func_src(wrapper),
          f"{wrapper} no longer delegates to {core} with the "
          "parsed request body")

if failures:
    print("FAIL check_clone_endpoint_body")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_clone_endpoint_body")
