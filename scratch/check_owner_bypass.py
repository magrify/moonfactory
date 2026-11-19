"""Local checks for the 2026-11-17 owner rule and the $1 pack.

Jack's rule: the owner account answers to the provider only. His
use is never blocked by his balance or by the net paid cost
ceiling, on any job creation path, and that is for his account
only; every other account keeps the existing rules exactly.

No Modal, no network, no generation, no Stripe charge: imports
pricing, execs the extracted is_owner / ceiling_allows defs from
app.py against plain dicts, and asserts the owner guard placement
in each job creation path's source.

Run:  modal-venv python scratch/check_owner_bypass.py
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


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the $1 pack ----------------------------------------------------------

check(100 in pricing.TOPUP_PACKS_CENTS,
      f"$1 pack missing from TOPUP_PACKS_CENTS: {pricing.TOPUP_PACKS_CENTS}")
check(pricing.TOPUP_PACKS_CENTS == [100, 500, 1000, 2000],
      f"pack list drifted: {pricing.TOPUP_PACKS_CENTS}")
pub = pricing.public_pricing()
check(pub["topup_packs_cents"] == [100, 500, 1000, 2000],
      "public pricing does not expose the $1 pack")
check('data-topup="100"' in SRC, "Billing panel has no Top up $1 button")
check('<option value="100">$1</option>' in SRC,
      "auto reload amount picker has no $1 option")

# -- ceiling_allows, exercised for real -----------------------------------

ns = {}
exec(func_src("is_owner"), ns)
exec(func_src("ceiling_allows"), ns)
ceiling_allows = ns["ceiling_allows"]

owner = {"is_owner": True, "net_paid_cents": 0.0, "cost_settled_cents": 0.0}
check(ceiling_allows(owner, 10**9) is True,
      "owner blocked by the ceiling at zero paid in")
owner_rich_use = {"is_owner": True, "net_paid_cents": 100.0,
                  "cost_settled_cents": 10**9}
check(ceiling_allows(owner_rich_use, 10**9) is True,
      "owner blocked by the ceiling past his settled cost")
plain = {"net_paid_cents": 0.0, "cost_settled_cents": 0.0}
check(ceiling_allows(plain, 100.0) is False,
      "non owner with nothing paid in passed the ceiling")
plain_ok = {"net_paid_cents": 500.0, "cost_settled_cents": 100.0}
check(ceiling_allows(plain_ok, 400.0) is True,
      "non owner inside his ceiling was refused")
check(ceiling_allows(plain_ok, 400.01) is False,
      "non owner past his ceiling was allowed")
check(ceiling_allows({}, 1.0) is False,
      "a user dict with no fields passed the ceiling")

# -- per path guard placement ---------------------------------------------
# Per second lanes: the owner hold branch sits before the auto
# reload branch, so a reload can never fire for the owner.
body = func_src("create_api_job")
check("elif is_owner(user):" in body, "create_api_job has no owner branch")
check(body.index("elif is_owner(user):")
      < body.index("elif attempt_auto_reload"),
      "create_api_job reaches auto reload before the owner branch")
check("if not ceiling_allows(user, est[\"cap_raw_cents\"]):" in body,
      "create_api_job lost its ceiling check")

# House lanes: reload attempt and refusal wrapped in not is_owner,
# the charge itself unconditional.
body = func_src("create_job")
check("if not is_owner(user):" in body,
      "house lane create has no owner guard")
check(body.index("if not is_owner(user):") < body.index("charged = price"),
      "house lane charge is not after the owner guard")

# Character training: same wrap.
body = func_src("train_character")
check("if not is_owner(user):" in body,
      "character training has no owner guard")
check(body.index("if not is_owner(user):")
      < body.index("attempt_auto_reload"),
      "character training reload attempt is not inside the owner guard")

# Provider image shelf: same wrap on the balance refusal.
body = func_src("create_image")
check("if not is_owner(user):" in body,
      "image create has no owner guard")
check("if not ceiling_allows(user, pricing.IMAGE_MEASURED_COST_CENTS):"
      in body, "house image ceiling check missing")

# Image payment rail names balance for the owner.
body = func_src("_image_payment")
check("or is_owner(user)" in body, "_image_payment has no owner rail")

# Clips: owner hold branch before the auto reload branch, and the
# quote names the balance rail for the owner.
body = func_src("create_clip")
check("elif is_owner(user):" in body, "clip create has no owner branch")
check(body.index("elif is_owner(user):")
      < body.index("elif attempt_auto_reload"),
      "clip create reaches auto reload before the owner branch")
body = func_src("_clip_quote_for")
check("or is_owner(user)" in body, "clip quote has no owner rail")

# Per second quote names the balance rail for the owner.
body = func_src("_quote_for")
check("or is_owner(user)" in body, "per second quote has no owner rail")

# Auto reload itself still refuses amounts outside the pack list,
# which now includes $1.
body = func_src("attempt_auto_reload")
check("amount not in pricing.TOPUP_PACKS_CENTS" in body,
      "auto reload lost its pack validation")

# Pricing rule pins (Jack's restatement, 2026-11-19): the owner
# pays raw cost on every per-second lane with no markup floor;
# no customer sells below raw cost; a lane whose raw cost meets
# or exceeds Higgsfield's price (at_cost_residual) sells at the
# cheapest non-losing figure, raw cost grossed up by the Stripe
# percentage fee only; a lane dropped to cost to undercut
# (at_cost_undercut) sells at exactly raw cost.
for _model, _tiers in pricing.PROVIDER_RATES_CENTS_PER_SECOND.items():
    for _res in _tiers:
        _f = pricing.frontier_lane(_model, _res)
        _o = pricing.frontier_lane(_model, _res, owner=True)
        check(abs(_o["sell_cents_per_second"]
                  - _f["cost_cents_per_second"]) < 0.0011,
              f"owner sell != raw cost on {_model} {_res}: "
              f"{_o['sell_cents_per_second']} vs "
              f"{_f['cost_cents_per_second']}")
        check(_f["sell_cents_per_second"]
              >= _f["cost_cents_per_second"] - 0.0011,
              f"customer sell below raw cost on {_model} {_res}")
        _hf = _f["higgsfield_effective_cents_per_second"]
        if _hf is not None and _f["pricing_basis"] == "standard":
            check(_f["sell_cents_per_second"] < _hf,
                  f"standard lane not under Higgsfield on "
                  f"{_model} {_res}")
        if _f["pricing_basis"] == "at_cost_residual":
            _want = pricing._ceil3(
                _f["cost_cents_per_second"] / (1 - pricing.STRIPE_PCT))
            check(abs(_f["sell_cents_per_second"] - _want) < 0.0011,
                  f"residual sell not fee-grossed cost on "
                  f"{_model} {_res}: {_f['sell_cents_per_second']} "
                  f"vs {_want}")
            check(_f["sell_cents_per_second"]
                  * (1 - pricing.STRIPE_PCT)
                  >= _f["cost_cents_per_second"] - 0.0011,
                  f"residual sell loses money after fees on "
                  f"{_model} {_res}")
        if _f["pricing_basis"] == "at_cost_undercut":
            check(abs(_f["sell_cents_per_second"]
                      - _f["cost_cents_per_second"]) < 0.0011,
                  f"undercut lane not at raw cost on "
                  f"{_model} {_res}")

if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL OWNER BYPASS CHECKS PASSED")
