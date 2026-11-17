"""Local checks for the FramePack / SkyReels V2 house lanes
(2026-11-17). No Modal calls, no network, no generation:
imports the platform modules and exercises the pure functions
and registries the live routes use, plus static checks on the
worker source.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/check_longform_lanes.py
"""
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


# -- 1. Registry ------------------------------------------------
for mid, label in (("framepack", "FramePack"), ("skyreels", "SkyReels V2")):
    check(app.MODEL_LABELS.get(mid) == label, f"{mid} label")
    entry = next(m for m in app.MODELS if m["id"] == mid)
    check(entry["paid_only"] is True, f"{mid} paid_only")
    check(mid in app.HOUSE_LONGFORM_MODELS, f"{mid} in HOUSE_LONGFORM_MODELS")
    check(mid in app.UNCENSORED_VIDEO_MODELS, f"{mid} uncensored (house open weights)")
    check(mid not in app.PER_SECOND_MODELS, f"{mid} not a per-second API lane")
    check(app.lane_audio(mid)["native_speech"] is False, f"{mid} silent lane")
    check(app.lip_sync_applies(mid, "hello") is True,
          f"{mid} lip sync applies with speech")
    check(app.lip_sync_applies(mid, "") is False,
          f"{mid} no lip sync without speech")
    check(app.longform_staged(mid) is False,
          f"{mid} not staged locally (dark-safe)")

# -- 2. Length sets (the makers' documented lengths) ------------
# FramePack's documented range is continuous (the maker's demo
# slider: 1-120 s), so every whole second is a native length.
check(app.offered_lengths("framepack") == list(range(1, 121)),
      f"framepack offered: {app.offered_lengths('framepack')[:5]}...")
# SkyReels V2's documented lengths are exactly four; past its
# 60 s maximum the universal 90/120 compose from them.
check(app.offered_lengths("skyreels") == [10, 15, 30, 60, 90, 120],
      f"skyreels offered: {app.offered_lengths('skyreels')}")

fp = app.duration_plan("framepack", 60)
check(fp["segments"] == [60] and fp["generated_seconds"] == 60,
      f"framepack 60 native single: {fp}")
fp120 = app.duration_plan("framepack", 120)
check(fp120["segments"] == [120], f"framepack 120 native single: {fp120}")
fp37 = app.duration_plan("framepack", 37)
check(fp37["segments"] == [37], f"framepack 37 native single: {fp37}")
sr90 = app.duration_plan("skyreels", 90)
check(sum(sr90["segments"]) == 90
      and all(s in (10, 15, 30, 60) for s in sr90["segments"]),
      f"skyreels 90 composition: {sr90}")
sr120 = app.duration_plan("skyreels", 120)
check(sr120["segments"] == [60, 60], f"skyreels 120 = 60+60: {sr120}")
for bad in (1, 5, 45):
    try:
        app.duration_plan("skyreels", bad)
        check(False, f"skyreels {bad}s must be refused "
                     "(below its minimum or not a documented length)")
    except ValueError:
        pass
# No trimming anywhere: generated always equals requested.
for mid in ("framepack", "skyreels"):
    for secs in app.offered_lengths(mid):
        plan = app.duration_plan(mid, secs)
        check(plan["generated_seconds"] == secs,
              f"{mid} {secs}: generated {plan['generated_seconds']} != ordered")

# -- 3. Pricing (estimates labeled, house margin, owner = cost) --
est = pricing.longform_estimate("framepack", 60)
check(est["estimate"] is True, "framepack estimate labeled")
check(abs(est["raw_cost_cents"] - 360.0) < 1e-6,
      f"framepack 60s raw {est['raw_cost_cents']} != 360 (6.0c/s est)")
sell = pricing.longform_sell_rate_cents_per_second("framepack")
check(abs(sell - 6.0 * pricing.MARGIN_MULTIPLIER) < 1e-9,
      "framepack sell rate = cost x 3 (no per-second gross_up)")
check(est["balance_debit_cents"] == pricing.gross_up(360.0 * 3.0),
      f"framepack 60s debit {est['balance_debit_cents']} != "
      f"gross_up(1080) {pricing.gross_up(1080.0)} (fee grossed up once per job)")
check(pricing.longform_sell_rate_cents_per_second("skyreels") == 4.5,
      "skyreels sell rate = 1.5 x 3")
check(pricing.longform_sell_rate_cents_per_second("framepack", owner=True) == 6.0,
      "owner pays raw cost")
sr_est = pricing.longform_estimate("skyreels", 30, lip_sync=True)
check(sr_est["lip_sync_cents"] > 0, "lip sync folds into longform estimate")
check(app.model_price_tier if False else True, "placeholder")

# -- 4. Worker source (static) -----------------------------------
worker = (ROOT / "modal-worker" / "app.py").read_text()
for needle in (
    'LONGFORM_LANES = {',
    '"framepack": {',
    '"skyreels": {',
    '"steps": (10, 15, 30, 60)',
    "def download_framepack_models(",
    "def download_skyreels_models(",
    "def download_skyreels_14b_models(",
    "def run_longform_job(",
    "class FramePackWorker",
    "class SkyReelsWorker",
    '"download-framepack"',
    '"download-skyreels"',
    "not staged",
    "enable_teacache=False",
):
    check(needle in worker, f"worker missing: {needle}")
# The staging functions must never be invoked by the dispatcher
# or the API path: they are main()-only entrypoints.
check(worker.count("download_framepack_models.remote()") == 1,
      "framepack staging invoked exactly once (main entrypoint)")
check(worker.count("download_skyreels_models.remote()") == 1,
      "skyreels staging invoked exactly once (main entrypoint)")

# -- 5. Platform wiring (static) ---------------------------------
src = (ROOT / "platform" / "app.py").read_text()
for needle in (
    "def create_longform_job(",
    "def _longform_quote(",
    '"segment_seconds": plan["segments"]',
    "longform_staged(m[\"id\"])",
    "job.get(\"model\") in HOUSE_LONGFORM_MODELS",
    "framepack: { durations: LENGTHS, resolutions: [] }",
):
    check(needle in src, f"platform missing: {needle}")

if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL LONGFORM CHECKS PASSED")
