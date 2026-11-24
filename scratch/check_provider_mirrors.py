"""Local checks for the 2026-11-17 provider mirror audit.

The SpicyAPI lesson: a status mirror written against a guessed
envelope can read a finished job as "still running" forever (it
waited for the word "success"; SpicyAPI's real word is
"succeeded", and the result rides data.output.assets[]). These
checks pin, for every provider mirror in platform/app.py:

- the provider's documented success words, failure words, and
  running words are the words the mirror actually branches on;
- the result is read from the provider's documented result
  location;
- any other 200 status word routes to _note_unparsed (counted,
  and the job fails clean past 30 strikes);
- the universal wall-clock poll bound exists in mirror_status
  and routes every job family to its clean fail path.

Behavioral checks exercise the module-level result reader
(_spicyapi_result_url) against payloads shaped like the real
envelope, including the edit-lane trap (a source URL under
data.input must never be taken for the result).

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_provider_mirrors.py
"""
import ast
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


# -- per-mirror vocabulary and result-location pins ------------------
# Each entry: mirror function -> tokens that must appear in its
# source (documented success / failure / running words, the
# unparsed route with its provider tag, the documented result
# location).
MIRROR_PINS = {
    "mirror_fal_status": [
        '"IN_QUEUE"', '"IN_PROGRESS"', '"COMPLETED"',
        '_note_unparsed(job, "fal"', "provider_response_url",
        'video.get("url")',
    ],
    "mirror_beatapi_status": [
        '"queued"', '"processing"', '"succeeded"', '"failed"',
        '_note_unparsed(job, "beatapi"', 'get("media")',
    ],
    "mirror_kie_status": [
        '"success"', '"fail"', '"waiting"', '"queuing"',
        '"generating"', '_note_unparsed(job, "kie"',
        '"resultJson"', '"resultUrls"',
    ],
    "mirror_cometapi_status": [
        '"completed"', '"failed"', '"in_progress"', '"queued"',
        '_note_unparsed(job, "cometapi"', 'video_url',
    ],
    "mirror_piapi_status": [
        '"succeeded"', '"complete"', '"rejected"', '"staged"',
        '"pending"', '_note_unparsed(job, "piapi"',
    ],
    "mirror_openrouter_status": [
        '"completed"', '"failed"', '"expired"', '"in_progress"',
        '_note_unparsed(job, "openrouter"', 'unsigned_urls',
    ],
    "mirror_novita_status": [
        '"TASK_STATUS_SUCCEED"', '"TASK_STATUS_QUEUED"',
        '"TASK_STATUS_RUNNING"', '_note_unparsed(job, "novita"',
        'video_url',
    ],
    "mirror_wavespeed_status": [
        '"completed"', '"failed"', '"created"', '"processing"',
        '_note_unparsed(job, "wavespeed"', 'outputs',
    ],
    "mirror_eachlabs_status": [
        '"success"', '"succeeded"', '"error"', '"starting"',
        '_note_unparsed(job, "eachlabs"', 'output',
    ],
    "mirror_runway_status": [
        '"SUCCEEDED"', '"FAILED"', '"THROTTLED"', '"PENDING"',
        '_note_unparsed(job, "runway"', 'output',
    ],
    "mirror_vidu_status": [
        '"success"', '"failed"', '"created"', '"queueing"',
        '_note_unparsed(job, "vidu"', 'creations',
    ],
    "mirror_minimax_status": [
        '"Success"', '"Fail"', '"Queueing"', '"Preparing"',
        '_note_unparsed(job, "minimax"', '_poll_retry_or_fail',
    ],
    "mirror_spicyapi_status": [
        '"succeeded"', '"success"', '"cancelled"', '"waiting"',
        '_note_unparsed(job, "spicyapi"', '"assets"',
    ],
    "mirror_hotapi_status": [
        '"succeeded"', '"failed"', '"processing"',
        '_note_unparsed(job, "hotapi"',
    ],
    "mirror_wiro_status": [
        '"task_postprocess_end"', '"task_cancel"', '"task_"',
        '_note_unparsed(job, "wiro"', '"outputs"', 'pexit',
    ],
    "mirror_provider_image": [
        '"succeeded"', "_spicyapi_result_url", '"tasklist"',
        '"task_postprocess_end"', 'get("media")', 'get("images")',
    ],
    "mirror_vidlab_status": [
        '"succeeded"', '"failed"', '"queued"', '"running"',
        '_note_unparsed(job, "vidlab"', 'get("outputs")',
        '"price_usd"',
    ],
}

