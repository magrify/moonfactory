"""Local checks for the 2026-11-27 debit-level undercut fix
(the owner's beat-Higgsfield rule: beat Higgsfield at every
price unless that leaves no profit, ideally at least 25%
profit over cost).

Background: the 2026-11-26 competitive teardown recorded
that on Wan 3.0 480p and 720p, Wan 3.0 Prime, and Seedance
2.5 480p, the whole-cent debit a customer actually pays
could round at or above Higgsfield's figure even where the
published per-second rate undercut it, so the undercut
claim failed at the debit level. Re-measurement against
the sold duration ranges (this check reads them out of
app.py's own MODEL_DURATION_BOUNDS / MODEL_DURATION_STEPS)
showed two distinct states:

- Wan 3.0 Prime (sells 3 to 30 seconds) and Seedance 2.5
  480p (4 to 30 seconds) only crossed at a 1-second debit,
  a duration neither lane sells; at every sold duration
  their debits already undercut, with margins over true
  cost above 160% and 300%. Their prices are unchanged
  here and pinned below so they stay that way.
- Wan 3.0 480p and 720p already undercut at every sold
  duration on the old at-cost basis (sell = frozen shelf
  cost), but the margin over their TRUE rerouted cost sat
  at 19.8% to 24.8%, under the owner's 25% target at
  nearly every duration. frontier_lane's undercut branch
  now sells them at the debit-safe rate: the largest whole
  cent strictly under Higgsfield's per-second figure
  (4.0 and 8.0 cents per second), which keeps
  ceil(rate x d) under Higgsfield's total at EVERY
  duration while lifting the worst-case margin over true
  cost to about 33%.

What is pinned:

- For each of the four cells (Prime at all three of its
  tiers), the ACTUAL balance debit from the shared pricing
  path (pricing.per_second_balance_debit_cents, the same
  function quote, create and settle all use) is strictly
  under Higgsfield's effective per-second figure times the
  duration, at every duration the lane sells, including
  the chained 45/60/90/120 second lengths.
- The same debit keeps at least 25% profit over the
  lane's true cost (pricing.per_second_lane_cost_cents)
  at every sold duration on all four cells.
- The two adjusted cells sell at exactly 4.0 / 8.0 cents
  per second on the debit_safe_undercut basis; the two
  verified cells keep their existing sell rates on the
  standard basis; and no other lane in the catalog sits
  on the debit_safe_undercut basis, so the branch change
  moved nothing else.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_debit_undercut.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def _literal_dicts(source, names):
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    return found


_app_src = (ROOT / "platform" / "app.py").read_text()
_dicts = _literal_dicts(
    _app_src, {"MODEL_DURATION_BOUNDS", "MODEL_DURATION_STEPS"})
_bounds = _dicts["MODEL_DURATION_BOUNDS"]
_steps = _dicts["MODEL_DURATION_STEPS"]
CHAINED = (45, 60, 90, 120)


def sold_durations(model):
    if model in _steps:
        base = list(_steps[model])
    else:
        low, high = _bounds.get(model, (3, 30))
        base = list(range(int(low), int(high) + 1))
    return sorted(set(base) | set(CHAINED))


CELLS = [
    ("wan3", "480p"),
    ("wan3", "720p"),
    ("wan3_prime", "480p"),
    ("wan3_prime", "720p"),
    ("wan3_prime", "1080p"),
    ("seedance", "480p"),
]

for model, res in CELLS:
    pic = pricing.frontier_lane(model, res)
    higgs = pic["higgsfield_effective_cents_per_second"]
    check(higgs is not None,
          f"{model} {res}: no Higgsfield figure to undercut")
    cost = pricing.per_second_lane_cost_cents(model, res)
    for d in sold_durations(model):
        debit = pricing.per_second_balance_debit_cents(model, res, d)
        check(debit < higgs * d,
              f"{model} {res} at {d}s: debit {debit}c not under "
              f"Higgsfield {higgs * d:.2f}c")
        margin = (debit - cost * d) / (cost * d)
        check(margin >= 0.25,
              f"{model} {res} at {d}s: margin over true cost "
              f"{margin * 100:.1f}% under the 25% target")

# The two adjusted cells: exact new rates and basis.
for model, res, want_sell in (("wan3", "480p", 4.0),
                              ("wan3", "720p", 8.0)):
    pic = pricing.frontier_lane(model, res)
    check(pic["pricing_basis"] == "debit_safe_undercut",
          f"{model} {res}: basis {pic['pricing_basis']} is not "
          f"debit_safe_undercut")
    check(abs(pic["sell_cents_per_second"] - want_sell) < 0.0011,
          f"{model} {res}: sell {pic['sell_cents_per_second']} "
          f"!= debit-safe rate {want_sell}")

# The two verified cells: prices untouched.
for model, res, want_sell in (("wan3_prime", "480p", 14.357),
                              ("wan3_prime", "720p", 14.357),
                              ("wan3_prime", "1080p", 14.357),
                              ("seedance", "480p", 14.308)):
    pic = pricing.frontier_lane(model, res)
    check(pic["pricing_basis"] == "standard",
          f"{model} {res}: basis moved to {pic['pricing_basis']}")
    check(abs(pic["sell_cents_per_second"] - want_sell) < 0.0011,
          f"{model} {res}: sell moved to "
          f"{pic['sell_cents_per_second']} from {want_sell}")

# The branch change touched nothing else: exactly two lanes
# catalog-wide sit on the debit-safe basis.
on_basis = [
    (m, r)
    for m, tiers in pricing.PROVIDER_RATES_CENTS_PER_SECOND.items()
    for r in tiers
    if pricing.frontier_lane(m, r)["pricing_basis"]
    == "debit_safe_undercut"
]
check(sorted(on_basis) == [("wan3", "480p"), ("wan3", "720p")],
      f"debit_safe_undercut lanes are {on_basis}, expected only "
      f"Wan 3.0 480p and 720p")

# The debit pinned above is the one quote, create and settle
# share: app.py routes its per-second quotes through
# pricing.per_second_estimate (which settles through
# per_second_balance_debit_cents) and its balance debit
# through per_second_balance_debit_cents directly.
check(_app_src.count("pricing.per_second_estimate(") >= 3,
      "app.py no longer routes quotes through the shared "
      "per-second estimate")
check("pricing.per_second_balance_debit_cents(" in _app_src,
      "app.py no longer debits through the shared pricing path")

if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL DEBIT UNDERCUT CHECKS PASSED")
