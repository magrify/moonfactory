"""Local checks for the CometAPI Midjourney image lanes
(wired 2026-11-27, frontier pass).

Moonfactory carried exactly one Midjourney lane, V7 on
VidLab. Midjourney's current lineup is V8.2 (the default
since 2026-07-24), V8.1, V7 and V6.1, plus the Niji line
(Niji 7, January 2026; Niji V8 launched 2026-11-24 with no
API announced anywhere, so it is not wired). Midjourney
sells no API of its own, Kie removed Midjourney in 2025
and fal never carried it; CometAPI relays it and the
platform already holds a CometAPI key for the Wan 3.0
Prime lane. These checks pin the wired state so a later
edit cannot quietly drop or reprice the lanes:

- V8.2, V8.1, V6.1 and Niji 7 each carry their own lane
  under their own name (never a composite), beside the
  pre-existing VidLab V7 lane, which is untouched;
- all four ride CometAPI's imagine relay
  (POST /mj/submit/imagine, poll GET /mj/task/{id}/fetch):
  the lane carries its bot type and its version flag
  (--v 8.2 / --v 8.1 / --v 6.1 on MID_JOURNEY, --niji 7
  on NIJI_JOURNEY) and the size key carries the speed
  mode, which the submit sends as the account filter;
- CometAPI prices the imagine operation by speed mode,
  not by version (its live catalog, read 2026-11-27:
  mj_fast_imagine $0.105 per task and mj_turbo_imagine
  $0.315, each with the 0.8 pricing ratio its storefront
  applies, rendering $0.084 / $0.252 payable per task);
  the rendered figures are the raw cost on every version
  lane. No relax imagine model exists in CometAPI's
  catalog, so relax does not sell on these lanes;
- the price shape is the standing one: the owner pays
  the raw provider cost, a customer pays raw x
  FRONTIER_MIN_MARKUP rounded up through the shared
  charge path, never a cent short (11 / 32 cents against
  the owner's rounded 8 / 25);
- each lane sells its two speed modes at the same six
  aspect shapes the VidLab V7 lane sells, the price
  riding the mode alone;
- all four are clean paid text to image lanes whose
  label $ marks match the tier the picker computes, and
  the image submit and mirror dispatch them through the
  CometAPI branches, the settle downloading the finished
  grid with the account's key (CometAPI's task media is
  access controlled).

The CometAPI submit payload stays PAYLOAD-UNVERIFIED
until a funded job proves it; no live job ran under the
Modal spend gate.

Run:  modal-venv python scratch/check_midjourney_cometapi_lanes.py
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

ASPECTS = ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3")

# --- the shelf shape ---------------------------------------------------

names = {lane_id: lane.get("label", "").split(" · ")[0]
         for lane_id, lane in pricing.IMAGE_LANES.items()}
mj = sorted(lid for lid, name in names.items()
            if name.startswith("Midjourney"))
check(mj == ["midjourney_v61", "midjourney_v7",
             "midjourney_v81", "midjourney_v82"],
      "the Midjourney family is V6.1, V7, V8.1 and V8.2, "
      "each under its own name")
check(pricing.IMAGE_LANES["midjourney_v7"].get("provider")
      == "vidlab",
      "the pre-existing V7 lane still rides VidLab, untouched")

for lane_id, tier_name, bot, version in (
        ("midjourney_v82", "Midjourney V8.2",
         "MID_JOURNEY", "--v 8.2"),
        ("midjourney_v81", "Midjourney V8.1",
         "MID_JOURNEY", "--v 8.1"),
        ("midjourney_v61", "Midjourney V6.1",
         "MID_JOURNEY", "--v 6.1"),
        ("midjourney_niji7", "Niji 7",
         "NIJI_JOURNEY", "--niji 7")):
    lane = pricing.IMAGE_LANES[lane_id]
    check(names[lane_id] == tier_name,
          f"{lane_id} serves its own name {tier_name}")
    check(lane.get("provider") == "cometapi",
          f"{lane_id} rides CometAPI, the verified route")
    check(lane.get("endpoint") == "mj/submit/imagine",
          f"{lane_id} endpoint drifted from the imagine relay")
    check(lane.get("mj_bot") == bot,
          f"{lane_id} bot type drifted from {bot}")
    check(lane.get("mj_version") == version,
          f"{lane_id} version flag drifted from {version}")
    check(not lane.get("uncensored") and not lane.get("free")
          and not lane.get("edit"),
          f"{lane_id} is a clean paid text to image lane")
    check(app.image_picker_class(lane_id) == "paid",
          f"{lane_id} groups as paid in the image picker")
    check(app.image_lane_name(lane_id) == tier_name,
          f"the picker serves the plain name {tier_name}")
    check(app.image_lane_tier(lane_id) == 4,
          f"{lane_id} bands at tier 4")
    check(lane["label"].split(" · ")[1] == "$$$$",
          f"{lane_id} label marks match its computed tier")

    # --- the size shelf: two modes, six shapes each --------
    expected = {"fast", "turbo"}
    for aspect in ASPECTS:
        expected.add(f"fast-{aspect}")
        expected.add(f"turbo-{aspect}")
    check(set(lane["raw_cents"]) == expected,
          f"{lane_id} sells exactly its two speed modes at "
          "the six V7 shapes, no relax (CometAPI's catalog "
          "carries no relax imagine model)")
    check(pricing.image_size_label(lane_id, "fast")
          == "Fast speed"
          and pricing.image_size_label(lane_id, "turbo")
          == "Turbo speed",
          f"{lane_id} speed modes carry their speed labels")
    check(pricing.image_size_label(lane_id, "fast-16:9")
          == "16:9 · fast",
          f"{lane_id} shaped sizes label by ratio and mode")

    # --- the price shape ------------------------------------
    check(lane["raw_cents"]["fast"] == 8.4
          and lane["raw_cents"]["turbo"] == 25.2,
          f"{lane_id} raw cost drifted from CometAPI's "
          "rendered per task figures ($0.084 / $0.252)")
    for size, raw in (("fast", 8.4), ("turbo", 25.2)):
        got_raw, debit = pricing.image_lane_price(
            lane_id, size)
        check(got_raw == raw
              and abs(debit - raw * pricing.FRONTIER_MIN_MARKUP)
              < 1e-9,
              f"{lane_id} {size} customer debit is not raw "
              "x the standard frontier markup")
        raw_o, debit_o = pricing.image_lane_price(
            lane_id, size, owner=True)
        check(raw_o == raw and debit_o == raw,
              f"{lane_id} {size} owner price is not the raw "
              "provider cost")
    check(pricing.image_lane_charge_cents(lane_id, "fast")
          == 11,
          f"{lane_id} fast customer charge is not the "
          "x1.25 round-up (11 cents)")
    check(pricing.image_lane_charge_cents(lane_id, "turbo")
          == 32,
          f"{lane_id} turbo customer charge is not the "
          "x1.25 round-up (32 cents)")
    check(pricing.image_lane_charge_cents(
        lane_id, "fast", owner=True) == 8
        and pricing.image_lane_charge_cents(
            lane_id, "turbo", owner=True) == 25,
        f"{lane_id} owner charges are not the rounded raw "
        "figures (8 / 25 cents)")

# --- the dispatch route --------------------------------------------------
# The image submit and mirror live inside the api() function
# and branch on the lane's provider: the CometAPI branches
# post the imagine relay with the lane's bot and version
# flag and the mode as the account filter, poll the task
# fetch, and settle with the key on the media download.
check('elif provider == "cometapi":' in SRC
      and 'f"{COMETAPI_BASE}/mj/submit/imagine"' in SRC
      and '"accountFilter": {"modes": [mode.upper()]}' in SRC
      and 'lane["mj_version"]' in SRC
      and 'lane["mj_bot"]' in SRC,
      "image submits dispatch CometAPI lanes through the "
      "imagine relay branch")
check('f"{COMETAPI_BASE}/mj/task/"' in SRC
      and 'data.get("imageUrl")' in SRC
      and 'headers=_api_headers("cometapi"))' in SRC,
      "image polls dispatch CometAPI lanes through the "
      "task fetch branch and settle with the key")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_midjourney_cometapi_lanes")