for fname, tokens in MIRROR_PINS.items():
    src = func_src(fname)
    check(bool(src), f"{fname} not found in app.py")
    for token in tokens:
        check(token in src, f"{fname} is missing pinned token {token}")

# SpicyAPI video must read output.assets BEFORE the Kie-shaped
# resultJson fallback (the verified envelope order).
spicy = func_src("mirror_spicyapi_status")
check(0 <= spicy.find('"assets"') < spicy.find('"resultJson"'),
      "mirror_spicyapi_status must read output.assets before resultJson")

# The Wiro image branch must not settle off a loose payload scan.
img = func_src("mirror_provider_image")
check("_first_image_url(data)" not in img,
      "mirror_provider_image still loose-scans the Wiro payload")

# -- the universal safety net -----------------------------------------

check(app.PROVIDER_POLL_LIMIT_SECONDS == 7200,
      "provider poll limit drifted")
check(app.HOUSE_POLL_LIMIT_SECONDS == 7200, "house poll limit drifted")
check(app.IMAGE_POLL_LIMIT_SECONDS == 1800, "image poll limit drifted")
check(app.CLIP_POLL_LIMIT_SECONDS == 3900, "clip poll limit drifted")
check(app.TRAINING_POLL_LIMIT_SECONDS == 3600,
      "training poll limit drifted")

ms = func_src("mirror_status")
check("_fail_stale_job(job)" in ms,
      "mirror_status does not enforce the poll bound")
check("CLIP_POLL_LIMIT_SECONDS" in ms and "_clip_release_hold(job)" in ms,
      "mirror_status clip branch lost its stale-job release")

stale = func_src("_fail_stale_job")
for token in ("fail_fal_job", "_fail_provider_image",
              "_restore_house_image"):
    check(token in stale, f"_fail_stale_job does not route via {token}")

unparsed = func_src("_note_unparsed")
check("fail_fal_job" in unparsed and "> 30" in unparsed,
      "_note_unparsed lost its fail-safe bound")
check("unparsed_payload" in unparsed,
      "_note_unparsed no longer keeps the payload shape")

hist = func_src("mirror_image_status")
check("_restore_house_image(job)" in hist,
      "mirror_image_status does not share the house image restore")

# -- terminal records are inert in mirror_status (perf pin,
# Jack 2026-11-24): a done or errored job must never reach a
# sub-mirror, a provider call, or a store write from a read
# path. Every collaborator below raises if it is touched, so
# the only way through is the terminal guard at the top.
import time as _time  # noqa: E402


def _raiser(name):
    def _raise(*args, **kwargs):
        raise AssertionError(f"terminal mirror touched {name}")
    return _raise


class _WriteGuard(dict):
    writes = 0

    def __setitem__(self, key, value):
        _WriteGuard.writes += 1
        super().__setitem__(key, value)


