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
    "wan3": {"480p": 5.0, "720p": 10.0, "1080p": 20.0},
    "wan3_prime": {"480p": 6.8, "720p": 14.0, "1080p": 28.0},
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
    """Worst-case cost per billed second for a per-second lane."""
    table = rates if rates is not None else PROVIDER_RATES_CENTS_PER_SECOND
    return float(table[model][resolution]) + POST_COST_CENTS_PER_SECOND


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
