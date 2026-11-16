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
    # Wan 3.0 Prime via BeatAPI: $0.484 / $0.84 / $1.68 per
    # 8-second clip = 6.05 / 10.50 / 21.00 per second. fal's route
    # (6.8 / 14.0 / 28.0) is the fallback below.
    "wan3_prime": {"480p": 6.05, "720p": 10.5, "1080p": 21.0},
    # Seedance 2.5 via BeatAPI (Pass 2 supplier change, 2026-11-16):
    # $0.096 / $0.194 / $0.481 per second, no reference video, audio
    # included at no extra cost. With reference videos BeatAPI bills
    # a LOWER per-second rate over output + reference seconds, so
    # estimating every billed second at the no-reference rate is the
    # conservative direction, and settlement uses the provider's own
    # reported charge, never this estimate.
    "seedance": {"480p": 9.6, "720p": 19.4, "1080p": 48.1},
    # Veo 3.1 via fal, with audio (fal lists silent at half):
    # $0.40/s at 720p and 1080p; no 480p tier exists. BeatAPI sells
    # Veo only as fixed 8-second per-clip billing, which does not
    # fit this per-second lane shape, so fal stays primary here
    # despite the higher per-second figure (route-rule caveat,
    # 2026-11-16).
    "veo3": {"720p": 40.0, "1080p": 40.0},
    # Veo 3.1 Fast via fal, with audio: $0.15/s at 720p and 1080p.
    "veo3_fast": {"720p": 15.0, "1080p": 15.0},
    # Kling 3.0 via BeatAPI (route rule): $0.88 / $1.04 per
    # 8-second clip = 11.0 / 13.0 per second, sound included at
    # the same rate. fal's with-audio route (12.6 / 16.8) is the
    # fallback below; fal's endpoints stay wired in app.py.
    "kling3": {"720p": 11.0, "1080p": 13.0},
    # --- Full-shelf wiring, 2026-11-16 (model availability audit).
    # Every rate below is the primary supplier's published rate,
    # verified 2026-11-16; none of these lanes carries a measured
    # Higgsfield effective, so frontier_lane() sells them at the
    # hard minimum (cost x 1.25) with can_undercut unanswered.
    # Kling 4.0 via BeatAPI (kling-v4): carried at the
    # WITH-video-input rates, $1.44 / $2.24 per 8-second clip
    # (no-input is $1.20 / $1.84); settlement uses the provider's
    # reported charge, so carrying the higher rate only ever
    # overstates the estimate.
    "kling4": {"720p": 18.0, "1080p": 28.0},
    # Kling 3.0 Omni via fal, with audio: Standard $0.112/s,
    # Pro $0.14/s; resolution selects the tier, as with Kling 3.0.
    "kling3_omni": {"720p": 11.2, "1080p": 14.0},
    # Seedance 2.0 via BeatAPI (seedance-2): carried at the
    # WITH-video-input rates ($0.618 / $1.247 / $3.107 per
    # 8-second clip; no-input $0.509 / $1.027 / $2.563).
    "seedance2": {"480p": 7.725, "720p": 15.593, "1080p": 38.842},
    # Seedance 2.0 Fast via BeatAPI: with-input $0.486 / $0.980
    # per 8-second clip (no-input $0.409 / $0.826).
    "seedance2_fast": {"480p": 6.072, "720p": 12.251},
    # Seedance 2.0 Mini via BeatAPI: with-input $0.263 / $0.589
    # per 8-second clip (no-input $0.216 / $0.485). Silent model.
    "seedance2_mini": {"480p": 3.283, "720p": 7.363},
    # Veo 3.1 Lite via fal, with audio: $0.05/s at 720p,
    # $0.08/s at 1080p (silent is half).
    "veo3_lite": {"720p": 5.0, "1080p": 8.0},
    # Gemini Omni 1.2 Flash via BeatAPI: $0.392 / $0.5824 per
    # 8-second clip. This is also the pinned successor lane for
    # the Veo 3.1 family, whose fal endpoints carry a
    # 2026-11-17 deprecation date.
    "omni12": {"720p": 4.9, "1080p": 7.28},
    # Gemini Omni 1.1 Flash via BeatAPI: $0.48 / $0.72 per
    # 8-second clip.
    "omni11": {"720p": 6.0, "1080p": 9.0},
    # Hailuo 2.3 via fal is priced per clip, not per second:
    # Standard $0.28 (6 s) / $0.56 (10 s), Pro $0.49 (6 s) /
    # $0.82 (10 s). Carried per second at the worst case in each
    # tier ($0.56/10 s and $0.49/6 s), so the estimate never
    # understates; the lane is silent.
    "hailuo23": {"720p": 5.6, "1080p": 8.2},
    # MiniMax H3 via BeatAPI (route rule: $0.02/s entry vs fal's
    # $0.05/s): $0.16 / $0.24 / $0.376 per 8-second clip.
    # Reference videos bill at the output rate on this supplier,
    # so the output + reference-second estimate is exact.
    "minimax_h3": {"480p": 2.0, "720p": 3.0, "1080p": 4.7},
    # Grok Imagine Video 1.5 via BeatAPI: $0.20 / $0.40 / $0.72
    # per 8-second clip.
    "grok": {"480p": 2.5, "720p": 5.0, "1080p": 9.0},
    # Luma Ray 3.2 via fal is priced per clip ($0.50 / $1.00 /
    # $2.00 at 5 s, double at 10 s): a flat per-second rate at
    # every duration, carried as published.
    "luma_ray32": {"480p": 10.0, "720p": 20.0, "1080p": 40.0},
    # LTX-2.5 via fal, Pro tier: $0.12/s at 720p, $0.17/s at
    # 1080p (4K exists at $0.34/s and is not sold).
    "ltx25": {"720p": 12.0, "1080p": 17.0},
    # LTX-2.5 Fast via fal: $0.09/s at 720p, $0.13/s at 1080p.
    "ltx25_fast": {"720p": 9.0, "1080p": 13.0},
    # HappyHorse 1.1 via BeatAPI: $0.96 / $1.24 per 8-second clip.
    "happyhorse": {"720p": 12.0, "1080p": 15.5},
    # Pika 2.2 via fal is priced per clip; the audit's published
    # approximations ($0.20 / $0.45 per 5-second clip, doubling
    # at 10 s) convert to these per-second rates.
    "pika22": {"720p": 4.0, "1080p": 9.0},
    # Vidu Q2 deliberately carries NO rate: the audit found no
    # published per-second price, and an invented one would sell
    # the lane at a guessed cost. The lane stays paused (the
    # platform pauses any model with no rates) until a published
    # rate is recorded here.
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
    "hailuo23": ("720p", "1080p"),
    "ltx25": ("720p", "1080p"),
    "ltx25_fast": ("720p", "1080p"),
    "happyhorse": ("720p", "1080p"),
    "pika22": ("720p", "1080p"),
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


