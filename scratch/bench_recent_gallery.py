"""Timing fixture for the studio recent list and the gallery
feed (Jack, 2026-11-24: both still slow after the perf pass).

Execs the REAL list_jobs / mirror_status / gallery sources
from platform/app.py against a seeded store and times the
paths, counting store gets/writes, mirror invocations, and
provider HTTP calls (a recording stub: any call is a finding,
terminal jobs must never make one).

Run before and after a change to get the honest pair:

  ~/workspace/sinclip/modal-venv/bin/python \
      scratch/bench_recent_gallery.py
"""
import ast
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app as app_module  # noqa: E402  (constants + module helpers)

SRC = Path(__import__("os").environ.get(
    "MF_BENCH_SRC", ROOT / "platform" / "app.py")).read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


class CountingDict(dict):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.gets = 0
        self.writes = 0

    def get(self, key, default=None):
        self.gets += 1
        return super().get(key, default)

    def __setitem__(self, key, value):
        self.writes += 1
        super().__setitem__(key, value)


class RecordingRequests:
    def __init__(self):
        self.calls = []

    def _record(self, method, url, **kw):
        self.calls.append((method, url))

        class Resp:
            status_code = 599
            content = b""

            def json(self):
                return {}
        return Resp()

    def get(self, url, **kw):
        return self._record("GET", url, **kw)

    def post(self, url, **kw):
        return self._record("POST", url, **kw)


def base_ns():
    ns = {}
    for name in (
            "pricing", "OUTPUT_CLASSIFIER_SINCE", "API_POLL_PROVIDERS",
            "URL_RESULT_PROVIDERS", "CLIP_POLL_LIMIT_SECONDS",
            "TEMPLATE_POLL_LIMIT_SECONDS", "SHORT_POLL_LIMIT_SECONDS",
            "DRAMA_POLL_LIMIT_SECONDS", "PROVIDER_POLL_LIMIT_SECONDS",
            "HOUSE_POLL_LIMIT_SECONDS", "IMAGE_POLL_LIMIT_SECONDS",
            "COMFY_QUEUED_LIMIT_SECONDS", "COMFY_RUNNING_LIMIT_SECONDS",
            "COMFY_DEAD_HEARTBEAT_SECONDS", "COMFY_DEAD_JOB_MIN_AGE_SECONDS",
            "WORKER_BASE", "DEFAULT_MODEL", "job_is_nsfw",
            "prompt_is_nsfw", "job_is_uncensored", "_now"):
        if hasattr(app_module, name):
            ns[name] = getattr(app_module, name)
    ns.setdefault("URL_RESULT_PROVIDERS", set())
    ns.setdefault("DEFAULT_MODEL", "wan2.2")
    ns["_now"] = time.time
    return ns


def exec_all(ns, names):
    for name in names:
        src = func_src(name)
        if src:
            exec(src, ns)
    return ns


MIRROR_FUNCS = [
    "_poll_gate", "mirror_fal_status", "mirror_beatapi_status",
    "mirror_provider_image", "mirror_image_status",
    "mirror_api_status", "mirror_drama_status", "mirror_status",
] + [n for n in (
    "mirror_kie_status", "mirror_vidlab_status",
    "mirror_cometapi_status", "mirror_piapi_status",
    "mirror_openrouter_status", "mirror_novita_status",
    "mirror_wavespeed_status", "mirror_eachlabs_status",
    "mirror_runway_status", "mirror_vidu_status",
    "mirror_minimax_status", "mirror_spicyapi_status",
    "mirror_hotapi_status", "mirror_wiro_status") if func_src(n)]


def make_job(jid, user, provider, kind, status, created, **extra):
    job = {
        "id": jid, "user_id": user, "provider": provider,
        "kind": kind, "status": status, "created_at": created,
        "lane": "draft", "model": "wan2.2", "prompt": "a red barn",
        "user_prompt": "a red barn", "last_poll": time.time(),
        "worker_job_id": "w-" + jid,
        "provider_status_url": "https://provider.invalid/status",
        "provider_response_url": "https://provider.invalid/result",
        "provider_task_id": "t-" + jid,
        "fal_status_url": "https://provider.invalid/s",
        "fal_response_url": "https://provider.invalid/r",
    }
    job.update(extra)
    return job


