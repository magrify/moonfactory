"""Behavioral checks for the FrameHush result resolver,
pinned against the RAW envelopes captured live on 2026-11-26
while diagnosing proof job f1779ebed7c55c03 (sanitized
fixtures below; no key material, task id and access token
redacted).

What the capture proved, and what these checks pin:

- The task COMPLETED on FrameHush's side ~3 minutes after
  submit. The GET /v1/videos/{id} poll answer is a FLAT
  object — status and progress at top level, no data wrapper,
  and NO result URL, NO charged_price, NO duration:
  {"completed_at": ..., "created_at": ..., "id": "task_...",
   "model": "seedance-2-5", "object": "video",
   "progress": 100, "status": "completed"}
- The shipped resolver's fallback, GET /v1/tasks/{id}/
  artifacts/video/content/signed-url, answers 404
  ("Invalid URL") — the endpoint does not exist. Every poll
  therefore read "completed but no URL", burned the
  30-strike poll-error budget (~18 minutes), and failed a
  finished job as MODEL_UNAVAILABLE with a full refund.
- The real result location is the artifacts list:
  GET /v1/tasks/{id}/artifacts -> 200
  {"artifacts": [{"key": "video", "type": "video",
    "content_url": "https://www.framehush.com/v1/tasks/
    {id}/artifacts/video/content?access=..."}],
   "legacy_content_url": "https://..."}
  and GET .../artifacts/video/content?download=1 streams
  the MP4 itself (200, video/mp4).

No Modal calls, no network, no generation: the resolver's
source is exec'd against a stubbed requests module, the
same pattern check_provider_mirrors uses for mirror_status.

Run:  modal-venv python scratch/check_framehush_poll_envelope.py
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


# -- sanitized fixtures, shaped exactly like the capture --------------

POLL_COMPLETED = {
    "completed_at": 1795755439,
    "created_at": 1795755285,
    "id": "task_REDACTED",
    "model": "seedance-2-5",
    "object": "video",
    "progress": 100,
    "status": "completed",
}

CONTENT_URL = ("https://www.framehush.com/v1/tasks/task_REDACTED"
               "/artifacts/video/content?access=REDACTED")
LEGACY_URL = ("https://www.framehush.com/v1/tasks/task_REDACTED"
              "/artifacts/video/content")

ARTIFACTS_LIST = {
    "artifacts": [
        {"key": "video", "type": "video", "content_url": CONTENT_URL},
    ],
    "legacy_content_url": LEGACY_URL,
}


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Requests:
    """Records every GET and answers from a per-test handler."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        return self.handler(url)


def load_resolver(handler):
    ns = {
        "requests": _Requests(handler),
        "_api_headers": lambda provider: {},
        "FRAMEHUSH_BASE": "https://www.framehush.com",
    }
    exec(func_src("_framehush_result_url"), ns)
    return ns["_framehush_result_url"], ns["requests"]


JOB = {"id": "job1", "provider_task_id": "task_REDACTED"}
ARTIFACTS_URL = ("https://www.framehush.com/v1/tasks/"
                 "task_REDACTED/artifacts")

# 1. The captured shape: completed poll answer with no URL,
#    artifacts list carries the file -> content_url, fetched
#    from the artifacts LIST endpoint (never signed-url).
resolver, stub = load_resolver(lambda url: _Resp(200, ARTIFACTS_LIST))
url = resolver(dict(JOB), dict(POLL_COMPLETED))
check(url == CONTENT_URL,
      "resolver did not return the artifacts content_url for "
      "the captured completed envelope")
check(stub.calls == [ARTIFACTS_URL],
      f"resolver called {stub.calls}, not the artifacts list")

# 2. A URL riding the poll answer is still read first, with
#    no HTTP call at all.
resolver, stub = load_resolver(
    lambda url: (_ for _ in ()).throw(AssertionError("http called")))
url = resolver(dict(JOB), {"video_url": "https://x/v.mp4"})
check(url == "https://x/v.mp4" and stub.calls == [],
      "resolver lost the poll-answer URL fast path")

# 3. Empty artifacts list -> the legacy content URL.
resolver, stub = load_resolver(lambda url: _Resp(
    200, {"artifacts": [], "legacy_content_url": LEGACY_URL}))
