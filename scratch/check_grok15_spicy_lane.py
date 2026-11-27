#!/usr/bin/env python3
"""Grok Imagine 1.5 spicy lane pins (2026-11-27).

The lane sells Grok Imagine Video 1.5 in the maker's own
less-restrictive mode: UnificAlly's grok.com build
(xai/grok-imagine-1.5-fast-video), whose own API reference
documents the video_preset enum (custom / spicy / fun /
normal, spicy described as "Extremely spicy/NSFW") and
whose model page states NSFW output comes via the presets
on this build only. The preset rides in the submit input
as video_preset="spicy" on every job. Unlike the Kie
grok_unc lane, the UnificAlly schema documents NO downgrade
of the preset with image inputs, so the lane takes a start
frame and up to 7 reference images like the moderated 1.5
lane; it takes no end frame (the fast build's schema has
no end-frame field), refused rather than dropped.

Pinned facts:
- the lane id, its Uncensored-group membership, the plain
  model-name label, and the paid_only flag;
- the UnificAlly route and its API key env name;
- the published rates (4.0 / 7.0 / 12.5 cents per second
  at 480p / 720p / 1080p) and the standard sell math: the
  shelf basis (rate + the frozen 0.1c post figure) times
  1.25 for customers, raw cost (rate + the 0.01c measured
  post figure) for the owner; Higgsfield sells no
  uncensored Grok shelf, so no comparison figure exists;
- the documented capability shape: any whole second from
  1 to 30, the three resolutions, reference caps (7, 0, 0),
  image-references-only membership, and NOT text-only;
- the preset carried in the UnificAlly submit payload in
  text, image and reference modes alike, with the start
  frame and reference images riding as public URLs and the
  aspect ratio sent only where the build uses it;
- job validation: an end frame is refused, reference
  videos and audios are refused, and a start image or
  reference images are accepted (no silent downgrade is
  documented on this route, so nothing is refused on
  downgrade grounds);
- the submit dispatch reaches UnificAlly for this lane,
  and the mirror dispatch polls UnificAlly tasks;
- the companion IMAGE lane on the same route: Grok Imagine
  Image with the supplier's documented enable_nsfw flag
  sent on every job, flagged uncensored in its pricing
  entry, flat $0.01 per image at the single 1K tier in the
  channel's five documented shapes, its submit payload and
  its mirror settle driven for real like the video lane's.
"""
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "platform"))

failures = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


import pricing  # noqa: E402

src = (ROOT / "platform" / "app.py").read_text()

# --- pricing facts -------------------------------------------------
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND["grok15_unc"]
check("grok15_unc rates are UnificAlly's published 4.0/7.0/12.5",
      rates == {"480p": 4.0, "720p": 7.0, "1080p": 12.5})
check("grok15_unc sells exactly 480p/720p/1080p",
      pricing.MODEL_RESOLUTIONS["grok15_unc"]
      == ("480p", "720p", "1080p"))

for res, sell, cost in (("480p", 5.125, 4.01),
                        ("720p", 8.875, 7.01),
                        ("1080p", 15.75, 12.51)):
    pic = pricing.frontier_lane("grok15_unc", res)
    check(f"grok15_unc {res} customer sell is shelf x1.25",
          pic["sell_cents_per_second"] == sell)
    check(f"grok15_unc {res} has no Higgsfield figure",
          pic["higgsfield_effective_cents_per_second"] is None)
    own = pricing.frontier_lane("grok15_unc", res, owner=True)
    check(f"grok15_unc {res} owner price is raw cost",
          own["sell_cents_per_second"] == cost)

check("10s 480p customer debit rounds up to 52c",
      pricing.per_second_balance_debit_cents(
          "grok15_unc", "480p", 10) == 52)
check("10s 720p customer debit rounds up to 89c",
      pricing.per_second_balance_debit_cents(
          "grok15_unc", "720p", 10) == 89)
check("10s 1080p customer debit rounds up to 158c",
      pricing.per_second_balance_debit_cents(
          "grok15_unc", "1080p", 10) == 158)