def frontier_lane(model: str, resolution: str, rates=None) -> dict:
    """The Amendment 3 price picture for one per-second lane at one
    resolution: provider cost per second, Higgsfield's effective
    per-second price for the comparable output, the hard minimum,
    the balance sell rate, and whether the lane can actually
    undercut. Higgsfield's effective price is documented on the
    720p basis; other resolutions scale it by the provider rate
    ratio, the same shape as Higgsfield's own credit table."""
    table = rates if rates is not None else PROVIDER_RATES_CENTS_PER_SECOND
    cost = per_second_lane_cost_cents(model, resolution, table)
    minimum = cost * FRONTIER_MIN_MARKUP
    if model not in HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP:
        # No measured Higgsfield effective for this lane (see the
        # note on the table): the lane sells at the hard minimum and
        # the undercut question stays honestly unanswered, rather
        # than pricing against an invented competitor figure.
        return {
            "cost_cents_per_second": round(cost, 3),
            "higgsfield_effective_cents_per_second": None,
            "minimum_cents_per_second": round(minimum, 3),
            "sell_cents_per_second": round(minimum, 3),
            "can_undercut": None,
        }
    eff_720 = (
        HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP[model] / CLIP_BASIS_SECONDS
    )
    ratio = float(table[model][resolution]) / float(table[model]["720p"])
    higgsfield = eff_720 * ratio
    target = higgsfield * UNDERCUT_FRACTION
    if target >= minimum:
        sell, can_undercut = target, True
    else:
        sell, can_undercut = minimum, False
    return {
        "cost_cents_per_second": round(cost, 3),
        "higgsfield_effective_cents_per_second": round(higgsfield, 3),
        "minimum_cents_per_second": round(minimum, 3),
        "sell_cents_per_second": round(sell, 3),
        "can_undercut": can_undercut,
    }