_ms_ns = {
    "jobs": _WriteGuard(),
    "users": {},
    "meta": {},
    "dramas": {},
    "requests": _raiser("requests"),
    "_now": _time.time,
    "_worker_headers": _raiser("_worker_headers"),
    "WORKER_BASE": "https://worker.invalid",
    "mirror_drama_status": _raiser("mirror_drama_status"),
    "mirror_image_status": _raiser("mirror_image_status"),
    "mirror_provider_image": _raiser("mirror_provider_image"),
    "mirror_fal_status": _raiser("mirror_fal_status"),
    "mirror_beatapi_status": _raiser("mirror_beatapi_status"),
    "mirror_api_status": _raiser("mirror_api_status"),
    "_fail_stale_job": _raiser("_fail_stale_job"),
    "_clip_release_hold": _raiser("_clip_release_hold"),
    "_comfy_maybe_trigger": _raiser("_comfy_maybe_trigger"),
    "_hf_maybe_trigger": _raiser("_hf_maybe_trigger"),
    "_poll_limit_for": _raiser("_poll_limit_for"),
    "API_POLL_PROVIDERS": {"kie", "vidlab"},
    "CLIP_POLL_LIMIT_SECONDS": 3900,
    "TEMPLATE_POLL_LIMIT_SECONDS": 3900,
    "SHORT_POLL_LIMIT_SECONDS": 3900,
    "COMFY_QUEUED_LIMIT_SECONDS": 3900,
    "COMFY_RUNNING_LIMIT_SECONDS": 3900,
    "COMFY_DEAD_HEARTBEAT_SECONDS": 3900,
    "COMFY_DEAD_JOB_MIN_AGE_SECONDS": 0,
}
exec(func_src("mirror_status"), _ms_ns)
_mirror_status = _ms_ns["mirror_status"]
check('not in ("queued", "running")' in func_src("mirror_status"),
      "mirror_status lost its terminal guard")
for _provider, _kind in (
        ("clip", "clip_job"), ("hyperframes", "template"),
        ("shorts", "short"), ("drama", "drama"),
        ("kaggle-comfy", "generation"), ("fal", "generation"),
        ("beatapi", "generation"), ("kie", "generation"),
        (None, "generation"), (None, "image"),
        ("fal", "image")):
    for _status in ("done", "error"):
        _job = {"id": "t1", "user_id": "u1",
                "provider": _provider, "kind": _kind,
                "status": _status, "created_at": 1.0,
                "last_poll": 0.0, "image_model": "shelf-x",
                "worker_job_id": "w1"}
        _WriteGuard.writes = 0
        try:
            _out = _mirror_status(dict(_job))
            check(_out == _job and _WriteGuard.writes == 0,
                  f"terminal {_provider}/{_kind}/{_status} job "
                  f"was changed or written by mirror_status")
        except AssertionError as exc:
            check(False, f"terminal {_provider}/{_kind}/"
                         f"{_status}: {exc}")

train_poll = func_src("_refresh_character_training")
check("TRAINING_POLL_LIMIT_SECONDS" in train_poll,
      "character training poll has no time bound")
train_submit = func_src("train_character")
check("train_started_at" in train_submit,
      "train_character does not stamp the training start")

# -- behavioral: the SpicyAPI result reader against real shapes ------

edit_payload = {
    "state": "succeeded",
    "input": {"image_url": "https://example.com/SOURCE.png"},
    "output": {"assets": [{"url": "https://example.com/RESULT.png"}]},
}
check(app._spicyapi_result_url(edit_payload)
      == "https://example.com/RESULT.png",
      "spicyapi reader must take output.assets, never data.input")

kie_shaped = {
    "state": "succeeded",
    "resultJson": '{"resultUrls": ["https://example.com/KIE.png"]}',
}
check(app._spicyapi_result_url(kie_shaped)
      == "https://example.com/KIE.png",
      "spicyapi reader lost the resultJson fallback")

plain = {"state": "succeeded", "resultUrl": "https://example.com/P.png"}
check(app._spicyapi_result_url(plain) == "https://example.com/P.png",
      "spicyapi reader lost the resultUrl fallback")

empty = {"state": "succeeded", "input": {"image_url": "https://x/S.png"}}
check(app._spicyapi_result_url(empty) is None,
      "spicyapi reader must return None when only a source URL exists")

if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("ALL PROVIDER MIRROR CHECKS PASSED")
