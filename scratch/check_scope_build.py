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
check(app.ref_caps_for("kling4") == (10, 5, 3), "Kling 4.0 caps 10/5/3")
check(app.ref_caps_for("kling3_omni") == (10, 5, 5), "Kling 3.0 Omni caps")
check(app.ref_caps_for("wan3") == (10, 5, 5), "unverified lanes keep platform cap")
check(app.MODEL_REF_VIDEO_SECONDS_CAP["seedance"] == 30.0,
      "Seedance 2.5 combined reference video cap is 30s")
check(app.MODEL_REF_VIDEO_SECONDS_CAP["wan_motion"] == 30.0,
      "motion driving video cap is 30s")

# --- duration corrections -------------------------------------------------
check(app.MODEL_DURATION_STEPS["wan22_unc"] == (5,),
      "Wan 2.2 maker cap is 5s fixed")
check(app.duration_plan("wan22_unc", 10)["segments"] == [5, 5],
      "Wan 2.2 10s chains two 5s segments")
check(app.duration_plan("kling4", 30)["segments"] == [30], "Kling 4.0 native 30")
check(app.MODEL_DURATION_BOUNDS["ltx25"] == (6, 20), "LTX-2.5 native cap 20")
check(app.duration_plan("ltx25", 15)["segments"] == [15], "LTX-2.5 native 15")
check(app.MODEL_DURATION_BOUNDS["veo3"] == (4, 8), "Veo native cap 8")
check(all(s in (4, 6, 8) for s in app.duration_plan("veo3", 10)["segments"]),
      "Veo chains only its native steps")

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

if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL SCOPE CHECKS PASSED")
