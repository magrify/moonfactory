"""Moonfactory pricing, derived from measured generation costs.

Implements BUSINESS-RULES.md Rule 2 and Rule 8 in code:

- Every lane has a hard price floor: worst-case measured cost x margin
  multiplier, grossed up so Stripe's fee (2.9% + 30c) comes out of the
  price, not out of the margin.
- The Creator plan's included production seconds are not a marketing
  number. They are whatever the plan's net revenue covers at the
  production floor rate. If measured costs fall, the allowance rises on
  its own; if they rise, it shrinks before the business loses money.
- A lane whose selling price would sit below its floor is paused in
  code (LANE_PAUSED), never sold at a loss.

Measured costs (Modal list rates, from modal-worker/STATUS.md):
- Draft job (Wan 2.2 5B, one ~5 s clip): worst observed ~$0.50 per job
  including a lost attempt across the test runs.
- Production segment (Wan 2.2 A14B, 5.06 s, L40S): ~$0.15 per segment
  worst case. The lane now runs the LightX2V 4-step distilled recipe
  (20 steps -> 4, cfg 1.0, LoRA on both UNETs), benchmarked 2026-11-16
  at 216 s wall = ~$0.12 per segment with quality holding against the
  old 20-step render (23m39s wall, ~$0.77); 15 cents carries the
  cold-start variance above the verified measure.
"""

MARGIN_MULTIPLIER = 3.0
STRIPE_PCT = 0.029
STRIPE_FIXED_CENTS = 30

SEGMENT_SECONDS = 5.06

# Worst-case measured cost per job, in cents.
MEASURED_COST_CENTS = {
    "draft": 50,
    "production": 15,
    # Effects (Pass 9): priced as a Production segment. The proof
    # render's measured cost is recorded in the effects section at
    # the bottom of this file; the entry lands at the Production
    # worst case when the distilled recipe measures inside it.
    "effects": 15,
}

CREATOR_PRICE_CENTS = 2000  # $20/mo

# Top-up packs (Rule 8: a sensible minimum so the fixed fee does not
# eat the purchase).
TOPUP_PACKS_CENTS = [500, 1000, 2000]


def gross_up(net_cents: float) -> int:
    """Smallest whole-cent price whose net after Stripe fees is >= net."""
    import math

    return math.ceil((net_cents + STRIPE_FIXED_CENTS) / (1 - STRIPE_PCT))


def net_of(price_cents: int) -> float:
    """Net revenue kept from a price after Stripe fees."""
    return price_cents * (1 - STRIPE_PCT) - STRIPE_FIXED_CENTS


def floor_price_cents(lane: str) -> int:
    """Hard per-job price floor for a lane (Rule 2)."""
    return gross_up(MEASURED_COST_CENTS[lane] * MARGIN_MULTIPLIER)


# Selling prices for per-job top-up charges, rounded up to a clean
# number at or above the floor.
def _round_up_price(floor_cents: int) -> int:
    for candidate in (99, 149, 199, 249, 299, 349, 399, 499, 599, 799, 999):
        if candidate >= floor_cents:
            return candidate
    return floor_cents


JOB_PRICE_CENTS = {lane: _round_up_price(floor_price_cents(lane))
                   for lane in MEASURED_COST_CENTS}


# ---------------------------------------------------------------------------
# Owner pricing exemption (Jack, 2026-11-16): Jack's own account
# (support@magrify.com, flagged is_owner on its user record) is the
# ONLY account that pays no markup. For that account every charge
# resolves to raw cost on the cheapest route: the figure the
# multipliers are applied to (MEASURED_COST_CENTS for the self-hosted
# lanes, provider rate + post cost for the per-second lanes,
# IMAGE_MEASURED_COST_CENTS for start images, CLIP_COST for
# clipping), with no margin multiplier and no Stripe gross-up. Every
# other account pays the standard prices above, unchanged. The
# helpers below take the flag explicitly; app.py reads it from the
# requesting account's record via is_owner(). Settled-cost accounting
# is untouched: it always recorded actual cost, so for the owner the
# charge now simply equals the cost that was already being settled.
# ---------------------------------------------------------------------------


def job_price_cents(lane: str, owner: bool = False) -> int:
    """The per-job balance price for a self-hosted lane: the marked-up
    JOB_PRICE_CENTS for everyone, raw measured cost for the owner."""
    if owner:
        return int(MEASURED_COST_CENTS[lane])
    return JOB_PRICE_CENTS[lane]


def job_price_table(owner: bool = False) -> dict:
    """JOB_PRICE_CENTS as the requesting account should see it."""
    return {lane: job_price_cents(lane, owner) for lane in MEASURED_COST_CENTS}


def lane_paused(lane: str) -> bool:
    """Rule 2: a lane sells only at or above its floor."""
    return JOB_PRICE_CENTS[lane] < floor_price_cents(lane)


# ---------------------------------------------------------------------------
# Per-second lanes (Wan 3.0 and Wan 3.0 Prime). Design:
# API-LANES-DESIGN.md sections 5 and 6. These lanes carry a per-second
# measured cost instead of a per-job one: the provider rate for the
# chosen resolution plus the measured local post cost per finished
# second. Included-budget debits are raw cost, because the budget
# itself is denominated in cost cents.
#
# Balance (pay-per-video) pricing follows BUSINESS-RULES Amendment 3
# (Jack, 2026-11-16): these lanes are priced to undercut Higgsfield's
# effective per-clip price for the comparable output where possible,
# NOT held to the 3x floor the self-hosted lanes use. The sell rate
# is just under Higgsfield's effective rate when that clears the hard
# minimum of cost x FRONTIER_MIN_MARKUP; otherwise the lane sells at
# the minimum and is recorded here as cannot-undercut (data, surfaced
# in /api/pricing, never user copy). The minimum is also the
# after-fees guarantee: balance is bought with a top-up whose Stripe
# fee is already paid at purchase, so a debit at cost x 1.25 nets
# positive on every job. Self-hosted lanes keep the 3x floor above.
# ---------------------------------------------------------------------------

RESOLUTIONS = ("480p", "720p", "1080p")