check("10s 480p owner debit is raw cost rounded up (41c)",
      pricing.per_second_balance_debit_cents(
          "grok15_unc", "480p", 10, owner=True) == 41)

# --- app.py static facts -------------------------------------------
check("lane registered with the plain model name, paid only",
      '{"id": "grok15_unc", "label": "Grok Imagine Video 1.5", '
      '"paid_only": True}' in src)
check("lane is in the per-second model list",
      '"grok_unc", "grok15_unc",' in src)
check("lane is in the uncensored group set",
      '"grok15_unc",' in src
      and "UNCENSORED_VIDEO_MODELS" in src)
check("lane routes to UnificAlly",
      '"grok15_unc": "unifically"' in src)
check("UnificAlly key env is named",
      '"unifically": "UNIFICALLY_API_KEY"' in src)
check("UnificAlly is a URL-result provider",
      '"framehush", "unifically",' in src)
check("UnificAlly base URL is the documented one",
      'UNIFICALLY_BASE = "https://api.unifically.com"' in src)
check("lane is NOT text-only (no documented downgrade)",
      '"grok15_unc",' not in src.split("TEXT_ONLY_MODELS = (")[1]
      .split(")")[0])
check("lane takes image references only",
      '"grok", "grok15", "grok15_unc", "happyhorse",' in src)
check("duration bounds are the schema's 1-30",
      '"grok15_unc": (1, 30),' in src)
check("reference caps are (7, 0, 0)",
      '"grok15_unc": (7, 0, 0),' in src)
check("lane declares native speech and audio",
      '"grok15_unc": {"native_speech": True, "native_audio": True,'
      in src)
check("studio picker offers the three resolutions",
      "grok15_unc: { durations: LENGTHS, resolutions: "
      "['480p', '720p', '1080p'] }," in src)
check("submit dispatch reaches UnificAlly",
      'elif provider == "unifically":\n'
      '            submit_unifically_job(job, body, params, prompt)'
      in src)
check("mirror dispatch polls UnificAlly",
      'if provider == "unifically":\n'
      '            return mirror_unifically_status(job)' in src)
check("an end frame is refused in validation",
      'if model == "grok15_unc" and end_image:' in src)

# --- the submit payload and the mirror, driven for real ------------
# The platform's provider functions live inside the app builder,
# so the pin extracts their source from the AST and exercises it
# in a namespace of fakes, the house pattern (check_audio_kaggle).
import ast

PTREE = ast.parse(src)