def seed_list_store():
    jobs = CountingDict()
    t = 1_700_000_000.0
    n = [0]

    def add(user, provider, kind, status, count, **extra):
        for _ in range(count):
            n[0] += 1
            jid = f"j{n[0]:04d}"
            jobs[jid] = make_job(
                jid, user, provider, kind, status,
                t + n[0] * 10, **extra)

    # The owner's history: 600 terminal records.
    add("u1", "fal", "generation", "done", 250)
    add("u1", "kie", "generation", "done", 100)
    add("u1", "kaggle-comfy", "generation", "done", 80)
    add("u1", None, "generation", "done", 60)
    add("u1", None, "image", "done", 20)
    add("u1", "fal", "image", "done", 20, image_model="shelf-x")
    add("u1", "clip", "clip_job", "done", 15)
    add("u1", "shorts", "short", "done", 10)
    add("u1", "hyperframes", "template", "done", 10)
    add("u1", "drama", "drama", "done", 10)
    add("u1", "fal", "generation", "error", 15)
    add("u1", "kie", "generation", "error", 10)
    # Six active records with a fresh poll stamp.
    add("u1", "fal", "generation", "running", 2)
    add("u1", "kie", "generation", "queued", 1)
    add("u1", "kaggle-comfy", "generation", "queued", 1)
    add("u1", None, "generation", "running", 1)
    add("u1", None, "image", "running", 1)
    # Children never list.
    add("u1", "fal", "generation", "done", 2, short_parent="j0001")
    add("u1", "fal", "generation", "done", 1, drama_parent="j0002")
    # Everyone else's records share the same store.
    add("u2", "fal", "generation", "done", 400)
    # Active records are young and freshly polled (a stale
    # active would be failed by the poll bound, by design).
    now = time.time()
    for job in jobs.values():
        if job["status"] in ("queued", "running"):
            job["created_at"] = now - 30
            job["last_poll"] = now
    return jobs


def bench_list():
    ns = base_ns()
    jobs = seed_list_store()
    requests_stub = RecordingRequests()
    triggers = {"count": 0}
    ns.update({
        "jobs": jobs,
        "users": {"id:u1": {"id": "u1", "plan": "creator"}},
        "meta": CountingDict(),
        "dramas": CountingDict(),
        "requests": requests_stub,
        "require_user": lambda request, mf_session: {"id": "u1"},
        "Cookie": lambda default="": default,
        "Request": object,
        "_worker_headers": lambda: {},
        "_comfy_maybe_trigger": lambda: triggers.__setitem__(
            "count", triggers["count"] + 1),
        "_hf_maybe_trigger": lambda: None,
        "_fail_stale_job": lambda job: job.__setitem__("status", "error"),
        "_clip_release_hold": lambda job: None,
        "_restore_house_image": lambda job: None,
        "save_user": lambda user: None,
        "usage": lambda user: {},
        "included_cost_used": lambda user: 0.0,
        "_poll_limit_for": lambda job: 7200,
        "MODEL_LABELS": {},
        "lane_audio": lambda model: {"native_speech": False},
    })
    exec_all(ns, MIRROR_FUNCS)
    exec_all(ns, ["is_paid", "gallery_visible", "public_job",
                  "list_jobs"])
    mirror_calls = {"count": 0}
    real_mirror = ns["mirror_status"]

    def counting_mirror(job):
        mirror_calls["count"] += 1
        return real_mirror(job)
    ns["mirror_status"] = counting_mirror

    class Req:
        query_params = {"recent": "1"}

    times = []
    result = None
    for _ in range(5):
        jobs.gets = 0
        jobs.writes = 0
        mirror_calls["count"] = 0
        start = time.perf_counter()
        result = ns["list_jobs"](Req(), "")
        times.append(time.perf_counter() - start)
    print(f"[list recent] mean {sum(times) / len(times) * 1000:.1f} ms "
          f"runs {[round(t * 1000) for t in times]}")
    print(f"[list recent] payload jobs {len(result['jobs'])} "
          f"totals v={result.get('total_videos')} "
          f"i={result.get('total_images')}")
    print(f"[list recent] last run: store gets {jobs.gets}, "
          f"store writes {jobs.writes}, "
          f"mirror calls {mirror_calls['count']}, "
          f"provider HTTP calls {len(requests_stub.calls)}, "
          f"kernel triggers {triggers['count']}")
    return result