# Provider list rates, USD cents per billed second (output seconds
# plus reference-video input seconds). Seeded from the provider's
# published model pages, verified 2026-11-16. The platform refreshes
# this table on a schedule into its live config and pauses the lanes
# rather than quote from a table older than
# PROVIDER_RATE_STALE_SECONDS (Rule 2 auto-pause).
PROVIDER_RATES_CENTS_PER_SECOND = {
    # Wan 3.0 via BeatAPI (route rule, Jack 2026-11-16: the
    # cheapest route is primary). BeatAPI publishes $0.284 /
    # $0.568 / $1.088 per 8-second clip = 3.55 / 7.10 / 13.60 per
    # second; fal's route (5.0 / 10.0 / 20.0) is recorded as the
    # fallback below. This supplier bills reference-video seconds
    # at the output rate (its Prime page notes a 1.5x factor on
    # video references); settlement uses the provider's own
    # reported charge, never this estimate.
    "wan3": {"480p": 3.55, "720p": 7.1, "1080p": 13.6},
    # Wan 3.0 Prime via CometAPI (follow-up pass, 2026-11-16):
    # CometAPI publishes a single $0.0544/s figure with no
    # resolution split, carried flat at every resolution (flagged:
    # resolution basis unconfirmed). BeatAPI's rates (6.05 /
    # 10.50 / 21.00) are in DISPLACED_RATES below; fal's route
    # (6.8 / 14.0 / 28.0) is the fallback below.
    "wan3_prime": {"480p": 5.44, "720p": 5.44, "1080p": 5.44},
    # Seedance 2.5, mixed route (follow-up pass, 2026-11-16;
    # round 2, same day): 480p and 720p via CometAPI at its API
    # catalog's stated actual prices, $0.0824 / $0.1848 per second
    # (read 2026-11-16 from CometAPI's own models API, the
    # seedance-2-5 entry's "Actual price / second" column: 80% of
    # the official $0.103 / $0.231). The 480p tier moved off
    # BeatAPI ($0.096 seeded; its live page read ~$0.0968) in
    # round 2: the CometAPI figure is the cheapest verified 480p
    # rate anywhere in the sweep. 1080p stays on BeatAPI at
    # $0.481 per second, no reference video, audio included at no
    # extra cost (CometAPI surfaces no 1080p for this model; the
    # displaced BeatAPI 720p rate is in DISPLACED_RATES below).
    # With reference videos BeatAPI bills a LOWER per-second rate
    # over output + reference seconds, so estimating every billed
    # second at the no-reference rate is the conservative
    # direction, and settlement uses the provider's own reported
    # charge, never this estimate.
    "seedance": {"480p": 8.24, "720p": 18.48, "1080p": 48.1},
    # Veo 3.1 via PiAPI (follow-up pass, 2026-11-16): $0.12/s is
    # the cheapest published per-second rate anywhere, on the
    # NO-AUDIO basis (PiAPI's with-audio rate is $0.24/s). The
    # model itself generates dialogue natively; this route does
    # not, so the lane's audio capability is silent-with-voiceover
    # (the Kokoro voiceover path covers talking content; see
    # LANE_AUDIO in app.py). Carried flat: the published figure
    # has no resolution split. fal's with-audio route ($0.40/s)
    # is the fallback below.
    "veo3": {"720p": 12.0, "1080p": 12.0},
    # Veo 3.1 Fast via fal, with audio: $0.15/s at 720p and 1080p.
    "veo3_fast": {"720p": 15.0, "1080p": 15.0},
    # Kling 3.0 via Kie (follow-up pass, 2026-11-16; round-2
    # cost correction, same day): Kie's own model page
    # (kie.ai/kling-3-0) prices kling-3.0/video per second at
    # 15.1 credits ($0.0755) at 720p and 20.2 credits ($0.101) at
    # 1080p WITHOUT audio, and 22.7 credits ($0.1135) / 30.2
    # credits ($0.151) WITH audio. This lane ships sound on (the
    # Kie submit sends sound=true), so the with-audio rates are
    # the cost basis; the $0.0755 "from" figure seeded in the
    # follow-up pass was the 720p no-audio rate and understated
    # every job the lane ran. BeatAPI's rates (11.0 / 13.0,
    # sound included) are in DISPLACED_RATES below; fal's
    # with-audio route (12.6 / 16.8) is the fallback below and
    # fal's endpoints stay wired in app.py.
    "kling3": {"720p": 11.35, "1080p": 15.1},
    # --- Full-shelf wiring, 2026-11-16 (model availability audit).
    # Every rate below is the primary supplier's published rate,
    # verified 2026-11-16; none of these lanes carries a measured
    # Higgsfield effective, so frontier_lane() sells them at the
    # hard minimum (cost x 1.25) with can_undercut unanswered.
    # Kling 4.0 via Kie (follow-up pass, 2026-11-16; round-2
    # cost correction, same day): Kie's own model page
    # (kie.ai/kling-4-0) prices keyframe-to-video and
    # reference-to-video WITHOUT a video input at 28.5 credits
    # ($0.1425/s) at 720p and 42.5 credits ($0.2125/s) at 1080p.
    # The $0.085/s figure seeded in the follow-up pass is Kie's
    # WITH-reference-video rate, which is per BILLED second over
    # input + output seconds (output x 2 when the input is no
    # longer than the output), not per output second; the lane's
    # standard job shapes (text, keyframe) bill at the no-input
    # rates, so those are the cost basis. Settlement still uses
    # Kie's reported consumeCredits where one exists. BeatAPI's
    # with-input rates (18.0 / 28.0) are in DISPLACED_RATES below.
    "kling4": {"720p": 14.25, "1080p": 21.25},
    # Kling 3.0 Omni via Kie (follow-up pass, 2026-11-16): from
    # $0.0755/s (the from-price does not split standard/pro;
    # carried flat pending console confirmation). fal's with-audio
    # rates (Standard $0.112/s, Pro $0.14/s) are the fallback
    # below; fal's endpoints stay wired in app.py.
    # Round-2 note (2026-11-16): the submit sends sound=true for
    # this lane too, and third-party reads of Kie's Omni matrix
    # put its sound-on 720p rate at 18-20 credits ($0.09-0.10/s),
    # above this flat seed. The seed is NOT re-based on those
    # reads: they are estimated third-party figures, not Kie's
    # own published page, and the lane has no Higgsfield figure
    # to enforce against. It stays flagged for a Kie console
    # confirmation, the same status as before this round.
    # FINISH-RUN CORRECTION (2026-11-16): the console confirmation
    # arrived via Kie's own authenticated catalog API
    # (api.kie.ai/api/v1/models, pricingDesc for
    # kling-3.0-omni/*): with native audio 720p is 20.2 credits
    # ($0.101/s) and 1080p is 25.2 credits ($0.126/s). The submit
    # sends sound=true, so those are this lane's rates; the old
    # flat 7.55 seed priced the lane BELOW its true cost.
    "kling3_omni": {"720p": 10.1, "1080p": 12.6},
    # Seedance 2.0 via BeatAPI (seedance-2): carried at the
    # WITH-video-input rates ($0.618 / $1.247 / $3.107 per
    # 8-second clip; no-input $0.509 / $1.027 / $2.563).
    "seedance2": {"480p": 7.725, "720p": 15.593, "1080p": 38.842},
    # Seedance 2.0 Fast via Kie (follow-up pass, 2026-11-16):
    # from $0.034/s, carried flat (the from-price publishes no
    # resolution split; pending console confirmation). BeatAPI's
    # with-input rates are in DISPLACED_RATES below.
    # FINISH-RUN CORRECTION (2026-11-16): Kie's catalog API
    # (bytedance/seedance-2-fast pricingDesc) splits the rate by
    # video input: 480p $0.034 with-video / $0.059 no-video;
    # 720p $0.075 with-video / $0.124 no-video. Text and image
    # modes (the common jobs) carry no video input, so the lane
    # is seeded at the no-video rates. Reference mode WITH a
    # video bills the with-video rate over input+output seconds;
    # that shape is not separately seeded (flagged, audit doc).
    "seedance2_fast": {"480p": 5.9, "720p": 12.4},
    # Seedance 2.0 Mini via Kie (follow-up pass, 2026-11-16):
    # from $0.012/s, carried flat on the same basis. Silent model
    # on every route. BeatAPI's with-input rates are in
    # DISPLACED_RATES below.
    # FINISH-RUN CORRECTION (2026-11-16): same catalog read
    # (bytedance/seedance-2-mini pricingDesc): 480p $0.012
    # with-video / $0.019 no-video; 720p $0.025 with-video /
    # $0.041 no-video. Seeded at the no-video rates, same basis
    # and same reference-mode flag as Fast above.
    "seedance2_mini": {"480p": 1.9, "720p": 4.1},
    # Veo 3.1 Lite via Eachlabs (follow-up pass, 2026-11-16):
    # Eachlabs publishes $0.03-0.08/s on the no-audio basis (three
    # suppliers tie at $0.03/s); carried at the bottom of the
    # published range for 720p and the top for 1080p, pending
    # console confirmation. Like Veo 3.1, the model generates
    # dialogue natively but this route does not, so the lane is
    # silent-with-voiceover. fal's with-audio rates ($0.05/s at
    # 720p, $0.08/s at 1080p) are the fallback below.
    "veo3_lite": {"720p": 3.0, "1080p": 8.0},
    # Gemini Omni 1.2 Flash via Kie (follow-up pass, 2026-11-16):
    # from $0.02/s, carried flat (the published figure's
    # resolution basis is unconfirmed). BeatAPI's rates ($0.392 /
    # $0.5824 per 8-second clip) are in DISPLACED_RATES below.
    # This is also the pinned successor lane for the Veo 3.1
    # family, whose fal endpoints carry a 2026-11-17 deprecation
    # date.
    # FINISH-RUN CORRECTION (2026-11-16): Kie's catalog API
    # (google/gemini-omni-flash-1-2 pricingDesc) resolves the
    # basis: $0.02/s is the 360P rate; 720P is $0.045/s and
    # 1080P is $0.05/s. The lane sells 720p/1080p, so it is
    # seeded at those rates (Kie stays cheapest: BeatAPI's
    # displaced rates are 4.9 / 7.28).
    "omni12": {"720p": 4.5, "1080p": 5.0},
    # Gemini Omni 1.1 Flash via BeatAPI: $0.48 / $0.72 per
    # 8-second clip.
    "omni11": {"720p": 6.0, "1080p": 9.0},
    # Hailuo 2.3 via Kie (follow-up pass, 2026-11-16): Kie sells
    # it per clip, from $0.15 per 6 s 768p clip, carried as
    # $0.15/6 s = 2.5c/s at the 720p tier. Kie publishes no
    # Pro/1080p price, so the lane is 720p-only until one appears
    # (MODEL_RESOLUTIONS below). The per-second carry assumes the
    # per-clip price scales with duration; settlement corrects to
    # the supplier's reported charge where one exists. The model
    # is silent on every route (no native audio). fal's per-clip
    # rates are the fallback below.
    "hailuo23": {"720p": 2.5},
    # MiniMax H3 via BeatAPI (route rule: $0.02/s entry vs fal's
    # $0.05/s): $0.16 / $0.24 / $0.376 per 8-second clip.
    # Reference videos bill at the output rate on this supplier,
    # so the output + reference-second estimate is exact.
    "minimax_h3": {"480p": 2.0, "720p": 3.0, "1080p": 4.7},
    # Grok Imagine Video 1.5 via Kie (follow-up pass, 2026-11-16):
    # Kie publishes $0.012/s at 480p and $0.0225/s at 720p, the
    # cheapest route at both resolutions; no 1080p price is
    # published, so the lane drops its 1080p tier
    # (MODEL_RESOLUTIONS below). BeatAPI's rates ($0.20 / $0.40 /
    # $0.72 per 8-second clip) are in DISPLACED_RATES below.
    "grok": {"480p": 1.2, "720p": 2.25},
    # Luma Ray 3.2 via fal is priced per clip ($0.50 / $1.00 /
    # $2.00 at 5 s, double at 10 s): a flat per-second rate at
    # every duration, carried as published.
    "luma_ray32": {"480p": 10.0, "720p": 20.0, "1080p": 40.0},
    # LTX-2.5 via fal, Pro tier: $0.12/s at 720p, $0.17/s at
    # 1080p (4K exists at $0.34/s and is not sold).
    "ltx25": {"720p": 12.0, "1080p": 17.0},
    # LTX-2.5 Fast via fal: $0.09/s at 720p, $0.13/s at 1080p.
    "ltx25_fast": {"720p": 9.0, "1080p": 13.0},
    # HappyHorse 1.1 via OpenRouter (follow-up pass, 2026-11-16):
    # OpenRouter publishes $0.0988/s at 720p and $0.1278/s at
    # 1080p, cheaper than BeatAPI's $0.96 / $1.24 per 8-second
    # clip (DISPLACED_RATES below).
    "happyhorse": {"720p": 9.88, "1080p": 12.78},
    # Pika 2.2 via fal is priced per clip; the audit's published
    # approximations ($0.20 / $0.45 per 5-second clip, doubling
    # at 10 s) convert to these per-second rates.
    "pika22": {"720p": 4.0, "1080p": 9.0},
    # Vidu Q2 deliberately carries NO rate: the audit found no
    # published per-second price, and an invented one would sell
    # the lane at a guessed cost. The lane stays paused (the
    # platform pauses any model with no rates) until a published
    # rate is recorded here.
    # Vidu Q3 via Novita (follow-up pass, 2026-11-16): the first
    # published per-second route for Q3. Novita's Turbo endpoints
    # publish off-peak $0.0179/s at 540p and $0.0268/s at 720p
    # (peak $0.0536/s at 720p); the lane sends off_peak=true to
    # realize the off-peak rates, and the platform's 480p tier
    # maps to Novita's 540p tier. No Turbo 1080p rate is
    # published, so the lane tops out at 720p. The direct Vidu
    # route is wired as a spare in app.py but stays unpriced:
    # Vidu sells credits and publishes no per-second price.
    "vidu_q3": {"480p": 1.79, "720p": 2.68},
    # Runway Gen-4.5 direct (follow-up pass, 2026-11-16): $0.12/s
    # (12 credits/s at $0.01 per credit). The published figure has
    # no resolution split, so it is carried flat.
    "runway_gen45": {"720p": 12.0, "1080p": 12.0},
    # --- Uncensored video lanes (2026-11-17 wiring pass). Every
    # lane below is an uncensored build of its model on a
    # supplier that adds no refusal layer of its own, flagged
    # uncensored in app.py's UNCENSORED_VIDEO_MODELS. Higgsfield
    # has no uncensored shelf, so no lane here carries a
    # Higgsfield figure: frontier_lane() sells each at the
    # standard hard minimum (cost x FRONTIER_MIN_MARKUP) and the
    # owner at raw cost, like the other un-compared lanes.
    # Wan 2.2 (uncensored build) via SpicyAPI: the supplier's
    # own catalog API, read 2026-11-17 with the stored key,
    # prices its image-to-video endpoint at $0.019/s at 480p
    # and $0.038/s at 720p. Image mode only on this supplier
    # (no text endpoint is published for the build); silent,
    # like the base model.
    "wan22_unc": {"480p": 1.9, "720p": 3.8},
    # Wan 3.0 (uncensored build) via SpicyAPI, same catalog
    # read: $0.057 / $0.114 / $0.228 per second. Image mode
    # only, same basis as above. HotAPI's route for the same
    # build is in DISPLACED_RATES below.
    "wan3_unc": {"480p": 5.7, "720p": 11.4, "1080p": 22.8},
    # Seedance 2.5 (uncensored build) via SpicyAPI, same
    # catalog read: $0.216 / $0.432 / $1.08 per second, the
    # same with generate_audio on or off (the lane ships
    # audio on). A 4K tier exists at $2.16/s and is not sold.
    # Image mode only, same basis as above.
    "seedance25_unc": {"480p": 21.6, "720p": 43.2, "1080p": 108.0},
    # Vidu Q3 (uncensored build) via SpicyAPI, same catalog
    # read: $0.0665/s at its 540p tier, $0.1425 at 720p,
    # $0.152 at 1080p. The platform's 480p tier maps to the
    # supplier's 540p tier, the same mapping the Novita Q3
    # lane uses. Image mode only, same basis as above.
    "vidu_q3_unc": {"480p": 6.65, "720p": 14.25, "1080p": 15.2},
    # MiniMax H3 (uncensored build) via HotAPI: the supplier's
    # model page, read 2026-11-17, prices $0.02/s at 480p,
    # $0.03/s at its 768p tier (the platform's 720p tier) and
    # $0.04/s at 1080p. FLAGGED: the page shows those figures
    # as struck-through promo prices against list prices of
    # $0.08 / $0.12 / $0.16 per second. The lane is seeded at
    # the current selling prices; if the promo ends, the seed
    # understates cost until it is re-read, so the funded
    # verification pass must confirm the charged rate before
    # this lane lights.
    "minimax_h3_unc": {"480p": 2.0, "720p": 3.0, "1080p": 4.0},
    # Wan 2.6 Uncensored via Wiro (the only source): Wiro
    # prices per run, and the run price scales linearly with
    # duration (720p $0.50 / $1.00 / $1.50 and 1080p $0.75 /
    # $1.50 / $2.25 for 5 / 10 / 15 s), so it carries as a
    # flat per-second rate, exactly like the Luma per-clip
    # carry above.
    "wan26_unc": {"720p": 10.0, "1080p": 15.0},
    # Wan 2.7 Uncensored via Wiro: per run, linear in
    # duration (720p $0.65 / $1.30 / $1.95 and 1080p $1.00 /
    # $2.00 / $3.00 for 5 / 10 / 15 s), carried per-second on
    # the same basis. HotAPI's route is in DISPLACED_RATES
    # below.
    "wan27_unc": {"720p": 13.0, "1080p": 20.0},
    # Wan 3.0 Pro Uncensored via Wiro (the only source): per
    # run, linear in duration; the 1080p tier is $0.90 per
    # 5 s = $0.18/s. 2K ($1.00 per 5 s) and 4K ($1.15 per
    # 5 s) tiers exist and are not sold, like LTX's 4K.
    "wan3pro_unc": {"1080p": 18.0},
    # Vidu Q4, PixVerse V6 and Seedance 2.5 Flash are wired via
    # WaveSpeed in app.py but deliberately carry NO rate here:
    # WaveSpeed publishes only per-run "from" prices for them
    # (Q4 from $0.095/run, PixVerse from $0.04/run; the Flash
    # per-second figure is not published on its model page), and
    # a per-run price cannot seed a per-second lane without
    # inventing a rate. They price-pause like Vidu Q2 until a
    # per-second price is published.
}

