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

# -- 2. Length plans (per-model native lists, 2026-11-17) ----------------
# The picker lists each model's REAL native lengths (every whole
# second in a maker's range, or exactly its steps), plus the
# universal long lengths (45/60/90/120) past its maximum where an
# exact segment composition exists. Every offered length plans
# EXACTLY: native segments sum to the ordered length, nothing is
# trimmed, nothing over-generates. Anything else is refused.
table = {}
PROBES = [1, 2, 3, 7, 9, 11, 20, 33, 44]
for model in app.PER_SECOND_MODELS:
    natives = app.native_lengths(model)
    native_min, native_max = natives[0], natives[-1]
    table[model] = (native_min, native_max)
    offered = app.offered_lengths(model)
    check(offered == sorted(offered) and offered,
          f"{model}: offered lengths empty or unsorted {offered}")
    check(offered[:len(natives)] == natives,
          f"{model}: offered list does not start with its natives")
    check(all(L in (45, 60, 90, 120) for L in offered[len(natives):]),
          f"{model}: a non-universal length past the native maximum")
    for length in sorted(set(natives) | set(app.UNIVERSAL_LONG_LENGTHS)
                         | set(PROBES)):
        try:
            plan = app.duration_plan(model, length)
        except ValueError:
            check(length not in offered,
                  f"{model} {length}s: refused but still offered")
            continue
        check(length in offered,
              f"{model} {length}s: planned but not offered")
        segs = plan["segments"]
        check(all(s in natives for s in segs),
              f"{model} {length}s: segment not a native length {segs}")
        check(plan["generated_seconds"] == sum(segs) == length,
              f"{model} {length}s: generated != ordered length "
              f"({plan['generated_seconds']})")
        check("trim" not in plan, f"{model} {length}s: plan still trims")
        check(plan["chained"] == (len(segs) > 1),
              f"{model} {length}s: chained flag wrong")
        if length in natives:
            check(segs == [length],
                  f"{model} {length}s: native length not a single pass")

try:
    app.duration_plan("veo3", 7)
    check(False, "duration_plan accepted a non-native length (7s on Veo)")
except ValueError:
    pass

try:
    app.duration_plan("wan3", 33)
    check(False, "duration_plan accepted a non-universal long length (33s)")
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
print(f"universal long lengths: {list(app.UNIVERSAL_LONG_LENGTHS)}")
if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
for model in app.PER_SECOND_MODELS:
    nmin, nmax = table[model]
    plans = {L: app.duration_plan(model, L)["segments"]
             for L in app.offered_lengths(model)}
    print(f"PLAN {model} native {nmin}-{nmax} :: "
          + " | ".join(f"{L}s={plans[L]}" for L in plans))