def per_second_balance_debit_cents(model: str, resolution: str,
                                   billed_seconds: float, rates=None) -> int:
    """What a per-second job charges prepaid balance: the Amendment 3
    sell rate for the lane times the billed seconds, rounded up."""
    import math

    sell = frontier_lane(model, resolution, rates)["sell_cents_per_second"]
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
                        reference_video_seconds: float = 0.0, rates=None) -> dict:
    """The exact quote for a per-second-lane job (amended Rule 3):
    raw cost, the balance debit at the Amendment 3 sell rate, and
    the hold caps. The included-budget side is untouched by the
    amendment: budget debits stay denominated at raw cost."""
    import math

    billed = float(output_seconds) + float(reference_video_seconds)
    raw = per_second_lane_cost_cents(model, resolution, rates) * billed
    debit = per_second_balance_debit_cents(model, resolution, billed, rates)
    sell = frontier_lane(model, resolution, rates)["sell_cents_per_second"]
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


def public_pricing() -> dict:
    """Everything the pricing page is allowed to say, computed here so
    no page ever hardcodes a number the formula would contradict."""
    return {
        "margin_multiplier": MARGIN_MULTIPLIER,
        "creator_price_cents": CREATOR_PRICE_CENTS,
        "creator_included_production_seconds": creator_included_production_seconds(),
        "job_price_cents": JOB_PRICE_CENTS,
        "floor_price_cents": {lane: floor_price_cents(lane)
                              for lane in MEASURED_COST_CENTS},
        "measured_cost_cents": MEASURED_COST_CENTS,
        "topup_packs_cents": TOPUP_PACKS_CENTS,
        "segment_seconds": SEGMENT_SECONDS,
        "lanes_paused": {lane: lane_paused(lane) for lane in MEASURED_COST_CENTS},
        "included_budget_cents": round(included_budget_cents(), 2),
        "clipping": {
            "included_cents_per_source_minute": CLIP_INCLUDED_CENTS_PER_MINUTE,
            "balance_cents_per_source_minute": CLIP_BALANCE_CENTS_PER_MINUTE,
            "cost_cents_per_source_minute": CLIP_COST_CENTS_PER_MINUTE,
            "max_source_seconds": CLIP_MAX_SOURCE_SECONDS,
            "max_upload_bytes": CLIP_MAX_UPLOAD_BYTES,
            "max_clips_per_job": CLIP_MAX_CLIPS_PER_JOB,
        },
        "image": {
            "price_cents": IMAGE_PRICE_CENTS,
            "measured_cost_cents": IMAGE_MEASURED_COST_CENTS,
        },
        "per_second_rates_cents": PROVIDER_RATES_CENTS_PER_SECOND,
        "post_cost_cents_per_second": POST_COST_CENTS_PER_SECOND,
        "frontier_min_markup": FRONTIER_MIN_MARKUP,
        "frontier_lanes": {
            model: {
                res: frontier_lane(model, res)
                for res in PROVIDER_RATES_CENTS_PER_SECOND[model]
            }
            for model in HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP
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


def clip_quote(duration_seconds: float) -> dict:
    import math

    minutes = clip_source_minutes(duration_seconds)
    return {
        "source_minutes": minutes,
        "included_debit_cents": round(minutes * CLIP_INCLUDED_CENTS_PER_MINUTE, 2),
        "balance_charge_cents": int(math.ceil(minutes * CLIP_BALANCE_CENTS_PER_MINUTE)),
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


def image_quote() -> dict:
    return {
        "price_cents": IMAGE_PRICE_CENTS,
        "measured_cost_cents": IMAGE_MEASURED_COST_CENTS,
    }