# Fallback supplier data, kept for the record and never shown to
# users: Seedance 2.5 via fal costs $0.215/s (480p) and $0.462/s
# (720p) with no 1080p tier, which is why the lane's primary
# supplier is BeatAPI (Pass 2). The Wan and Kling 3.0 rows are
# the fal routes those lanes ran on before the 2026-11-16 route
# rule made BeatAPI primary; their fal endpoints stay wired in
# app.py, so a fallback is a provider flip, not a rebuild. The
# Seedance 2.0 rows are fal's published text-to-video rates from
# the 2026-11-16 audit, recorded in case that family's primary
# lane ever needs a fallback wiring. If a primary supplier ever
# goes away, this table is the starting point.
FALLBACK_PROVIDER_RATES_CENTS_PER_SECOND = {
    "seedance": {"480p": 21.5, "720p": 46.2},
    "wan3": {"480p": 5.0, "720p": 10.0, "1080p": 20.0},
    "wan3_prime": {"480p": 6.8, "720p": 14.0, "1080p": 28.0},
    "kling3": {"720p": 12.6, "1080p": 16.8},
    "seedance2": {"480p": 30.34, "720p": 30.34, "1080p": 30.34},
    "seedance2_fast": {"720p": 24.34},
    "seedance2_mini": {"720p": 20.34},
    # fal rates displaced by the 2026-11-16 follow-up pass when
    # those lanes moved to cheaper primaries (Kie, PiAPI,
    # Eachlabs). Their fal endpoints stay wired in app.py where
    # they exist, so a fallback is a provider flip, not a rebuild.
    "kling3_omni": {"720p": 11.2, "1080p": 14.0},
    "hailuo23": {"720p": 5.6, "1080p": 8.2},
    "veo3": {"720p": 40.0, "1080p": 40.0},
    "veo3_lite": {"720p": 5.0, "1080p": 8.0},
}

