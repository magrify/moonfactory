"""Local checks for the 2026-11-23 price rounding and clone
fold fixes (Jack's orders after the Generate button price
audit).

Fix 1 (rounding): customers are charged 1.25x raw, rounded
UP whenever the math lands on a fraction, never a cent short;
the owner sees and pays the provider's raw price. One shared
rounding path (pricing.customer_debit_cents) serves the image
quote and image creation, and the video quote, creation
estimate and settle. Verified here programmatically:

- every image lane x size x owner flag: the shared charge
  figure is an integer, a customer's charge is never below
  1.25x raw, and the owner's charge is the raw price exactly
  as the pre-fix rounding produced it;
- every per-second video lane x resolution x a spread of
  durations x owner flag x lip sync x clone pick: the quote
  debit is a whole number of cents, the creation estimate
  (the same function) equals it, the settle debit recomputed
  the settle's way equals it at the ordered length, the hold
  cap covers it, and a customer's settle never lands below
  the lane's intended sell figure; on lanes whose sell rate
  is the 1.25x minimum itself, the settle is never below
  1.25x the settled raw cost. A flat-per-clip lane (one
  whose supplier bills per generation, recorded in
  pricing.FLAT_CLIP_COST_CENTS) is measured against its true
  raw instead of the rate table's per-second ceiling: the
  per-clip cost times the generations the length takes,
  plus post, with the intended sell figure the standard
  markup on that raw through the shared rounding path. (The named at-cost exception
  lanes, whose sell rate Jack set at raw cost or raw cost
  plus the Stripe percentage so the lane can sit under
  Higgsfield, are counted separately: their rule is never
  below their own sell figure, and the rounding path is
  what this check pins there too.)

Fix 2 (clone fold): picking Voice cloning folds its one
time price into the one generate price on every surface:
the per-second quote and creation estimate include it, the
settle keeps it, the house balance rail charges it at
creation, the shorts create charges it at creation, and the
refund helper returns it (folded figure adjusted) when no
clone is ever delivered. The owner pays $0 throughout.

Run:  modal-venv python scratch/check_price_rounding.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import pricing  # noqa: E402

SRC = (ROOT / "platform" / "app.py").read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


# ---------------------------------------------------------------
# The shared rounding path itself.
# ---------------------------------------------------------------
check(pricing.customer_debit_cents(7.5) == 8,
      "customer rounding: 7.5 does not round up to 8")
check(pricing.customer_debit_cents(2.5) == 3,
      "customer rounding: 2.5 does not round up to 3")
check(pricing.customer_debit_cents(5.0) == 5,
      "customer rounding: a whole figure moved")
check(pricing.customer_debit_cents(59.000000000001) == 59,
      "customer rounding: float dust above whole rounded up")
check(pricing.customer_debit_cents(58.999999999999) == 59,
      "customer rounding: float dust below whole rounded down")
check(pricing.customer_debit_cents(0.0) == 0,
      "customer rounding: zero moved")

# ---------------------------------------------------------------
# Fix 1, images: every lane x size x owner flag.
# ---------------------------------------------------------------
image_combos = 0
image_old_charge_changes = 0
for lane_id, lane in pricing.IMAGE_LANES.items():
    for size in lane["raw_cents"]:
        for owner in (False, True):
            image_combos += 1
            raw, debit = pricing.image_lane_price(
                lane_id, size, owner=owner)
            charge = pricing.image_lane_charge_cents(
                lane_id, size, owner=owner)
            check(isinstance(charge, int),
                  f"image {lane_id} {size} owner={owner}: "
                  "charge is not whole cents")
            if owner:
                check(charge == int(round(debit)),
                      f"image {lane_id} {size} owner: charge "
                      "moved off the raw price")
            else:
                check(charge >= raw * pricing.FRONTIER_MIN_MARKUP - 1e-9,
                      f"image {lane_id} {size}: customer charge "
                      f"{charge} is below 1.25x raw {raw}")
                check(charge == pricing.customer_debit_cents(debit),
                      f"image {lane_id} {size}: charge is not "
                      "the shared rounding of the 1.25x debit")
                if charge != int(round(debit)):
                    image_old_charge_changes += 1
check(image_combos > 500,
      f"image coverage too thin: {image_combos} combos")
# The quote endpoint and creation both resolve the charge
# through the shared function, so display equals charge.
check(SRC.count("pricing.image_lane_charge_cents(") >= 2,
      "image quote and create do not both use the shared "
      "charge function")

# ---------------------------------------------------------------
# Fix 1, video: every per-second lane x resolution x duration
# spread x owner flag x lip sync x clone pick. The settle
# debit is recomputed exactly the way settle_fal_job builds
# it (shared lane debit, shared lip rounding, folded clone
# price) at the ordered length.
# ---------------------------------------------------------------
DURATIONS = [1, 5, 10, 15, 30, 45, 60, 90, 120]
video_combos = 0
video_floor_combos = 0
at_cost_lanes = set()
for model, tiers in pricing.PROVIDER_RATES_CENTS_PER_SECOND.items():
    for resolution in tiers:
        lane_cost = pricing.per_second_lane_cost_cents(
            model, resolution)
        picture = pricing.frontier_lane(model, resolution)
        sell = picture["sell_cents_per_second"]
        at_cost = sell < lane_cost * pricing.FRONTIER_MIN_MARKUP - 1e-9
        if at_cost:
            at_cost_lanes.add((model, resolution,
                               picture.get("pricing_basis")))
        for duration in DURATIONS:
            for owner in (False, True):
                for lip in (False, True):
                    for clone in (False, True):
                        video_combos += 1
                        est = pricing.per_second_estimate(
                            model, resolution, duration, 0.0, None,
                            owner=owner, lip_sync=lip,
                            voice_clone=clone)
                        debit = est["balance_debit_cents"]
                        tag = (f"video {model} {resolution} "
                               f"{duration}s owner={owner} "
                               f"lip={lip} clone={clone}")
                        check(isinstance(debit, int),
                              f"{tag}: quote debit is not whole "
                              f"cents ({debit!r})")
                        settle = pricing.per_second_balance_debit_cents(
                            model, resolution, duration, None,
                            owner=owner)
                        lip_raw = 0.0
                        if lip:
                            lip_raw, lip_debit = pricing.lip_sync_costs(
                                duration, owner=owner)
                            settle += pricing.customer_debit_cents(
                                lip_debit)
                        if clone:
                            settle += pricing.voice_clone_price_cents(
                                owner=owner)
                        check(settle == debit,
                              f"{tag}: settle debit {settle} != "
                              f"quote debit {debit}")
                        check(est["cap_balance_cents"] >= debit,
                              f"{tag}: hold cap does not cover "
                              "the debit")
                        if owner:
                            continue
                        # The 1.25x floor covers the rate
                        # components (the lane and lip sync).
                        # The clone piece is a defined flat
                        # product price, the 2 cents Jack named
                        # in the fold order, pinned exactly by
                        # the intended figure below and the
                        # estimate delta checks, not re-derived
                        # from its raw basis here.
                        if pricing.flat_clip_cost_cents(
                                model, resolution) is not None:
                            # A flat-per-clip lane: the true
                            # settled raw is the per-clip cost
                            # times the generations the length
                            # takes plus post (the rate table
                            # carries only the worst-case
                            # per-second ceiling for this lane),
                            # and the intended sell figure is
                            # the flat picture's own debit — the
                            # standard markup on that raw
                            # through the shared rounding path —
                            # with the lip and clone pieces
                            # added the same way as elsewhere.
                            raw_settled = pricing.flat_clip_raw_cents(
                                model, resolution, duration) + lip_raw
                            intended = pricing._flat_clip_picture(
                                model, resolution,
                                duration)["balance_debit_cents"]
                            if lip:
                                intended += \
                                    pricing.customer_debit_cents(
                                        lip_debit)
                            if clone:
                                intended += \
                                    pricing.voice_clone_price_cents()
                        else:
                            raw_settled = lane_cost * duration \
                                + lip_raw
                            intended = sell * duration
                            if lip:
                                intended += lip_debit
                            if clone:
                                intended += \
                                    pricing.voice_clone_price_cents()
                        check(debit >= intended - 1e-9,
                              f"{tag}: debit {debit} is below "
                              f"the intended sell figure {intended}")
                        if not at_cost:
                            video_floor_combos += 1
                            check(
                                debit >= raw_settled
                                * pricing.FRONTIER_MIN_MARKUP - 1e-6,
                                f"{tag}: settle {debit} is below "
                                f"1.25x settled raw {raw_settled}")
check(video_combos > 10000,
      f"video coverage too thin: {video_combos} combos")
check(video_floor_combos > 5000,
      f"video 1.25x floor coverage too thin: {video_floor_combos}")

# ---------------------------------------------------------------
# Fix 2, the clone fold: estimate deltas and source wiring.
# ---------------------------------------------------------------
for owner in (False, True):
    base = pricing.per_second_estimate(
        "wan3", "720p", 10, 0.0, None, owner=owner)
    folded = pricing.per_second_estimate(
        "wan3", "720p", 10, 0.0, None, owner=owner,
        voice_clone=True)
    want = pricing.voice_clone_price_cents(owner=owner)
    check(folded["balance_debit_cents"]
          - base["balance_debit_cents"] == want,
          f"clone fold: estimate delta is not the clone price "
          f"(owner={owner})")
    check(folded["voice_clone_cents"] == want,
          f"clone fold: estimate does not report the folded "
          f"amount (owner={owner})")
    check(abs(folded["raw_cost_cents"] - base["raw_cost_cents"]
              - pricing.VOICE_CLONE_RAW_CENTS) < 1e-9,
          f"clone fold: raw basis missing from the estimate "
          f"(owner={owner})")
check(pricing.voice_clone_price_cents() == 2,
      "clone price moved off 2 cents")
check(pricing.voice_clone_price_cents(owner=True) == 0,
      "owner clone price is not $0")
check(pricing.public_pricing()["voice_clone_price_cents"] == 2,
      "pricing payload does not carry the customer clone price")
check(pricing.public_pricing(owner=True)["voice_clone_price_cents"] == 0,
      "pricing payload does not carry the owner clone price")

# Quote, create, settle, house, shorts: the fold is wired on
# every surface, and the stage debit sees the stamp instead
# of charging a second time.
check('voice_clone=(body.get("speech_voice") or "") == CLONE_VOICE_ID'
      in SRC, "jobs quote does not fold the clone pick")
check("voice_clone=(speech_voice == CLONE_VOICE_ID)" in SRC,
      "per-second create does not fold the clone pick")
check(SRC.count('job.get("clone_in_price")') >= 2,
      "settle does not carry the folded clone price")
check(SRC.count('"clone_in_price"] = True') >= 3,
      "creation stamps are not on all three create paths")
check("price += clone_add" in SRC,
      "house balance rail does not fold the clone price")
check('"charged_cents": clone_add' in SRC,
      "shorts create does not charge the folded clone price")
check("clone_folded_cents" in SRC.split(
    "def _clone_refund_if_unsuccessful")[1].split(
    "def _queue_clone_child")[0],
    "refund helper does not adjust the folded charged figure")
check("voice_clone_price_cents" in SRC.split(
    '@web.get("/api/shorts/config")')[1].split(
    '@web.post("/api/shorts")')[0],
    "shorts config does not carry the clone price")
check("speech_voice: ($('speech') && !$('speech').disabled)"
      " ? $('speech-voice').value : ''" in SRC,
      "studio quote request does not carry the voice pick")
check("pricingData.voice_clone_price_cents" in SRC,
      "house price line does not fold the clone price")
check("shortCfg.voice_clone_price_cents" in SRC,
      "shorts button does not carry the folded clone price")

print(f"images: {image_combos} combos, "
      f"{image_old_charge_changes} charges moved by the fix")
print(f"video: {video_combos} combos, "
      f"{video_floor_combos} checked against the 1.25x floor, "
      f"{len(at_cost_lanes)} at-cost exception lane tiers "
      f"checked against their own sell figure")
if failures:
    print("PRICE ROUNDING CHECKS FAILED:")
    for f in failures[:40]:
        print(" -", f)
    if len(failures) > 40:
        print(f" ... and {len(failures) - 40} more")
    sys.exit(1)
print("ALL PRICE ROUNDING CHECKS PASSED")
