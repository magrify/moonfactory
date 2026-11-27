"""Local checks for the 2026-11-27 UnificAlly catalog pass.

Pins the whole-catalog wiring so a later edit cannot quietly
break it:

- the key plumbing: UNIFICALLY_API_KEY is the provider's env,
  and every UnificAlly call sends a browser user-agent (the
  supplier's Cloudflare answers the default requests UA with
  a 403 error 1010);
- the route map: every lane that resolves to UnificAlly (base,
  per-resolution override, or per-mode override) is in the
  submit's model map with the verified slug and input style,
  and the split lanes resolve exactly as recorded (Seedance
  2.5 Flash text/reference on UnificAlly, image on VidLab;
  Seedance 2.0 480p on BeatAPI, the rest on UnificAlly;
  MiniMax H3 Max 480p/720p on UnificAlly, 1080p on VidLab);
- the money: swapped lanes carry the UnificAlly basis rows,
  their customer sells are frozen on shelves built from the
  displaced rates, the displaced rows are recorded, and the
  new lanes are complete (picker entry, per-second lane,
  audio record, native lengths, a rate at every tier);
- the payloads, driven for real through the extracted submit
  and image branch with fakes: Seedance omni references,
  Kling omni elements / frame modes, the Wan shape, the
  Hailuo 768p mapping, the Veo shape, and the image styles
  (grok censored, grok quality, mp, kling image, sized);
- the image shelf: the seven rerouted lanes carry the
  UnificAlly provider, endpoint and cheaper raw rows, the
  eight new lanes are well formed, and image prices follow
  the standard x1.25 / owner-at-cost machinery.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_unifically_catalog.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def fn_source(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    raise AssertionError(f"{name} not found")


def literal_assign(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    return None


# -- plumbing ---------------------------------------------------

check(app.PROVIDER_KEY_ENV.get("unifically") == "UNIFICALLY_API_KEY",
      "unifically key env missing")
headers_src = fn_source("_api_headers")
check('provider == "unifically"' in headers_src
      and '"User-Agent": "Mozilla/5.0' in headers_src,
      "_api_headers sends no browser UA for unifically")
check("mirror_unifically_status" in fn_source("mirror_api_status"),
      "mirror dispatch lost unifically")
check('provider == "unifically"' in fn_source("submit_api_job"),
      "submit dispatch lost unifically")

UA_MAP = literal_assign("UNIFICALLY_VIDEO_MODELS")
check(isinstance(UA_MAP, dict) and len(UA_MAP) == 28,
      f"UNIFICALLY_VIDEO_MODELS should hold 28 models, "
      f"found {len(UA_MAP or {})}")

EXPECTED_MAP = {
    "grok15_unc": ("xai/grok-imagine-1.5-fast-video", "grok"),
    "grok15_lite": ("xai/grok-imagine-1.5-lite-video", "grok_lite"),
    "seedance25_flash": ("bytedance/seedance-2.5-flash", "seedance"),
    "seedance2": ("bytedance/seedance-2.0-pro", "seedance"),
    "seedance2_fast": ("bytedance/seedance-2.0-fast", "seedance"),
    "seedance2_mini": ("bytedance/seedance-2.0-mini", "seedance"),
    "seedance10pro_fast": ("bytedance/seedance-1.0-pro-fast",
                           "seedance1"),
    "seedance10": ("bytedance/seedance-1.0", "seedance1"),
    "seedance10_pro": ("bytedance/seedance-1.0-pro", "seedance1_pro"),
    "kling21": ("kuaishou/kling-2.1-video", "kling_mode"),
    "kling21_master": ("kuaishou/kling-2.1-master-video",
                       "kling_mode"),
    "kling25_turbo": ("kuaishou/kling-2.5-turbo-video", "kling_mode"),
    "kling26_std": ("kuaishou/kling-2.6-video", "kling_mode"),
    "kling3_turbo": ("kuaishou/kling-3.0-turbo-video", "kling_turbo"),
    "kling4": ("kuaishou/kling-4.0-video", "kling_omni"),
    "kling4_flash": ("kuaishou/kling-4.0-flash-video", "kling_omni"),
    "kling_o1": ("kuaishou/kling-o1-video", "kling_omni"),
    "omni12": ("google/gemini-omni-flash-1.2-video", "omni"),
    "hailuo23_fast": ("hailuo/minimax-2.3-fast", "hailuo"),
    "minimax_h3_max": ("hailuo/minimax-h3-max", "hailuo"),
    "hailuo20": ("hailuo/minimax-2.0", "hailuo"),
    "h3_lite": ("hailuo/minimax-h3-lite", "hailuo"),
    "h3_fast": ("hailuo/minimax-h3-fast", "hailuo"),
    "h3_max_turbo": ("hailuo/minimax-h3-max-turbo", "hailuo_frames"),
    "wan27": ("alibaba/wan-2.7-video", "wan"),
    "happyhorse10": ("alibaba/happyhorse-1.0-video", "wan"),
    "veo3": ("google/veo-3.1-quality", "veo"),
    "veo3_lite_relaxed": ("google/veo-3.1-lite-relaxed", "veo"),
}
check(UA_MAP == EXPECTED_MAP, "UNIFICALLY_VIDEO_MODELS drifted")

# every lane resolving to unifically anywhere is in the map
for model in set(app.MODEL_PROVIDER) \
        | set(app.MODEL_PROVIDER_OVERRIDES) \
        | set(app.MODEL_PROVIDER_MODE_OVERRIDES):
    tiers = pricing.MODEL_RESOLUTIONS.get(model, pricing.RESOLUTIONS)
    for res in tiers:
        for mode in (None, "text", "image", "reference"):
            if app.provider_for(model, res, mode) == "unifically":
                check(model in UA_MAP,
                      f"{model} resolves unifically ({res}, {mode}) "
                      f"but is not in the submit map")

# -- the splits ---------------------------------------------------

check(app.MODEL_PROVIDER_MODE_OVERRIDES.get("seedance25_flash")
      == {"text": "unifically", "reference": "unifically",
          "image": "vidlab"},
      "seedance25_flash mode split drifted")
check(app.MODEL_PROVIDER_OVERRIDES.get("seedance2")
      == {"720p": "unifically", "1080p": "unifically",
          "4K": "unifically"},
      "seedance2 resolution split drifted")
check(app.MODEL_PROVIDER_OVERRIDES.get("minimax_h3_max")
      == {"480p": "unifically", "720p": "unifically"},
      "minimax_h3_max resolution split drifted")
for model, res, mode, want in (
        ("seedance25_flash", "480p", "text", "unifically"),
        ("seedance25_flash", "720p", "reference", "unifically"),
        ("seedance25_flash", "480p", "image", "vidlab"),
        ("seedance2", "480p", None, "beatapi"),
        ("seedance2", "720p", None, "unifically"),
        ("seedance2", "4K", None, "unifically"),
        ("minimax_h3_max", "720p", None, "unifically"),
        ("minimax_h3_max", "1080p", None, "vidlab")):
    check(app.provider_for(model, res, mode) == want,
          f"provider_for({model}, {res}, {mode}) != {want}")

# -- the money: basis, shelf, displaced ----------------------------

BASIS = {
    "seedance25_flash": {"480p": 4.16, "720p": 8.96},
    "seedance10pro_fast": {"480p": 0.7, "720p": 1.5, "1080p": 3.7},
    "seedance2": {"480p": 7.725, "720p": 12.9, "1080p": 31.0,
                  "4K": 74.1},
    "seedance2_fast": {"480p": 4.44, "720p": 9.18},
    "seedance2_mini": {"480p": 1.8, "720p": 3.8},
    "hailuo23_fast": {"720p": 2.88, "1080p": 4.95},
    "minimax_h3_max": {"480p": 4.5, "720p": 7.2, "1080p": 16.0},
    "omni12": {"720p": 4.28, "1080p": 4.75},
    "grok15_lite": {"480p": 1.0, "720p": 1.5, "1080p": 7.0},
    "veo3": {"720p": 30.0, "1080p": 30.625, "4K": 45.0},
    "kling4": {"720p": 12.6, "1080p": 18.9},
    "kling4_flash": {"720p": 7.56},
    "kling_o1": {"720p": 7.56, "1080p": 10.08},
    "kling26_std": {"720p": 3.78},
    "wan27": {"720p": 9.0, "1080p": 13.5},
    "kling21": {"720p": 5.04, "1080p": 8.82},
    "kling21_master": {"1080p": 25.2},
    "kling25_turbo": {"720p": 3.78, "1080p": 6.3},
    "kling3_turbo": {"720p": 10.08, "1080p": 12.6},
    "seedance10": {"480p": 1.5, "720p": 3.0},
    "seedance10_pro": {"480p": 2.2, "720p": 3.7, "1080p": 9.0},
    "hailuo20": {"720p": 5.04, "1080p": 7.35},
    "h3_lite": {"480p": 2.25, "720p": 3.6},
    "h3_fast": {"480p": 4.14, "720p": 7.2},
    "h3_max_turbo": {"480p": 2.25, "720p": 3.6},
    "happyhorse10": {"720p": 11.9, "1080p": 20.4},
    "veo3_lite_relaxed": {"720p": 1.875, "1080p": 2.5, "4K": 16.875},
}
for model, row in BASIS.items():
    check(pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(model) == row,
          f"{model} basis row drifted: "
          f"{pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(model)}")

SHELF = {
    "seedance25_flash": {"480p": 8.5, "720p": 18.5},
    "seedance10pro_fast": {"480p": 1.5, "720p": 3.0, "1080p": 6.0},
    "seedance2": {"480p": 7.725, "720p": 15.593, "1080p": 38.842,
                  "4K": 80.19},
    "seedance2_mini": {"480p": 1.9, "720p": 4.1},
    "hailuo23_fast": {"720p": 4.0, "1080p": 6.0},
    "minimax_h3_max": {"480p": 5.0, "720p": 8.0, "1080p": 16.0},
    "omni12": {"720p": 4.5, "1080p": 5.0},
    "grok15_lite": {"480p": 2.0, "720p": 3.0},
    "kling4": {"720p": 13.5, "1080p": 20.0},
    "kling4_flash": {"720p": 8.5},
    "kling_o1": {"720p": 9.0, "1080p": 12.0},
    "kling26_std": {"720p": 5.0},
    "wan27": {"720p": 9.0, "1080p": 14.0},
}
for model, row in SHELF.items():
    check(pricing.SHELF_PROVIDER_RATES_CENTS_PER_SECOND.get(model)
          == row, f"{model} shelf row drifted")

DISPLACED = {
    "seedance25_flash_vidlab": {"480p": 8.5, "720p": 18.5},
    "seedance10pro_fast_vidlab": {"480p": 1.5, "720p": 3.0,
                                  "1080p": 6.0},
    "seedance2_beatapi": {"720p": 15.593, "1080p": 38.842,
                          "4K": 80.19},
    "seedance2_fast_spicyapi": {"480p": 4.5, "720p": 9.68},
    "seedance2_mini_kie": {"480p": 1.9, "720p": 4.1},
    "hailuo23_fast_vidlab": {"720p": 4.0, "1080p": 6.0},
    "minimax_h3_max_vidlab": {"480p": 5.0, "720p": 8.0},
    "omni12_kie": {"720p": 4.5, "1080p": 5.0},
    "grok15_lite_fal": {"480p": 2.0, "720p": 3.0},
    "veo3_kie": {"720p": 31.25, "1080p": 31.875, "4K": 46.25},
    "kling4_vidlab": {"720p": 13.5, "1080p": 20.0},
    "kling4_flash_kie": {"720p": 8.5},
    "kling_o1_vidlab": {"720p": 9.0, "1080p": 12.0},
    "kling26_std_vidlab": {"720p": 5.0},
    "wan27_vidlab": {"720p": 9.0, "1080p": 14.0},
}
for key, row in DISPLACED.items():
    check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get(key) == row,
          f"displaced {key} drifted")

# customer sells frozen at the pre-pass figures (recorded
# 2026-11-27 before the swaps landed)
FROZEN_SELLS = {
    ("seedance25_flash", "480p"): 10.75,
    ("seedance25_flash", "720p"): 23.25,
    ("seedance10pro_fast", "480p"): 2.0,
    ("seedance10pro_fast", "720p"): 3.875,
    ("seedance10pro_fast", "1080p"): 7.625,
    ("seedance2", "480p"): 9.782,
    ("seedance2", "720p"): 19.617,
    ("seedance2", "1080p"): 48.678,
    ("seedance2", "4K"): 100.363,
    ("seedance2_fast", "480p"): 6.375,
    ("seedance2_fast", "720p"): 13.875,
    ("seedance2_mini", "480p"): 2.5,
    ("seedance2_mini", "720p"): 5.25,
    ("hailuo23_fast", "720p"): 5.125,
    ("hailuo23_fast", "1080p"): 7.625,
    ("minimax_h3_max", "480p"): 6.375,
    ("minimax_h3_max", "720p"): 10.125,
    ("minimax_h3_max", "1080p"): 20.125,
    ("omni12", "720p"): 5.75,
    ("omni12", "1080p"): 6.375,
    ("grok15_lite", "480p"): 2.625,
    ("grok15_lite", "720p"): 3.875,
    ("kling4", "720p"): 14.007,
    ("kling4", "1080p"): 20.701,
    ("kling4_flash", "720p"): 10.75,
    ("kling_o1", "720p"): 11.375,
    ("kling_o1", "1080p"): 15.125,
    ("kling26_std", "720p"): 6.375,
    ("wan27", "720p"): 11.375,
    ("wan27", "1080p"): 17.625,
}
for (model, res), want in FROZEN_SELLS.items():
    got = pricing.frontier_lane(model, res)["sell_cents_per_second"]
    check(abs(got - want) < 1e-9,
          f"{model} {res} sell moved {want} -> {got}")

# flat lanes: veo3 on the UnificAlly flat row, the relaxed
# Lite a new flat lane with the Veo steps
check(pricing.FLAT_CLIP_COST_CENTS.get("veo3")
      == {"720p": 120.0, "1080p": 122.5, "4K": 180.0},
      "veo3 flat row drifted")
check(pricing.FLAT_CLIP_COST_CENTS.get("veo3_lite_relaxed")
      == {"720p": 7.5, "1080p": 10.0, "4K": 67.5},
      "veo3_lite_relaxed flat row drifted")
check(pricing.FLAT_CLIP_STEPS.get("veo3_lite_relaxed") == (4, 6, 8),
      "veo3_lite_relaxed flat steps drifted")

# -- new video lanes are complete ---------------------------------

NEW_VIDEO = ["kling21", "kling21_master", "kling25_turbo",
             "kling3_turbo", "seedance10", "seedance10_pro",
             "hailuo20", "h3_lite", "h3_fast", "h3_max_turbo",
             "happyhorse10", "veo3_lite_relaxed"]
model_ids = {m["id"] for m in app.MODELS}
for model in NEW_VIDEO:
    check(model in model_ids, f"{model} missing from MODELS")
    entry = next(m for m in app.MODELS if m["id"] == model)
    check(entry.get("paid_only") is True, f"{model} not paid_only")
    check(model in app.PER_SECOND_MODELS,
          f"{model} missing from PER_SECOND_MODELS")
    check(model in app.LANE_AUDIO, f"{model} missing LANE_AUDIO")
    check(len(app.native_lengths(model)) >= 1,
          f"{model} has no native lengths")
check("kling21" in app.START_IMAGE_REQUIRED_MODELS,
      "kling21 lost its start-frame requirement")
check("seedance10_pro" in app.START_IMAGE_REQUIRED_MODELS,
      "seedance10_pro lost its frame requirement")
check("omni12" not in app.SIZE_PICKER_SINGLE
      and "seedance2_mini" not in app.SIZE_PICKER_SINGLE,
      "a UA-resolution lane is still picker-singled")
check(app.ref_caps_for("kling4") == (10, 0, 0), "kling4 caps")
check(app.ref_caps_for("kling4_flash") == (10, 0, 0),
      "kling4_flash caps")
check("seedance2_fast" not in app.IMAGE_REFS_ONLY_MODELS,
      "seedance2_fast still image-refs-only")
check(app.MODEL_DURATION_BOUNDS.get("seedance10pro_fast") == (4, 12),
      "seedance10pro_fast bounds not tightened to the UA schema")
check("kling_o1" not in app.MODEL_DURATION_STEPS
      and app.MODEL_DURATION_BOUNDS.get("kling_o1") == (3, 10),
      "kling_o1 lengths not moved to the (3, 10) range")

# -- driven video submits ------------------------------------------

jobs = {}
captured = {}


def _fake_submit_post(job, url, headers, payload):
    captured["url"] = url
    captured["json"] = payload
    return {"task_id": "ua-pin-1", "status": "processing"}


def _boom(job, msg):
    raise RuntimeError(f"unexpected submit failure: {msg}")


submit_ns = {
    "UNIFICALLY_VIDEO_MODELS": UA_MAP,
    "UNIFICALLY_BASE": "https://api.unifically.com",
    "jobs": jobs,
    "_submit_post": _fake_submit_post,
    "_submit_failed": _boom,
    "_api_headers": lambda provider: {"Authorization": "Bearer <redacted>"},
    "_beatapi_media_url": lambda ref, kind: (
        "https://media.example/" + kind + ".png"),
}
exec(fn_source("submit_unifically_job"), submit_ns)
submit = submit_ns["submit_unifically_job"]

BASE_PARAMS = {"duration": 8, "resolution": "720p",
               "aspect_ratio": "16:9", "mode": "text",
               "start_image": None, "end_image": None,
               "reference_image_urls": [],
               "reference_video_urls": [],
               "reference_audio_urls": []}


def drive(model, **over):
    params = dict(BASE_PARAMS)
    params.update(over)
    job = {"id": f"pin-{model}", "model": model, "status": "queued"}
    submit(job, {}, params, "a test prompt")
    return captured["json"]


p = drive("seedance2", mode="reference",
          reference_image_urls=["data:img"],
          reference_video_urls=["data:vid"],
          reference_audio_urls=["data:aud"])
check(p["model"] == "bytedance/seedance-2.0-pro",
      "seedance2 submit slug")
check(p["input"]["generate_audio"] is True
      and p["input"]["image_urls"]
      == ["https://media.example/reference-image.png"]
      and p["input"]["video_urls"]
      == ["https://media.example/reference-video.png"]
      and p["input"]["audio_urls"]
      == ["https://media.example/reference-audio.png"],
      "seedance2 omni reference payload")

p = drive("seedance25_flash", mode="text")
check(p["model"] == "bytedance/seedance-2.5-flash"
      and p["input"]["generate_audio"] is True,
      "seedance25_flash text payload")

p = drive("kling4", mode="reference", resolution="1080p",
          reference_image_urls=["data:img"])
check(p["input"]["video_mode"] == "elements"
      and p["input"]["mode"] == "pro"
      and p["input"]["native_audio"] is True,
      "kling4 elements payload")

p = drive("kling4_flash", mode="image", start_image="data:s",
          end_image="data:e")
check(p["input"]["video_mode"] == "start_end_frame"
      and "end_image_url" not in p["input"],
      "kling4_flash frame payload must drop the end frame")

p = drive("wan27", mode="reference",
          reference_image_urls=["data:img"],
          reference_video_urls=["data:vid"])
check(p["input"]["mode"] == "r2v"
      and p["input"]["resolution"] == "720P"
      and p["input"]["ratio"] == "16:9"
      and p["input"]["reference_video_urls"]
      == ["https://media.example/reference-video.png"],
      "wan27 r2v payload")

p = drive("hailuo23_fast", mode="image", start_image="data:s")
check(p["input"]["resolution"] == "768p"
      and p["input"]["start_image_url"]
      == "https://media.example/first-frame.png",
      "hailuo23_fast 768p payload")

p = drive("omni12", mode="reference",
          reference_image_urls=["data:img"])
check(p["input"]["reference_image_urls"]
      == ["https://media.example/reference-image.png"],
      "omni12 reference payload")

p = drive("veo3", mode="text")
check(p["model"] == "google/veo-3.1-quality"
      and p["input"]["nsfw_check"] is False,
      "veo3 payload")

p = drive("kling21", mode="image", start_image="data:s")
check(p["input"]["mode"] == "std"
      and p["input"]["start_image_url"]
      == "https://media.example/first-frame.png",
      "kling21 payload")

p = drive("grok15_lite", mode="text")
check("video_preset" not in p["input"],
      "grok15_lite must not carry the spicy preset")

# -- image shelf ----------------------------------------------------

IMG_SWAPS = {
    "grok_imagine_image": ("xai/grok-imagine-1.0-image",
                           {"1K": 1.0, "2K": 1.0}),
    "grok_imagine_image20_quality": ("xai/grok-imagine-2.0-image",
                                     {"1K": 3.0, "2K": 4.0}),
    "qwen_image20_pro": ("alibaba/qwen-image-2.0-pro",
                         {"square": 6.75}),
    "seedream40": ("bytedance/seedream-4.0",
                   {"1K": 2.7, "2K": 2.7, "4K": 2.7}),
    "seedream45": ("bytedance/seedream-4.5",
                   {"2K": 3.6, "4K": 3.6}),
    "seedream50_flash": ("bytedance/seedream-5.0-flash",
                         {"1K": 1.62, "1.5K": 1.62, "2K": 1.62}),
    "flux3_klein": ("black-forest-labs/flux-3-image-klein",
                    {"0.6MP": 0.8, "1MP": 1.07, "2MP": 1.07,
                     "4MP": 1.61}),
}
for lane_id, (endpoint, base_raw) in IMG_SWAPS.items():
    lane = pricing.IMAGE_LANES[lane_id]
    check(lane["provider"] == "unifically",
          f"{lane_id} not rerouted to unifically")
    check(lane["endpoint"] == endpoint, f"{lane_id} endpoint drifted")
    for tier, raw in base_raw.items():
        got, _debit = pricing.image_lane_price(lane_id, tier)
        check(got == raw, f"{lane_id} {tier} raw {got} != {raw}")

IMG_DISPLACED = {
    "grok_imagine_image_vidlab": {"1K": 2.0, "2K": 2.0},
    "grok_imagine_image20_quality_vidlab": {"1K": 5.0, "2K": 7.0},
    "qwen_image20_pro_vidlab": {"square": 7.0},
    "seedream40_vidlab": {"square": 2.9},
    "seedream45_vidlab": {"square": 4.0},
    "seedream50_flash_vidlab": {"square": 2.0},
    "flux3_klein_vidlab": {"1K": 1.0, "1.5K": 2.0, "2K": 2.0,
                           "4K": 4.0},
}
for key, row in IMG_DISPLACED.items():
    check(pricing.DISPLACED_IMAGE_RATES_CENTS.get(key) == row,
          f"displaced image {key} drifted")

NEW_IMAGE = ["flux3_klein_base", "flux2_klein9b", "kling3_image",
             "kling3_omni_image", "kling4_image", "kling_o1_image",
             "wan26_image", "zimage_turbo"]
for lane_id in NEW_IMAGE:
    lane = pricing.IMAGE_LANES.get(lane_id) or {}
    check(lane.get("provider") == "unifically",
          f"{lane_id} missing or not on unifically")
    check(bool(lane.get("endpoint")), f"{lane_id} has no endpoint")
    check(not lane.get("free"), f"{lane_id} must not be free")
    for size, raw in lane["raw_cents"].items():
        check(raw > 0, f"{lane_id} {size} non-positive price")
        got_raw, debit = pricing.image_lane_price(lane_id, size)
        check(got_raw == raw, f"{lane_id} {size} raw mismatch")
        check(abs(debit - raw * pricing.FRONTIER_MIN_MARKUP) < 1e-9,
              f"{lane_id} {size} customer price is not raw x 1.25")
        _r, owner_debit = pricing.image_lane_price(
            lane_id, size, owner=True)
        check(owner_debit == raw, f"{lane_id} {size} owner not raw")

# the 2.0 spicy lane's price basis: the cheapest documented
# 2.0 cell (low x 1k = $0.02), owner at cost, customer x1.25
lane20 = pricing.IMAGE_LANES["grok_imagine20_unc"]
check(lane20["provider"] == "unifically"
      and lane20.get("uncensored") is True
      and lane20["raw_cents"]["1K"] == 2.0,
      "grok_imagine20_unc basis drifted from the low x 1k cell")
_raw, _debit = pricing.image_lane_price("grok_imagine20_unc", "1K")
check(_debit == 2.5, "grok_imagine20_unc customer price is not x1.25")

# -- driven image submits --------------------------------------------

img_captured = {}


def _fake_img_post(job, url, headers, payload):
    img_captured["url"] = url
    img_captured["json"] = payload
    return {"task_id": "ua-img-1", "status": "processing"}


img_ns = {
    "pricing": pricing,
    "UNIFICALLY_BASE": "https://api.unifically.com",
    "jobs": jobs,
    "_submit_post": _fake_img_post,
    "_api_headers": lambda provider: {"Authorization": "Bearer <redacted>"},
    "_beatapi_media_url": lambda ref, kind: (
        "https://media.example/" + kind + ".png"),
}
exec(fn_source("_submit_provider_image"), img_ns)
img_submit = img_ns["_submit_provider_image"]


def drive_img(lane_id, size, refs=None):
    job = {"id": f"pin-img-{lane_id}", "image_model": lane_id,
           "image_size": size, "status": "queued"}
    img_submit(job, "a test image", reference_images=refs)
    return img_captured["json"]


p = drive_img("grok_imagine_image", "1K-16:9")
check(p["input"]["enable_nsfw"] is False,
      "censored grok lane must send enable_nsfw off")
p = drive_img("grok_imagine_image20_quality", "1K-16:9")
check(p["input"]["quality"] == "medium"
      and p["input"]["resolution"] == "1k",
      "grok quality payload")
p = drive_img("flux3_klein", "0.6MP-16:9", refs=["data:ref"])
check(p["input"]["resolution"] == "0.6mp"
      and p["input"]["image_urls"]
      == ["https://media.example/edit-source.png"],
      "flux3_klein mp payload")
p = drive_img("kling4_image", "2K-1:1")
check(p["input"]["resolution"] == "2k"
      and p["input"]["mode"] == "standard",
      "kling4_image payload")
p = drive_img("seedream40", "1K-16:9")
check(p["input"]["resolution"] == "1K",
      "seedream40 sized payload")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_unifically_catalog")