def bench_gallery():
    ns = base_ns()
    tmp = Path(tempfile.mkdtemp(prefix="mf-bench-"))
    jobs = CountingDict()
    t = 1_700_000_000.0
    # 74 listable jobs (files on disk), mixed kinds.
    for i in range(74):
        jid = f"g{i:03d}"
        kind = "image" if i >= 50 else "generation"
        jobs[jid] = make_job(
            jid, "u-paid", "fal", kind, "done", t + i * 10,
            output_nsfw_score=0.01, output_flagged=False)
        suffix = ".png" if kind == "image" else ".mp4"
        (tmp / f"{jid}{suffix}").write_bytes(b"x" * 64)
    # 100 finished but not listable (flagged output), 126 others.
    for i in range(100):
        jid = f"x{i:03d}"
        jobs[jid] = make_job(
            jid, "u-paid", "fal", "generation", "done",
            t + 1000 + i * 10, output_nsfw_score=0.99,
            output_flagged=True)
    for i in range(126):
        jid = f"y{i:03d}"
        jobs[jid] = make_job(
            jid, "u2", "fal", "generation",
            "done" if i % 3 else "error", t + 2000 + i * 10,
            output_nsfw_score=0.01, output_flagged=False)
    users = {
        "id:u-paid": {"id": "u-paid", "plan": "creator",
                      "handle": "owner"},
        "id:u2": {"id": "u2", "plan": "creator"},
    }
    ns.update({
        "jobs": jobs,
        "users": users,
        "meta": CountingDict(),
        "requests": RecordingRequests(),
        "FILES_DIR": tmp,
        "data_volume": type("V", (), {"reload": lambda self: None})(),
        "time": time,
        "threading": __import__("threading"),
        "HOUSE_IMAGE_LABEL": "House image",
        "MODEL_LABELS": {},
        "_gallery_cache": {"at": 0.0, "items": None,
                           "building_since": 0.0},
        "GALLERY_CACHE_SECONDS": 30,
    })
    exec_all(ns, ["is_paid", "display_handle", "gallery_visible",
                  "gallery_file_ready", "public_gallery_item",
                  "_gallery_build", "_gallery_refresh",
                  "_gallery_kick", "_gallery_feed_finished",
                  "gallery_list"])
    gl = ns["gallery_list"]
    cache = ns["_gallery_cache"]

    start = time.perf_counter()
    cold = gl()
    cold_ms = (time.perf_counter() - start) * 1000
    print(f"[gallery] cold build {cold_ms:.1f} ms, "
          f"items {len(cold['items'])}")

    start = time.perf_counter()
    gl()
    print(f"[gallery] warm serve "
          f"{(time.perf_counter() - start) * 1000:.1f} ms")

    # A visitor arriving after the cache window lapses.
    cache["at"] = time.time() - 31
    start = time.perf_counter()
    stale = gl()
    stale_ms = (time.perf_counter() - start) * 1000
    print(f"[gallery] post-lapse serve {stale_ms:.1f} ms, "
          f"items {len(stale['items'])}")
    if "_gallery_kick" in ns:
        # Background refresh: report how long until it lands.
        start = time.perf_counter()
        while time.time() - cache["at"] > 31 \
                and time.perf_counter() - start < 30:
            time.sleep(0.05)
        print(f"[gallery] background refresh landed after "
              f"{(time.perf_counter() - start) * 1000:.1f} ms")
        # A job that finishes now enters without a rebuild wait.
        jid = "g-new"
        jobs[jid] = make_job(
            jid, "u-paid", "fal", "generation", "done",
            t + 99999, output_nsfw_score=0.01,
            output_flagged=False)
        (tmp / f"{jid}.mp4").write_bytes(b"x" * 64)
        if "_gallery_feed_finished" in ns:
            ns["_gallery_feed_finished"](jobs[jid])
            served = gl()
            found = any(i["id"] == jid for i in served["items"])
            print(f"[gallery] finished job inserted into feed: "
                  f"{found}")


if __name__ == "__main__":
    bench_list()
    bench_gallery()
