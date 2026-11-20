"""Local checks for the Cloudflare Workers AI free image
route (2026-11-20).

Jack approved wiring Cloudflare's free image tier as two
picker lanes: FLUX.2 Klein 4B and FLUX.1 Schnell, raw cost
$0 inside the account's 10,000 free daily neurons, sitting
in the image picker's Free Models group. The account is on
Workers PAID (plan checked 2026-11-20), where usage past
the free allowance would bill, so the platform's own neuron
ledger is the only thing between the route and a bill:
these checks pin the lanes, the estimates, and the cap
behavior (reserve, settle to the metered figure, release,
and a hard stop at the budget). The cap defs are extracted
from app.py and exec'd against a plain dict standing in for
the meta store, the way check_free_counter proves its
rules. No Modal calls, no network, no renders.

Run:  modal-venv python scratch/check_cloudflare_images.py
"""
import ast
import math
import re
import sys
import time
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


# --- the lanes ---------------------------------------------------
KLEIN = pricing.IMAGE_LANES.get("flux2_klein4b") or {}
SCHNELL = pricing.IMAGE_LANES.get("flux1_schnell") or {}
check(KLEIN.get("provider") == "cloudflare",
      "FLUX.2 Klein 4B rides the cloudflare provider")
check(KLEIN.get("endpoint") == "@cf/black-forest-labs/flux-2-klein-4b",
      "Klein endpoint is the Cloudflare model id")
check(KLEIN.get("label") == "FLUX.2 Klein 4B",
      "Klein picker label is its plain name")
check(KLEIN.get("free") is True, "Klein carries the free flag")
check(KLEIN.get("picker_order") == 1, "Klein is the first free lane")
check(all(v == 0.0 for v in KLEIN.get("raw_cents", {}).values())
      and len(KLEIN.get("raw_cents", {})) >= 1,
      "Klein raw cost is $0 at every size")
check(SCHNELL.get("provider") == "cloudflare",
      "FLUX.1 Schnell rides the cloudflare provider")
check(SCHNELL.get("endpoint") == "@cf/black-forest-labs/flux-1-schnell",
      "Schnell endpoint is the Cloudflare model id")
check(SCHNELL.get("label") == "FLUX.1 Schnell",
      "Schnell picker label is its plain name")
check(SCHNELL.get("free") is True, "Schnell carries the free flag")
check(SCHNELL.get("picker_order") == 2, "Schnell is the second free lane")
check(SCHNELL.get("raw_cents") == {"1024x1024": 0.0},
      "Schnell sells only 1024x1024, the one size its API makes")
raw, debit = pricing.image_lane_price("flux2_klein4b", "1024x1024")
check(raw == 0.0 and debit == 0.0,
      "Klein prices at exactly $0 raw and $0 customer")
raw, debit = pricing.image_lane_price("flux1_schnell", "1024x1024",
                                      owner=True)
check(raw == 0.0 and debit == 0.0, "Schnell owner price is $0 too")
check(not KLEIN.get("uncensored") and not SCHNELL.get("uncensored"),
      "the free lanes are standard censored models, not uncensored")

# --- the picker class ---------------------------------------------
check(app.image_picker_class("flux2_klein4b") == "free",
      "Klein groups as free in the image picker")
check(app.image_picker_class("flux1_schnell") == "free",
      "Schnell groups as free in the image picker")
check(app.image_picker_price_cents("flux2_klein4b") == 0.0
      and app.image_picker_price_cents("flux1_schnell") == 0.0,
      "both free lanes sort at a $0 picker price")
check(app.PROVIDER_KEY_ENV.get("cloudflare") == "CLOUDFLARE_API_TOKEN",
      "the cloudflare key env name follows the provider key pattern")

# --- the budget constants ------------------------------------------
for name, want in (("CF_DAILY_FREE_NEURONS", "10_000.0"),
                   ("CF_NEURON_SAFETY_MARGIN", "500.0")):
    m = re.search(rf"{name} = ({re.escape(want)})", SRC)
    check(bool(m), f"{name} is {want}")
check("CF_NEURON_BUDGET = CF_DAILY_FREE_NEURONS - CF_NEURON_SAFETY_MARGIN"
      in SRC, "the dispatch budget is the allowance minus the margin")

