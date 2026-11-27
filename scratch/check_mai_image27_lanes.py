"""Local checks for the MAI Image 2.7 image lanes (wired
2026-11-27, frontier auto-wire sweep).

Microsoft released MAI Image 2.7 and MAI Image 2.7 Flash on
2026-11-17 as the successor pair to the carried 2.6 lanes.
These checks pin the wired state so a later edit cannot
quietly drop or reprice them:

- exactly two image lanes carry the MAI Image 2.7 name, the
  flagship and its Flash tier, each under its own name;
- both ride fal, the cheapest verified route (fal's token
  card measures $0.034816 / $0.01024 per 1024x1024 image and
  LiteLLM's fal reads checked 2026-11-27 put them at
  $0.0349 / $0.0103, both far under SpicyAPI's $0.084 /
  $0.030 at 1K);
- fal bills per token, so like the GPT Image 2.6 fal lanes
  the only size sold is the measured 1024x1024;
- the price shape is the standing one: the owner pays the
  raw provider cost, a customer pays raw x
  FRONTIER_MIN_MARKUP rounded up through the shared charge
  path, never a cent short (5 / 2 cents against the owner's
  rounded 3 / 1);
- both lanes are clean paid text to image lanes whose label
  $ marks match the tier the picker computes, and image
  submits dispatch them through the shared fal branch.

The fal submit payload stays PAYLOAD-UNVERIFIED, like every
fal image lane, until a funded job proves it; no live job
ran under the Modal spend gate.

Run:  modal-venv python scratch/check_mai_image27_lanes.py
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


SRC = (ROOT / "platform" / "app.py").read_text()

# --- the 2.7 shelf shape ---------------------------------------------

names = {lane_id: lane.get("label", "").split(" · ")[0]
         for lane_id, lane in pricing.IMAGE_LANES.items()}
pair = sorted(lid for lid, name in names.items()
              if name.startswith("MAI Image 2.7"))
check(pair == ["mai_image27", "mai_image27_flash"],
      "exactly the flagship and Flash lanes carry the MAI "
      "Image 2.7 name")

for lane_id, tier_name, endpoint, raw, tier, marks in (
        ("mai_image27", "MAI Image 2.7",
         "microsoft/mai-image-2.7/text-to-image", 3.49, 2, "$$"),
        ("mai_image27_flash", "MAI Image 2.7 Flash",
         "microsoft/mai-image-2.7/flash/text-to-image",
         1.03, 1, "$")):
    lane = pricing.IMAGE_LANES[lane_id]
    check(names[lane_id] == tier_name,
          f"{lane_id} serves its own name {tier_name}")
    check(lane.get("provider") == "fal",
          f"{lane_id} rides fal, the cheapest verified route")
    check(lane.get("endpoint") == endpoint,
          f"{lane_id} endpoint drifted from {endpoint}")
    check(not lane.get("uncensored") and not lane.get("free")
          and not lane.get("edit"),
          f"{lane_id} is a clean paid text to image lane")
    check(app.image_picker_class(lane_id) == "paid",
          f"{lane_id} groups as paid in the image picker")
    check(app.image_lane_name(lane_id) == tier_name,
          f"the picker serves the plain name {tier_name}")
    check(app.image_lane_tier(lane_id) == tier,
          f"{lane_id} bands at tier {tier}")
    check(lane["label"].split(" · ")[1] == marks,
          f"{lane_id} label marks match its computed tier")

    # --- the price shape -------------------------------------------
    check(list(lane["raw_cents"]) == ["1024x1024"],
          f"{lane_id} sells only the measured 1024x1024 size")
    check(lane["raw_cents"]["1024x1024"] == raw,
          f"{lane_id} raw cost drifted from the fal figure")
    got_raw, debit = pricing.image_lane_price(
        lane_id, "1024x1024")
    check(got_raw == raw
          and abs(debit - raw * pricing.FRONTIER_MIN_MARKUP)
          < 1e-9,
          f"{lane_id} customer debit is not raw x the "
          "standard frontier markup")
    raw_o, debit_o = pricing.image_lane_price(
        lane_id, "1024x1024", owner=True)
    check(raw_o == raw and debit_o == raw,
          f"{lane_id} owner price is not the raw provider "
          "cost")

check(pricing.image_lane_charge_cents(
    "mai_image27", "1024x1024") == 5,
    "mai_image27 customer charge is not the x1.25 round-up "
    "(5 cents)")
check(pricing.image_lane_charge_cents(
    "mai_image27_flash", "1024x1024") == 2,
    "mai_image27_flash customer charge is not the x1.25 "
    "round-up (2 cents)")

# --- the dispatch route ------------------------------------------------
# The image submit lives inside the api() function and branches
# on the lane's provider, so the 2.7 lanes dispatch through the
# shared fal branch, which posts to fal's queue with the
# pixel size as explicit dimensions.
check('if provider == "fal":' in SRC
      and "https://queue.fal.run/{endpoint}" in SRC,
      "image submits dispatch fal lanes through the shared "
      "fal branch")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_mai_image27_lanes")
