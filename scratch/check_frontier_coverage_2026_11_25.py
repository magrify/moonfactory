"""Local checks for the 2026-11-25 frontier coverage pass.

Pins the pass's three changes so a later edit cannot quietly
break them:

- Veo 3.1 (Quality) rerouted from PiAPI to Kie: PiAPI is out
  under Jack's no-expiring-credits rule, and Kie bills the
  model flat per clip with native audio ($1.25 / $1.275 /
  $1.85 per clip at 720p / 1080p / 4K). The governing cost
  lives in FLAT_CLIP_COST_CENTS, the rate table carries only
  the worst-case per-second ceiling (the flat cost at the
  4 s shortest length), the displaced PiAPI rates are
  recorded, fal's with-audio rates stay the fallback, and
  the lane's audio record now says native speech/audio.
- Veo 3.1 Fast rerouted from fal's per-second route to the
  same Kie flat per-clip pricing ($0.30 / $0.325 / $0.90 per
  clip), a quarter of fal's 720p per-second cost at native
  lengths; fal's rates move to the fallback table.
- GPT Image 2.6 Sunburst wired as its own image lane beside
  the Flare lane (which now names Flare in its label): fal
  serves both 2.6 tiers on OpenAI's identical token card, so
  the Sunburst lane carries the same measured raw figure on
  the same single size; the standard x1.25 customer markup
  applies and the owner pays raw.

Both Veo submits ride Kie's unified veo-3-1 jobs endpoint
with the tier inside the input and stay PAYLOAD-UNVERIFIED
until a real job runs on the funded Kie balance.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_frontier_coverage_2026_11_25.py
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


rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND

# -- Veo 3.1 Fast on Kie; Quality moved to UnificAlly ------------------
# Veo 3.1 (Quality) left Kie in the 2026-11-27 UnificAlly
# catalog audit (flat $1.20 / $1.225 / $1.80 under Kie's
# $1.25 / $1.275 / $1.85; the Kie ceilings are recorded as
# the displaced veo3_kie row). It is pinned separately
# below the loop. Veo 3.1 Fast stays on Kie: UnificAlly's
# Fast prices are identical, so there was nothing to win.

for model, label, flat, ceiling in (
        ("veo3_fast", "Veo 3.1 Fast",
         {"720p": 30.0, "1080p": 32.5, "4K": 90.0},
         {"720p": 7.5, "1080p": 8.125, "4K": 22.5})),:
    entry = next(m for m in app.MODELS if m["id"] == model)
    check(entry.get("label") == label,
          f"{model} label is not its own name {label}")
    check(entry.get("paid_only") is True,
          f"{model} is not paid_only (an API-cost lane is never free)")
    check(app.MODEL_PROVIDER.get(model) == "kie",
          f"{model} does not route to kie")
    for res in flat:
        check(app.provider_for(model, res) == "kie",
              f"provider_for does not resolve {model} {res} to kie")
    check(pricing.FLAT_CLIP_COST_CENTS.get(model) == flat,
          f"{model} flat clip costs drifted from Kie's published card")
    check(rates.get(model) == ceiling,
          f"{model} rate-table ceiling is not the flat cost at 4 s")
    check(pricing.FLAT_CLIP_STEPS.get(model) == (4, 6, 8),
          f"{model} flat clip steps drifted from (4, 6, 8)")
    check(pricing.flat_clip_segments(model, 60) == 8,
          f"{model} 60s does not compose to 8 flat generations")
    lane = pricing.frontier_lane(model, "720p", rates)
    check(lane["pricing_basis"] == "standard",
          f"{model} 720p does not resolve on the standard basis")
    check(lane["can_undercut"] is True,
          f"{model} 720p no longer undercuts Higgsfield's figure")
    check(lane.get("flat_clip_cost_cents") == flat["720p"]
          and lane.get("cost_basis") == "flat_per_clip",
          f"{model} frontier picture lost its flat fields")

# -- Veo 3.1 (Quality) on UnificAlly (2026-11-27) --------------
entry = next(m for m in app.MODELS if m["id"] == "veo3")
check(entry.get("label") == "Veo 3.1",
      "veo3 label is not its own name Veo 3.1")
check(entry.get("paid_only") is True,
      "veo3 is not paid_only (an API-cost lane is never free)")
check(app.MODEL_PROVIDER.get("veo3") == "unifically",
      "veo3 does not route to unifically")
for res in ("720p", "1080p", "4K"):
    check(app.provider_for("veo3", res) == "unifically",
          f"provider_for does not resolve veo3 {res} to unifically")
check(pricing.FLAT_CLIP_COST_CENTS.get("veo3")
      == {"720p": 120.0, "1080p": 122.5, "4K": 180.0},
      "veo3 flat clip costs drifted from UnificAlly's card")
check(rates.get("veo3")
      == {"720p": 30.0, "1080p": 30.625, "4K": 45.0},
      "veo3 rate-table ceiling is not the flat cost at 4 s")
check(pricing.FLAT_CLIP_STEPS.get("veo3") == (4, 6, 8),
      "veo3 flat clip steps drifted from (4, 6, 8)")
check(pricing.flat_clip_segments("veo3", 60) == 8,
      "veo3 60s does not compose to 8 flat generations")
lane = pricing.frontier_lane("veo3", "720p", rates)
check(lane["pricing_basis"] == "standard",
      "veo3 720p does not resolve on the standard basis")
check(lane["can_undercut"] is True,
      "veo3 720p no longer undercuts Higgsfield's figure")
check(lane.get("flat_clip_cost_cents") == 120.0
      and lane.get("cost_basis") == "flat_per_clip",
      "veo3 frontier picture lost its flat fields")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("veo3_kie")
      == {"720p": 31.25, "1080p": 31.875, "4K": 46.25},
      "displaced Kie ceilings for veo3 are not recorded")

check("veo3" not in app.MODEL_PROVIDER_OVERRIDES,
      "veo3 still carries a per-resolution provider override")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("veo3_piapi")
      == {"720p": 12.0, "1080p": 12.0},
      "displaced PiAPI rates for veo3 are not recorded")
check(pricing.FALLBACK_PROVIDER_RATES_CENTS_PER_SECOND.get("veo3")
      == {"720p": 40.0, "1080p": 40.0, "4K": 60.0},
      "fal fallback rates for veo3 drifted")
check(pricing.FALLBACK_PROVIDER_RATES_CENTS_PER_SECOND.get("veo3_fast")
      == {"720p": 15.0, "1080p": 15.0, "4K": 35.0},
      "fal fallback rates for veo3_fast are not recorded")

# The quote prices the clip, not the second.
est = pricing.per_second_estimate("veo3", "720p", 8, 0.0, rates)
check(est["raw_cost_cents"] == 120.08,
      "veo3 720p 8s raw is not the flat cost plus cut post")
check(est["balance_debit_cents"] == 151,
      "veo3 720p 8s customer debit is not ceil(120.8 x 1.25)")
est = pricing.per_second_estimate("veo3_fast", "720p", 8, 0.0, rates)
check(est["raw_cost_cents"] == 30.08,
      "veo3_fast 720p 8s raw is not the flat cost plus cut post")
check(est["balance_debit_cents"] == 39,
      "veo3_fast 720p 8s customer debit is not ceil(30.8 x 1.25)")
owner_est = pricing.per_second_estimate(
    "veo3_fast", "720p", 8, 0.0, rates, owner=True)
check(owner_est["balance_debit_cents"] == 31,
      "veo3_fast 720p 8s owner debit is not the raw cost path")

audio = app.LANE_AUDIO.get("veo3") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "veo3 audio record lost its native speech/audio")

# -- the Kie Veo submit --------------------------------------------------

check("_submit_kie_veo" in func_src("submit_kie_job"),
      "submit_kie_job does not dispatch veo3/veo3_fast to the Kie Veo submit")
submit = func_src("_submit_kie_veo")
check(bool(submit), "_submit_kie_veo not found")
for token in ('"veo-3-1"', "KIE_VEO_TIERS", '"generation_type"',
              '"FIRST_AND_LAST_FRAMES_2_VIDEO"', '"REFERENCE_2_VIDEO"',
              '"TEXT_2_VIDEO"', '"image_urls"', '"resolution"', '"Auto"',
              "_beatapi_media_url", "_submit_failed",
              "PAYLOAD-UNVERIFIED"):
    check(token in submit, f"_submit_kie_veo missing {token}")

# -- GPT Image 2.6 Sunburst ----------------------------------------------

lane = pricing.IMAGE_LANES.get("gpt_image_26_sunburst") or {}
check(lane.get("label") == "GPT Image 2.6 Sunburst · $$$",
      "gpt_image_26_sunburst label is not its own name")
check(lane.get("provider") == "fal",
      "gpt_image_26_sunburst does not ride fal")
check(lane.get("endpoint")
      == "openai/gpt-image-2.6/sunburst/text-to-image",
      "gpt_image_26_sunburst endpoint drifted")
check(lane.get("raw_cents") == {"1024x1024": 5.3},
      "gpt_image_26_sunburst raw cost drifted from the measured card")
check(pricing.image_lane_charge_cents(
    "gpt_image_26_sunburst", "1024x1024") == 7,
    "gpt_image_26_sunburst customer charge is not the x1.25 round-up")
check(pricing.image_lane_charge_cents(
    "gpt_image_26_sunburst", "1024x1024", owner=True) == 5,
    "gpt_image_26_sunburst owner charge is not the raw cost")
check(pricing.IMAGE_LANES["gpt_image_26"]["label"]
      == "GPT Image 2.6 Flare · $$$",
      "the 2.6 Flare lane no longer names Flare beside its Sunburst pair")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_frontier_coverage_2026_11_25")