# Rates displaced by the 2026-11-16 follow-up pass to a cheaper
# primary on a NON-fal supplier, plus spare-route rates, kept for
# the record exactly like the fal fallback table above. Nothing
# reads this table; it exists so a route flip never has to
# re-derive a published price.
DISPLACED_RATES_CENTS_PER_SECOND = {
    # BeatAPI rates displaced by Kie / CometAPI / OpenRouter.
    "wan3_prime": {"480p": 6.05, "720p": 10.5, "1080p": 21.0},
    "kling3": {"720p": 11.0, "1080p": 13.0},
    "kling4": {"720p": 18.0, "1080p": 28.0},
    "seedance": {"720p": 19.4},
    "seedance2_fast": {"480p": 6.072, "720p": 12.251},
    "seedance2_mini": {"480p": 3.283, "720p": 7.363},
    "omni12": {"720p": 4.9, "1080p": 7.28},
    "grok": {"480p": 2.5, "720p": 5.0, "1080p": 9.0},
    "happyhorse": {"720p": 12.0, "1080p": 15.5},
    # Spare route: MiniMax direct for H3 (768P flagship $0.08/s).
    # BeatAPI stays primary; the direct submit/poll functions are
    # wired in app.py and this is the published rate if the lane
    # ever flips.
    "minimax_h3_direct": {"720p": 8.0},
    # Uncensored-lane routes displaced by the 2026-11-17 pass
    # (cheapest route wired primary; these stay for the record).
    # HotAPI's Wan 3.0 (uncensored build) route: $0.075/s at
    # 480p from its published guide (its higher tiers were not
    # read; SpicyAPI is cheaper at the tier that was).
    "wan3_unc": {"480p": 7.5},
    # HotAPI's Wan 2.7 (uncensored build) route: $0.26/s at
    # 720p and $0.40/s at 1080p from its published guide.
    "wan27_unc": {"720p": 26.0, "1080p": 40.0},
}

