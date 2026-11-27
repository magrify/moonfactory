"""Local checks for the GPT Image 2.5 image lanes (2026-11-26).

Jack asked whether Moonfactory carries GPT Image 2.5 and then
ordered it added. The verification pass found the model already
on the shelf: OpenAI's 2.5 ships through every aggregator only
as its Flare / Sunburst tier pair (there is no un-suffixed base
endpoint on fal, BeatAPI, Kie, Eachlabs, CometAPI, SpicyAPI,
VidLab, Numes, or FrameHush), and both tiers were wired as
their own lanes in the 2026-11-23 VidLab pass (commit c4fb5ea)
but never pinned. These checks pin that state so a later edit
cannot quietly drop or reprice them:

- exactly two image lanes carry the GPT Image 2.5 name, the
  Flare and Sunburst tiers, and no lane is named a bare
  "GPT Image 2.5" (a bare lane would mislabel a tier);
- both lanes ride VidLab, the cheapest verified route in the
  2026-11-26 route audit (VidLab $0.02 / $0.02 / $0.03 per
  image at 1K / 2K / 4K, under Kie's $0.03 / $0.05 / $0.08 and
  fal's token card, the same card that measures about $0.053
  at 1024x1024 on the 2.6 pair);
- the price shape is the standing one: the owner pays the raw
  provider cost, a customer pays raw x FRONTIER_MIN_MARKUP
  rounded up through the shared charge path, never a cent
  short (3 / 3 / 4 cents against the owner's 2 / 2 / 3);
- both lanes are clean paid text to image lanes at the $
  tier, and image submits dispatch them through the shared
  VidLab branch by provider.

The VidLab submit payload stays PAYLOAD-UNVERIFIED, like every
VidLab lane, until a funded job proves it; no live job ran
under the Modal spend gate.

Run:  modal-venv python scratch/check_gpt_image25_lanes.py
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


# --- the 2.5 shelf shape ---------------------------------------------
names = {lane_id: lane.get("label", "").split(" · ")[0]
         for lane_id, lane in pricing.IMAGE_LANES.items()}
pair = sorted(lid for lid, name in names.items()
              if name.startswith("GPT Image 2.5"))
check(pair == ["gpt_image25_flare", "gpt_image25_sunburst"],
      "exactly the Flare and Sunburst lanes carry the GPT Image "
      "2.5 name")
check("GPT Image 2.5" not in names.values(),
      "no lane is named a bare GPT Image 2.5 (the model ships "
      "only as its tier pair)")

for lane_id, tier_name, endpoint in (
        ("gpt_image25_flare", "GPT Image 2.5 Flare",
         "gpt-image-2.5-flare"),
        ("gpt_image25_sunburst", "GPT Image 2.5 Sunburst",
         "gpt-image-2.5-sunburst")):
    lane = pricing.IMAGE_LANES[lane_id]
    check(names[lane_id] == tier_name,
          f"{lane_id} serves its own name {tier_name}")
    check(lane.get("provider") == "vidlab",
          f"{lane_id} rides VidLab, the cheapest verified route")
    check(lane.get("endpoint") == endpoint,
          f"{lane_id} endpoint drifted from {endpoint}")
    check(not lane.get("uncensored") and not lane.get("free")
          and not lane.get("edit"),
          f"{lane_id} is a clean paid text to image lane")
    check(app.image_picker_class(lane_id) == "paid",
          f"{lane_id} groups as paid in the image picker")
    check(app.image_lane_name(lane_id) == tier_name,
          f"the picker serves the plain name {tier_name}")
    check(app.image_lane_tier(lane_id) == 1,
          f"{lane_id} bands at the $ tier like its label says")

    # --- the price shape -------------------------------------------
    for size, raw in (("1K", 2.0), ("2K", 2.0), ("4K", 3.0)):
        check(lane["raw_cents"][size] == raw,
              f"{lane_id} raw cost at {size} drifted from the "
              "VidLab catalog figure")
        got_raw, debit = pricing.image_lane_price(lane_id, size)
        check(got_raw == raw
              and abs(debit - raw * pricing.FRONTIER_MIN_MARKUP)
              < 1e-9,
              f"{lane_id} customer debit at {size} is not raw x "
              "the standard frontier markup")
        raw_o, debit_o = pricing.image_lane_price(
            lane_id, size, owner=True)
        check(raw_o == raw and debit_o == raw,
              f"{lane_id} owner price at {size} is not the raw "
              "provider cost")
    check([pricing.image_lane_charge_cents(lane_id, s)
           for s in ("1K", "2K", "4K")] == [3, 3, 4],
          f"{lane_id} customer charge is not the x1.25 round-up "
          "(3 / 3 / 4 cents)")
    check([pricing.image_lane_charge_cents(lane_id, s, owner=True)
           for s in ("1K", "2K", "4K")] == [2, 2, 3],
          f"{lane_id} owner charge is not the raw cost "
          "(2 / 2 / 3 cents)")

# --- the dispatch route ------------------------------------------------
# The image submit lives inside the api() function and branches
# on the lane's provider, so the 2.5 lanes dispatch through the
# shared VidLab branch with the tier sent as the resolution.
check('elif provider == "vidlab":' in SRC
      and 'image_input["resolution"] = tier.lower()' in SRC,
      "image submits dispatch VidLab lanes through the shared "
      "VidLab branch, tier as resolution")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_gpt_image25_lanes")
