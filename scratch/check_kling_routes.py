"""Local checks for the Kling route hunt (2026-11-27).

What is pinned:

- The hunt's price record stands in pricing.py: the kling3
  lane basis stays the Kie with-audio row (11.35 / 15.1 /
  37.75) because reference jobs share the row and still run
  on Kie; SpicyAPI's cheaper text/image rates (10.08 / 13.44
  / 33.6, live catalog 2026-11-27) are recorded in
  DISPLACED_RATES as kling3_spicyapi; the kling4 basis was
  VidLab (13.5 / 20.0) at the hunt, and moved to UnificAlly
  (12.6 / 18.9) later the same day in the catalog audit
  (the VidLab row stands as the displaced kling4_vidlab
  record; the lane's shelf and sells did not move).
- No customer price moves: both lanes keep their at-cost
  residual treatment against Higgsfield's policy figures
  (kling3 720p sells 11.792 against 9.833; kling4 sells
  14.007 / 20.701 against 11.063 / 15.979). Those cells are
  the no-profit cases the owner was told about: even the
  cheapest route's raw cost exceeds Higgsfield's retail, so
  no compliant undercut exists and none is faked here.
- The mode split is wired: text and image jobs on kling3
  dispatch to SpicyAPI (MODEL_PROVIDER_MODE_OVERRIDES),
  reference jobs stay on Kie, and provider_for answers the
  base provider when no mode is given (the rate sweep and
  the shorts path rely on that).
- The SpicyAPI submit carries kling3's per-mode endpoints
  and the fields its schema lists (generate_audio,
  aspect_ratio, last_image_url), spells the 4K tier 4k, and
  sends image_url only when an opening frame exists (the
  guard that also fixes text-mode submits for the lanes
  wired to this supplier in the 2026-11-26 audit).

No Modal calls, no network, no generation.

Run:  python3 scratch/check_kling_routes.py
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


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# --- pricing record -------------------------------------------------
check(pricing.PROVIDER_RATES_CENTS_PER_SECOND["kling3"] == {
    "720p": 11.35, "1080p": 15.1, "4K": 37.75},
    "kling3 lane basis must stay the Kie with-audio row")
check(pricing.PROVIDER_RATES_CENTS_PER_SECOND["kling4"] == {
    "720p": 12.6, "1080p": 18.9},
    "kling4 lane basis must stay the UnificAlly row "
    "(2026-11-27 catalog audit; the VidLab row is the "
    "displaced kling4_vidlab record)")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND["kling3_spicyapi"] == {
    "720p": 10.08, "1080p": 13.44, "4K": 33.6},
    "SpicyAPI kling3 rates must be recorded as kling3_spicyapi")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND["kling3"] == {
    "720p": 11.0, "1080p": 13.0},
    "BeatAPI kling3 displaced record must stand")

# --- the no-profit cells keep their honest treatment ----------------
for model, tier, sell, higgs in (
        ("kling3", "720p", 11.792, 9.833),
        ("kling4", "720p", 14.007, 11.063),
        ("kling4", "1080p", 20.701, 15.979)):
    lane = pricing.frontier_lane(model, tier)
    check(close(lane["sell_cents_per_second"], sell, 1e-3),
          f"{model} {tier} sell moved: {lane['sell_cents_per_second']}")
    check(lane["pricing_basis"] == "at_cost_residual",
          f"{model} {tier} must stay at_cost_residual, "
          f"got {lane['pricing_basis']}")
    check(close(
        pricing.HIGGSFIELD_POLICY_CENTS_PER_SECOND[model][tier],
        higgs, 1e-3),
        f"{model} {tier} Higgsfield policy figure moved")
    check(lane["sell_cents_per_second"] > higgs,
          f"{model} {tier} is a known over-Higgsfield cell; "
          "if this flips, the hunt record needs rewriting")

lane = pricing.frontier_lane("kling3", "1080p")
check(close(lane["sell_cents_per_second"], 19.0, 1e-3),
      f"kling3 1080p sell moved: {lane['sell_cents_per_second']}")

# Even the cheapest route cannot undercut at 720p: SpicyAPI's
# raw rate alone exceeds Higgsfield's retail.
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND[
    "kling3_spicyapi"]["720p"]
    > pricing.HIGGSFIELD_POLICY_CENTS_PER_SECOND["kling3"]["720p"],
    "kling3 720p no-profit premise changed; re-derive the cell")

# --- app.py wiring (source-level; app.py is not imported) -----------
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def literal_assign(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    return None


overrides = literal_assign("MODEL_PROVIDER_MODE_OVERRIDES")
check(overrides == {"kling3": {"text": "spicyapi",
                               "image": "spicyapi"},
                    "seedance25_flash": {
                        "text": "unifically",
                        "reference": "unifically",
                        "image": "vidlab"}},
      f"mode override table wrong: {overrides}")

model_ids = literal_assign("SPICYAPI_MODEL_IDS")
check(isinstance(model_ids, dict)
      and model_ids.get("kling3") == {
          "text": "kling/3.0/text-to-video",
          "image": "kling/3.0/image-to-video"},
      "SPICYAPI_MODEL_IDS kling3 endpoints wrong")

for table in ("SPICYAPI_AUDIO_MODELS", "SPICYAPI_ASPECT_MODELS",
              "SPICYAPI_END_FRAME_MODELS"):
    members = literal_assign(table)
    check(members is not None and "kling3" in members,
          f"kling3 missing from {table}")

provider_fn = next(
    (n for n in TREE.body
     if isinstance(n, ast.FunctionDef) and n.name == "provider_for"),
    None)
check(provider_fn is not None
      and [a.arg for a in provider_fn.args.args] == [
          "model", "resolution", "mode"],
      "provider_for must take (model, resolution, mode)")
check("MODEL_PROVIDER_MODE_OVERRIDES" in SRC,
      "provider_for must consult the mode override table")

submit_src = ast.get_source_segment(
    SRC, next(n for n in ast.walk(TREE)
              if isinstance(n, ast.FunctionDef)
              and n.name == "submit_spicyapi_job"))
check('if params["start_image"]:' in submit_src,
      "SpicyAPI submit must guard image_url on an opening frame")
check('("ltx25", "kling3")' in submit_src,
      "SpicyAPI submit must spell kling3's 4K tier 4k")

check(SRC.count('provider_for(model, params["resolution"],\n') >= 3,
      "quote and create paths must pass the job mode to provider_for")

if failures:
    print("FAIL check_kling_routes")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_kling_routes")