# --- behavior: the extracted cap defs ------------------------------
ns = {"meta": {}, "time": time, "math": math}
for const in ("CF_DAILY_FREE_NEURONS", "CF_NEURON_SAFETY_MARGIN",
              "CF_NEURON_BUDGET", "CF_SCHNELL_STEPS"):
    m = re.search(rf"^    {const} = (.+)$", SRC, re.M)
    check(bool(m), f"{const} assignment found")
    ns[const] = eval(m.group(1), dict(ns))
for name in ("_cf_neuron_day_key", "_cf_neurons_used",
             "_cf_neuron_estimate", "_cf_neuron_budget_allows",
             "_cf_neuron_reserve", "_cf_neuron_settle",
             "_cf_neuron_release"):
    src = func_src(name)
    check(bool(src), f"{name} exists in app.py")
    exec(src, ns)

estimate = ns["_cf_neuron_estimate"]
reserve = ns["_cf_neuron_reserve"]
settle = ns["_cf_neuron_settle"]
release = ns["_cf_neuron_release"]
allows = ns["_cf_neuron_budget_allows"]
meta = ns["meta"]

check(abs(estimate("flux2_klein4b", "1024x1024") - 104.2) < 1e-9,
      "Klein 1024x1024 estimates 104.2 neurons (4 tiles at 26.05)")
check(abs(estimate("flux2_klein4b", "512x512") - 104.2) < 1e-9,
      "Klein estimates carry the four tile floor the probes metered")
check(abs(estimate("flux2_klein4b", "1024x768") - 104.2) < 1e-9,
      "Klein 1024x768 estimates 104.2 neurons")
check(abs(estimate("flux1_schnell", "1024x1024") - 172.8) < 1e-9,
      "Schnell 1024x1024 estimates 172.8 neurons (tiles x (4.80 + 9.60 x 4 steps))")

# Reserve right up to the budget; the next reservation must
# refuse and leave the ledger untouched.
est = estimate("flux2_klein4b", "1024x1024")
taken = 0
while reserve(est):
    taken += 1
    if taken > 1000:
        break
used_at_cap = ns["_cf_neurons_used"]()
check(used_at_cap <= ns["CF_NEURON_BUDGET"],
      "reservations never cross the budget")
check(used_at_cap + est > ns["CF_NEURON_BUDGET"],
      "the ledger stops within one image of the budget")
check(not allows(est), "the budget refuses past the cap")
before = ns["_cf_neurons_used"]()
check(reserve(est) is False and ns["_cf_neurons_used"]() == before,
      "a refused reservation leaves the ledger untouched")

# Settle corrects a reservation to the metered figure;
# release gives a reservation back in full.
meta.clear()
check(reserve(est), "a fresh day reserves again")
settle(est, 100.0)
check(abs(ns["_cf_neurons_used"]() - 100.0) < 1e-9,
      "settle corrects the reservation to the metered neurons")
check(reserve(est), "a second reservation lands")
release(est)
check(abs(ns["_cf_neurons_used"]() - 100.0) < 1e-9,
      "release gives a reservation back in full")

# --- source pins: the create flow and the submit -------------------
create_src = func_src("create_image")
check("free_lane = bool(lane.get(\"free\"))" in create_src,
      "create_image reads the lane's free flag")
check("if not free_lane and not is_paid(user):" in create_src,
      "the paid accounts gate skips free lanes")
check("served_free" in create_src
      and "count_generation(user, free_job=served_free)" in create_src,
      "a free lane serving a free account counts as a free tier job")
check("_cf_neuron_budget_allows" in create_src
      and create_src.index("_cf_neuron_budget_allows")
      < create_src.index("jobs[job_id] = job"),
      "the neuron budget is checked before any job is created")
submit_src = func_src("_submit_provider_image")
check('provider == "cloudflare"' in submit_src
      and "_submit_cloudflare_image" in submit_src,
      "the provider submit dispatches cloudflare to its own submit")
cf_src = func_src("_submit_cloudflare_image")
check("_cf_neuron_reserve(estimate)" in cf_src,
      "the cloudflare submit reserves neurons before the call")
check("cf-ai-neurons" in cf_src and "_cf_neuron_settle" in cf_src,
      "the cloudflare submit settles to the metered neuron header")
check("_cf_neuron_release(estimate)" in cf_src,
      "a clean refusal releases the reservation")
check("CLOUDFLARE_API_TOKEN" in cf_src
      and "CLOUDFLARE_ACCOUNT_ID" in cf_src,
      "the cloudflare submit reads its key and account from the secret env")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_cloudflare_images")
