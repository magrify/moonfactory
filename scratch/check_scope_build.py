"""Local checks for the 2026-11-17 scope build. No Modal calls,
no network, no generation: imports the platform modules and
exercises the pure functions the live routes use.

Run:  modal-venv python scratch/check_scope_build.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


# --- lip sync pricing -------------------------------------------------
raw, debit = pricing.lip_sync_costs(10.0)
check(raw == 50.0, f"lip sync raw 10s should be 50c, got {raw}")
check(debit == 62.5, f"lip sync debit 10s should be 62.5c, got {debit}")
raw_o, debit_o = pricing.lip_sync_costs(10.0, owner=True)
check(raw_o == debit_o == 50.0, "owner lip sync pays raw cost")

base = pricing.per_second_estimate("wan3", "720p", 10.0, 0.0)
with_lip = pricing.per_second_estimate("wan3", "720p", 10.0, 0.0,
                                       lip_sync=True)
check(with_lip["raw_cost_cents"] > base["raw_cost_cents"],
      "lip sync estimate must exceed the plain estimate")
check(abs((with_lip["raw_cost_cents"] - base["raw_cost_cents"]) - 50.0) < 1e-9,
      "lip sync adds exactly its raw cost to the estimate")
check(with_lip["cap_raw_cents"] >= with_lip["raw_cost_cents"],
      "lip sync cap covers the folded cost")
check(with_lip["cap_balance_cents"] >= with_lip["balance_debit_cents"],
      "lip sync balance cap covers the folded debit")
check(with_lip["lip_sync_cents"] == 50.0, "estimate reports the lip sync cents")
check(base["lip_sync_cents"] == 0.0, "plain estimate reports zero lip sync")

# Reference seconds never carry lip sync cost.
with_ref = pricing.per_second_estimate("seedance2", "720p", 10.0, 5.0,
                                       lip_sync=True)
plain_ref = pricing.per_second_estimate("seedance2", "720p", 10.0, 5.0)
check(abs((with_ref["raw_cost_cents"] - plain_ref["raw_cost_cents"]) - 50.0) < 1e-9,
      "lip sync prices output seconds only, not reference seconds")

# --- lip sync trigger ---------------------------------------------------
check(app.lip_sync_applies("wan3", "hello there") is True,
      "spoken line on a non-speech lane plans lip sync")
check(app.lip_sync_applies("seedance", "hello there") is False,
      "speech-native lane needs no lip sync stage")
check(app.lip_sync_applies("wan3", "") is False,
      "no spoken line, no lip sync")
check(app.lip_sync_applies("wan2.2", "hello") is True,
      "house lane spoken line plans lip sync")

# --- reference caps ------------------------------------------------------
check(app.ref_caps_for("seedance") == (30, 10, 10), "Seedance 2.5 caps 30/10/10")
check(app.ref_caps_for("seedance2") == (9, 3, 3), "Seedance 2.0 caps 9/3/3")
check(app.ref_caps_for("seedance2_fast") == (9, 3, 3), "Seedance 2.0 Fast caps")
check(app.ref_caps_for("veo3") == (3, 0, 0), "Veo 3.1 caps 3 images only")
check(app.ref_caps_for("veo3_fast") == (3, 0, 0), "Veo 3.1 Fast caps")
check(app.ref_caps_for("kling4") == (1, 1, 0),
      "Kling 4.0 caps 1/1/0 on the VidLab route (2026-11-23 reroute)")
check(app.ref_caps_for("kling3_omni") == (10, 5, 5), "Kling 3.0 Omni caps")
check(app.ref_caps_for("wan3") == (10, 5, 5), "unverified lanes keep platform cap")
check(app.MODEL_REF_VIDEO_SECONDS_CAP["seedance"] == 30.0,
      "Seedance 2.5 combined reference video cap is 30s")
check(app.MODEL_REF_VIDEO_SECONDS_CAP["wan_motion"] == 30.0,
      "motion driving video cap is 30s")

# --- duration corrections -------------------------------------------------
check(app.MODEL_DURATION_STEPS["wan22_unc"] == (5,),
      "Wan 2.2 maker cap is 5s fixed")
check(app.duration_plan("wan22_unc", 45)["segments"] == [5] * 9,
      "Wan 2.2 45s chains nine 5s segments")
check(app.duration_plan("kling4", 30)["segments"] == [30], "Kling 4.0 native 30")
check(app.MODEL_DURATION_BOUNDS["ltx25"] == (6, 20), "LTX-2.5 native cap 20")
check(app.duration_plan("ltx25", 10)["segments"] == [10], "LTX-2.5 native 10")
check(app.MODEL_DURATION_BOUNDS["veo3"] == (4, 8), "Veo native cap 8")
check(all(s in (4, 6, 8) for s in app.duration_plan("veo3", 60)["segments"]),
      "Veo chains only its native steps")

# --- length rule: per-model native lists + universal long lengths --------
check(app.native_lengths("veo3") == [4, 6, 8], "Veo natives are 4/6/8 only")
check(app.native_lengths("wan22_unc") == [5], "Wan 2.2 is fixed at 5")
check(app.native_lengths("hailuo23") == [6, 10], "Hailuo natives are 6/10")
check(app.native_lengths("ltx25") == [6, 8, 10, 12, 14, 16, 18, 20],
      "LTX natives are the even lengths to 20")
check(app.native_lengths("seedance") == list(range(4, 31)),
      "Seedance 2.5 natives are every whole second 4 to 30")
check(app.native_lengths("grok") == list(range(1, 16)),
      "Grok natives are every whole second 1 to 15")
check(app.offered_lengths("veo3") == [4, 6, 8, 60, 90, 120],
      f"Veo offered = natives + composable long lengths, got "
      f"{app.offered_lengths('veo3')}")
check(45 not in app.offered_lengths("veo3"),
      "45s has no exact Veo composition (all steps even): not offered")
check(app.offered_lengths("hailuo23") == [6, 10, 60, 90, 120],
      "Hailuo offered = 6/10 + composable long lengths")
check(app.offered_lengths("luma_ray32") == [5, 10, 45, 60, 90, 120],
      "Luma offered = 5/10 + all long lengths")
check(app.offered_lengths("wan22_unc") == [5, 45, 60, 90, 120],
      "Wan 2.2 uncensored offered = its fixed 5 + long lengths")
check(app.offered_lengths("seedance") == list(range(4, 31)) + [45, 60, 90, 120],
      "Seedance 2.5 offered = 4..30 + long lengths")
check(1 not in app.offered_lengths("seedance"), "no 1s on Seedance (min 4)")
for mid in app.PER_SECOND_MODELS:
    for L in app.offered_lengths(mid):
        p = app.duration_plan(mid, L)
        check(p["generated_seconds"] == L,
              f"{mid} {L}s: generated must equal ordered (no trim)")
        check(all(s in app.native_lengths(mid) for s in p["segments"]),
              f"{mid} {L}s: every segment is a native length")

# --- motion transfer lane --------------------------------------------------
check("wan_motion" in app.PER_SECOND_MODELS, "wan_motion is a per-second lane")
check("wan_motion" not in app.UNCENSORED_VIDEO_MODELS,
      "wan_motion is not an uncensored lane")
check(app.MODEL_PROVIDER["wan_motion"] == "fal", "wan_motion routes via fal")
check(pricing.PROVIDER_RATES_CENTS_PER_SECOND["wan_motion"]["720p"] == 6.0,
      "wan_motion rate is the published $0.06/s")
check(pricing.MODEL_RESOLUTIONS["wan_motion"] == ("720p",),
      "wan_motion sells 720p only")
check(app.FAL_ENDPOINTS[("wan_motion", "reference")] == "fal-ai/wan-motion",
      "wan_motion endpoint wired")
labels = [m["id"] for m in app.MODELS]
check("wan_motion" in labels, "wan_motion is on the shelf")
order = app.grouped_model_order(
    app.MODELS,
    lambda mid: None)
check(len(order) == len(app.MODELS), "grouped order keeps every model")

# --- every per-second lane has a rate or is a known price-paused lane -----
paused = {"vidu_q2", "vidu_q4", "seedance25_flash", "pixverse_v6"}
for mid in app.PER_SECOND_MODELS:
    rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(mid) or {}
    sells = pricing.MODEL_RESOLUTIONS.get(mid, pricing.RESOLUTIONS)
    for res in sells:
        if mid in paused:
            continue
        check(res in rates, f"{mid} missing a rate for {res}")

# --- image shelf (item 1) ------------------------------------------
check(len(pricing.IMAGE_LANES) == 54,
      "image shelf has 54 lanes (33 VidLab lanes added 2026-11-23, "
      "GPT Image 2.6 Sunburst added 2026-11-25, LongCat-Image "
      "added 2026-11-26, MAI Image 2.7 and 2.7 Flash added "
      "2026-11-27, Midjourney V8.2, V8.1, V6.1 and Niji 7 "
      "added 2026-11-27 on CometAPI; superseded 2026-11-25: "
      "was 48 until "
      "GPT Image 1 was removed the same day, its upstream shut "
      "down by OpenAI 2026-10-23, leaving 47 until LongCat)")
check(pricing.IMAGE_LANES["krea2_verite"]["provider"] == "kaggle-comfy"
      and pricing.IMAGE_LANES["krea2_verite"]["endpoint"] == "krea2-verite"
      and pricing.IMAGE_LANES["krea2_verite"].get("uncensored") is True,
      "Krea 2 Verite rides the house Kaggle worker as an uncensored lane")
krea_raw, krea_debit = pricing.image_lane_price(
    "krea2_verite", "832x1216")
check(krea_raw == 1.9 and abs(krea_debit - 2.375) < 1e-9,
      "Krea 2 Verite: raw basis $0.019, customer $0.02375 at any size")
krea_raw_o, krea_debit_o = pricing.image_lane_price(
    "krea2_verite", "832x1216", owner=True)
check(krea_raw_o == 0.0 and krea_debit_o == 0.0,
      "the owner pays the Kaggle route's $0 raw cost on Krea 2 Verite")
check(pricing.IMAGE_LANES["sdxl_ovh"]["provider"] == "ovhcloud"
      and pricing.IMAGE_LANES["sdxl_ovh"]["label"] == "SDXL"
      and pricing.IMAGE_LANES["sdxl_ovh"]["free"] is True,
      "SDXL rides OVHcloud AI Endpoints as a free lane")
sdxl_raw, sdxl_debit = pricing.image_lane_price(
    "sdxl_ovh", "1024x1024")
check(sdxl_raw == 0.0 and sdxl_debit == 0.0,
      "the SDXL lane prices at $0 raw and $0 customer")
check(pricing.IMAGE_LANES["stock"]["provider"] == "pexels"
      and pricing.IMAGE_LANES["stock"]["label"] == "Stock image",
      "the stock lane returns Pexels photos under Jack's name for it")
stock_raw, stock_debit = pricing.image_lane_price("stock", "large")
check(stock_raw == 0.0 and stock_debit == 0.0,
      "the stock lane prices at $0 raw and $0 customer")
check(pricing.IMAGE_LANES["flux2_klein4b"]["provider"] == "cloudflare"
      and pricing.IMAGE_LANES["flux2_klein4b"]["endpoint"]
      == "@cf/black-forest-labs/flux-2-klein-4b",
      "FLUX.2 Klein 4B rides Cloudflare Workers AI")
check(pricing.IMAGE_LANES["flux1_schnell"]["provider"] == "cloudflare"
      and pricing.IMAGE_LANES["flux1_schnell"]["endpoint"]
      == "@cf/black-forest-labs/flux-1-schnell",
      "FLUX.1 Schnell rides Cloudflare Workers AI")
cf_raw, cf_debit = pricing.image_lane_price("flux2_klein4b", "1024x1024")
check(cf_raw == 0.0 and cf_debit == 0.0,
      "the Cloudflare free lanes price at $0 raw and $0 customer")
raw, debit = pricing.image_lane_price("nano_banana_pro", "1K")
check(raw == 4.0 and abs(debit - 5.0) < 1e-9,
      "Nano Banana Pro: raw $0.04, customer $0.05 (under Higgsfield $0.098)")
raw_o, debit_o = pricing.image_lane_price("nano_banana_pro", "1K", owner=True)
check(debit_o == raw_o == 4.0, "owner pays raw on the image shelf")
check(pricing.IMAGE_LANES["zimage_unc"]["endpoint"]
      == "alibaba/z-image-spicy/text-to-image", "Z-Image slug from the catalog")
check(pricing.IMAGE_LANES["zimage_pro_unc"]["endpoint"]
      == "alibaba/z-image-spicy-pro/text-to-image",
      "Z-Image Pro slug from the catalog")
raw, debit = pricing.image_lane_price("zimage_pro_unc", "2048x2048")
check(raw == 1.9 and abs(debit - 2.375) < 1e-9,
      "Z-Image Pro: flat raw $0.019, customer $0.02375 at any size")
check(pricing.IMAGE_LANES["qwen_edit_unc"]["endpoint"]
      == "alibaba/qwen-image-spicy-edit/edit",
      "Qwen Image Edit slug from the catalog")
check(pricing.IMAGE_LANES["qwen_edit_unc"].get("edit") is True,
      "Qwen Image Edit is flagged as an edit lane")
raw, debit = pricing.image_lane_price("qwen_edit_unc", "match")
check(raw == 3.8 and abs(debit - 4.75) < 1e-9,
      "Qwen Image Edit: flat raw $0.038, customer $0.0475")
check(pricing.image_size_label("qwen_edit_unc", "match")
      == "Same size as your image",
      "Qwen Image Edit picker says the output matches the input size")
check(app._first_image_url({"data": {"images": [
    {"url": "https://x.test/a.png", "content_type": "image/png"}]}})
    == "https://x.test/a.png", "image URL scan finds nested results")
check(app._first_image_url({"video": {"url": "https://x.test/v.mp4"}}) is None,
      "image URL scan ignores videos")

if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL SCOPE CHECKS PASSED")
