"""Local checks for the 2026-11-25 Veo 3.1 Lite reroute to Kie.

Pins the lane's shape so a later edit cannot quietly break it:

- the lane keeps its own name, paid only, and routes to Kie
  at both resolutions (the Eachlabs submit stays wired as
  the spare; its displaced per-second rates are recorded);
- pricing is honest about Kie's flat per-clip billing: the
  governing cost lives in FLAT_CLIP_COST_CENTS ($0.15 per
  720p clip, $0.175 per 1080p clip, any sold length, from
  Kie's own authenticated catalog), the rate table carries
  only the worst-case per-second ceiling (the flat cost at
  the 4 s shortest length), and the quote, the debit and
  the settle all price from the per-clip figure — a
  customer's debit is the same at 4 s and at 8 s, cost x
  1.25 rounded up through the shared path; the owner pays
  raw cost;
- the frontier picture carries the flat fields so the
  pricing payload never presents the per-second ceiling as
  the price of a longer clip, and the lane still undercuts
  Higgsfield's 720p figure on the standard basis;
- durations stay the model's own 4 / 6 / 8 steps, both
  priced resolutions are real picker choices again (the Kie
  submit sends the resolution), and the audio record says
  the lane now carries Veo's native audio;
- the Kie Veo submit follows Kie's current Veo reference
  (unified veo-3-1 model id, tier inside the input,
  generation_type, image_urls), stays PAYLOAD-UNVERIFIED,
  the Kie mirror reads the Veo resultJson nesting, and the
  settle prices flat lanes from the per-clip cost.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_veo3_lite_kie_lane.py
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

# -- picker entry and routing ------------------------------------------

entry = next(m for m in app.MODELS if m["id"] == "veo3_lite")
check(entry.get("label") == "Veo 3.1 Lite",
      "veo3_lite label is not its own name Veo 3.1 Lite")
check(entry.get("paid_only") is True,
      "veo3_lite is not paid_only (an API-cost lane is never free)")
check("veo3_lite" in app.PER_SECOND_MODELS,
      "veo3_lite missing from PER_SECOND_MODELS")
check("veo3_lite" in app.TEXT_IMAGE_ONLY_MODELS,
      "veo3_lite lost its text/image-only shape")
check(app.MODEL_PROVIDER.get("veo3_lite") == "kie",
      "veo3_lite does not route to kie")
check(app.provider_for("veo3_lite", "720p") == "kie",
      "provider_for does not resolve veo3_lite 720p to kie")
check(app.provider_for("veo3_lite", "1080p") == "kie",
      "provider_for does not resolve veo3_lite 1080p to kie")
check(app.PROVIDER_KEY_ENV.get("kie") == "KIE_API_KEY",
      "kie key env missing")

# -- pricing: flat per clip, honestly carried ---------------------------

check(pricing.FLAT_CLIP_COST_CENTS.get("veo3_lite")
      == {"720p": 15.0, "1080p": 17.5},
      "veo3_lite flat clip costs drifted from $0.15 / $0.175")
check(rates.get("veo3_lite") == {"720p": 3.75, "1080p": 4.375},
      "veo3_lite rate-table ceiling is not the flat cost at 4 s")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("veo3_lite_eachlabs")
      == {"720p": 3.0, "1080p": 8.0},
      "displaced Eachlabs rates for veo3_lite are not recorded")
check(pricing.flat_clip_cost_cents("veo3_lite", "720p") == 15.0,
      "flat_clip_cost_cents does not resolve veo3_lite 720p")

lane = pricing.frontier_lane("veo3_lite", "720p", rates)
check(lane["pricing_basis"] == "standard",
      "veo3_lite 720p does not resolve on the standard basis")
check(lane["sell_cents_per_second"] == 4.813,
      "veo3_lite 720p ceiling sell is not (3.75 + 0.1) x 1.25")
check(lane["can_undercut"] is True,
      "veo3_lite 720p no longer undercuts Higgsfield's figure")
check(lane.get("flat_clip_cost_cents") == 15.0
      and lane.get("cost_basis") == "flat_per_clip",
      "veo3_lite frontier picture lost its flat fields")

# The quote prices the clip, not the second: the customer
# debit is the flat cost (plus post) x 1.25 rounded up, the
# same at every sold length.
for seconds, raw_want, debit_want, owner_want in (
        (4, 15.04, 20, 16), (6, 15.06, 20, 16), (8, 15.08, 20, 16)):
    est = pricing.per_second_estimate(
        "veo3_lite", "720p", seconds, 0.0, rates)
    check(est["raw_cost_cents"] == raw_want,
          f"veo3_lite 720p {seconds}s raw is not the flat cost")
    check(est["balance_debit_cents"] == debit_want,
          f"veo3_lite 720p {seconds}s customer debit is not 20")
    owner_est = pricing.per_second_estimate(
        "veo3_lite", "720p", seconds, 0.0, rates, owner=True)
    check(owner_est["balance_debit_cents"] == owner_want,
          f"veo3_lite 720p {seconds}s owner debit is not raw cost")
est = pricing.per_second_estimate("veo3_lite", "1080p", 8, 0.0, rates)
check(est["raw_cost_cents"] == 17.58,
      "veo3_lite 1080p 8s raw is not the flat cost plus cut post")
check(est["balance_debit_cents"] == 23,
      "veo3_lite 1080p customer debit is not ceil(18.3 x 1.25)")
check(pricing.per_second_balance_debit_cents(
    "veo3_lite", "720p", 8, rates) == 20,
    "settle-path debit for veo3_lite 720p 8s is not the flat price")
check(pricing.per_second_balance_debit_cents(
    "veo3_lite", "720p", 8, rates, owner=True) == 16,
    "settle-path owner debit for veo3_lite is not raw cost")

# A chained length is that many separately billed clips: the
# flat cost scales by the fewest-segment composition, the
# same count the app layer's duration plan makes.
check(pricing.flat_clip_segments("veo3_lite", 4) == 1,
      "a single 4s clip is not one flat generation")
check(pricing.flat_clip_segments("veo3_lite", 8) == 1,
      "a single 8s clip is not one flat generation")
check(pricing.flat_clip_segments("veo3_lite", 60) == 8,
      "60s does not compose to 8 flat generations (7x8 + 4)")
check(pricing.flat_clip_segments("veo3_lite", 120) == 15,
      "120s does not compose to 15 flat generations (15x8)")
est = pricing.per_second_estimate("veo3_lite", "720p", 60, 0.0, rates)
check(est["raw_cost_cents"] == 120.6,
      "veo3_lite 720p 60s raw is not 8 clips plus cut post")
check(est["balance_debit_cents"] == 158,
      "veo3_lite 720p 60s customer debit is not ceil(126 x 1.25)")
owner_est = pricing.per_second_estimate(
    "veo3_lite", "720p", 60, 0.0, rates, owner=True)
check(owner_est["balance_debit_cents"] == 121,
      "veo3_lite 720p 60s owner debit is not the raw cost")

# -- lengths, sizes, audio ------------------------------------------------

check(app.MODEL_DURATION_BOUNDS.get("veo3_lite") == (4, 8),
      "veo3_lite duration bounds drifted from (4, 8)")
check(app.MODEL_DURATION_STEPS.get("veo3_lite") == (4, 6, 8),
      "veo3_lite duration steps drifted from (4, 6, 8)")
check("veo3_lite" not in app.SIZE_PICKER_SINGLE,
      "veo3_lite is back in SIZE_PICKER_SINGLE (Kie sends resolution)")
check(app.offered_resolutions("veo3_lite", rates)
      == ["720p", "1080p"],
      "veo3_lite picker does not offer both priced resolutions")
audio = app.LANE_AUDIO.get("veo3_lite") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "veo3_lite audio record lost its native speech/audio")

# -- submit, mirror, settle ----------------------------------------------

check("_submit_kie_veo_lite" in func_src("submit_kie_job"),
      "submit_kie_job does not dispatch veo3_lite to its Veo submit")
submit = func_src("_submit_kie_veo_lite")
check(bool(submit), "_submit_kie_veo_lite not found")
for token in ('"veo-3-1"', '"veo3_lite"', '"generation_type"',
              '"FIRST_AND_LAST_FRAMES_2_VIDEO"', '"TEXT_2_VIDEO"',
              '"image_urls"', '"resolution"', '"Auto"',
              "_beatapi_media_url", "_submit_failed",
              "PAYLOAD-UNVERIFIED"):
    check(token in submit, f"_submit_kie_veo_lite missing {token}")
mirror = func_src("mirror_kie_status")
check("result_urls" in mirror,
      "mirror_kie_status does not read the Veo resultJson nesting")
settle = func_src("settle_fal_job")
check("flat_clip_cost_cents" in settle,
      "settle_fal_job does not price flat lanes from the clip cost")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_veo3_lite_kie_lane")