# Resolutions each per-second model can actually be ordered at.
# Models absent here accept every entry in RESOLUTIONS.
MODEL_RESOLUTIONS = {
    "seedance": ("480p", "720p", "1080p"),
    "veo3": ("720p", "1080p"),
    "veo3_fast": ("720p", "1080p"),
    "kling3": ("720p", "1080p"),
    "kling4": ("720p", "1080p"),
    "kling3_omni": ("720p", "1080p"),
    "seedance2_fast": ("480p", "720p"),
    "seedance2_mini": ("480p", "720p"),
    "veo3_lite": ("720p", "1080p"),
    "omni12": ("720p", "1080p"),
    "omni11": ("720p", "1080p"),
    "hailuo23": ("720p",),
    "ltx25": ("720p", "1080p"),
    "ltx25_fast": ("720p", "1080p"),
    "happyhorse": ("720p", "1080p"),
    "pika22": ("720p", "1080p"),
    "grok": ("480p", "720p"),
    "vidu_q3": ("480p", "720p"),
    "vidu_q4": ("720p", "1080p"),
    "seedance25_flash": ("480p", "720p"),
    "pixverse_v6": ("720p", "1080p"),
    "runway_gen45": ("720p", "1080p"),
    "wan22_unc": ("480p", "720p"),
    "wan3_unc": ("480p", "720p", "1080p"),
    "seedance25_unc": ("480p", "720p", "1080p"),
    "vidu_q3_unc": ("480p", "720p", "1080p"),
    "minimax_h3_unc": ("480p", "720p", "1080p"),
    "wan26_unc": ("720p", "1080p"),
    "wan27_unc": ("720p", "1080p"),
    "wan3pro_unc": ("1080p",),
}
PROVIDER_RATE_STALE_SECONDS = 24 * 3600

# Local post cost per finished second for per-second-lane outputs,
# in cents. Measured 2026-11-16: the loudness pass on a 5 s clip is
# a fraction of a CPU second on the platform container; carried at
# 0.1c/s so the lane cost is never understated. The optional GPU
# upscale pass is not sold on these lanes yet and is not in here.
POST_COST_CENTS_PER_SECOND = 0.1

# Hard per-job spend cap = estimate x tolerance (Rule 3). The cap is
# what gets held before dispatch; settling never exceeds it.
HOLD_TOLERANCE = 1.1

# --- Amendment 3 frontier pricing -------------------------------------
# Hard minimum markup on per-second lanes: never sell a second below
# cost x 1.25. Where Higgsfield's effective price clears that
# minimum, the sell rate sits just under it (UNDERCUT_FRACTION of
# their rate); where it does not, the minimum binds.
FRONTIER_MIN_MARKUP = 1.25
UNDERCUT_FRACTION = 0.97

# Higgsfield's effective price for one comparable clip, in cents, on
# the comparison basis used across PARITY-PROGRAM.md: 5 seconds,
# 720p, with audio, at Plus monthly credit rates (~$0.049/credit).
# Wan 3.0: 9 credits -> $0.44 (PARITY-PROGRAM.md s.2). Wan 3.0 Prime:
# 15 credits at the same credit rate -> $0.74 (Higgsfield credit
# tables, read 2026-11). Both sit BELOW what the 3x floor charged
# (152c and 212c per 5 s clip from balance), and Wan 3.0's sits below
# raw provider cost itself, which is why the minimum binds there.
CLIP_BASIS_SECONDS = 5.0
HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP = {
    "wan3": 44.0,
    "wan3_prime": 74.0,
    # Seedance 2.5 is Higgsfield's flagship lane: about $1.72 per
    # 5 s 720p clip on the same credit math (PARITY-PROGRAM.md).
    "seedance": 172.0,
    # Veo 3.1, Veo 3.1 Fast and Kling 3.0 deliberately carry no
    # effective figure: the parity research supplies none for them,
    # and an invented one would poison the undercut test. Their
    # lanes price at the 1.25x minimum until a measured Higgsfield
    # effective exists.
}