def fn_source(name):
    for node in ast.walk(PTREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found")


jobs = {}
captured = {}
settled = {}


def _fake_submit_post(job, url, headers, payload):
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = payload
    return {"task_id": "task-pin-1", "status": "processing",
            "model": "xai/grok-imagine-1.5-fast-video", "cost": 0.0}


def _boom(job, msg):
    raise RuntimeError(f"unexpected submit failure: {msg}")


# The 2026-11-27 catalog pass generalized the submit
# around a per-model map; the pin extracts it too and the
# driven Grok payloads below are byte-for-byte the ones
# this pin has always asserted.
_UA_MAP = None
for _node in ast.walk(PTREE):
    if isinstance(_node, ast.Assign) and any(
            isinstance(t, ast.Name)
            and t.id == "UNIFICALLY_VIDEO_MODELS"
            for t in _node.targets):
        _UA_MAP = ast.literal_eval(_node.value)
check("the UnificAlly map carries the grok lane",
      (_UA_MAP or {}).get("grok15_unc")
      == ("xai/grok-imagine-1.5-fast-video", "grok"))

submit_ns = {
    "UNIFICALLY_VIDEO_MODELS": _UA_MAP,
    "UNIFICALLY_BASE": "https://api.unifically.com",
    "jobs": jobs,
    "_submit_post": _fake_submit_post,
    "_submit_failed": _boom,
    "_api_headers": lambda provider: {"Authorization": "Bearer pin"},
    "_beatapi_media_url": lambda ref, kind: (
        "https://media.example/" + kind + ".png"),
}
exec(fn_source("submit_unifically_job"), submit_ns)
submit = submit_ns["submit_unifically_job"]

job = {"id": "pin-job", "model": "grok15_unc",
       "user_id": "pin-user", "status": "queued"}
params = {"duration": 10, "resolution": "720p",
          "aspect_ratio": "16:9", "mode": "text",
          "start_image": None, "end_image": None,
          "reference_image_urls": [], "reference_video_urls": [],
          "reference_audio_urls": []}
submit(job, {}, dict(params), "a lighthouse")
check("text submit posts to the UnificAlly tasks endpoint",
      captured["url"] == "https://api.unifically.com/v1/tasks")
payload = captured["json"]
check("text submit names the fast 1.5 build",
      payload["model"] == "xai/grok-imagine-1.5-fast-video")
check("text submit carries the spicy preset",
      payload["input"]["video_preset"] == "spicy")
check("text submit keeps the nsfw check off",
      payload["input"]["nsfw_check"] is False)
check("text submit carries duration, resolution, aspect",
      payload["input"]["duration"] == 10
      and payload["input"]["resolution"] == "720p"
      and payload["input"]["aspect_ratio"] == "16:9")
check("text submit records the provider task id",
      job["provider_task_id"] == "task-pin-1")

job2 = {"id": "pin-job-2", "model": "grok15_unc",
        "user_id": "pin-user", "status": "queued"}
p2 = dict(params)
p2["mode"] = "image"
p2["start_image"] = "data:image/png;base64,AAAA"
submit(job2, {}, p2, "animate her")
inp2 = captured["json"]["input"]
check("image submit keeps the spicy preset (no downgrade "
      "documented on this route)",
      inp2["video_preset"] == "spicy")
check("image submit rides the start frame as a URL",
      inp2["start_image_url"]
      == "https://media.example/first-frame.png")
check("image submit sends no aspect ratio",
      "aspect_ratio" not in inp2)

job3 = {"id": "pin-job-3", "model": "grok15_unc",
        "user_id": "pin-user", "status": "queued"}
p3 = dict(params)
p3["mode"] = "reference"
p3["reference_image_urls"] = ["data:image/png;base64,BBBB"]
submit(job3, {}, p3, "her, at the beach")
inp3 = captured["json"]["input"]
check("reference submit keeps the spicy preset",
      inp3["video_preset"] == "spicy")
check("reference submit rides the references as URLs",
      inp3["image_urls"]
      == ["https://media.example/reference-image.png"])


class _TaskResp:
    status_code = 200

    def json(self):
        return {"task_id": "task-pin-1", "status": "completed",
                "output": {"video_url": "https://cdn.example/out.mp4"},
                "cost": 0.7}


class _FakeRequests:
    @staticmethod
    def get(url, headers=None, timeout=None):
        captured["poll_url"] = url
        return _TaskResp()


mirror_ns = {
    "UNIFICALLY_BASE": "https://api.unifically.com",
    "jobs": jobs,
    "requests": _FakeRequests,
    "_api_headers": lambda provider: {"Authorization": "Bearer pin"},
    "_poll_gate": lambda job: True,
    "_poll_done": lambda job, url, cost_cents=None, duration=None:
        settled.update(url=url, cost_cents=cost_cents),
    "_poll_fail": _boom,
    "_poll_retry_or_fail": lambda job: settled.update(retry=True),
    "_note_unparsed": lambda job, provider, status, data:
        settled.update(unparsed=status),
}
exec(fn_source("mirror_unifically_status"), mirror_ns)
mjob = {"id": "pin-job", "provider_task_id": "task-pin-1",
        "status": "running"}
mirror_ns["mirror_unifically_status"](mjob)
check("mirror polls the task endpoint",
      captured["poll_url"]
      == "https://api.unifically.com/v1/tasks/task-pin-1")
check("mirror settles a completed task with its video URL",
      settled.get("url") == "https://cdn.example/out.mp4")
check("mirror settles at the supplier's own USD cost in cents",
      settled.get("cost_cents") == 70.0)

# --- the spicy IMAGE lane on the same route -------------------------
ilane = pricing.IMAGE_LANES["grok_imagine_unc"]
check("image lane rides UnificAlly's Grok Imagine 1.0 Image",
      ilane["provider"] == "unifically"
      and ilane["endpoint"] == "xai/grok-imagine-1.0-image")
check("image lane carries the uncensored flag",
      ilane.get("uncensored") is True)
check("image lane is flat $0.01 at the 1K tier",
      ilane["raw_cents"]["1K"] == 1.0)
check("image lane sells exactly the channel's five shapes",
      set(ilane["raw_cents"].keys())
      == {"1K", "1K-16:9", "1K-9:16", "1K-3:2", "1K-2:3"})
check("image customer charge is raw x1.25 rounded up (2c)",
      pricing.image_lane_charge_cents("grok_imagine_unc", "1K")
      == 2)
check("image owner charge is the raw cent",
      pricing.image_lane_charge_cents(
          "grok_imagine_unc", "1K", owner=True) == 1)
# Generalized 2026-11-27: the branch sends enable_nsfw
# from the lane's uncensored flag (on for this lane); the
# driven submit below asserts the actual payload.
check("image submit branch sends enable_nsfw on every job",
      'elif provider == "unifically":' in src
      and '"enable_nsfw"] = bool(' in src)
check("image mirror branch polls UnificAlly tasks",
      'elif provider == "unifically":\n'
      '                resp = requests.get(' in src)

img_captured = {}
img_settled = {}


def _fake_img_submit_post(job, url, headers, payload):
    img_captured["url"] = url
    img_captured["json"] = payload
    return {"task_id": "img-pin-1", "status": "processing"}


img_ns = {
    "pricing": pricing,
    "UNIFICALLY_BASE": "https://api.unifically.com",
    "jobs": jobs,
    "_submit_post": _fake_img_submit_post,
    "_api_headers": lambda provider: {"Authorization": "Bearer pin"},
}
exec(fn_source("_submit_provider_image"), img_ns)
ijob = {"id": "pin-img", "image_model": "grok_imagine_unc",
        "image_size": "1K-9:16", "status": "queued"}
img_ns["_submit_provider_image"](ijob, "a portrait")
check("image submit posts to the UnificAlly tasks endpoint",
      img_captured["url"] == "https://api.unifically.com/v1/tasks")
check("image submit names the 1.0 image model",
      img_captured["json"]["model"] == "xai/grok-imagine-1.0-image")
check("image submit carries enable_nsfw and no nsfw check",
      img_captured["json"]["input"]["enable_nsfw"] is True
      and img_captured["json"]["input"]["nsfw_check"] is False)
check("image submit carries the picked shape",
      img_captured["json"]["input"]["aspect_ratio"] == "9:16")
check("image submit records the provider task id",
      ijob["provider_task_id"] == "img-pin-1")


class _ImgTaskResp:
    status_code = 200

    def json(self):
        return {"task_id": "img-pin-1", "status": "completed",
                "output": {"image_url": "https://cdn.example/i.png"},
                "cost": 0.01}


class _FakeImgRequests:
    @staticmethod
    def get(url, headers=None, timeout=None):
        img_captured["poll_url"] = url
        return _ImgTaskResp()


imirror_ns = {
    "UNIFICALLY_BASE": "https://api.unifically.com",
    "jobs": jobs,
    "requests": _FakeImgRequests,
    "_now": lambda: 1000.0,
    "_api_headers": lambda provider: {"Authorization": "Bearer pin"},
    "_settle_provider_image": lambda job, url, headers=None:
        img_settled.update(url=url),
    "_fail_provider_image": lambda job, msg:
        img_settled.update(failed=msg),
}
exec(fn_source("mirror_provider_image"), imirror_ns)
mijob = {"id": "pin-img", "provider": "unifically",
         "provider_task_id": "img-pin-1", "status": "running",
         "created_at": 900.0, "last_poll": 0}
imirror_ns["mirror_provider_image"](mijob)
check("image mirror polls the task endpoint",
      img_captured["poll_url"]
      == "https://api.unifically.com/v1/tasks/img-pin-1")
check("image mirror settles a completed task with its URL",
      img_settled.get("url") == "https://cdn.example/i.png")

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("ALL GROK SPICY ROUTE PINS PASS")