url = resolver(dict(JOB), dict(POLL_COMPLETED))
check(url == LEGACY_URL,
      "resolver did not fall back to legacy_content_url")

# 4. A non-video artifact ahead of the video one is skipped.
mixed = {"artifacts": [
    {"key": "cover", "type": "image",
     "content_url": "https://x/cover.jpg"},
    {"key": "video", "type": "video", "content_url": CONTENT_URL},
], "legacy_content_url": LEGACY_URL}
resolver, stub = load_resolver(lambda url: _Resp(200, mixed))
url = resolver(dict(JOB), dict(POLL_COMPLETED))
check(url == CONTENT_URL,
      "resolver took a non-video artifact's content_url")

# 5. Artifacts endpoint erroring -> None (the mirror's
#    retry-or-fail budget governs), never an exception.
resolver, stub = load_resolver(lambda url: _Resp(404, {"error": {}}))
url = resolver(dict(JOB), dict(POLL_COMPLETED))
check(url is None,
      "resolver must return None when the artifacts list fails")

# 6. The invented endpoint stays gone, everywhere in app.py.
check("content/signed-url" not in SRC,
      "the invented artifacts signed-url endpoint is back in app.py")
resolver_src = func_src("_framehush_result_url")
for token in ('v1/tasks/', '/artifacts"',
              '"content_url"', '"legacy_content_url"'):
    check(token in resolver_src,
          f"_framehush_result_url missing pinned token {token}")

# 7. The mirror still reads the poll status at TOP level (the
#    captured envelope is flat, not data-wrapped) and routes
#    unknown words to the unparsed fail-safe.
mirror = func_src("mirror_framehush_status")
check('data.get("status")' in mirror,
      "mirror_framehush_status no longer reads top-level status")
check('(data.get("data")' not in mirror,
      "mirror_framehush_status grew a data-wrapper read the "
      "captured envelope does not have")

# -- the MAGI-2 lane rides the identical fix (owner, 2026-11-26:
#    "just fix how you had to fix the other one") ------------------
# magi2 dispatches by provider, not by model, through the one
# shared FrameHush client: its model id comes from the shared
# FRAMEHUSH_VIDEO_MODELS map, its mirror is mirror_framehush_
# status, and its result resolution is the resolver exercised
# above. These pins keep the magi2 path from ever forking off
# the fixed envelope handling.

import app as _app  # noqa: E402

check(_app.MODEL_PROVIDER.get("magi2") == "framehush",
      "magi2 no longer routes to the shared framehush client")
check('"magi2": "magi-2-1080P"' in SRC,
      "FRAMEHUSH_VIDEO_MODELS lost the magi2 model id")
submit_src = func_src("submit_framehush_job")
check('FRAMEHUSH_VIDEO_MODELS[job["model"]]' in submit_src,
      "submit_framehush_job no longer reads the per-lane model "
      "id from the shared map (a lane fork is growing)")

POLL_COMPLETED_MAGI = dict(POLL_COMPLETED, model="magi-2-1080P")
resolver, stub = load_resolver(lambda url: _Resp(200, ARTIFACTS_LIST))
url = resolver(dict(JOB), dict(POLL_COMPLETED_MAGI))
check(url == CONTENT_URL and stub.calls == [ARTIFACTS_URL],
      "a magi-2-1080P completed envelope does not resolve "
      "through the artifacts list like seedance's does")

# Settlement fallback, shared by both lanes: the mirror passes
# the reported charged_price through as cost_cents (None when
# the envelope reports none, as the captured one does), and
# settle_fal_job only overrides the seeded-rate cost when a
# provider charge is actually on the job.
check("cost_cents=cost" in mirror,
      "mirror_framehush_status no longer passes its settled "
      "cost (or None) to _poll_done")
settle_src = func_src("settle_fal_job")
check('if job.get("provider_cost_cents") is not None:' in settle_src
      and "per_second_lane_cost_cents" in settle_src,
      "settle_fal_job lost the seeded-rate fallback for jobs "
      "whose provider reported no charge")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_framehush_poll_envelope")