# Per-resolution overrides, added by the 2026-11-16 pricing audit.
# The clip table above is a single 720p figure per model; other
# resolutions were scaled from it by the provider-rate ratio. That
# scaling is wrong where Higgsfield publishes its own
# per-resolution credit counts, because Higgsfield's resolution
# multipliers are its own, not the provider's. Published counts
# (Higgsfield's pricing-page table and Generate-button reads,
# 2026-11-03 to 2026-11-09, converted at the same Plus monthly
# credit rate as the table above, $59 / 1200 credits = 4.9167c):
# Seedance 2.5 charges 15 credits per 5 s clip at 480p and 80 at
# 1080p, against 35 at 720p (a 2.29x step the provider-rate ratio
# overstated as 2.62x, which had the 1080p lane selling ABOVE
# Higgsfield's effective price, and the 480p lane too); Wan 3.0
# charges 17.5 credits per 5 s at 1080p. Cells recorded here
# replace the scaled figure outright; every other cell keeps the
# scaled figure.
HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP_BY_RESOLUTION = {
    "seedance": {"480p": 73.75, "1080p": 393.33},
    "wan3": {"1080p": 86.04},
}

# Round 2 (2026-11-16, Jack's strengthened rule): EVERY public
# price must be cheaper than Higgsfield's, no matter what — or
# at raw provider cost where undercutting with a markup is
# impossible but raw cost is still below Higgsfield's price.
# Never below raw cost. A lane whose raw cost meets or exceeds
# Higgsfield's price even at cost is priced at cost and flagged
# (pricing_basis "at_cost_residual" in the frontier_lane
# payload). The x1.25 minimum yields where it blocks this rule.
#
# The table below carries Higgsfield's effective per-second
# price for the compared lanes that are NOT in the Amendment 3
# target tables above. It is comparison-and-cap only: adding a
# lane here can only lower or confirm its sell rate, never raise
# it (lanes in the target tables price toward 0.97x Higgsfield;
# these stay at the x1.25 minimum unless the cap binds). Figures
# are Higgsfield's published credit counts converted at the same
# Plus monthly rate as the audit (4.9167c per credit), per
# second:
# - Kling 3.0: 10 credits per 5 s at 720p WITH sound (the lane
#   ships sound on). Its 1080p sound-on count is unpublished, so
#   that tier carries no figure (round-1 audit: NOCOMP).
# - Kling 4.0: 11.25 credits per 5 s at 720p, 16.25 at 1080p.
# - Seedance 2.0: ~22 credits per 5 s at 720p (21.633). At 1080p
#   the more recent read governs (CamClo, crawled 2026-11-13:
#   ~60 credits -> 59.0); an October 2026 read (~45 credits ->
#   44.25) conflicts and is recorded in the audit doc — the
#   lane's floor sell sits under the governing figure either
#   way it is read for 720p, and under the CamClo figure at
#   1080p.
# - Veo 3.1: ~40 credits per 4 s (49.167). Veo 3.1 Fast: ~16
#   credits per 4 s (19.667). Veo 3.1 Lite: 12 credits per 8 s
#   at 720p (7.375). Grok Imagine: 22.5 credits per 5 s at 720p
#   (22.125).
HIGGSFIELD_POLICY_CENTS_PER_SECOND = {
    "kling3": {"720p": 9.833},
    "kling4": {"720p": 11.063, "1080p": 15.979},
    "seedance2": {"720p": 21.633, "1080p": 59.0},
    "veo3": {"720p": 49.167, "1080p": 49.167},
    "veo3_fast": {"720p": 19.667, "1080p": 19.667},
    "veo3_lite": {"720p": 7.375},
    "grok": {"720p": 22.125},
}


def _higgsfield_policy_cents_per_second(model, resolution):
    """The round-2 comparison figure for a lane outside the
    Amendment 3 target tables, or None where no published
    Higgsfield figure exists for that cell."""
    return HIGGSFIELD_POLICY_CENTS_PER_SECOND.get(
        model, {}).get(resolution)


def _higgsfield_effective_cents_per_second(model, resolution, table):
    """Higgsfield's effective price per second for the comparable
    output, or None where no figure exists. A recorded
    per-resolution figure wins; otherwise the model's 720p clip
    figure scales by the provider-rate ratio, the same shape as
    before."""
    by_res = HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP_BY_RESOLUTION.get(
        model, {})
    if resolution in by_res:
        return by_res[resolution] / CLIP_BASIS_SECONDS
    eff = HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP.get(model)
    if eff is None:
        return None
    return (
        eff / CLIP_BASIS_SECONDS
        * float(table[model][resolution])
        / float(table[model]["720p"])
    )


def _ceil3(value: float) -> float:
    """Round UP to 3 decimals: a published minimum or sell rate is
    never a rounding hair below the exact figure it claims. The
    wan3 480p minimum is exactly 4.5625, which round() published
    as 4.562, a sell rate technically under the 1.25x floor (the
    per-job balance debit ceils to whole cents, so no job ever
    actually charged under the floor; this makes the published
    rate itself exact)."""
    import math

    return math.ceil(value * 1000) / 1000.0


