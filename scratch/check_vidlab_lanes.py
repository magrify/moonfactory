"""Local checks for the 2026-11-23 VidLab wiring.

Pins the VidLab route's shape so a later edit cannot quietly
break it:

- every VidLab-routed video model is a real picker entry, a
  per-second lane, has a catalog slug, a seeded rate for every
  tier it sells, an audio record, and at least one native
  length;
- the five reroutes (Kling 4.0, Seedance 2.0 Fast, Seedance
  2.5 Flash, PixVerse V6, Vidu Q4) resolve to VidLab, the
  displaced Kie rates are recorded, and the three formerly
  price-paused lanes carry rates now;
- the submit and mirror functions exist, post to the
  documented generations endpoint with an idempotency key,
  and branch on VidLab's documented status words, reading the
  result from the generation's outputs and the settled cost
  from its price_usd;
- the rate refresh reads VidLab's public catalog for the
  VidLab-routed tiers;
- every VidLab image lane is well formed (endpoint, positive
  prices, a tier-signed label, the standard x1.25 customer
  markup, the owner at raw), Nano Banana Pro rides VidLab at
  the cheaper catalog prices, and no VidLab lane is free.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_vidlab_lanes.py
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


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- provider plumbing ------------------------------------------------

check(app.PROVIDER_KEY_ENV.get("vidlab") == "VIDLAB_API_KEY",
      "vidlab key env missing")
check("vidlab" in app.API_POLL_PROVIDERS,
      "vidlab missing from API_POLL_PROVIDERS")
check("vidlab" in app.URL_RESULT_PROVIDERS,
      "vidlab missing from URL_RESULT_PROVIDERS")
check(app.VIDLAB_BASE == "https://vidlab.ai", "VIDLAB_BASE drifted")

submit = func_src("submit_vidlab_job")
check(bool(submit), "submit_vidlab_job not found")
for token in ('f"{VIDLAB_BASE}/v1/generations"', '"Idempotency-Key"',
              '"max_cost_usd"', "VIDLAB_VIDEO_MODELS",
              "_submit_failed"):
    check(token in submit, f"submit_vidlab_job missing {token}")
mirror = func_src("mirror_vidlab_status")
check(bool(mirror), "mirror_vidlab_status not found")
for token in ('"succeeded"', '"failed"', '"queued"', '"running"',
              '_note_unparsed(job, "vidlab"', 'get("outputs")',
              '"price_usd"', "_poll_done", "_poll_fail"):
    check(token in mirror, f"mirror_vidlab_status missing {token}")
check('provider == "vidlab"' in func_src("submit_api_job"),
      "submit_api_job does not dispatch vidlab")
check("mirror_vidlab_status" in func_src("mirror_api_status"),
      "mirror_api_status does not dispatch vidlab")

refresh = func_src("refresh_provider_rates")
check("VIDLAB_VIDEO_MODELS" in refresh and "/v1/models" in refresh,
      "rate refresh does not read the VidLab catalog")

# -- video lanes --------------------------------------------------------

vidlab_models = sorted(
    m for m, p in app.MODEL_PROVIDER.items() if p == "vidlab")
check(len(vidlab_models) == 25,
      f"expected 25 vidlab video models, found {len(vidlab_models)}")
model_ids = {m["id"] for m in app.MODELS}
for model in vidlab_models:
    check(model in model_ids, f"{model} missing from MODELS")
    check(model in app.PER_SECOND_MODELS,
          f"{model} missing from PER_SECOND_MODELS")
    entry = next(m for m in app.MODELS if m["id"] == model)
    check(entry.get("paid_only") is True,
          f"{model} is not paid_only (VidLab lanes are never free)")
    spec = app.VIDLAB_VIDEO_MODELS.get(model)
    check(spec and spec.get("slug"), f"{model} has no VidLab slug")
    tiers = pricing.MODEL_RESOLUTIONS.get(model, pricing.RESOLUTIONS)
    rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(model) or {}
    for tier in tiers:
        check(tier in rates, f"{model} has no seeded rate at {tier}")
    for tier in (spec.get("rate_variants") or {}):
        check(tier in rates,
              f"{model} rate_variants names unrated tier {tier}")
    check(model in app.LANE_AUDIO, f"{model} has no LANE_AUDIO entry")
    check(len(app.native_lengths(model)) >= 1,
          f"{model} has no native lengths")
    check(app.provider_for(model, tiers[0]) == "vidlab",
          f"{model} does not resolve to vidlab")

# The reroutes and unpauses.
for model in ("kling4", "seedance2_fast", "seedance25_flash",
              "pixverse_v6", "vidu_q4"):
    check(app.MODEL_PROVIDER.get(model) == "vidlab",
          f"{model} was not rerouted to vidlab")
check(pricing.PROVIDER_RATES_CENTS_PER_SECOND["kling4"]
      == {"720p": 13.5, "1080p": 20.0}, "kling4 VidLab rates drifted")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("kling4_kie")
      == {"720p": 14.25, "1080p": 21.25},
      "displaced Kie kling4 rates not recorded")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("seedance2_fast_kie")
      == {"480p": 5.9, "720p": 12.4},
      "displaced Kie seedance2_fast rates not recorded")
# Kling 4.0 and Seedance 2.0 Fast sell every priced tier again:
# the VidLab submit sends the resolution.
check("kling4" not in app.SIZE_PICKER_SINGLE,
      "kling4 still picker-restricted to one tier")
check("seedance2_fast" not in app.SIZE_PICKER_SINGLE,
      "seedance2_fast still picker-restricted to one tier")
rates = {"kling4": pricing.PROVIDER_RATES_CENTS_PER_SECOND["kling4"]}
check(app.offered_resolutions("kling4", rates) == ["720p", "1080p"],
      "kling4 does not offer both tiers")

# -- image lanes --------------------------------------------------------

vidlab_lanes = sorted(
    lane_id for lane_id, lane in pricing.IMAGE_LANES.items()
    if lane["provider"] == "vidlab")
check(len(vidlab_lanes) == 33,
      f"expected 33 vidlab image lanes, found {len(vidlab_lanes)}")
# Superseded 2026-11-25: was 34 until GPT Image 1 was removed
# the same day (OpenAI shut the model down 2026-10-23; Jack's
# order: a dead lane is not left listed). See
# check_gpt_image1_retired.py.
for lane_id in vidlab_lanes:
    lane = pricing.IMAGE_LANES[lane_id]
    check(bool(lane.get("endpoint")), f"{lane_id} has no endpoint")
    check(not lane.get("free"), f"{lane_id} must not be a free lane")
    check(" · $" in lane["label"],
          f"{lane_id} label carries no tier sign")
    for size, raw in lane["raw_cents"].items():
        check(raw > 0, f"{lane_id} {size} has a non-positive price")
        got_raw, debit = pricing.image_lane_price(lane_id, size)
        check(got_raw == raw, f"{lane_id} {size} raw price mismatch")
        check(abs(debit - raw * pricing.FRONTIER_MIN_MARKUP) < 1e-9,
              f"{lane_id} {size} customer price is not raw x 1.25")
        _r, owner_debit = pricing.image_lane_price(
            lane_id, size, owner=True)
        check(owner_debit == raw,
              f"{lane_id} {size} owner price is not raw cost")
check(pricing.IMAGE_LANES["nano_banana_pro"]["provider"] == "vidlab",
      "nano_banana_pro was not rerouted to vidlab")
_nbp = pricing.IMAGE_LANES["nano_banana_pro"]["raw_cents"]
check({k: _nbp[k] for k in ("1K", "2K", "4K")}
      == {"1K": 4.0, "2K": 4.0, "4K": 8.0},
      "nano_banana_pro VidLab prices drifted")
check(pricing.IMAGE_LANES["grok_imagine15_edit"].get("edit") is True,
      "grok_imagine15_edit lost its edit flag")

img_submit = func_src("_submit_provider_image")
check('provider == "vidlab"' in img_submit
      and "VIDLAB_BASE" in img_submit,
      "image submit has no vidlab branch")
img_mirror = func_src("mirror_provider_image")
check('provider == "vidlab"' in img_mirror
      and 'get("outputs")' in img_mirror,
      "image mirror has no vidlab branch")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_vidlab_lanes")
