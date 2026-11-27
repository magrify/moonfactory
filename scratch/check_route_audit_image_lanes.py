"""Local checks for the 2026-11-27 route audit follow-up:
the five image lanes the audit listed as clear wins but did
not wire (Nano Banana and Seedream 5.5 to BeatAPI, MAI Image
2.6, MAI Image 2.6 Flash and Flux 3 to SpicyAPI), wired once
the owner ruled that image customer prices follow cost down
(no shelf freeze for images, unlike the video lanes).

Pins:
- each lane routes to its cheaper provider with the new
  provider's endpoint id (BeatAPI's google/nano-banana and
  bytedance/seedream-5.5; SpicyAPI's microsoft/mai-image-2.6,
  microsoft/mai-image-2.6-flash and
  black-forest-labs/flux-3-image text-to-image builds);
- the raw cents are the audit's evidence figures (BeatAPI
  offers read 2026-11-26: Nano Banana $0.015 flat, Seedream
  5.5 $0.014 / $0.014 / $0.024; SpicyAPI's catalog: MAI Image
  2.6 flat $0.0335, Flash flat $0.0165, Flux 3 Image $0.0408
  / $0.0595 / $0.085 / $0.51595 by pixel variant);
- the displaced VidLab rates are recorded in
  DISPLACED_IMAGE_RATES_CENTS and the VidLab submit branch
  stays wired in app.py as the flip back;
- the customer price followed the cost DOWN through the one
  shared charge path (raw x FRONTIER_MIN_MARKUP rounded up,
  never a cent short): Nano Banana 4c -> 2c, Seedream 5.5 2K
  4c -> 3c, MAI Image 2.6 2K 12c -> 5c, Flash 2K 7c -> 3c,
  Flux 3 7c / 9c / 13c / 77c -> 6c / 8c / 11c / 65c; the
  owner pays the raw provider cost;
- each lane's label $ mark matches the band its new picker
  price lands in;
- the BeatAPI image branch sends no resolution for a flat
  lane's "square" base (Nano Banana's only size), only the
  ratio, mirroring the VidLab branch's square handling.

The submit payloads stay PAYLOAD-UNVERIFIED, like every
shelf branch, until a funded job proves them; no live job
ran under the Modal spend gate.

Run:  modal-venv python scratch/check_route_audit_image_lanes.py
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

# lane: (provider, endpoint, {size: new raw}, {size: new charge},
#        {size: old charge on the displaced VidLab route})
LANES = {
    "nano_banana": (
        "beatapi", "google/nano-banana",
        {"square": 1.5}, {"square": 2}, {"square": 4}),
    "seedream55": (
        "beatapi", "bytedance/seedream-5.5",
        {"1K": 1.4, "1.5K": 1.4, "2K": 2.4},
        {"1K": 2, "1.5K": 2, "2K": 3},
        {"1K": 2, "1.5K": 2, "2K": 4}),
    "mai_image26": (
        "spicyapi", "microsoft/mai-image-2.6/text-to-image",
        {"1K": 3.35, "2K": 3.35}, {"1K": 5, "2K": 5},
        {"1K": 5, "2K": 12}),
    "mai_image26_flash": (
        "spicyapi", "microsoft/mai-image-2.6-flash/text-to-image",
        {"1K": 1.65, "2K": 1.65}, {"1K": 3, "2K": 3},
        {"1K": 3, "2K": 7}),
    "flux3_image": (
        "spicyapi", "black-forest-labs/flux-3-image/text-to-image",
        {"1K": 4.08, "1.5K": 5.95, "2K": 8.5, "4K": 51.6},
        {"1K": 6, "1.5K": 8, "2K": 11, "4K": 65},
        {"1K": 7, "1.5K": 9, "2K": 13, "4K": 77}),
}

disp = pricing.DISPLACED_IMAGE_RATES_CENTS

for lane_id, (provider, endpoint, raws, charges, old_charges) \
        in LANES.items():
    lane = pricing.IMAGE_LANES[lane_id]
    check(lane.get("provider") == provider,
          f"{lane_id} does not route to {provider}")
    check(lane.get("endpoint") == endpoint,
          f"{lane_id} endpoint drifted from {endpoint}")
    # pricing.py expands each base tier into its shaped keys
    # ("1K-16:9") at the tier's price: the base tiers must be
    # exactly the audit figures and every shaped key must
    # carry its own tier's figure.
    check(all(lane["raw_cents"].get(s) == r
              for s, r in raws.items())
          and all(lane["raw_cents"][k] == raws[k.split("-")[0]]
                  for k in lane["raw_cents"]),
          f"{lane_id} raw cents drifted from the audit figures")
    for size, raw in raws.items():
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
        check(pricing.image_lane_charge_cents(lane_id, size)
              == charges[size],
              f"{lane_id} customer charge at {size} is not "
              f"{charges[size]}c after the move")
    # the label's $ mark matches the band the new picker price
    # lands in (the shelf's standing label rule)
    marks = lane["label"].split(" · ")[1]
    check(len(marks) == app.image_lane_tier(lane_id)
          and set(marks) == {"$"},
          f"{lane_id} label mark does not match its new tier")

# displaced VidLab rates recorded in full
check(disp["nano_banana_vidlab"] == {"square": 3.0},
      "nano_banana VidLab rates not recorded displaced")
check(disp["seedream55_vidlab"]
      == {"1K": 1.6, "1.5K": 1.6, "2K": 2.8},
      "seedream55 VidLab rates not recorded displaced")
check(disp["mai_image26_vidlab"] == {"1K": 4.0, "2K": 9.0},
      "mai_image26 VidLab rates not recorded displaced")
check(disp["mai_image26_flash_vidlab"] == {"1K": 2.0, "2K": 5.0},
      "mai_image26_flash VidLab rates not recorded displaced")
check(disp["flux3_image_vidlab"]
      == {"1K": 5.0, "1.5K": 7.0, "2K": 10.0, "4K": 61.0},
      "flux3_image VidLab rates not recorded displaced")

# the price really followed cost down: every moved lane's
# total customer charge across its sizes is lower than the
# displaced route's was, and no moved size charges more
for lane_id, (_p, _e, _r, charges, old_charges) in LANES.items():
    for size in charges:
        check(charges[size] <= old_charges[size],
              f"{lane_id} charge at {size} rose on a cheaper "
              "route (image prices follow cost down)")
    check(sum(charges.values()) < sum(old_charges.values()),
          f"{lane_id} total customer charge did not drop")

# --- the dispatch routes -------------------------------------
check('elif provider == "vidlab":' in SRC,
      "the VidLab image branch (the flip back) is gone")
check('elif provider == "beatapi":' in SRC
      and 'elif provider == "spicyapi":' in SRC,
      "the BeatAPI / SpicyAPI image branches are gone")
check('if tier == "square":' in SRC
      and 'beat_body["aspect_ratio"] = aspect or "1:1"' in SRC,
      "the BeatAPI branch does not mirror the square handling")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_route_audit_image_lanes")