def frontier_lane(model: str, resolution: str, rates=None,
                  owner: bool = False) -> dict:
    """The Amendment 3 price picture for one per-second lane at one
    resolution: provider cost per second, Higgsfield's effective
    per-second price for the comparable output, the hard minimum,
    the balance sell rate, and whether the lane can actually
    undercut. Higgsfield's effective price is documented on the
    720p basis; other resolutions scale it by the provider rate
    ratio, the same shape as Higgsfield's own credit table.
    Round 2 (2026-11-16) adds the at-cost cap and the
    pricing_basis field; see HIGGSFIELD_POLICY_CENTS_PER_SECOND.
    For the owner account (owner=True) the sell rate is the raw
    cost itself: no markup, per the owner pricing exemption."""
    table = rates if rates is not None else PROVIDER_RATES_CENTS_PER_SECOND
    cost = per_second_lane_cost_cents(model, resolution, table)
    minimum = cost * FRONTIER_MIN_MARKUP
    higgsfield = _higgsfield_effective_cents_per_second(
        model, resolution, table)
    if owner:
        picture = {
            "cost_cents_per_second": round(cost, 3),
            "minimum_cents_per_second": round(minimum, 3),
            "sell_cents_per_second": round(cost, 3),
            "can_undercut": None,
        }
        picture["higgsfield_effective_cents_per_second"] = (
            round(higgsfield, 3) if higgsfield is not None else None
        )
        return picture
    # Round 2 (2026-11-16): the comparison figure is the
    # Amendment 3 target-table figure where one exists, else the
    # policy-table figure. Target-table lanes price toward 0.97x
    # Higgsfield exactly as before; every other lane sells at the
    # hard minimum. The at-cost cap below then applies to BOTH:
    # where the resulting sell rate would sit at or above
    # Higgsfield's figure, the lane sells at raw cost instead —
    # under Higgsfield where cost allows (pricing_basis
    # "at_cost_undercut"), or flagged as a residual exception
    # where even raw cost meets or exceeds Higgsfield's price
    # ("at_cost_residual"). The cap never raises a price and
    # never prices below cost.
    policy = _higgsfield_policy_cents_per_second(model, resolution)
    effective = higgsfield if higgsfield is not None else policy
    if higgsfield is not None:
        target = higgsfield * UNDERCUT_FRACTION
        if target >= minimum:
            sell, can_undercut = target, True
        else:
            sell, can_undercut = minimum, False
    else:
        # No target-table figure: the lane sells at the hard
        # minimum. Where no policy figure exists either, the
        # undercut question stays honestly unanswered rather than
        # pricing against an invented competitor figure.
        sell, can_undercut = minimum, None
    basis = "standard"
    if effective is not None and sell >= effective:
        sell = cost
        basis = ("at_cost_residual" if cost >= effective
                 else "at_cost_undercut")
        can_undercut = sell < effective
    elif effective is not None and can_undercut is None:
        can_undercut = sell < effective
    return {
        "cost_cents_per_second": round(cost, 3),
        "higgsfield_effective_cents_per_second": (
            round(effective, 3) if effective is not None else None),
        "minimum_cents_per_second": _ceil3(minimum),
        "sell_cents_per_second": _ceil3(sell),
        "can_undercut": can_undercut,
        "pricing_basis": basis,
    }


def per_second_balance_debit_cents(model: str, resolution: str,
                                   billed_seconds: float, rates=None,
                                   owner: bool = False) -> int:
    """What a per-second job charges prepaid balance: the Amendment 3
    sell rate for the lane times the billed seconds, rounded up.
    The owner pays raw cost instead (owner pricing exemption)."""
    import math

    sell = frontier_lane(
        model, resolution, rates, owner=owner
    )["sell_cents_per_second"]
    return math.ceil(sell * float(billed_seconds))


def production_cost_per_second() -> float:
    """Measured production cost per second, cents."""
    return MEASURED_COST_CENTS["production"] / SEGMENT_SECONDS


def included_budget_cents() -> float:
    """The Creator plan's one included render budget, in cents of
    measured cost: net plan revenue divided by the margin
    multiplier. Every lane debits this same budget at its own
    per-second cost (Rule 3 amendment, 2026-11-16)."""
    return net_of(CREATOR_PRICE_CENTS) / MARGIN_MULTIPLIER


def per_second_lane_cost_cents(model: str, resolution: str, rates=None) -> float:
    """Worst-case cost per billed second for a per-second lane.
    Raises ValueError for a model or resolution the rate table does
    not carry, so an unorderable combination fails as a validation
    error instead of a KeyError."""
    table = rates if rates is not None else PROVIDER_RATES_CENTS_PER_SECOND
    try:
        rate = float(table[model][resolution])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"no provider rate for {model} at {resolution}"
        ) from None
    return rate + POST_COST_CENTS_PER_SECOND


def per_second_estimate(model: str, resolution: str, output_seconds: float,
                        reference_video_seconds: float = 0.0, rates=None,
                        owner: bool = False) -> dict:
    """The exact quote for a per-second-lane job (amended Rule 3):
    raw cost, the balance debit at the Amendment 3 sell rate, and
    the hold caps. The included-budget side is untouched by the
    amendment: budget debits stay denominated at raw cost. For the
    owner the sell rate is raw cost, so the debit and the balance
    hold cap both resolve to cost (owner pricing exemption)."""
    import math

    billed = float(output_seconds) + float(reference_video_seconds)
    raw = per_second_lane_cost_cents(model, resolution, rates) * billed
    debit = per_second_balance_debit_cents(
        model, resolution, billed, rates, owner=owner
    )
    sell = frontier_lane(
        model, resolution, rates, owner=owner
    )["sell_cents_per_second"]
    return {
        "billed_seconds": billed,
        "raw_cost_cents": raw,
        "sell_cents_per_second": sell,
        "balance_debit_cents": debit,
        "cap_raw_cents": raw * HOLD_TOLERANCE,
        "cap_balance_cents": math.ceil(sell * billed * HOLD_TOLERANCE),
    }


