"""Local checks for the 2026-11-17 menu + length fix. No Modal,
no network, no generation: imports the platform modules and
exercises the pure functions the live routes use.

Run:  modal-venv python scratch/check_menu_lengths.py
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


def lowest_sell(model_id):
    if model_id == app.DEFAULT_MODEL:
        return pricing.JOB_PRICE_CENTS["production"] / 5.0
    rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(model_id) or {}
    sells = []
    for res in rates:
        try:
            sells.append(
                pricing.frontier_lane(model_id, res)["sell_cents_per_second"])
        except Exception:
            pass
    return min(sells) if sells else None


# -- 1. Menu grouping -------------------------------------------------
order = app.grouped_model_order(app.MODELS, lowest_sell)
ids = [m["id"] for m in order]
check(len(ids) == len(app.MODELS), "grouped order lost a model")
check(len(set(ids)) == len(ids), "grouped order duplicated a model")

by_label = {}
for pos, m in enumerate(order):
    by_label.setdefault(m["label"], []).append(pos)
for label, positions in by_label.items():
    check(positions == list(range(positions[0], positions[0] + len(positions))),
          f"group {label} is not contiguous: {positions}")

group_rates = []
for label, positions in by_label.items():
    rates = [lowest_sell(order[p]["id"]) for p in positions]
    priced = [r for r in rates if r is not None]
    group_rates.append(min(priced) if priced else None)
priced_groups = [r for r in group_rates if r is not None]
check(priced_groups == sorted(priced_groups),
      f"priced groups not ascending by cheapest member: {group_rates}")
none_seen = False
for r in group_rates:
    if r is None:
        none_seen = True
    check(not (none_seen and r is not None),
          "a priced group sits after an unpriced group")

for label, positions in by_label.items():
    names = [app.model_display_name(order[p]) for p in positions]
    check(names == sorted(names),
          f"group {label} not alphabetical by display name: {names}")

pairs = [("wan3", "wan3_unc"), ("seedance", "seedance25_unc"),
         ("minimax_h3", "minimax_h3_unc"), ("vidu_q3", "vidu_q3_unc"),
         ("wan2.2", "wan22_unc")]
for base, unc in pairs:
    check(abs(ids.index(base) - ids.index(unc)) == 1,
          f"{base} and {unc} are not adjacent")

# -- 2. Length plans ----------------------------------------------------
table = {}
for model in app.PER_SECOND_MODELS:
    low, high = app.MODEL_DURATION_BOUNDS.get(model, (3, 30))
    steps = app.MODEL_DURATION_STEPS.get(model)
    native_min = min(steps) if steps else low
    native_max = max(steps) if steps else high
    table[model] = (native_min, native_max)
    for length in app.LENGTH_OPTIONS:
        plan = app.duration_plan(model, length)
        segs = plan["segments"]
        check(all(isinstance(s, int) and s > 0 for s in segs),
              f"{model} {length}s: bad segments {segs}")
        if steps:
            check(all(s in steps for s in segs),
                  f"{model} {length}s: segment not a native step {segs}")
        else:
            check(all(low <= s <= high for s in segs),
                  f"{model} {length}s: segment outside native range {segs}")
        check(plan["generated_seconds"] == sum(segs),
              f"{model} {length}s: generated != sum(segments)")
        check(plan["generated_seconds"] >= length,
              f"{model} {length}s: generated below requested")
        check(plan["generated_seconds"] - length < native_max,
              f"{model} {length}s: over-generation {plan['generated_seconds']}")
        check(plan["chained"] == (len(segs) > 1),
              f"{model} {length}s: chained flag wrong")
        if length <= native_max and length >= native_min and not steps:
            check(segs == [length] and not plan["trim"],
                  f"{model} {length}s: in-range length not a single native pass")

try:
    app.duration_plan("wan3", 7)
    check(False, "duration_plan accepted a non-menu length (7s)")
except ValueError:
    pass

# -- 3. Pricing follows the generated seconds ---------------------------
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
for model, res in (("wan3", "720p"), ("kling4", "720p"),
                   ("seedance", "720p")):
    plan120 = app.duration_plan(model, 120)
    est_full = pricing.per_second_estimate(
        model, res, plan120["generated_seconds"], 0.0, rates)
    est_first_seg_only = pricing.per_second_estimate(
        model, res, plan120["segments"][0], 0.0, rates)
    check(est_full["billed_seconds"] == plan120["generated_seconds"],
          f"{model}: billed seconds != generated seconds")
    check(est_full["cap_raw_cents"] > est_first_seg_only["cap_raw_cents"],
          f"{model}: ceiling cap does not cover the full chain")
    check(est_full["balance_debit_cents"]
          > est_first_seg_only["balance_debit_cents"],
          f"{model}: full chain not priced above its first segment")

print(f"models checked: {len(app.MODELS)} "
      f"({len(app.PER_SECOND_MODELS)} per-second lanes)")
print(f"lengths per model: {list(app.LENGTH_OPTIONS)}")
if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
for model in app.PER_SECOND_MODELS:
    nmin, nmax = table[model]
    plans = {L: app.duration_plan(model, L)["segments"]
             for L in app.LENGTH_OPTIONS}
    print(f"PLAN {model} native {nmin}-{nmax} :: "
          + " | ".join(f"{L}s={plans[L]}" for L in app.LENGTH_OPTIONS))