def creator_included_production_seconds() -> int:
    """Production seconds the Creator plan can include at the floor.

    Net plan revenue divided by the per-second floor cost of the
    production lane (measured cost x multiplier, per second).
    """
    net = net_of(CREATOR_PRICE_CENTS)
    floor_cost_per_second = (
        MEASURED_COST_CENTS["production"] * MARGIN_MULTIPLIER / SEGMENT_SECONDS
    )
    return int(net // floor_cost_per_second)


def public_pricing(owner: bool = False) -> dict:
    """Everything the pricing page is allowed to say, computed here so
    no page ever hardcodes a number the formula would contradict.
    With owner=True the sell figures resolve to raw cost (owner
    pricing exemption); the standard figures stay in the payload
    alongside (floor prices, measured costs) exactly as before."""
    return {
        "margin_multiplier": MARGIN_MULTIPLIER,
        "creator_price_cents": CREATOR_PRICE_CENTS,
        "creator_included_production_seconds": creator_included_production_seconds(),
        "job_price_cents": job_price_table(owner),
        "floor_price_cents": {lane: floor_price_cents(lane)
                              for lane in MEASURED_COST_CENTS},
        "measured_cost_cents": MEASURED_COST_CENTS,
        "topup_packs_cents": TOPUP_PACKS_CENTS,
        "segment_seconds": SEGMENT_SECONDS,
        "lanes_paused": {lane: lane_paused(lane) for lane in MEASURED_COST_CENTS},
        "included_budget_cents": round(included_budget_cents(), 2),
        "clipping": {
            "included_cents_per_source_minute": CLIP_INCLUDED_CENTS_PER_MINUTE,
            "balance_cents_per_source_minute": clip_balance_rate_cents(owner),
            "cost_cents_per_source_minute": CLIP_COST_CENTS_PER_MINUTE,
            "max_source_seconds": CLIP_MAX_SOURCE_SECONDS,
            "max_upload_bytes": CLIP_MAX_UPLOAD_BYTES,
            "max_clips_per_job": CLIP_MAX_CLIPS_PER_JOB,
        },
        "image": {
            "price_cents": image_price_cents(owner),
            "measured_cost_cents": IMAGE_MEASURED_COST_CENTS,
        },
        "per_second_rates_cents": PROVIDER_RATES_CENTS_PER_SECOND,
        "post_cost_cents_per_second": POST_COST_CENTS_PER_SECOND,
        "frontier_min_markup": FRONTIER_MIN_MARKUP,
        "frontier_lanes": {
            model: {
                res: frontier_lane(model, res, owner=owner)
                for res in PROVIDER_RATES_CENTS_PER_SECOND[model]
            }
            for model in set(HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP)
            | set(HIGGSFIELD_POLICY_CENTS_PER_SECOND)
        },
    }


# ---------------------------------------------------------------------------
# Clipping (CLIPPING-DESIGN.md section 6). Cost measured in the design's
# smoke test and production estimate: worst case 0.3 cents per source
# minute all in (CPU compute about 0.20, hosted picking about 0.02,
# storage and egress about 0.02, retries absorbed). The Rule 2 floor at
# the 3x multiplier is therefore 0.9 cents per source minute, which is
# exactly the rate the Creator plan's shared included budget is debited
# at (marketed as up to 300 clipping minutes a month). Balance users pay
# 5 cents per source minute, about half of the main competitor's
# effective 9.7 cents and about 16x worst-case cost. Free accounts get
# no clipping at launch. Source minutes are the probed duration rounded
# up to whole minutes. Amendment 2's hard ceiling applies with the
# worst-case cost figure below.
# ---------------------------------------------------------------------------

CLIP_COST_CENTS_PER_MINUTE = 0.3
CLIP_INCLUDED_CENTS_PER_MINUTE = 0.9
CLIP_BALANCE_CENTS_PER_MINUTE = 5.0
CLIP_MAX_SOURCE_SECONDS = 3 * 3600
CLIP_MAX_UPLOAD_BYTES = 2 * 1024 ** 3
CLIP_MAX_CLIPS_PER_JOB = 6


def clip_source_minutes(duration_seconds: float) -> int:
    """Source minutes billed for a clip job: probed duration rounded
    up to whole minutes, at least one."""
    import math

    return max(1, math.ceil(float(duration_seconds) / 60.0))


def clip_balance_rate_cents(owner: bool = False) -> float:
    """The per-source-minute balance rate for clipping: the standard
    rate for everyone, raw cost for the owner (owner exemption)."""
    return CLIP_COST_CENTS_PER_MINUTE if owner else CLIP_BALANCE_CENTS_PER_MINUTE


def clip_quote(duration_seconds: float, owner: bool = False) -> dict:
    import math

    minutes = clip_source_minutes(duration_seconds)
    return {
        "source_minutes": minutes,
        "included_debit_cents": round(minutes * CLIP_INCLUDED_CENTS_PER_MINUTE, 2),
        "balance_charge_cents": int(math.ceil(minutes * clip_balance_rate_cents(owner))),
        "worst_case_cost_cents": round(minutes * CLIP_COST_CENTS_PER_MINUTE, 2),
        "max_source_seconds": CLIP_MAX_SOURCE_SECONDS,
        "max_upload_bytes": CLIP_MAX_UPLOAD_BYTES,
        "max_clips_per_job": CLIP_MAX_CLIPS_PER_JOB,
    }


# ---------------------------------------------------------------------------
# Start-image generation (PARITY-PROGRAM Pass 3). A self-hosted lane,
# so Rule 2 applies in full: the price is the worst-case measured
# cost x the margin multiplier, grossed up for Stripe fees by the
# same gross_up() the video lanes use, charged exactly at the floor
# with no rounding to a nicer number.
#
# Measured 2026-11-16 (one proof render through the deployed worker,
# 9:16 at 720x1280, 8 steps): 64.3 s of L40S wall for the worker
# call, cold start and model load included (24.1 s of that was the
# generation itself), at the L40S list rate of $0.000542/s =
# $0.0349 per image. Carried at 5 cents worst case, the same
# cold-start headroom shape the production lane uses (verified
# $0.117 carried at 15c). Floor: gross_up(5 x 3) = 47 cents per
# image from balance. The Creator included budget debits at the raw
# carried cost, 5 cents per image (Amendment 1 one-budget rule).
# Free accounts may generate start images for their Draft clips,
# counted exactly like a Draft render against the free ceilings
# (5 lifetime per account, platform-wide total in config): the free
# tier is a capped marketing budget and a first frame is part of a
# Draft clip's cost either way.
# ---------------------------------------------------------------------------

IMAGE_MEASURED_COST_CENTS = 5.0
IMAGE_PRICE_CENTS = gross_up(IMAGE_MEASURED_COST_CENTS * MARGIN_MULTIPLIER)


# ---------------------------------------------------------------------------
# Effects (PARITY-PROGRAM Pass 9, pulled forward). A self-hosted
# lane (Wan 2.1 I2V 480p base plus one effect LoRA per job), so
# Rule 2 applies in full. The lane renders with the effects app's
# fast preset: the LightX2V 2.1 step-distill LoRA at strength 1.0,
# 6 steps, euler, cfg 1.0 (the stock recipe is 20 steps at cfg 6).
# Measured 2026-11-16 (two proof renders through the platform,
# Inflate on the balloon benchmark, 480x832, seed 12345): 101.5 s
# and 93.6 s of worker wall on L40S; the worse of the two is
# EFFECTS_MEASURED_SECONDS, which is $0.055 at the L40S list rate
# of $0.000542/s. That measure sits inside the Production lane's
# 15 cent worst case, so effects price exactly as Production
# segments: MEASURED_COST_CENTS["effects"] = 15, the balance price
# is JOB_PRICE_CENTS["effects"] (the same 99 cents the formula
# gives Production), and a Creator's included budget debits at the
# raw carried cost, 15 cents per effect. If a later measure ever
# pushes the lane past 15 cents, this entry must rise before the
# lane sells another job (Rule 2).
# ---------------------------------------------------------------------------

EFFECTS_MEASURED_SECONDS = 101.5


def image_price_cents(owner: bool = False) -> int:
    """The balance price for one start image: the floor price for
    everyone, raw measured cost for the owner (owner exemption)."""
    import math

    if owner:
        return int(math.ceil(IMAGE_MEASURED_COST_CENTS))
    return IMAGE_PRICE_CENTS


def image_quote(owner: bool = False) -> dict:
    return {
        "price_cents": image_price_cents(owner),
        "measured_cost_cents": IMAGE_MEASURED_COST_CENTS,
    }
