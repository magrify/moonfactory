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
    # The house Uncut lane (2026-11-18, Jack's order): the
    # community Wan 2.2 NSFW LoRA pair on the house A14B worker.
    # Priced exactly as the house Production lane (same measured
    # cost entry, so the shared formulas give it the same job
    # price and the same raw cost for the owner).
    "nsfw": 15,
    # The LTX-2.5 Uncensored lane (2026-11-20, Jack's order): the
    # ChrisColeTech FP8 build of LTX-2.5 on the house worker,
    # Modal execution (the one house lane that stayed on Modal
    # when the others moved to the free Kaggle worker; Jack's
    # call). PROVISIONAL entry: no render has run yet, so this
    # is the pre-render estimate's worst case, not a measurement
    # — Modal's L40S list rate ($0.000542/s, the rate the entries
    # above derive from) times a 480 s worst-case wall for one
    # 5 s 720p-class clip (cold start, the 37.7 GB model set
    # loading, 8 distilled steps, joint audio/video decode)
    # = $0.26. The expected wall is about 270 s (~$0.15). The
    # approved proof render's measured wall replaces this entry
    # before the lane is promoted anywhere; the shared formulas
    # give the lane its job price and the owner this raw cost
    # in the meantime, exactly the Uncut lane's structure.
    "ltx_unc": 26,
    # HunyuanVideo 1.5 (2026-11-26, Jack's order): Tencent's
    # 8.3B model on the house Kaggle worker (the 480p FP8
    # distilled builds), priced on the Production lane's
    # serving basis like the other Kaggle house lanes: the
    # same 15 cent entry, so the shared formulas give it the
    # same 99 cent job price and the owner the same raw cost
    # per segment. PROVISIONAL in the LTX sense: no render has
    # run yet, so this is the house basis, not a measurement;
    # the first measured wall replaces it if it lands higher.
    "hunyuan15": 15,
}

CREATOR_PRICE_CENTS = 2000  # $20/mo

# Top-up packs (Jack, 2026-11-19): the Billing panel presets
# are $5, $10, $20, $50, and $100, with a custom amount field
# beside them. The smallest top up is $1, preset or custom,
# and the full picked or typed amount is what lands in the
# balance, exactly as top ups have always credited. Auto
# reload keeps its own smaller amount list: a reload is a
# standing instruction picked once, not a panel preset, and
# its $1 option stays valid under the same minimum.
TOPUP_PACKS_CENTS = [500, 1000, 2000, 5000, 10000]
TOPUP_MIN_CENTS = 100
RELOAD_AMOUNTS_CENTS = [100, 500, 1000, 2000]
# Autopay charge ceiling (Jack, 2026-11-27): one automatic
# top up triggered by a blocked job never exceeds $50. When
# a job needs more than that on top of the balance, no charge
# is attempted and the job refuses the way it always has.
AUTOPAY_MAX_CHARGE_CENTS = 5000


def valid_topup_cents(amount) -> bool:
    """A top up amount the checkout accepts: any whole cent
    amount at or above the $1 minimum (the presets are simply
    the panel's one tap versions of valid amounts)."""
    try:
        cents = int(amount)
    except (TypeError, ValueError):
        return False
    return cents >= TOPUP_MIN_CENTS


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
    # Wan 3.0 via FrameHush (route audit, 2026-11-26: the
    # cheapest route is primary). FrameHush's estimate endpoint,
    # read on the platform's own account, prices wan-3.0 at
    # $0.15 / $0.30 / $0.485 per 5-second clip = 3.0 / 6.0 / 9.7
    # per second, under BeatAPI's $0.284 / $0.568 / $1.088 per
    # 8-second clip (3.55 / 7.10 / 13.60, recorded in
    # DISPLACED_RATES below as wan3_beatapi); fal's route
    # (5.0 / 10.0 / 20.0) is recorded as the fallback below.
    # The customer shelf stays frozen on the BeatAPI-built
    # rates (SHELF_PROVIDER_RATES below); the saving lands
    # as margin.
    "wan3": {"480p": 3.0, "720p": 6.0, "1080p": 9.7},
    # Wan 3.0 Prime via CometAPI (follow-up pass, 2026-11-16):
    # CometAPI publishes a single $0.0544/s figure with no
    # resolution split, carried flat at every resolution (flagged:
    # resolution basis unconfirmed). BeatAPI's rates (6.05 /
    # 10.50 / 21.00) are in DISPLACED_RATES below; fal's route
    # (6.8 / 14.0 / 28.0) is the fallback below.
    "wan3_prime": {"480p": 5.44, "720p": 5.44, "1080p": 5.44},
    # Wan Motion (motion transfer) via fal: fal-ai/wan-motion
    # publishes $0.06 per video-second at 720p (fal model page,
    # read 2026-11-17). The output follows the driving video's
    # length; the rate is per output second.
    "wan_motion": {"720p": 6.0},
    # Seedance 2.5 via FrameHush (rerouted 2026-11-26, route
    # rule: the cheapest verified route is primary): FrameHush's
    # seedance-2-5 endpoint bills per generated second at
    # $0.035 (480p) / $0.047 (720p) / $0.08 (1080p), read
    # 2026-11-26 from the platform's own signed-in account
    # (account rates equal the public rates; failed tasks are
    # not charged, the charge lands on completion, and the
    # completed response reports the exact charged_price, which
    # settlement uses, never this estimate). The route this
    # displaces is the 2026-11-16 mixed route (CometAPI at
    # 480p/720p, $0.0824 / $0.1848; BeatAPI at 1080p, $0.481);
    # those rates are recorded in DISPLACED_RATES below and
    # both submits stay wired in app.py as spares. FrameHush
    # 4K tier (wired 2026-11-27): FrameHush publishes the tier
    # at $0.09/s (read on the platform's own account through
    # its estimate endpoint, 2026-11-26), and Higgsfield's own
    # published Seedance 2.5 credit counts now include a 4K
    # count (140 credits per 5 s clip; see
    # HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP_BY_RESOLUTION), so
    # the tier prices against a measured Higgsfield figure
    # like the other three. The FrameHush submit sends the
    # resolution on every job, so the tier is a real choice
    # the moment it is priced here and listed in
    # MODEL_RESOLUTIONS.
    "seedance": {"480p": 3.5, "720p": 4.7, "1080p": 8.0,
                 "4K": 9.0},
    # Veo 3.1 via Kie (rerouted 2026-11-25, frontier coverage
    # pass): Kie bills the model flat per clip, native audio
    # included — 250 credits ($1.25) per 720p video, 255
    # credits ($1.275) per 1080p video, 370 credits ($1.85)
    # per 4K video (Kie's published Veo 3.1 pricing, read
    # 2026-11-25; the governing cost lives in
    # FLAT_CLIP_COST_CENTS below). The rate table carries only
    # the worst-case per-second ceiling, the flat cost at the
    # lane's shortest 4 s length. The route this displaces,
    # PiAPI at $0.12/s on the no-audio basis, is out under
    # Jack's no-expiring-credits rule (PiAPI credits expire);
    # its rates are recorded in DISPLACED_RATES below and its
    # submit stays wired as a spare. fal's with-audio route
    # ($0.40/s, $0.60/s at 4K) is the fallback below and now
    # covers every tier alike, 4K included.
    "veo3": {"720p": 31.25, "1080p": 31.875, "4K": 46.25},
    # Veo 3.1 Fast via Kie (rerouted 2026-11-25, frontier
    # coverage pass): flat per clip with native audio — 60
    # credits ($0.30) per 720p video, 65 credits ($0.325) per
    # 1080p video, 180 credits ($0.90) per 4K video (Kie's
    # published Veo 3.1 pricing, read 2026-11-25; governing
    # cost in FLAT_CLIP_COST_CENTS below, rate table carries
    # the 4 s ceiling only). Displaces the fal per-second
    # route ($0.15/s at 720p and 1080p, $0.35/s at 4K, with
    # audio), whose rates move to the fallback table below;
    # the fal endpoints stay wired in app.py.
    "veo3_fast": {"720p": 7.5, "1080p": 8.125, "4K": 22.5},
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
    # 4K tier (2026-11-17, 4K expansion): the same Kie page
    # prices the model's 4K mode at 75.5 credits ($0.3775/s)
    # with or without audio, 3840x2160; the Kie submit sends
    # mode="4K" for the tier (app.py).
    # Mode split (Kling route hunt, 2026-11-27): text and
    # image jobs run on SpicyAPI at 10.08 / 13.44 / 33.6
    # (recorded in DISPLACED_RATES as kling3_spicyapi), the
    # cheapest verified route for those modes; this row stays
    # the Kie basis because reference jobs share it and still
    # run on Kie (SpicyAPI publishes no Kling 3.0 reference
    # endpoint), so quotes, holds and the cost ceiling never
    # understate a reference job's cost.
    "kling3": {"720p": 11.35, "1080p": 15.1, "4K": 37.75},
    # --- Full-shelf wiring, 2026-11-16 (model availability audit).
    # Every rate below is the primary supplier's published rate,
    # verified 2026-11-16; none of these lanes carries a measured
    # Higgsfield effective, so frontier_lane() sells them at the
    # hard minimum (cost x 1.25) with can_undercut unanswered.
    # Kling 4.0 via VidLab (rerouted 2026-11-23, Jack's order:
    # VidLab is the cheapest verified route): VidLab's own
    # catalog (GET /v1/models, read 2026-11-23) prices kling-4.0
    # at $0.135/s at 720p and $0.20/s at 1080p, below Kie's
    # no-input rates ($0.1425 / $0.2125, in DISPLACED_RATES
    # below as kling4_kie). Settlement uses the provider's own
    # reported charge (the generation's price_usd), never this
    # estimate. BeatAPI's with-input rates (18.0 / 28.0) are in
    # DISPLACED_RATES below.
    # Kling route hunt (2026-11-27): every other supplier's
    # live catalog was re-read and none beats VidLab for the
    # text/image basis (Kie keyframe 14.25 / 21.25, BeatAPI
    # 15.0 / 23.0, fal 15.7 / 23.5, SpicyAPI 18.84 / 28.2;
    # Eachlabs and CometAPI carry the model but publish no
    # per-second price). VidLab's own with-video-input rates
    # (8.0 / 12.0) already apply to reference jobs carrying a
    # video and settle at the reported charge. Both tiers'
    # no-input cost still exceeds Higgsfield's retail (11.063
    # / 15.979 in the policy table), so the lane stays an
    # at-cost residual: the no-profit case, reported to the
    # owner 2026-11-27.
    "kling4": {"720p": 13.5, "1080p": 20.0},
    # Kling 4.0 Flash via Kie (wired 2026-11-27, Kling route
    # hunt): Kie's catalog prices both Flash endpoints at a
    # flat $0.085/s at 720p, the lane's only tier (Kie's
    # kling-4.0/flash-keyframe-to-video and
    # kling-4.0/flash-reference-to-video entries, read
    # 2026-11-27). The reference endpoint's with-video rate
    # is $0.0565/s but bills the input duration too, so the
    # no-video rate is the honest lane basis, the same shape
    # as Kling 4.0's own reference billing note above.
    # Durations 3-20 s, native audio generated by default.
    # Higgsfield sells Flash at $0.1475/s (the policy figure
    # below), so the lane prices at the standard x1.25
    # minimum and undercuts with the owner's margin intact.
    "kling4_flash": {"720p": 8.5},
    # MAGI-2 by Sand.ai via FrameHush (rerouted 2026-11-26,
    # route rule: the cheapest verified route is primary):
    # FrameHush's catalog carries the model as magi-2-1080P at
    # a flat $0.06 per generated second, 1080p only, generated
    # audio included, priced live against the platform's own
    # FrameHush account through its estimate endpoint (4s
    # $0.24, 5s $0.30, 10s $0.60, 30s $1.80, perfectly linear;
    # read 2026-11-26). That undercuts the route wired
    # 2026-11-25, Higgsfield's own API at a flat $0.08/s
    # (Higgsfield's MAGI-2 API reference,
    # open.higgsfield.ai/models/sand/magi-2, read 2026-11-25);
    # its rate is recorded in DISPLACED_RATES below as
    # magi2_higgsfield and its submit stays wired in app.py as
    # the spare. Standard pricing (Jack's ruling, 2026-11-25):
    # customers pay the normal markup through the standard
    # customer_debit_cents path like every regular lane; this
    # lane is NOT an at-cost exception and carries no
    # HIGGSFIELD_POLICY_CENTS_PER_SECOND entry. The owner pays
    # the raw rate, as on every lane. CUSTOMER SHELF FREEZE
    # (2026-11-26, Jack: "cut OUR cost, not our users'
    # prices"): the reroute does not move the customer price —
    # the shelf basis keeps the $0.08/s rate the shelf was
    # built on (SHELF_PROVIDER_RATES_CENTS_PER_SECOND below)
    # and the saving lands as margin.
    "magi2": {"1080p": 6.0},
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
    # 4K tier (2026-11-17, 4K expansion): Kie's own Kling 3.0
    # Omni page (kie.ai/kling-o3) prices 4K at 75.5 credits
    # ($0.3775/s) with or without native audio, and Kie's API
    # docs give the omni resolution enum as 720p / 1080p / 4k;
    # the Kie submit sends resolution="4k" for the tier (app.py).
    "kling3_omni": {"720p": 10.1, "1080p": 12.6, "4K": 37.75},
    # Seedance 2.0 via BeatAPI (seedance-2): carried at the
    # WITH-video-input rates ($0.618 / $1.247 / $3.107 per
    # 8-second clip; no-input $0.509 / $1.027 / $2.563).
    # 4K tier (2026-11-17, 4K expansion): BeatAPI's own
    # seedance-2 page prices the 4K tier at $6.4152 per 8-second
    # clip with video input ($5.225889 without), so the
    # with-input basis carries 80.19c/s.
    "seedance2": {"480p": 7.725, "720p": 15.593, "1080p": 38.842,
                  "4K": 80.19},
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
    # Seedance 2.0 Fast via SpicyAPI (route audit,
    # 2026-11-26): its catalog prices seedance-2.0-fast at
    # $0.04499712/s at 480p and $0.096768/s at 720p (the
    # generate_audio flag does not change the price), carried
    # as 4.5 / 9.68, under VidLab's $0.05 / $0.11 (recorded in
    # DISPLACED_RATES below as seedance2_fast_vidlab) and
    # Kie's no-video rates ($0.059 / $0.124, in DISPLACED_RATES
    # as seedance2_fast). The lane stays image-references-only
    # in app.py (IMAGE_REFS_ONLY_MODELS), which is also the
    # basis these prices were read on. The customer shelf
    # stays frozen on the VidLab-built rates
    # (SHELF_PROVIDER_RATES below).
    "seedance2_fast": {"480p": 4.5, "720p": 9.68},
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
    # Veo 3.1 Lite via Kie (rerouted 2026-11-25, Jack's
    # no-expiry wins): Kie bills the model FLAT PER CLIP, not
    # per second — 30 credits ($0.15) per 720p video and 35
    # credits ($0.175) per 1080p video, native audio included
    # (Kie's own authenticated catalog
    # API, api.kie.ai/api/v1/models pricingDesc for veo-3-1,
    # read 2026-11-25; kie.ai/veo-3-1 agrees). The rate
    # table's contract is worst-case cost per billed second,
    # so the flat cost is carried here at the lane's shortest
    # sold length (4 s): 15.0 / 4 = 3.75 and 17.5 / 4 = 4.375.
    # Every longer clip costs less per second, never more.
    # The governing cost is FLAT_CLIP_COST_CENTS below: the
    # quote, the balance debit and the settle all price the
    # lane from the true per-clip figure, not from this
    # per-second ceiling. The displaced Eachlabs no-audio
    # rates ($0.03 / $0.08 per second) are in
    # DISPLACED_RATES_CENTS_PER_SECOND below as
    # veo3_lite_eachlabs; fal's with-audio rates
    # ($0.05/s at 720p, $0.08/s at 1080p) are the fallback
    # below. The old Eachlabs route was silent (the model
    # speaks natively but that tier did not); the Kie route
    # carries the model's native audio in the same pass.
    "veo3_lite": {"720p": 3.75, "1080p": 4.375},
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
    # MiniMax H3 via FrameHush (route audit, 2026-11-26):
    # FrameHush's estimate endpoint, read on the platform's
    # own account, prices MiniMax-H3 at a flat $0.075 per
    # 5-second clip at every tier = 1.5 per second, under
    # BeatAPI's $0.16 / $0.24 / $0.376 per 8-second clip
    # (2.0 / 3.0 / 4.7, recorded in DISPLACED_RATES below as
    # minimax_h3_beatapi). The flat estimate is carried flat;
    # the customer shelf stays frozen on the BeatAPI-built
    # rates (SHELF_PROVIDER_RATES below).
    "minimax_h3": {"480p": 1.5, "720p": 1.5, "1080p": 1.5},
    # Grok Imagine Video 1.5 via Kie (follow-up pass, 2026-11-16):
    # Kie publishes $0.012/s at 480p and $0.0225/s at 720p, the
    # cheapest route at both resolutions. BeatAPI's rates ($0.20 /
    # $0.40 / $0.72 per 8-second clip) are in DISPLACED_RATES below.
    # 1080p tier added 2026-11-17 (Jack's order): Kie now publishes
    # 8 credits ($0.04/s) at 1080p on its own Grok Imagine Video 1.5
    # page (re-verified live 2026-11-17); no 1080p price existed
    # when the lane was first wired, so the tier was dropped then.
    "grok": {"480p": 1.2, "720p": 2.25, "1080p": 4.0},
    # Grok Imagine Video 1.5 as its own lane (2026-11-17, Jack's
    # approval): xAI's grok-imagine-video-1.5 served on Kie under
    # the model id grok-imagine-video-1-5-preview, at the same
    # published Kie rates as the lane above (2.4 / 4.5 / 8 credits
    # per second = $0.012 / $0.0225 / $0.04; Kie's own Grok Imagine
    # Video 1.5 page, read 2026-11-17). Durations 1-15 s, native
    # 1080p, native audio incl. lip-synced dialogue. fal's rates
    # ($0.08 / $0.14 / $0.25 per second) are the fallback below;
    # BeatAPI's ($0.20 / $0.40 / $0.72 per 8-second clip) are in
    # DISPLACED_RATES; xAI direct matches fal and needs an xAI key
    # the platform does not hold, so Kie is the cheapest route
    # that works with existing accounts.
    "grok15": {"480p": 1.2, "720p": 2.25, "1080p": 4.0},
    # Grok Imagine Video 1.5 Lite via fal (2026-11-17): fal's own
    # Grok Imagine page prices Lite at $0.02/s (480p) and $0.03/s
    # (720p), matching xAI's direct API exactly; Kie and BeatAPI
    # publish no Lite listing, so fal is the cheapest verified
    # route. The 1080p tier ($0.14/s) is NOT sold: xAI and fal
    # both document it as upscaled from the 720p generation, and
    # upscaled tiers are never sold as genuine (same rule as the
    # Seedance 2.5 Uncensored 4K tier below). Text and image
    # modes only, 1-15 s, native synchronized audio incl.
    # lip-synced dialogue.
    "grok15_lite": {"480p": 2.0, "720p": 3.0},
    # Luma Ray 3.2 via fal is priced per clip ($0.50 / $1.00 /
    # $2.00 at 5 s, double at 10 s): a flat per-second rate at
    # every duration, carried as published.
    "luma_ray32": {"480p": 10.0, "720p": 20.0, "1080p": 40.0},
    # LTX-2.5 via SpicyAPI (route audit, 2026-11-26): its
    # catalog prices lightricks/ltx-2.5 at $0.09 / $0.13 /
    # $0.30 per second (720p / 1080p / 4K), under fal's Pro
    # tier ($0.12 / $0.17 / $0.34, recorded in the fallback
    # table below; the fal endpoints stay wired in app.py, so
    # a fallback is a provider flip). The move also takes the
    # lane off fal's unfunded account, where it cannot run at
    # all today. The customer shelf stays frozen on the
    # fal-built rates (SHELF_PROVIDER_RATES below).
    "ltx25": {"720p": 9.0, "1080p": 13.0, "4K": 30.0},
    # LTX-2.5 Fast via fal: $0.09/s at 720p, $0.13/s at 1080p.
    # 4K tier (2026-11-17, 4K expansion): fal's own LTX-2.5 Fast
    # page prices 4K at $0.30/s; the fal submit maps the tier to
    # its 2160p schema value like the Pro lane (app.py).
    "ltx25_fast": {"720p": 9.0, "1080p": 13.0, "4K": 30.0},
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
    # audio on). A 4K tier exists at $2.16/s and is not sold:
    # SpicyAPI's own pricing pages label that tier "(4K,
    # upscaled)", and upscaled tiers are never sold as 4K
    # (checked again 2026-11-17, 4K expansion).
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
    # Wan 2.6 Uncensored via SpicyAPI (route moved from Wiro
    # 2026-11-17, catalog completion pass): the supplier's
    # own model page prices $0.095/s at 720p and $0.1425/s at
    # 1080p, under the Wiro route's $0.10 / $0.15 per second
    # (recorded in DISPLACED_RATES below). Image mode only;
    # the build generates no audio (supplied-track pacing
    # only, which the platform does not send).
    "wan26_unc": {"720p": 9.5, "1080p": 14.25},
    # Wan 2.7 Uncensored via SpicyAPI (same route move): its
    # own model page prices $0.1235/s at 720p and $0.19/s at
    # 1080p, under Wiro's $0.13 / $0.20 (DISPLACED_RATES).
    # Image mode only; generate_audio on by default there.
    "wan27_unc": {"720p": 12.35, "1080p": 19.0},
    # --- SpicyAPI catalog completion (2026-11-17): the rest
    # of the supplier's spicy video families, each priced
    # from its own model page (read 2026-11-17). All are
    # image-to-video only on this supplier. Full record:
    # ~/workspace/sinclip/spicyapi-video-catalog-2026-11-17.md
    # Wan 3.0 Prime (uncensored build) via SpicyAPI:
    # $0.09 / $0.18 / $0.36 per second; generates sound by
    # default (generate_audio on in the submit wiring).
    "wan3prime_unc": {"480p": 9.0, "720p": 18.0, "1080p": 36.0},
    # Wan 2.2 LoRA (uncensored build) via SpicyAPI: $0.024/s
    # at 480p, $0.048/s at 720p, for its 5 or 8 second steps.
    # Silent. Its page also publishes a video-extend task at
    # $0.038 / $0.076 per second; the platform's chaining
    # covers longer lengths, so extend is not wired.
    "wan22_lora_unc": {"480p": 2.4, "720p": 4.8},
    # Seedance 2.0 (uncensored build) via SpicyAPI: $0.114 /
    # $0.228 / $0.57 per second. A 4K tier is published at
    # $1.14/s with no native/upscaled label on its page, so
    # it is NOT sold (genuine-4K rule); flagged in the
    # catalog record. generate_audio on.
    "seedance2_unc": {"480p": 11.4, "720p": 22.8, "1080p": 57.0},
    # Seedance 2.0 Fast (uncensored build) via SpicyAPI:
    # $0.081/s at 480p, $0.1742/s at 720p. generate_audio on.
    "seedance2fast_unc": {"480p": 8.1, "720p": 17.42},
    # Seedance 2.0 Mini (uncensored build) via SpicyAPI:
    # $0.0387/s at 480p, $0.0821/s at 720p. This build
    # generates sound (generate_audio on), unlike the
    # moderated Mini lane, which is silent on its route.
    "seedance2mini_unc": {"480p": 3.87, "720p": 8.21},
    # Seedance 1.5 Pro (uncensored build) via SpicyAPI: its
    # page splits the rate by its audio switch; the lane
    # ships audio on, so it carries the audio-on rates
    # ($0.024 / $0.052 / $0.104 per second). The audio-off
    # rates ($0.012 / $0.026 / $0.052) are the same page's
    # other column, recorded here for the day the lane ever
    # sells a silent mode.
    "seedance15pro_unc": {"480p": 2.4, "720p": 5.2, "1080p": 10.4},
    # LTX 2.3 (uncensored build) via SpicyAPI: $0.019 /
    # $0.038 / $0.057 per second. Silent: its endpoint
    # publishes no audio field.
    "ltx23_unc": {"480p": 1.9, "720p": 3.8, "1080p": 5.7},
    # LTX 2.3 LoRA (uncensored build) via SpicyAPI: $0.0285 /
    # $0.0475 / $0.0665 per second. Silent, same basis. Its
    # built-in LoRA overrides are not sent (the supplier's
    # default mix applies).
    "ltx23_lora_unc": {"480p": 2.85, "720p": 4.75, "1080p": 6.65},
    # Wan 3.0 Pro Uncensored via Wiro (the only source): per
    # run, linear in duration; the 1080p tier is $0.90 per
    # 5 s = $0.18/s. 2K ($1.00 per 5 s = $0.20/s) and 4K
    # ($1.15 per 5 s = $0.23/s) tiers wired 2026-11-17,
    # parity pass, at the supplier's own tier labels.
    "wan3pro_unc": {"1080p": 18.0, "2K": 20.0, "4K": 23.0},
    # --- VidLab wiring (2026-11-23, Jack's order). Every rate
    # below is VidLab's own catalog price (GET /v1/models, read
    # 2026-11-23), converted to cents per second. The platform
    # refresh re-reads VidLab's public catalog on the same
    # schedule and keeps these lanes' live rates fresh (app.py).
    # Settlement uses the provider's own reported charge (the
    # generation's price_usd), never this estimate.
    # VidLab 1.5: flat $0.02/s at any length; the model makes
    # fixed 10-second clips at one size, carried at the 720p
    # tier (app.py restricts the lane to 10 s and 720p).
    "vidlab15": {"720p": 2.0},
    # VidLab 2: $0.221/s at 720p, $0.377/s at 1080p.
    "vidlab2": {"720p": 22.1, "1080p": 37.7},
    # Vidra 3.0: $0.10 / $0.23 / $0.57 / $1.54 per second at
    # 480p / 720p / 1080p / 4k (its 2k tier at $1.40 is not
    # sold; the platform has no 2K video tier on this lane).
    "vidra3": {"480p": 10.0, "720p": 23.0, "1080p": 57.0,
               "4K": 154.0},
    # Seedance 2.5 Boost: 1080p only at $0.27/s.
    "seedance25_boost": {"1080p": 27.0},
    # Seedance 2.5 Flash via VidLab: $0.085/s at 480p,
    # $0.185/s at 720p. This UNPAUSES the lane: it was wired
    # via WaveSpeed with no published per-second rate (see the
    # closing note this block replaced); VidLab publishes one.
    "seedance25_flash": {"480p": 8.5, "720p": 18.5},
    # PixVerse V6 via VidLab, UNPAUSED the same way: carried
    # at the WITH-AUDIO rates ($0.05/s at 720p, $0.10/s at
    # 1080p; the silent rates are $0.04 / $0.08) because the
    # submit sends generate_audio=true, the lane's shipped
    # shape. Its 540p tier is not sold on this lane.
    "pixverse_v6": {"720p": 5.0, "1080p": 10.0},
    # PixVerse V6 Max via VidLab, with-audio basis like its
    # sibling: $0.05 / $0.07 / $0.13 per second at its 540p /
    # 720p / 1080p tiers; the platform's 480p tier maps to the
    # supplier's 540p tier in the submit (app.py).
    "pixverse_v6_max": {"480p": 5.0, "720p": 7.0, "1080p": 13.0},
    # Vidu Q4 Preview via Kie (route audit, 2026-11-26):
    # Kie's vidu-q4 pages price the tiers at $0.036 / $0.076 /
    # $0.096 per second (540p / 720p / 1080p), under VidLab's
    # $0.06 / $0.12 / $0.13 (recorded in DISPLACED_RATES below
    # as vidu_q4_vidlab). The platform's 480p tier maps to the
    # supplier's 540p tier, the same mapping the VidLab route
    # used. The customer shelf stays frozen on the VidLab-built
    # rates (SHELF_PROVIDER_RATES below).
    "vidu_q4": {"480p": 3.6, "720p": 7.6, "1080p": 9.6},
    # Wan 2.5 via VidLab: $0.05 / $0.08 / $0.13 per second.
    "wan25": {"480p": 5.0, "720p": 8.0, "1080p": 13.0},
    # Wan 2.6 via VidLab: $0.07/s at 720p, $0.11/s at 1080p.
    "wan26": {"720p": 7.0, "1080p": 11.0},
    # Wan 2.6 Flash via VidLab, WITH-AUDIO basis: $0.05/s at
    # 720p, $0.07/s at 1080p (silent is $0.03 / $0.05); the
    # submit sends generate_audio=true.
    "wan26_flash": {"720p": 5.0, "1080p": 7.0},
    # Wan 2.7 via VidLab: $0.09/s at 720p, $0.14/s at 1080p.
    "wan27": {"720p": 9.0, "1080p": 14.0},
    # Kling 2.6 Standard via VidLab: flat $0.05/s, one size,
    # carried at the 720p tier.
    "kling26_std": {"720p": 5.0},
    # Kling O1 via VidLab: $0.09/s at 720p, $0.12/s at 1080p.
    "kling_o1": {"720p": 9.0, "1080p": 12.0},
    # Hailuo 2.3 Fast via VidLab: $0.04/s at its 768p tier
    # (the platform's 720p tier maps to it in the submit),
    # $0.06/s at 1080p.
    "hailuo23_fast": {"720p": 4.0, "1080p": 6.0},
    # MiniMax H3 Max via VidLab (the HOSTED API route; the
    # H3 weights license does not govern hosted API terms):
    # $0.05 / $0.08 / $0.16 per second at 480p / its 768p tier
    # (platform 720p) / 1080p.
    "minimax_h3_max": {"480p": 5.0, "720p": 8.0, "1080p": 16.0},
    # Vidu Q3 Pro via VidLab: $0.07 / $0.15 / $0.16 per second
    # at its 540p (platform 480p) / 720p / 1080p tiers.
    "vidu_q3_pro": {"480p": 7.0, "720p": 15.0, "1080p": 16.0},
    # Vidu Q3 Turbo via VidLab: $0.04 / $0.06 / $0.07.
    "vidu_q3_turbo": {"480p": 4.0, "720p": 6.0, "1080p": 7.0},
    # Vidu Q3 Mix via VidLab: $0.13/s at 720p, $0.15/s at
    # 1080p (reference-images model; no 540p tier published).
    "vidu_q3_mix": {"720p": 13.0, "1080p": 15.0},
    # SkyReels V4 Fast via VidLab: $0.08 / $0.11 / $0.28.
    "skyreels_v4_fast": {"480p": 8.0, "720p": 11.0, "1080p": 28.0},
    # SkyReels V4 Standard via VidLab: $0.11 / $0.14 / $0.35.
    "skyreels_v4_std": {"480p": 11.0, "720p": 14.0, "1080p": 35.0},
    # Seedance 1.5 Pro via VidLab (the moderated model; the
    # uncensored build stays on SpicyAPI): $0.03 / $0.06 /
    # $0.14 per second. This route publishes no audio switch,
    # so the lane is silent-with-voiceover here (LANE_AUDIO).
    "seedance15pro": {"480p": 3.0, "720p": 6.0, "1080p": 14.0},
    # Seedance 1.0 Pro Fast via VidLab: $0.015 / $0.03 /
    # $0.06 per second.
    "seedance10pro_fast": {"480p": 1.5, "720p": 3.0, "1080p": 6.0},
    # Vidu Q2 still deliberately carries NO rate (see its note
    # above the VidLab block): no published per-second price
    # anywhere, VidLab included (it lists no Vidu Q2).
}

# Fallback supplier data, kept for the record and never shown to
# users: Seedance 2.5 via fal costs $0.215/s (480p) and $0.462/s
# (720p) with no 1080p tier (the lane's primary is FrameHush
# since 2026-11-26; before that the mixed CometAPI/BeatAPI
# route, whose rates sit in DISPLACED_RATES below). The Wan and Kling 3.0 rows are
# the fal routes those lanes ran on before the 2026-11-16 route
# rule made BeatAPI primary; their fal endpoints stay wired in
# app.py, so a fallback is a provider flip, not a rebuild. The
# Seedance 2.0 rows are fal's published text-to-video rates from
# the 2026-11-16 audit, recorded in case that family's primary
# lane ever needs a fallback wiring. If a primary supplier ever
# goes away, this table is the starting point.
FALLBACK_PROVIDER_RATES_CENTS_PER_SECOND = {
    "seedance": {"480p": 21.5, "720p": 46.2},
    # LTX-2.5 via fal, Pro tier ($0.12 / $0.17 / $0.34 per
    # second): the lane's primary until the 2026-11-26 route
    # audit moved it to SpicyAPI; the fal endpoints stay
    # wired in app.py.
    "ltx25": {"720p": 12.0, "1080p": 17.0, "4K": 34.0},
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
    # veo3's rows are fal's with-audio rates ($0.40/s, $0.60/s
    # at 4K): the fallback since the lane moved to Kie's flat
    # per-clip pricing on 2026-11-25 (the fal route served the
    # lane's 4K tier until then via MODEL_PROVIDER_OVERRIDES).
    "veo3": {"720p": 40.0, "1080p": 40.0, "4K": 60.0},
    # Veo 3.1 Fast via fal, with audio ($0.15/s at 720p and
    # 1080p, $0.35/s at 4K): the lane's primary until the
    # 2026-11-25 move to Kie's flat per-clip pricing; the fal
    # endpoints stay wired in app.py as the fallback.
    "veo3_fast": {"720p": 15.0, "1080p": 15.0, "4K": 35.0},
    "veo3_lite": {"720p": 5.0, "1080p": 8.0},
    # Grok Imagine Video 1.5 via fal (2026-11-17): fal's own
    # page prices the 1.5 endpoints at $0.08 / $0.14 / $0.25 per
    # second (text / image / reference modes at 480p; input
    # images add $0.01 each on the image-led modes, absorbed in
    # the post cost like the other lanes). Recorded as the
    # fallback for the grok15 lane, whose primary is Kie.
    "grok15": {"480p": 8.0, "720p": 14.0, "1080p": 25.0},
}

# Rates displaced by the 2026-11-16 follow-up pass to a cheaper
# primary on a NON-fal supplier, plus spare-route rates, kept for
# the record exactly like the fal fallback table above. Nothing
# reads this table; it exists so a route flip never has to
# re-derive a published price.
DISPLACED_RATES_CENTS_PER_SECOND = {
    # BeatAPI rates displaced by the 2026-11-26 route audit's
    # FrameHush moves (Wan 3.0 and MiniMax H3; the suppliers'
    # model ids stay wired in app.py, so a flip back is a
    # provider change, not a rebuild).
    "wan3_beatapi": {"480p": 3.55, "720p": 7.1, "1080p": 13.6},
    "minimax_h3_beatapi": {"480p": 2.0, "720p": 3.0, "1080p": 4.7},
    # BeatAPI rates displaced by Kie / CometAPI / OpenRouter.
    "wan3_prime": {"480p": 6.05, "720p": 10.5, "1080p": 21.0},
    "kling3": {"720p": 11.0, "1080p": 13.0},
    # SpicyAPI's Kling 3.0 rates (live catalog, Kling route
    # hunt 2026-11-27): the wired route for the lane's text and
    # image modes under app.py's per-mode split, recorded here
    # because the lane's PROVIDER_RATES row stays the Kie basis
    # (reference mode shares the row and still runs on Kie).
    # With audio: $0.1008 / $0.1344 per second at 720p / 1080p,
    # $0.336 at 4K either way. Even this cheapest route's 720p
    # rate sits above Higgsfield's 720p retail (9.833 in the
    # policy table below), so the tier stays an at-cost
    # residual; the split's saving lands as margin.
    "kling3_spicyapi": {"720p": 10.08, "1080p": 13.44, "4K": 33.6},
    "kling4": {"720p": 18.0, "1080p": 28.0},
    "seedance": {"720p": 19.4},
    "seedance2_fast": {"480p": 6.072, "720p": 12.251},
    # VidLab rates displaced by the 2026-11-26 route audit's
    # SpicyAPI move for Seedance 2.0 Fast.
    "seedance2_fast_vidlab": {"480p": 5.0, "720p": 11.0},
    # VidLab rates displaced by the same audit's Kie move for
    # Vidu Q4 Preview.
    "vidu_q4_vidlab": {"480p": 6.0, "720p": 12.0, "1080p": 13.0},
    "seedance2_mini": {"480p": 3.283, "720p": 7.363},
    "omni12": {"720p": 4.9, "1080p": 7.28},
    "grok": {"480p": 2.5, "720p": 5.0, "1080p": 9.0},
    # BeatAPI's published rates for its grok-imagine-video-1.5
    # model id ($0.20 / $0.40 / $0.72 per 8-second clip),
    # displaced by Kie for the grok15 lane exactly as for the
    # lane above; the supplier id is recorded in app.py's
    # BEATAPI_MODEL_IDS so a flip needs no re-derivation.
    "grok15": {"480p": 2.5, "720p": 5.0, "1080p": 9.0},
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
    # Wiro routes displaced 2026-11-17 when the Wan 2.6 / 2.7
    # uncensored lanes moved to cheaper SpicyAPI rates (the
    # Wiro submit path stays wired in app.py as the spare).
    # Wiro priced per run, linear in duration: 2.6 at 720p
    # $0.50/$1.00/$1.50 and 1080p $0.75/$1.50/$2.25 for
    # 5/10/15 s; 2.7 at 720p $0.65/$1.30/$1.95 and 1080p
    # $1.00/$2.00/$3.00, carried per-second as before.
    "wan26_unc_wiro": {"720p": 10.0, "1080p": 15.0},
    "wan27_unc_wiro": {"720p": 13.0, "1080p": 20.0},
    # SpicyAPI's MiniMax H3 (uncensored build) rates, read
    # from its own model page 2026-11-17: $0.038/s at 480p,
    # $0.057 at its 540p tier (no platform tier), $0.084 at
    # its 768p tier (the platform's 720p), $0.152 at 1080p.
    # NOT wired: the lane's HotAPI route is cheaper at every
    # comparable tier (see the lane's rate entry above), so
    # the lane stays on HotAPI and these stay for the record.
    "minimax_h3_unc_spicyapi": {"480p": 3.8, "720p": 8.4,
                                "1080p": 15.2},
    # Kie routes displaced 2026-11-23 when the Kling 4.0 and
    # Seedance 2.0 Fast lanes moved to cheaper VidLab primaries
    # (the Kie submit paths stay wired in app.py as spares).
    "kling4_kie": {"720p": 14.25, "1080p": 21.25},
    "seedance2_fast_kie": {"480p": 5.9, "720p": 12.4},
    # Eachlabs rates displaced 2026-11-25 when Veo 3.1 Lite
    # moved to Kie's flat per-clip pricing (the Eachlabs
    # submit path stays wired in app.py as the spare). These
    # are the no-audio per-second rates the lane ran on from
    # the 2026-11-16 follow-up pass.
    "veo3_lite_eachlabs": {"720p": 3.0, "1080p": 8.0},
    # PiAPI rates displaced 2026-11-25 when Veo 3.1 moved to
    # Kie's flat per-clip pricing: $0.12/s flat at 720p and
    # 1080p on the NO-AUDIO basis (its with-audio rate was
    # $0.24/s), the route the lane ran on from the 2026-11-16
    # follow-up pass. Record only: PiAPI is out under Jack's
    # no-expiring-credits rule, so this route does not come
    # back; the PiAPI submit stays wired in app.py as a spare
    # for the record, like the other displaced routes.
    "veo3_piapi": {"720p": 12.0, "1080p": 12.0},
    # Seedance 2.5 routes displaced 2026-11-26 when the lane
    # moved to FrameHush's cheaper per-second endpoint: the
    # CometAPI tiers the lane ran on from the 2026-11-16
    # follow-up pass (its own models API "Actual price /
    # second": 80% of the official $0.103 / $0.231) and the
    # BeatAPI 1080p tier (BeatAPI's displaced 720p rate is the
    # plain "seedance" entry above). Both submits stay wired
    # in app.py as spares, so a flip back needs no
    # re-derivation.
    "seedance_cometapi": {"480p": 8.24, "720p": 18.48},
    "seedance_beatapi": {"1080p": 48.1},
    # Higgsfield's own MAGI-2 API rate, displaced 2026-11-26
    # when the lane moved to FrameHush's magi-2-1080P endpoint
    # ($0.06/s against this flat $0.08/s, generated audio
    # included on both routes). The Higgsfield submit stays
    # wired in app.py as the spare: it lights again if a
    # HIGGSFIELD_API_KEY ever lands in the secret store and
    # the lane is pointed back.
    "magi2_higgsfield": {"1080p": 8.0},
}

# Resolutions each per-second model can actually be ordered at.
# Models absent here accept every entry in RESOLUTIONS.
MODEL_RESOLUTIONS = {
    "seedance": ("480p", "720p", "1080p", "4K"),
    "seedance2": ("480p", "720p", "1080p", "4K"),
    "veo3": ("720p", "1080p", "4K"),
    "veo3_fast": ("720p", "1080p", "4K"),
    "kling3": ("720p", "1080p", "4K"),
    "kling4": ("720p", "1080p"),
    "kling4_flash": ("720p",),
    "kling3_omni": ("720p", "1080p", "4K"),
    "seedance2_fast": ("480p", "720p"),
    "seedance2_mini": ("480p", "720p"),
    "veo3_lite": ("720p", "1080p"),
    "omni12": ("720p", "1080p"),
    "omni11": ("720p", "1080p"),
    "hailuo23": ("720p",),
    "ltx25": ("720p", "1080p", "4K"),
    "ltx25_fast": ("720p", "1080p", "4K"),
    "happyhorse": ("720p", "1080p"),
    "pika22": ("720p", "1080p"),
    "grok": ("480p", "720p", "1080p"),
    "grok15": ("480p", "720p", "1080p"),
    # Lite sells only its genuine tiers: its 1080p is upscaled
    # from 720p on every route (see the rate entry above).
    "grok15_lite": ("480p", "720p"),
    "vidu_q3": ("480p", "720p"),
    "runway_gen45": ("720p", "1080p"),
    "wan22_unc": ("480p", "720p"),
    "wan3_unc": ("480p", "720p", "1080p"),
    "seedance25_unc": ("480p", "720p", "1080p"),
    "vidu_q3_unc": ("480p", "720p", "1080p"),
    "minimax_h3_unc": ("480p", "720p", "1080p"),
    "wan26_unc": ("720p", "1080p"),
    "wan27_unc": ("720p", "1080p"),
    "wan3pro_unc": ("1080p", "2K", "4K"),
    "wan3prime_unc": ("480p", "720p", "1080p"),
    "wan22_lora_unc": ("480p", "720p"),
    "seedance2_unc": ("480p", "720p", "1080p"),
    "seedance2fast_unc": ("480p", "720p"),
    "seedance2mini_unc": ("480p", "720p"),
    "seedance15pro_unc": ("480p", "720p", "1080p"),
    "ltx23_unc": ("480p", "720p", "1080p"),
    "ltx23_lora_unc": ("480p", "720p", "1080p"),
    "wan_motion": ("720p",),
    # VidLab lanes (2026-11-23): the tiers each lane sells.
    # vidu_q4 gains its 480p tier here (VidLab publishes the
    # 540p rate the platform tier maps to).
    "vidlab15": ("720p",),
    "vidlab2": ("720p", "1080p"),
    "vidra3": ("480p", "720p", "1080p", "4K"),
    "seedance25_boost": ("1080p",),
    "seedance25_flash": ("480p", "720p"),
    "pixverse_v6": ("720p", "1080p"),
    "pixverse_v6_max": ("480p", "720p", "1080p"),
    "vidu_q4": ("480p", "720p", "1080p"),
    "wan25": ("480p", "720p", "1080p"),
    "wan26": ("720p", "1080p"),
    "wan26_flash": ("720p", "1080p"),
    "wan27": ("720p", "1080p"),
    "kling26_std": ("720p",),
    "kling_o1": ("720p", "1080p"),
    "hailuo23_fast": ("720p", "1080p"),
    "minimax_h3_max": ("480p", "720p", "1080p"),
    "vidu_q3_pro": ("480p", "720p", "1080p"),
    "vidu_q3_turbo": ("480p", "720p", "1080p"),
    "vidu_q3_mix": ("720p", "1080p"),
    "skyreels_v4_fast": ("480p", "720p", "1080p"),
    "skyreels_v4_std": ("480p", "720p", "1080p"),
    "seedance15pro": ("480p", "720p", "1080p"),
    "seedance10pro_fast": ("480p", "720p", "1080p"),
    # MAGI-2 (2026-11-25): 1080p is the model's only
    # resolution on Higgsfield's API.
    "magi2": ("1080p",),
}
PROVIDER_RATE_STALE_SECONDS = 24 * 3600

# Flat per-clip provider costs, in cents per finished clip at
# any sold duration, for lanes whose supplier bills per
# generation rather than per second. The per-second rate table
# cannot express these honestly (a flat $0.15 clip costs
# 3.75c/s at 4 seconds and 1.875c/s at 8), so the quote, the
# balance debit and the settle price these lanes from this
# table; the rate table carries only the worst-case per-second
# ceiling (the flat cost at the lane's shortest sold length)
# for the displays and pause logic that need a per-second
# figure. Post cost still applies per finished second on top.
FLAT_CLIP_COST_CENTS = {
    # Veo 3.1 Lite via Kie (2026-11-25): 30 credits ($0.15)
    # per 720p video, 35 credits ($0.175) per 1080p video,
    # native audio included (Kie's authenticated catalog
    # pricingDesc for veo-3-1, read 2026-11-25). The price
    # buys ONE native-length video; a longer job the platform
    # chains from native segments is that many videos, priced
    # per segment by flat_clip_raw_cents below.
    "veo3_lite": {"720p": 15.0, "1080p": 17.5},
    # Veo 3.1 (Quality) via Kie (2026-11-25): 250 credits
    # ($1.25) per 720p video, 255 credits ($1.275) per 1080p
    # video, 370 credits ($1.85) per 4K video, native audio
    # included (Kie's published Veo 3.1 pricing, read
    # 2026-11-25). One price buys one native-length video;
    # chained lengths price per segment, as for the Lite.
    "veo3": {"720p": 125.0, "1080p": 127.5, "4K": 185.0},
    # Veo 3.1 Fast via Kie (2026-11-25): 60 credits ($0.30)
    # per 720p video, 65 credits ($0.325) per 1080p video,
    # 180 credits ($0.90) per 4K video, native audio included
    # (same published Kie pricing, read 2026-11-25).
    "veo3_fast": {"720p": 30.0, "1080p": 32.5, "4K": 90.0},
}

# The native segment lengths a flat-per-clip lane's price
# buys, per model: the lengths one flat-priced generation
# can be. A job longer than the longest is chained from
# these segments by the app layer, so its flat cost is the
# per-clip cost times the segment count.
FLAT_CLIP_STEPS = {
    "veo3_lite": (4, 6, 8),
    "veo3": (4, 6, 8),
    "veo3_fast": (4, 6, 8),
}


def flat_clip_cost_cents(model: str, resolution: str):
    """The flat per-clip provider cost for a lane billed per
    generation, or None for the per-second lanes. This is the
    governing cost wherever a lane appears in both tables."""
    return (FLAT_CLIP_COST_CENTS.get(model) or {}).get(resolution)


def flat_clip_segments(model: str, seconds: float) -> int:
    """How many flat-priced generations a job of this length
    takes on a flat-per-clip lane: one at any single native
    length; past the longest, the fewest native segments
    summing exactly to the length (the same composition the
    app layer's duration plan makes). A length no exact
    composition makes (the app refuses those lengths) is
    carried at the ceiling count, so the figure never
    understates the cost."""
    steps = FLAT_CLIP_STEPS.get(model)
    if not steps:
        raise ValueError(f"no flat clip steps for {model}")
    total = int(round(float(seconds)))
    if total <= 0:
        return 1
    best = {0: 0}
    for amount in range(1, total + 1):
        for step in steps:
            if step <= amount and (amount - step) in best:
                candidate = best[amount - step] + 1
                if amount not in best or candidate < best[amount]:
                    best[amount] = candidate
    if total in best:
        return best[total]
    import math

    return max(1, math.ceil(total / max(steps)))


def flat_clip_raw_cents(model: str, resolution: str,
                        seconds: float) -> float:
    """The true raw cost of one flat-per-clip job of this
    length: the per-clip cost times the generations the
    length takes, plus post per finished second."""
    flat = flat_clip_cost_cents(model, resolution)
    if flat is None:
        raise ValueError(
            f"no flat clip cost for {model} at {resolution}")
    return flat * flat_clip_segments(model, seconds) \
        + POST_COST_CENTS_PER_SECOND * float(seconds)


def _flat_clip_picture(model: str, resolution: str,
                       output_seconds: float, rates=None,
                       owner: bool = False) -> dict:
    """The money picture for one flat-per-clip job: raw cost
    (the flat clip cost times the generations the length
    takes, plus post per finished second), the balance debit
    at the standard rule (cost x FRONTIER_MIN_MARKUP through
    the shared rounding path; the owner pays raw cost), and
    the effective sell rate per second at this duration for
    the payloads that speak per-second. The
    Higgsfield cap is frontier_lane's own verdict for the
    lane, applied to the job's total: the cap is a property
    of the lane's pricing posture (its worst-case per-second
    figures against Higgsfield's), not of one job's length —
    a flat job's per-second equivalent falls as the clip
    lengthens and spikes below the lane's shortest real
    length, so judging the cap per job would let the same
    lane flip bases between durations. Where frontier_lane
    puts the lane on an at-cost basis, the job sells at raw
    cost (or raw cost grossed up by the Stripe percentage fee
    on the residual basis), never below cost."""
    if flat_clip_cost_cents(model, resolution) is None:
        raise ValueError(
            f"no flat clip cost for {model} at {resolution}")
    seconds = float(output_seconds)
    if seconds <= 0:
        raise ValueError("flat clip picture needs a duration")
    raw = flat_clip_raw_cents(model, resolution, seconds)
    if owner:
        return {
            "raw_cost_cents": raw,
            "balance_debit_cents": customer_debit_cents(raw),
            "sell_cents_per_second": round(raw / seconds, 3),
            "pricing_basis": "standard",
        }
    # The customer sell total is built on the shelf raw (the
    # frozen pre-cut post figure), so the post cut moves no
    # flat-lane price either; raw above stays the true cost for
    # the owner picture and for accounting.
    shelf_raw = raw + (SHELF_POST_COST_CENTS_PER_SECOND
                       - POST_COST_CENTS_PER_SECOND) * seconds
    sell_total = shelf_raw * FRONTIER_MIN_MARKUP
    basis = frontier_lane(model, resolution, rates)["pricing_basis"]
    if basis == "at_cost_residual":
        sell_total = shelf_raw / (1 - STRIPE_PCT)
    elif basis == "debit_safe_undercut":
        sell_total = shelf_raw
    return {
        "raw_cost_cents": raw,
        "balance_debit_cents": customer_debit_cents(sell_total),
        "sell_cents_per_second": round(sell_total / seconds, 3),
        "pricing_basis": basis,
    }


# Local post cost per finished second for per-second-lane outputs,
# in cents. Re-measured end to end 2026-11-26 (the owner's
# cost-cutting pass): the whole finishing chain for an API-lane
# job — the download, the loudnorm/mux pass (video stream copy,
# audio only), the SHA-256 stamp, the output classifier's frame
# grabs and ONNX inferences, the probes — totals roughly 2 to
# 3.5 CPU core-seconds per JOB on this workspace's cores (a 5 s
# 480p clip ~2.1, an 8 s 720p clip ~3.3, classifier inference
# estimated at the top of its range), which at Modal's published
# CPU price ($0.047 per core-hour) is about 0.0006c per finished
# second. The 0.1c/s figure carried since 2026-11-16 was a
# deliberate pad, roughly 150x the measured compute. The constant
# now sits at 0.01c/s: a 10x cut that still covers the padded
# worst case (a short 1080p clip, sparse keyframes, ~0.0015c/s)
# several times over. This is the TRUE cost: it is what the
# owner pays and what settled-cost accounting records. The
# optional GPU upscale pass is not sold on these lanes yet and
# is not in here.
POST_COST_CENTS_PER_SECOND = 0.01

# The shelf post figure, frozen at the pre-cut 0.1c/s (Jack,
# 2026-11-26: "cut OUR cost, not our users' prices"). Customer
# sell rates keep being built on a cost basis that carries THIS
# post figure, exactly as they were before the cut, so no
# customer price moves; the savings land as margin, the same
# shape as the FrameHush route swap. Only the true-cost side
# (owner price, cost accounting, hold caps) uses the cut figure
# above. The two figures differ by a constant 0.09c/s, so a
# future provider rate change flows through both bases alike.
SHELF_POST_COST_CENTS_PER_SECOND = 0.1

# Shelf provider rates frozen per lane (2026-11-26): where a
# cheaper route takes over a lane but the customer shelf price
# must not move (Jack: "cut OUR cost, not our users' prices"),
# the shelf basis keeps the provider rate the shelf price was
# built on and the route saving lands as margin — the same
# decoupling the shelf post figure above applies platform-
# wide, one level down. MAGI-2's shelf was built on
# Higgsfield's direct $0.08/s; the lane now runs on FrameHush
# at $0.06/s, which PROVIDER_RATES carries as the true rate
# (the owner price and all cost accounting use it).
SHELF_PROVIDER_RATES_CENTS_PER_SECOND = {
    "magi2": {"1080p": 8.0},
    # Route audit (2026-11-26): Wan 3.0 and MiniMax H3 moved
    # to FrameHush at lower true rates (PROVIDER_RATES above);
    # their shelves were built on the BeatAPI rates and stay
    # frozen there, so no customer price moves.
    "wan3": {"480p": 3.55, "720p": 7.1, "1080p": 13.6},
    "minimax_h3": {"480p": 2.0, "720p": 3.0, "1080p": 4.7},
    # LTX-2.5's shelf was built on fal's Pro rates; the lane
    # now runs on SpicyAPI at the lower true rates above.
    "ltx25": {"720p": 12.0, "1080p": 17.0, "4K": 34.0},
    # Seedance 2.0 Fast's shelf was built on VidLab's rates;
    # the lane now runs on SpicyAPI at the lower true rates.
    "seedance2_fast": {"480p": 5.0, "720p": 11.0},
    # Vidu Q4 Preview's shelf was built on VidLab's rates;
    # the lane now runs on Kie at the lower true rates.
    "vidu_q4": {"480p": 6.0, "720p": 12.0, "1080p": 13.0},
}

# Built-in lip sync (Jack's product rule, 2026-11-17): every video
# where someone or something is talking gets its mouth matched to
# the audio, automatically, as part of the finishing pipeline. It
# is not an option and not an upsell. The stage runs on fal's Sync
# Lipsync v2 (fal-ai/sync-lipsync/v2), the cheapest reliable hosted
# route with a published per-second price: $0.05 per output second
# (fal model page, read 2026-11-17). The cost folds into the quote,
# the hold and the pre-dispatch ceiling like any other cost; the
# customer side carries the standard frontier minimum markup, the
# owner pays raw cost, and it is never sold below cost.
LIPSYNC_CENTS_PER_SECOND = 5.0
LIPSYNC_MODEL = "fal-ai/sync-lipsync/v2"


# Character identity training (2026-11-17): the Soul ID
# equivalent is a hosted portrait LoRA trainer on fal
# (fal-ai/flux-lora-portrait-trainer), $6.00 flat at its default
# 2,500 steps (fal model page, read 2026-11-17). One-time per
# character; the customer charge is the raw cost at the standard
# frontier minimum markup, the owner pays raw cost. Trained
# stills generate on fal-ai/flux-lora at $0.035 per megapixel.
CHARACTER_TRAIN_MODEL = "fal-ai/flux-lora-portrait-trainer"
CHARACTER_TRAIN_RAW_CENTS = 600.0
CHARACTER_TRAIN_STEPS = 2500


def lip_sync_costs(output_seconds: float, owner: bool = False):
    """(raw_cents, balance_debit_cents) for the built-in lip sync
    stage on a job of the given output length."""
    raw = LIPSYNC_CENTS_PER_SECOND * float(output_seconds)
    debit = raw if owner else raw * FRONTIER_MIN_MARKUP
    return raw, debit


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
# Seedance 2.5's 4K count is 140 credits per 5 s clip
# (Higgsfield's own MCP connector guide, read 2026-11-27: a
# 10 s Seedance 2.5 clip costs 280 credits in 4K at
# Higgsfield's standard credit rates, the same rates its 720p
# and 1080p counts above come from), recorded here when the
# 4K tier was wired on 2026-11-27.
HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP_BY_RESOLUTION = {
    "seedance": {"480p": 73.75, "1080p": 393.33, "4K": 688.33},
    "wan3": {"1080p": 86.04},
}

# Round 2 (2026-11-16, Jack's strengthened rule): EVERY public
# price must be cheaper than Higgsfield's, no matter what — or
# at raw provider cost where undercutting with a markup is
# impossible but raw cost is still below Higgsfield's price.
# Never below raw cost. A lane whose raw cost meets or exceeds
# Higgsfield's price even at cost is flagged (pricing_basis
# "at_cost_residual" in the frontier_lane payload) and priced
# at the cheapest non-losing figure: raw cost grossed up by the
# Stripe percentage fee only (Jack's restatement, 2026-11-19;
# the round-2 text here said "priced at cost" — see the
# frontier_lane cap for the current treatment). The x1.25
# minimum yields where it blocks this rule.
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
# - Kling 4.0 Flash: $0.1475/s at 720p (Higgsfield's
#   published Flash rate, read 2026-11-27 in the Kling
#   route hunt).
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
    "kling4_flash": {"720p": 14.75},
    "seedance2": {"720p": 21.633, "1080p": 59.0},
    "veo3": {"720p": 49.167, "1080p": 49.167},
    "veo3_fast": {"720p": 19.667, "1080p": 19.667},
    "veo3_lite": {"720p": 7.375},
    "grok": {"720p": 22.125},
    # The grok15 lane prices against the same Higgsfield shelf
    # product (Grok Imagine at 22.5 credits per 5 s at 720p);
    # Lite carries no Higgsfield figure anywhere in the
    # primary-read records, so it prices at the x1.25 minimum.
    "grok15": {"720p": 22.125},
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
    # Customer sell math runs on the SHELF cost basis (the frozen
    # pre-cut post figure), so the 2026-11-26 post cut moves no
    # customer price; only the reported true cost and the owner
    # price below use the cut figure.
    shelf_cost = per_second_shelf_cost_cents(model, resolution, table)
    minimum = shelf_cost * FRONTIER_MIN_MARKUP
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
    # hard minimum. The cap below then applies to BOTH: where
    # the resulting sell rate would sit at or above Higgsfield's
    # figure, the lane drops toward cost to undercut — at the
    # debit-safe rate where cost allows (pricing_basis
    # "debit_safe_undercut"; "at_cost_undercut" through
    # 2026-11-26, when it sold at shelf cost exactly), or
    # flagged as a residual exception where even raw cost
    # meets or exceeds Higgsfield's price ("at_cost_residual").
    # A lane forced onto its hard minimum a hair UNDER the
    # figure takes the same debit-safe rate when the minimum
    # itself is not a whole cent (see the follow-up branch
    # below). The cap never prices below cost.
    policy = _higgsfield_policy_cents_per_second(model, resolution)
    effective = higgsfield if higgsfield is not None else policy
    at_hard_minimum = False
    if higgsfield is not None:
        target = higgsfield * UNDERCUT_FRACTION
        if target >= minimum:
            sell, can_undercut = target, True
        else:
            sell, can_undercut = minimum, False
            at_hard_minimum = True
    else:
        # No target-table figure: the lane sells at the hard
        # minimum. Where no policy figure exists either, the
        # undercut question stays honestly unanswered rather than
        # pricing against an invented competitor figure.
        sell, can_undercut = minimum, None
    basis = "standard"
    if effective is not None and sell >= effective:
        if shelf_cost >= effective:
            # Residual exception, restated by Jack 2026-11-19:
            # raw cost alone meets or exceeds Higgsfield's price,
            # so the lane sells at the cheapest figure that does
            # not lose money — raw cost grossed up by the Stripe
            # percentage fee only. Balance is prepaid (top-ups and
            # plan payments), so the fixed per-transaction fee is
            # a pack-level cost the platform already carries; the
            # per-second share of the fee is the 2.9% percentage,
            # and cost / (1 - pct) is the smallest rate whose net
            # after that fee is the cost itself. Selling at exact
            # raw cost (the round-2 treatment) netted the platform
            # a hair under cost on every prepaid second. The lane
            # stays above Higgsfield and stays flagged
            # (at_cost_residual); it is named to Jack, never sold
            # below cost, and clears the moment a cheaper route
            # or a Higgsfield price rise lets it undercut.
            sell = _ceil3(shelf_cost / (1 - STRIPE_PCT))
            basis = "at_cost_residual"
        else:
            # Debit-safe undercut (2026-11-27, the owner's
            # beat-Higgsfield rule: beat Higgsfield at every
            # price unless that leaves no profit, ideally with
            # at least 25% profit over cost). The round-2
            # treatment sold this branch at shelf cost exactly
            # (basis "at_cost_undercut" through 2026-11-26).
            # On a lane whose shelf basis is frozen above a
            # cheaper true route (Wan 3.0 after the 2026-11-26
            # FrameHush reroute), shelf cost sits only ~20%
            # over TRUE cost, under the owner's 25% target at
            # nearly every duration, even though Higgsfield's
            # figure leaves room above it. The lane now sells
            # at the highest rate that keeps the ACTUAL
            # whole-cent debit under Higgsfield's figure at
            # every billed length: a rate at or below the
            # largest whole cent strictly under Higgsfield's
            # per-second figure gives ceil(rate x d) <= that
            # cent x d < Higgsfield x d at every duration
            # d >= 1, so the undercut holds at the debit level,
            # not just on the published rate (the failure the
            # 2026-11-26 teardown's debit note recorded). The
            # standard minimum caps the rate from above and
            # the shelf cost floors it, so the rate never
            # drops below the old at-cost figure.
            import math

            debit_safe_cap = float(math.ceil(effective) - 1)
            sell = max(shelf_cost, min(minimum, debit_safe_cap))
            basis = "debit_safe_undercut"
        can_undercut = sell < effective
    elif (effective is not None and at_hard_minimum
          and shelf_cost < effective):
        # The same debit-safe treatment for a lane the
        # target table forces onto its hard minimum BELOW
        # Higgsfield's figure (2026-11-27 follow-up; the
        # first pass measured this cell out of scope and
        # recorded the crossing). Wan 3.0 1080p sells at
        # its minimum, 17.125c/s, because the 0.97x target
        # (16.692) sits under the minimum; the published
        # rate reads as an undercut of Higgsfield's
        # 17.208, but the minimum is not a whole cent, so
        # whole-cent rounding flips the actual debit over
        # Higgsfield's total at short billed lengths (2s:
        # 35c against 34.42c; 3s: 52c against 51.62c; 4s:
        # 69c against 68.83c; 9s: 155c against 154.87c).
        # Where the minimum itself exceeds the debit-safe
        # cap, the lane takes the same rate as the branch
        # above — the largest whole cent strictly under
        # Higgsfield's figure, capped by the standard
        # minimum and floored at shelf cost (17.0c/s here,
        # about a 75% margin over the lane's true cost) —
        # so the debit undercuts at every duration. Lanes
        # selling at the 0.97x target (Wan 3.0 Prime,
        # Seedance 2.5 480p) never sit on their minimum
        # and are untouched: their debits already undercut
        # at every sold duration.
        import math

        debit_safe_cap = float(math.ceil(effective) - 1)
        if minimum > debit_safe_cap:
            sell = max(shelf_cost, min(minimum, debit_safe_cap))
            basis = "debit_safe_undercut"
        can_undercut = sell < effective
    elif effective is not None and can_undercut is None:
        can_undercut = sell < effective
    picture = {
        "cost_cents_per_second": round(cost, 3),
        "higgsfield_effective_cents_per_second": (
            round(effective, 3) if effective is not None else None),
        "minimum_cents_per_second": _ceil3(minimum),
        "sell_cents_per_second": _ceil3(sell),
        "can_undercut": can_undercut,
        "pricing_basis": basis,
    }
    flat = flat_clip_cost_cents(model, resolution)
    if flat is not None:
        # A flat-per-clip lane: the per-second figures above
        # are the worst-case ceiling at the lane's shortest
        # sold length; the governing per-clip cost rides along
        # so the pricing payload never presents the ceiling as
        # the price of a longer clip.
        picture["flat_clip_cost_cents"] = flat
        picture["cost_basis"] = "flat_per_clip"
    return picture


def customer_debit_cents(amount: float) -> int:
    """The one rounding path for a customer's final debit
    (Jack, 2026-11-23): a fractional cent figure rounds UP to
    the next whole cent, never down, so the charged price is
    never a cent short of the figure the price was built from
    (for a standard lane, 1.25x raw cost). The quote, the
    creation estimate and the settle all resolve a customer
    debit through this function, so the displayed price and
    the charged price cannot disagree. The hairline epsilon
    keeps a mathematically whole figure that floats a hair
    above itself (59.000000000001) from rounding up a phantom
    cent; a figure a hair below whole (58.999999999999) still
    rounds up to the whole cent it names."""
    import math

    return int(math.ceil(float(amount) - 1e-9))


def per_second_balance_debit_cents(model: str, resolution: str,
                                   billed_seconds: float, rates=None,
                                   owner: bool = False) -> int:
    """What a per-second job charges prepaid balance: the Amendment 3
    sell rate for the lane times the billed seconds, rounded up
    through the shared customer rounding path. The owner pays
    raw cost instead (owner pricing exemption). A flat-per-clip
    lane prices from its per-clip cost instead (the flat cost
    plus post per billed second through the same standard
    rule), so its debit does not grow with the clip's length
    the way a per-second lane's does."""
    if flat_clip_cost_cents(model, resolution) is not None:
        return _flat_clip_picture(
            model, resolution, billed_seconds, rates=rates,
            owner=owner)["balance_debit_cents"]
    sell = frontier_lane(
        model, resolution, rates, owner=owner
    )["sell_cents_per_second"]
    return customer_debit_cents(sell * float(billed_seconds))


def production_cost_per_second() -> float:
    """Measured production cost per second, cents."""
    return MEASURED_COST_CENTS["production"] / SEGMENT_SECONDS


def included_budget_cents() -> float:
    """The Creator plan's one included render budget, in cents of
    measured cost: net plan revenue divided by the margin
    multiplier. Every lane debits this same budget at its own
    per-second cost (Rule 3 amendment, 2026-11-16)."""
    return net_of(CREATOR_PRICE_CENTS) / MARGIN_MULTIPLIER


def _provider_rate_cents(model: str, resolution: str, rates=None) -> float:
    """The provider's raw per-second rate for one lane at one
    resolution. Raises ValueError for a model or resolution the
    rate table does not carry, so an unorderable combination
    fails as a validation error instead of a KeyError."""
    table = rates if rates is not None else PROVIDER_RATES_CENTS_PER_SECOND
    try:
        return float(table[model][resolution])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"no provider rate for {model} at {resolution}"
        ) from None


def per_second_lane_cost_cents(model: str, resolution: str, rates=None) -> float:
    """Worst-case TRUE cost per billed second for a per-second
    lane: the provider rate plus the measured post cost
    (POST_COST_CENTS_PER_SECOND). This is the owner price basis
    and the settled-cost accounting basis."""
    return _provider_rate_cents(model, resolution, rates) \
        + POST_COST_CENTS_PER_SECOND


def _shelf_provider_rate_cents(model: str, resolution: str, rates=None) -> float:
    """The provider rate the customer shelf basis carries for
    one lane: the lane's frozen shelf rate where one is
    recorded in SHELF_PROVIDER_RATES_CENTS_PER_SECOND (it wins
    over any rate table, seed or live), else the lane's rate
    from the table in hand."""
    frozen = SHELF_PROVIDER_RATES_CENTS_PER_SECOND.get(
        model, {}).get(resolution)
    if frozen is not None:
        return float(frozen)
    return _provider_rate_cents(model, resolution, rates)


def per_second_shelf_cost_cents(model: str, resolution: str, rates=None) -> float:
    """The cost basis customer sell rates are built on: the
    shelf provider rate plus the FROZEN shelf post figure
    (SHELF_POST_COST_CENTS_PER_SECOND). Identical to the true
    cost until the 2026-11-26 post cut; since then it sits a
    constant 0.09c/s above the true cost, which is exactly the
    margin the cut creates. A lane whose route got cheaper
    under a shelf freeze (SHELF_PROVIDER_RATES_CENTS_PER_SECOND)
    sits higher above its true cost by the route saving too.
    Never use this for accounting."""
    return _shelf_provider_rate_cents(model, resolution, rates) \
        + SHELF_POST_COST_CENTS_PER_SECOND


def per_second_estimate(model: str, resolution: str, output_seconds: float,
                        reference_video_seconds: float = 0.0, rates=None,
                        owner: bool = False, lip_sync: bool = False,
                        voice_clone: bool = False) -> dict:
    """The exact quote for a per-second-lane job (amended Rule 3):
    raw cost, the balance debit at the Amendment 3 sell rate, and
    the hold caps. The included-budget side is untouched by the
    amendment: budget debits stay denominated at raw cost. For the
    owner the sell rate is raw cost, so the debit and the balance
    hold cap both resolve to cost (owner pricing exemption). When
    the job carries speech on a lane that does not speak natively,
    the built-in lip sync stage's cost folds in on the output
    seconds (never on reference seconds). The balance debit is a
    whole number of cents: every component resolves through the
    shared customer rounding path, exactly as the settle
    recomputes it, so the quote and the charge cannot disagree.
    When the job picks voice cloning, the one-time clone price
    folds into the quote, the caps and the debit the same way
    (Jack, 2026-11-23: part of the one generate price, never a
    separate fee); its raw basis joins the raw cost so the
    included-budget side carries it too. The owner pays the
    clone route's true raw cost, $0."""
    import math

    billed = float(output_seconds) + float(reference_video_seconds)
    flat = flat_clip_cost_cents(model, resolution)
    if flat is not None:
        # A flat-per-clip lane: raw cost is the per-clip cost
        # plus post on the output seconds (a flat supplier
        # bills the generation, not the reference material),
        # and the sell rate shown is the effective per-second
        # figure at this duration, so the quote, the caps and
        # the settle all describe the same per-clip price.
        raw = _flat_clip_picture(
            model, resolution, output_seconds, rates=rates,
            owner=owner)["raw_cost_cents"]
    else:
        raw = per_second_lane_cost_cents(
            model, resolution, rates) * billed
    debit = per_second_balance_debit_cents(
        model, resolution, billed, rates, owner=owner
    )
    if lip_sync:
        lip_raw, lip_debit = lip_sync_costs(output_seconds, owner=owner)
        raw += lip_raw
        debit += customer_debit_cents(lip_debit)
    if voice_clone:
        raw += VOICE_CLONE_RAW_CENTS
        debit += voice_clone_price_cents(owner=owner)
    if flat is not None:
        sell = _flat_clip_picture(
            model, resolution, output_seconds, rates=rates,
            owner=owner)["sell_cents_per_second"]
    else:
        sell = frontier_lane(
            model, resolution, rates, owner=owner
        )["sell_cents_per_second"]
    return {
        "billed_seconds": billed,
        "raw_cost_cents": raw,
        "sell_cents_per_second": sell,
        "balance_debit_cents": debit,
        "lip_sync_cents": (
            lip_sync_costs(output_seconds, owner=owner)[0]
            if lip_sync else 0.0),
        "voice_clone_cents": (
            voice_clone_price_cents(owner=owner)
            if voice_clone else 0),
        "cap_raw_cents": raw * HOLD_TOLERANCE,
        "cap_balance_cents": math.ceil(
            max(sell * billed, debit) * HOLD_TOLERANCE),
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
        "voice_clone_price_cents": voice_clone_price_cents(owner),
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
# Start-image generation (PARITY-PROGRAM Pass 3). The lane runs
# Z-Image Turbo on the house worker, which moved from Modal to
# Kaggle's free compute on 2026-11-19, so its real marginal cost
# is $0 per image. The 5 cent "measured cost" it carried before
# (and the 47 cent flat price grossed up from it) was an
# accounting carryover from a 2026-11-16 Modal L40S measurement,
# not a real cost. On Jack's order (2026-11-20) the lane sits in
# the image picker's Free Models group at $0, like the other
# $0-cost free lanes: customer price 0, owner price 0, no quote
# above 0 and no hold. Creations still count toward the user's
# 5 free generations, exactly like every other generation, and
# free accounts' start images still count against the free
# ceilings (5 lifetime per account, platform-wide total in
# config) exactly as before.
# ---------------------------------------------------------------------------

IMAGE_MEASURED_COST_CENTS = 0.0
IMAGE_PRICE_CENTS = (
    0 if IMAGE_MEASURED_COST_CENTS == 0
    else gross_up(IMAGE_MEASURED_COST_CENTS * MARGIN_MULTIPLIER))


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


# The curated image shelf (Jack, 2026-11-17): a few of the best
# image models only, priced per image. raw = the provider's
# published per-image price on the wired route (provider pages
# read 2026-11-17); the customer price is raw x
# FRONTIER_MIN_MARKUP, the owner pays raw, never below cost.
# Higgsfield sells images for credits, not a flat price: on its
# Plus plan ($0.049 per credit) Nano Banana Pro costs 2 credits
# (about $0.098) per image, which the BeatAPI route undercuts.
# Every endpoint slug and payload here is PAYLOAD-UNVERIFIED
# until a real job proves it. Jack funded the SpicyAPI account
# on 2026-11-17, so the SpicyAPI lanes can run; he tests them
# himself. The other providers' balances are still $0, so
# their lanes can only fail clean until funded.
# Label $ tier marks (Jack, 2026-11-19): image lanes carry
# their own fixed bands, read on the lane's customer price per
# image at its smallest size: $ up to 3c, $$ above 3c up to
# 4.5c, $$$ above 4.5c up to 10c, $$$$ above 10c. (The video
# picker's per second bands in app.py are separate and did
# not change.) Marks sit behind a bullet separator
# ("Name · $$"), the one format the whole shelf reads. Every
# lane's mark is recomputed from its served customer price
# and set to the band it lands in.
IMAGE_LANES = {
    "nano_banana_21": {
        "label": "Nano Banana 2.1 · $", "provider": "beatapi",
        "endpoint": "google/nano-banana-2.1",
        "raw_cents": {"1K": 1.6, "2K": 1.8, "4K": 3.5},
    },
    "nano_banana_pro": {
        "label": "Nano Banana Pro · $$$", "provider": "vidlab",
        "endpoint": "nano-banana-pro",
        # Reference images (2026-11-27): the lane takes
        # pictures alongside the prompt (the model's images
        # input on VidLab, the same shape its edit lanes
        # send), which is what the guided movie flow's start
        # frame step rides: the picked character's pictures
        # plus a product photo go in, the frame with the
        # product in hand comes out. The pictures are judged
        # by the input classifier at creation like any edit
        # lane's source, and the price stays per image.
        "references": True,
        # Rerouted to VidLab 2026-11-23 (cheapest route):
        # VidLab's catalog prices it at $0.04 per image at 1K
        # and 2K and $0.08 at 4K, under BeatAPI's $0.05 / $0.05
        # / $0.10 (BeatAPI route kept in app.py as the spare).
        # VidLab publishes seven aspect ratios for the model;
        # the lane's aspect set narrows to those in
        # IMAGE_ASPECTS below.
        "raw_cents": {"1K": 4.0, "2K": 4.0, "4K": 8.0},
    },
    "nano_banana_2": {
        "label": "Nano Banana 2 · $", "provider": "beatapi",
        "endpoint": "google/nano-banana-2",
        "raw_cents": {"1K": 2.0, "2K": 3.5, "4K": 4.0},
    },
    "gpt_image_26": {
        # fal bills this model per token; the raw figure is
        # fal's own published measured cost for a high-quality
        # 1024x1024 Flare image (about $0.053), the only size
        # sold until the funded pass measures the real charge.
        "label": "GPT Image 2.6 Flare · $$$", "provider": "fal",
        "endpoint": "openai/gpt-image-2.6/flare/text-to-image",
        "raw_cents": {"1024x1024": 5.3},
    },
    "gpt_image_26_sunburst": {
        # GPT Image 2.6 Sunburst (wired 2026-11-25, frontier
        # coverage pass): the detail tier of OpenAI's 2.6 pair,
        # carried as its own lane beside the Flare lane above.
        # OpenAI bills Sunburst and Flare on the identical
        # token card, and fal serves both, so the raw figure
        # is the same measured high-quality 1024x1024 cost
        # (about $0.053) the Flare lane carries, on the same
        # single size until the funded pass measures the real
        # charge. VidLab's catalog carries no 2.6 tier (read
        # 2026-11-23), so fal is the cheapest wired route.
        "label": "GPT Image 2.6 Sunburst · $$$", "provider": "fal",
        "endpoint": "openai/gpt-image-2.6/sunburst/text-to-image",
        "raw_cents": {"1024x1024": 5.3},
    },
    "seedream55_unc": {
        "label": "Seedream 5.5 · $$", "provider": "wiro",
        "uncensored": True,
        "endpoint": "bytedance/seedream-5-5-uncensored",
        # Wiro's own model page (read 2026-11-17) sells three
        # resolutions: 1k $0.028, 1.5k $0.028, 2k $0.043. Only 2K
        # was wired before, and at the 1K price; corrected here.
        "raw_cents": {"1K": 2.8, "1.5K": 2.8, "2K": 4.3},
    },
    "seedream5pro_unc": {
        "label": "Seedream 5.0 Pro · $$",
        "provider": "spicyapi",
        "uncensored": True,
        "endpoint": "bytedance/seedream-5-0-pro/text-to-image",
        # SpicyAPI's own model page (read 2026-11-17): 1k $0.036,
        # 1.5k $0.040, 2k $0.072. The 1.5k tier was not wired.
        "raw_cents": {"1K": 3.6, "1.5K": 4.0, "2K": 7.2},
    },
    "zimage_unc": {
        "label": "Z-Image · $", "provider": "spicyapi",
        "uncensored": True,
        "endpoint": "alibaba/z-image-spicy/text-to-image",
        # SpicyAPI's own model page (read 2026-11-17): no
        # resolution tiers at all; width and height are set
        # independently, 256-1536 px each, at one flat $0.01235
        # per image. The picker offers four pixel sizes, all at
        # the flat raw price; the submit sends width/height.
        # Variant verdict (2026-11-17): this endpoint IS the
        # base "Z-Image Spicy" from Jack's screenshots, not the
        # Pro; the wired price matches its published $0.01235.
        # Shape pass (2026-11-19): true 16:9 and 9:16 sizes
        # join the list, inside the same 256-1536 px range at
        # the same flat price (width and height move nothing).
        "raw_cents": {"1024x1024": 1.235, "1024x1536": 1.235,
                      "1536x1024": 1.235, "1536x1536": 1.235,
                      "1536x864": 1.235, "864x1536": 1.235},
    },
    "zimage_pro_unc": {
        "label": "Z-Image Pro · $", "provider": "spicyapi",
        "uncensored": True,
        "endpoint": "alibaba/z-image-spicy-pro/text-to-image",
        # SpicyAPI's own model page (read 2026-11-17): the Pro
        # tier of the Z-Image line. Same shape as the base tier
        # (width and height set independently, no tiers) but
        # 256-2560 px each, at one flat $0.019 per image. The
        # picker offers six pixel sizes at the flat raw price;
        # the submit sends width/height on the same z-image
        # branch as the base tier.
        "raw_cents": {"1024x1024": 1.9, "1024x1536": 1.9,
                      "1536x1024": 1.9, "2048x2048": 1.9,
                      "2560x1440": 1.9, "1440x2560": 1.9},
    },
    "krea2_verite": {
        # Krea 2 Verite (2026-11-21): the Verite realism
        # LoRA (f23gg/Krea-2-NSFW-V5) on Comfy-Org's int8
        # convrot build of Krea-2-Raw, running on the house
        # Kaggle ComfyUI worker like the house image lane,
        # not on a provider API. The lane rides the shelf
        # for its picker entry, its paid gate and its per
        # image charge, then converts to the house job shape
        # at submit (see _queue_krea2_on_kaggle in app.py).
        # The raw figure is a price basis, not a provider
        # bill: it matches the Z-Image Pro uncensored lane's
        # raw, so the customer price takes the same shape
        # (raw x FRONTIER_MIN_MARKUP = 2.375c, the $ band).
        # The route's true raw cost is $0 on Kaggle's free
        # compute, and that is what the owner pays
        # (image_lane_price returns $0 for the owner on a
        # kaggle-comfy lane). The LoRA carries no license
        # statement; Jack ordered the wiring knowing that
        # (2026-11-21), and the lane never lists in the
        # public gallery. Text to image only, no edit mode.
        # The tested frame leads the sizes; all three sell
        # at the one flat raw, like the Z-Image lanes.
        "label": "Krea 2 Verite · $", "provider": "kaggle-comfy",
        "uncensored": True,
        "endpoint": "krea2-verite",
        "raw_cents": {"832x1216": 1.9, "1024x1024": 1.9,
                      "1216x832": 1.9},
    },
    "longcat": {
        # LongCat-Image (2026-11-26, Jack's order): Meituan's
        # 6B text to image model (Apache 2.0), notably strong
        # at rendering text inside images, running on the
        # house Kaggle ComfyUI worker like the Krea lane
        # above, not on a provider API. The lane rides the
        # shelf for its picker entry, its paid gate and its
        # per image charge, then converts to the house job
        # shape at submit (see _queue_longcat_on_kaggle in
        # app.py). The raw figure is a price basis, not a
        # provider bill: it matches the Krea lane's raw, so
        # the customer price takes the same shape (raw x
        # FRONTIER_MIN_MARKUP = 2.375c, the $ band). The
        # route's true raw cost is $0 on Kaggle's free
        # compute, and that is what the owner pays
        # (image_lane_price returns $0 for the owner on a
        # kaggle-comfy lane). A clean lane: no uncensored
        # flag, so the regular gallery and input rules apply.
        # Text to image only, no edit mode. All three sizes
        # sell at the one flat raw, like the Krea lane.
        "label": "LongCat-Image · $", "provider": "kaggle-comfy",
        "endpoint": "longcat-image",
        "raw_cents": {"1024x1024": 1.9, "832x1216": 1.9,
                      "1216x832": 1.9},
    },
    "qwen_edit_unc": {
        "label": "Qwen Image Edit · $$$",
        "provider": "spicyapi",
        "uncensored": True,
        "endpoint": "alibaba/qwen-image-spicy-edit/edit",
        # SpicyAPI's own model page (read 2026-11-17): an EDIT
        # model, not text-to-image. One source image (image_url)
        # plus one plain-English edit instruction, $0.038 flat
        # per edit. The endpoint declares no size fields at
        # all; the output keeps the source image's size, so the
        # only size sold is "match" and the picker says so.
        "edit": True,
        "raw_cents": {"match": 3.8},
    },
    # The free image lanes (2026-11-20): Cloudflare Workers
    # AI, raw cost $0 inside the account's free daily neuron
    # allowance (10,000 neurons, resetting 00:00 UTC), so both
    # lanes honestly sit in the picker's Free Models group.
    # Cloudflare's own neuron rates (pricing page read
    # 2026-11-20): FLUX.2 Klein 4B meters 26.05 neurons per
    # output 512x512 tile (a 1024x1024 image is 4 tiles,
    # 104.2 neurons, about 95 images a day); FLUX.1 Schnell
    # meters 4.80 per tile plus 9.60 per step per tile (172.8
    # neurons at the 4 steps the lane runs, about 57 a day).
    # Both models are Apache 2.0 and Cloudflare's terms give
    # the customer all IP rights in outputs with no training
    # on inputs. The account is on Workers PAID, where usage
    # past the free allowance would bill at $0.011 per 1,000
    # neurons, so the platform tracks the day's neurons
    # itself and stops this route before the allowance is
    # spent (see _cf_neuron_* in app.py): the cap, not the
    # plan, is what keeps the route at $0. API shapes probed
    # live 2026-11-20: Klein takes a multipart form (prompt,
    # width, height, optional edit image) and answers JSON
    # with a base64 JPEG; Schnell takes JSON {prompt, steps}
    # only and always answers a 1024x1024 JPEG.
    "flux2_klein4b": {
        "label": "FLUX.2 Klein 4B", "provider": "cloudflare",
        "endpoint": "@cf/black-forest-labs/flux-2-klein-4b",
        "free": True, "picker_order": 1,
        "raw_cents": {"1024x1024": 0.0, "1024x768": 0.0,
                      "768x1024": 0.0},
    },
    "flux1_schnell": {
        "label": "FLUX.1 Schnell", "provider": "cloudflare",
        "endpoint": "@cf/black-forest-labs/flux-1-schnell",
        "free": True, "picker_order": 2,
        # Schnell's API takes no size: every image is
        # 1024x1024, so that is the only size sold.
        "raw_cents": {"1024x1024": 0.0},
    },
    # The stock photo lane (Jack, 2026-11-20): not a
    # generation model at all. The picker entry returns a
    # real stock photo matching the description, delivered
    # through the same image path as a generated image.
    # Three free stock sources stand behind the one entry,
    # searched in order: Pexels (25,000 requests a month on
    # the key the shorts lane already rides), then Unsplash
    # and Pixabay once their keys are stored
    # (UNSPLASH_ACCESS_KEY, PIXABAY_API_KEY); a source with
    # no key yet is skipped silently. Stock costs the
    # platform nothing inside the sources' free quotas, so
    # the lane sits in Free Models honestly at raw cost $0.
    # It sells one size because a found photo arrives at
    # the size the source serves, not a size the platform
    # picks. The create flow searches before any job
    # exists, so a description with no match fails clean
    # there, no job, no count, no charge (see create_image
    # in app.py).
    "stock": {
        "label": "Stock image", "provider": "pexels",
        "endpoint": "search",
        "free": True, "picker_order": 3,
        "raw_cents": {"large": 0.0},
    },
    # The OVHcloud free image lane (2026-11-20): SDXL on
    # OVHcloud AI Endpoints, priced at 0 EUR in OVHcloud's
    # own catalog with no credit pool behind it, so the
    # lane sits in Free Models honestly at raw cost $0.
    # The route is rate limited, not metered: 2 requests a
    # minute anonymously, 400 a minute with a project key
    # (OVH_AI_API_KEY, sent as the Bearer token the moment
    # one is stored). The platform enforces the pace itself
    # and fails clean past it, never spilling to a paid
    # route (see _ovh_rate_* in app.py). The endpoint
    # answers a 1024x1024 PNG to a prompt and an optional
    # negative prompt (probed live 2026-11-20, anonymous,
    # HTTP 200), so 1024x1024 is the one size sold. The
    # model's license is OpenRAIL++ (commercial use
    # allowed). OVHcloud lists the model as retiring on
    # Feb 10, 2027.
    "sdxl_ovh": {
        "label": "SDXL", "provider": "ovhcloud",
        "endpoint": "https://stable-diffusion-xl.endpoints.kepler.ai.cloud.ovh.net/api/text2image",
        "free": True, "picker_order": 4,
        "raw_cents": {"1024x1024": 0.0},
    },
    # --- VidLab image lanes (2026-11-23, Jack's order: the
    # models Moonfactory does not have yet, wired via VidLab
    # as the cheap route). Every price is VidLab's own catalog
    # figure (GET /v1/models, read 2026-11-23); the endpoint
    # is the VidLab model id the submit sends. Customer price
    # is raw x FRONTIER_MIN_MARKUP via image_lane_price, the
    # owner pays raw. Lanes whose maker publishes no
    # resolution tiers sell one flat size keyed "square" (the
    # model's aspect ratios ride IMAGE_ASPECTS below).
    "ugc_image1": {
        "label": "VidLab UGC Image 1 · $$$$", "provider": "vidlab",
        "endpoint": "ugc-image-1",
        # VidLab's own UGC image model: $0.09 flat per image.
        "raw_cents": {"square": 9.0},
    },
    "nano_banana": {
        "label": "Nano Banana · $", "provider": "beatapi",
        "endpoint": "google/nano-banana",
        # Rerouted to BeatAPI (route audit follow-up,
        # 2026-11-27): BeatAPI's offers price the original
        # Nano Banana at $0.015 flat per image, half VidLab's
        # $0.03 (recorded in DISPLACED_IMAGE_RATES_CENTS below
        # as nano_banana_vidlab; the VidLab submit branch
        # stays wired as the flip back). The customer price
        # follows the cost down (the owner's ruling,
        # 2026-11-27: image prices follow cost).
        "raw_cents": {"square": 1.5},
    },
    "nano_banana2_lite": {
        "label": "Nano Banana 2 Lite · $", "provider": "vidlab",
        "endpoint": "nano-banana-2-lite",
        "raw_cents": {"1K": 2.0},
    },
    "seedream55": {
        # The moderated Seedream 5.5 (the uncensored build
        # above stays on Wiro), rerouted to BeatAPI (route
        # audit follow-up, 2026-11-27): BeatAPI's offers
        # price it at $0.014 at 1k and 1.5k and $0.024 at 2k,
        # under VidLab's $0.016 / $0.016 / $0.028 (recorded
        # in DISPLACED_IMAGE_RATES_CENTS below as
        # seedream55_vidlab; the VidLab submit branch stays
        # wired as the flip back) and Wiro's $0.028 / $0.028
        # / $0.043 for the same tiers. The customer price
        # follows the cost down (the owner's ruling,
        # 2026-11-27: image prices follow cost).
        "label": "Seedream 5.5 · $", "provider": "beatapi",
        "endpoint": "bytedance/seedream-5.5",
        "raw_cents": {"1K": 1.4, "1.5K": 1.4, "2K": 2.4},
    },
    "seedream5pro": {
        # The moderated Seedream 5.0 Pro (uncensored build
        # stays on SpicyAPI): $0.04 flat across 1k / 1.5k /
        # 2k on VidLab.
        "label": "Seedream 5.0 Pro · $$$", "provider": "vidlab",
        "endpoint": "seedream-5.0-pro",
        "raw_cents": {"1K": 4.0, "1.5K": 4.0, "2K": 4.0},
    },
    "seedream50_lite": {
        "label": "Seedream 5.0 Lite · $$$", "provider": "vidlab",
        "endpoint": "seedream-5.0-lite",
        # 4k only, $0.04 per image.
        "raw_cents": {"4K": 4.0},
    },
    "seedream50_flash": {
        "label": "Seedream 5.0 Flash · $", "provider": "vidlab",
        "endpoint": "seedream-5.0-flash",
        "raw_cents": {"square": 2.0},
    },
    "seedream45": {
        "label": "Seedream 4.5 · $$$", "provider": "vidlab",
        "endpoint": "seedream-4.5",
        "raw_cents": {"square": 4.0},
    },
    "seedream40": {
        "label": "Seedream 4.0 · $$", "provider": "vidlab",
        "endpoint": "seedream-4.0",
        "raw_cents": {"square": 2.9},
    },
    "gpt_image2": {
        "label": "GPT Image 2 · $$", "provider": "vidlab",
        "endpoint": "gpt-image-2",
        # $0.03 per image at every tier on VidLab.
        "raw_cents": {"1K": 3.0, "2K": 3.0, "4K": 3.0},
    },
    # GPT Image 1 was REMOVED from the shelf 2026-11-25
    # (Jack's order): OpenAI shut the model down on
    # 2026-10-23, so the lane could only ever fail. A dead
    # lane is not left listed for customers to pick. Old job
    # records still carry image_model "gpt_image1"; every
    # read path resolves lanes with .get, so they render in
    # libraries and history exactly as before. The frontier
    # sweep must never re-wire it (maker-shutdown model;
    # ledger row records the removal).
    "gpt_image25_flare": {
        "label": "GPT Image 2.5 Flare · $", "provider": "vidlab",
        "endpoint": "gpt-image-2.5-flare",
        "raw_cents": {"1K": 2.0, "2K": 2.0, "4K": 3.0},
    },
    "gpt_image25_sunburst": {
        "label": "GPT Image 2.5 Sunburst · $", "provider": "vidlab",
        "endpoint": "gpt-image-2.5-sunburst",
        "raw_cents": {"1K": 2.0, "2K": 2.0, "4K": 3.0},
    },
    "flux2_pro": {
        "label": "Flux 2 Pro · $$", "provider": "vidlab",
        "endpoint": "flux-2-pro",
        # Priced per megapixel tier: $0.03 / $0.05 / $0.06 /
        # $0.08 at 1mp / 2mp / 3mp / 4mp.
        "raw_cents": {"1MP": 3.0, "2MP": 5.0, "3MP": 6.0,
                      "4MP": 8.0},
    },
    "flux2_flex": {
        "label": "Flux 2 Flex · $$$", "provider": "vidlab",
        "endpoint": "flux-2-flex",
        "raw_cents": {"1MP": 5.0, "2MP": 10.0, "3MP": 15.0,
                      "4MP": 20.0},
    },
    "flux2_max": {
        "label": "Flux 2 Max · $$$", "provider": "vidlab",
        "endpoint": "flux-2-max",
        "raw_cents": {"1MP": 7.0, "2MP": 10.0, "3MP": 13.0,
                      "4MP": 16.0},
    },
    "flux3_image": {
        "label": "Flux 3 · $$$", "provider": "spicyapi",
        "endpoint": "black-forest-labs/flux-3-image/text-to-image",
        # Rerouted to SpicyAPI (route audit follow-up,
        # 2026-11-27): its catalog prices the text-to-image
        # build per pixel variant, $0.0408 at 1024, $0.0595
        # at 1536, $0.085 at 2048 and $0.51595 at 4096,
        # under VidLab's $0.05 / $0.07 / $0.10 / $0.61
        # (recorded in DISPLACED_IMAGE_RATES_CENTS below as
        # flux3_image_vidlab; the VidLab submit branch stays
        # wired as the flip back). The 768-square variant
        # ($0.03485) is not sold, as before. The customer
        # price follows the cost down (the owner's ruling,
        # 2026-11-27: image prices follow cost).
        "raw_cents": {"1K": 4.08, "1.5K": 5.95, "2K": 8.5,
                      "4K": 51.6},
    },
    "flux3_klein": {
        "label": "Flux 3 Klein · $", "provider": "vidlab",
        "endpoint": "flux-3-klein",
        "raw_cents": {"1K": 1.0, "1.5K": 2.0, "2K": 2.0,
                      "4K": 4.0},
    },
    "flux_kontext_pro": {
        "label": "Flux Kontext Pro · $$$", "provider": "vidlab",
        "endpoint": "flux-kontext-pro",
        "raw_cents": {"square": 4.0},
    },
    "midjourney_v7": {
        # Midjourney V7 prices by speed mode, not size: relax
        # $0.06, fast $0.07, turbo $0.13. The size keys are
        # the mode values the submit sends as its quality.
        "label": "Midjourney V7 · $$$", "provider": "vidlab",
        "endpoint": "midjourney-v7",
        "raw_cents": {"relax": 6.0, "fast": 7.0, "turbo": 13.0},
    },
    "midjourney_v82": {
        # Midjourney V8.2 via CometAPI (frontier pass,
        # 2026-11-27): Midjourney's current default version
        # (since 2026-07-24) had no lane here; the only
        # Midjourney lane was V7 on VidLab above. Midjourney
        # sells no API of its own, Kie removed Midjourney in
        # 2025 and fal never carried it; CometAPI relays it
        # (POST /mj/submit/imagine, the version riding the
        # prompt as its native --v flag, the bot and speed
        # mode carried on the lane and size key) and the
        # platform already holds a CometAPI key for the Wan
        # 3.0 Prime lane. CometAPI prices the imagine
        # operation by speed mode, not by version: its live
        # catalog (read 2026-11-27) lists mj_fast_imagine
        # at $0.105 per task and mj_turbo_imagine at
        # $0.315, each with the 0.8 pricing ratio its
        # storefront applies, rendering $0.084 / $0.252
        # payable per task; the rendered figures are the
        # raw cost on every CometAPI version lane (V8.2,
        # V8.1, V6.1, Niji 7 below). No relax imagine model
        # exists in CometAPI's catalog, so relax does not
        # sell on these lanes. One imagine task returns one
        # 2x2 grid image, the same deliverable as the VidLab
        # V7 lane. PAYLOAD-UNVERIFIED until a funded job
        # proves the submit; no proof job ran under the
        # Modal spend gate.
        "label": "Midjourney V8.2 · $$$$",
        "provider": "cometapi",
        "endpoint": "mj/submit/imagine",
        "mj_bot": "MID_JOURNEY", "mj_version": "--v 8.2",
        "raw_cents": {"fast": 8.4, "turbo": 25.2},
    },
    "midjourney_v81": {
        # Midjourney V8.1 via CometAPI (frontier pass,
        # 2026-11-27): the prior version of the current
        # line (default 2026-06-10 to 2026-07-23), carried
        # as its own lane beside V8.2 on the same relay and
        # the same per mode figures as the V8.2 lane above.
        "label": "Midjourney V8.1 · $$$$",
        "provider": "cometapi",
        "endpoint": "mj/submit/imagine",
        "mj_bot": "MID_JOURNEY", "mj_version": "--v 8.1",
        "raw_cents": {"fast": 8.4, "turbo": 25.2},
    },
    "midjourney_v61": {
        # Midjourney V6.1 via CometAPI (frontier pass,
        # 2026-11-27): the legacy version CometAPI's own
        # imagine documentation uses as its worked example
        # (--v 6.1), carried as its own lane on the same
        # relay and the same per mode figures as above.
        "label": "Midjourney V6.1 · $$$$",
        "provider": "cometapi",
        "endpoint": "mj/submit/imagine",
        "mj_bot": "MID_JOURNEY", "mj_version": "--v 6.1",
        "raw_cents": {"fast": 8.4, "turbo": 25.2},
    },
    "midjourney_niji7": {
        # Niji 7 via CometAPI (frontier pass, 2026-11-27):
        # the current Niji anime line (January 2026),
        # carried as its own lane on the same relay: the
        # NIJI_JOURNEY bot with its --niji 7 flag, at the
        # same per mode figures as the Midjourney lanes
        # above. Niji V8 (launched 2026-11-24) has no API
        # announced anywhere and is not wired.
        "label": "Niji 7 · $$$$", "provider": "cometapi",
        "endpoint": "mj/submit/imagine",
        "mj_bot": "NIJI_JOURNEY", "mj_version": "--niji 7",
        "raw_cents": {"fast": 8.4, "turbo": 25.2},
    },
    "qwen_image20": {
        "label": "Qwen Image 2.0 · $$", "provider": "vidlab",
        "endpoint": "qwen-image-2.0",
        "raw_cents": {"square": 3.0},
    },
    "qwen_image20_pro": {
        "label": "Qwen Image 2.0 Pro · $$$", "provider": "vidlab",
        "endpoint": "qwen-image-2.0-pro",
        "raw_cents": {"square": 7.0},
    },
    "qwen_image30": {
        "label": "Qwen Image 3.0 · $$", "provider": "vidlab",
        "endpoint": "qwen-image-3.0",
        "raw_cents": {"1K": 3.0, "2K": 3.0},
    },
    "qwen_image30_pro": {
        "label": "Qwen Image 3.0 Pro · $$$", "provider": "vidlab",
        "endpoint": "qwen-image-3.0-pro",
        "raw_cents": {"1K": 4.0, "2K": 8.0},
    },
    "wan27_image": {
        "label": "Wan 2.7 Image · $$", "provider": "vidlab",
        "endpoint": "wan-2.7-image",
        "raw_cents": {"square": 3.0},
    },
    "wan27_image_pro": {
        "label": "Wan 2.7 Image Pro · $$$", "provider": "vidlab",
        "endpoint": "wan-2.7-image-pro",
        "raw_cents": {"square": 7.0},
    },
    "mai_image26": {
        # Rerouted to SpicyAPI (route audit follow-up,
        # 2026-11-27): its catalog prices the text-to-image
        # build flat at $0.0335 per image at any size, under
        # VidLab's $0.04 at 1k and $0.09 at 2k (recorded in
        # DISPLACED_IMAGE_RATES_CENTS below as
        # mai_image26_vidlab; the VidLab submit branch stays
        # wired as the flip back). The customer price
        # follows the cost down (the owner's ruling,
        # 2026-11-27: image prices follow cost).
        "label": "MAI Image 2.6 · $$", "provider": "spicyapi",
        "endpoint": "microsoft/mai-image-2.6/text-to-image",
        "raw_cents": {"1K": 3.35, "2K": 3.35},
    },
    "mai_image26_flash": {
        # Rerouted to SpicyAPI (route audit follow-up,
        # 2026-11-27): its catalog prices the text-to-image
        # build flat at $0.0165 per image at any size, under
        # VidLab's $0.02 at 1k and $0.05 at 2k (recorded in
        # DISPLACED_IMAGE_RATES_CENTS below as
        # mai_image26_flash_vidlab; the VidLab submit branch
        # stays wired as the flip back). The customer price
        # follows the cost down (the owner's ruling,
        # 2026-11-27: image prices follow cost).
        "label": "MAI Image 2.6 Flash · $",
        "provider": "spicyapi",
        "endpoint": "microsoft/mai-image-2.6-flash/text-to-image",
        "raw_cents": {"1K": 1.65, "2K": 1.65},
    },
    "mai_image27": {
        # MAI Image 2.7 (frontier sweep, 2026-11-27): Microsoft's
        # successor to the 2.6 pair above, released 2026-11-17 and
        # carried here as its own lane the moment a wired supplier
        # listed it. fal is the cheapest verified route: fal's own
        # token card ($34 per million output image tokens; a
        # 1024x1024 image measures 1,024 tokens, $0.034816) and
        # LiteLLM's fal price read ($0.0349 per image, checked
        # 2026-11-27) agree, and both sit far under SpicyAPI's
        # $0.084 at 1K. fal bills per token, so like the GPT Image
        # 2.6 fal lanes the only size sold is the measured
        # 1024x1024 until a funded pass measures the real charge
        # at other sizes. PAYLOAD-UNVERIFIED like every fal image
        # lane until a real job proves it; no proof job ran under
        # the Modal spend gate.
        "label": "MAI Image 2.7 · $$", "provider": "fal",
        "endpoint": "microsoft/mai-image-2.7/text-to-image",
        "raw_cents": {"1024x1024": 3.49},
    },
    "mai_image27_flash": {
        # MAI Image 2.7 Flash (frontier sweep, 2026-11-27): the
        # speed tier of the same 2026-11-17 Microsoft release,
        # carried as its own lane beside the flagship. Same fal
        # route and same reasoning: fal's token card ($10 per
        # million output image tokens, $0.01024 at 1024x1024) and
        # LiteLLM's fal read ($0.0103 per image, checked
        # 2026-11-27) agree, under SpicyAPI's $0.030 at 1K. Only
        # the measured 1024x1024 size sells, as on the flagship.
        "label": "MAI Image 2.7 Flash · $", "provider": "fal",
        "endpoint": "microsoft/mai-image-2.7/flash/text-to-image",
        "raw_cents": {"1024x1024": 1.03},
    },
    "hunyuan_image35": {
        "label": "Hunyuan Image 3.5 Preview · $$",
        "provider": "vidlab",
        "endpoint": "hunyuan-image-3.5-preview",
        "raw_cents": {"1K": 3.0, "2K": 3.0, "4K": 4.0},
    },
    "grok_imagine_image": {
        "label": "Grok Imagine Image · $", "provider": "vidlab",
        "endpoint": "grok-imagine-image",
        "raw_cents": {"1K": 2.0, "2K": 2.0},
    },
    "grok_imagine_image20": {
        "label": "Grok Imagine Image 2.0 · $", "provider": "vidlab",
        "endpoint": "grok-imagine-image-2.0",
        "raw_cents": {"1K": 2.0, "1.5K": 2.0, "2K": 2.0},
    },
    "grok_imagine_image20_quality": {
        "label": "Grok Imagine Image 2.0 Quality · $$$",
        "provider": "vidlab",
        "endpoint": "grok-imagine-image-2.0-quality",
        "raw_cents": {"1K": 5.0, "2K": 7.0},
    },
    "grok_imagine15_edit": {
        # An EDIT model: one source image plus an instruction,
        # $0.02 flat. Like the Qwen edit lane, the output keeps
        # the source image's size, so the only size is "match".
        "label": "Grok Imagine 1.5 Edit · $", "provider": "vidlab",
        "endpoint": "grok-imagine-1.5-edit",
        "edit": True,
        "raw_cents": {"match": 2.0},
    },
}


# Displaced image rates (cents per image): the raw costs a
# lane carried on the route a reroute moved it off, recorded
# the way DISPLACED_RATES_CENTS_PER_SECOND records the video
# lanes' old routes. The old provider's submit branch stays
# wired in app.py, so a flip back is restoring the lane's
# provider / endpoint / raw_cents from the row here. Image
# customer prices are NOT shelf frozen (the owner's ruling,
# 2026-11-27: image prices follow cost down), so no shelf
# table accompanies this one.
DISPLACED_IMAGE_RATES_CENTS = {
    # VidLab rates displaced by the 2026-11-27 route audit
    # follow-up (BeatAPI for the first two, SpicyAPI for the
    # rest).
    "nano_banana_vidlab": {"square": 3.0},
    "seedream55_vidlab": {"1K": 1.6, "1.5K": 1.6, "2K": 2.8},
    "mai_image26_vidlab": {"1K": 4.0, "2K": 9.0},
    "mai_image26_flash_vidlab": {"1K": 2.0, "2K": 5.0},
    "flux3_image_vidlab": {"1K": 5.0, "1.5K": 7.0, "2K": 10.0,
                           "4K": 61.0},
}


# Picker labels (Jack, 2026-11-17): sizes are shown as real pixel
# dimensions, never as 1K/2K/4K labels ("nobody knows what K
# means"). Each dimension is the provider's own published output
# for that size at the aspect the submits use (the providers'
# default, square unless a lane says otherwise): Google's Gemini
# image-generation table for the Nano Banana tiers (1:1 rows:
# 1024x1024 / 2048x2048 / 4096x4096), the Seedream family's
# published square presets for the Seedream tiers (1K 1024x1024,
# 2K 2048x2048; the 1.5K tier is the one fal's Seedream pricing
# bounds at 1536x1536, inside BytePlus's <=2.61MP band for the
# tier), and the worker's IMAGE_DIMS for the house lane aspects.
IMAGE_SIZE_LABELS = {
    "house": {"9:16": "720 x 1280", "16:9": "1280 x 720",
              "1:1": "960 x 960"},
    "nano_banana_21": {"1K": "1024 x 1024", "2K": "2048 x 2048",
                       "4K": "4096 x 4096"},
    "nano_banana_pro": {"1K": "1024 x 1024", "2K": "2048 x 2048",
                        "4K": "4096 x 4096"},
    "nano_banana_2": {"1K": "1024 x 1024", "2K": "2048 x 2048",
                      "4K": "4096 x 4096"},
    "seedream55_unc": {"1K": "1024 x 1024", "1.5K": "1536 x 1536",
                       "2K": "2048 x 2048"},
    "seedream5pro_unc": {"1K": "1024 x 1024", "1.5K": "1536 x 1536",
                         "2K": "2048 x 2048"},
    # Qwen Image Edit declares no sizes: the output keeps the
    # source image's dimensions, and the picker says exactly
    # that instead of inventing pixel sizes.
    "qwen_edit_unc": {"match": "Same size as your image"},
    # The stock lane's one size is the rendition Pexels
    # serves for a found photo; the picker says Large rather
    # than inventing pixel dimensions for someone else's
    # photograph.
    "stock": {"large": "Large"},
    # VidLab lanes (2026-11-23): tier dims follow the same
    # published square presets as the families above; the
    # word keys name what the key actually chooses (a quality
    # tier, a speed mode, a megapixel count, or the square
    # base of a flat lane).
    "nano_banana": {"square": "Square"},
    "nano_banana2_lite": {"1K": "1024 x 1024"},
    "seedream55": {"1K": "1024 x 1024", "1.5K": "1536 x 1536",
                   "2K": "2048 x 2048"},
    "seedream5pro": {"1K": "1024 x 1024", "1.5K": "1536 x 1536",
                     "2K": "2048 x 2048"},
    "seedream50_lite": {"4K": "4096 x 4096"},
    "seedream50_flash": {"square": "Square"},
    "seedream45": {"square": "Square"},
    "seedream40": {"square": "Square"},
    "gpt_image2": {"1K": "1024 x 1024", "2K": "2048 x 2048",
                   "4K": "4096 x 4096"},
    "gpt_image25_flare": {"1K": "1024 x 1024", "2K": "2048 x 2048",
                          "4K": "4096 x 4096"},
    "gpt_image25_sunburst": {"1K": "1024 x 1024",
                             "2K": "2048 x 2048",
                             "4K": "4096 x 4096"},
    "flux2_pro": {"1MP": "1 megapixel", "2MP": "2 megapixels",
                  "3MP": "3 megapixels", "4MP": "4 megapixels"},
    "flux2_flex": {"1MP": "1 megapixel", "2MP": "2 megapixels",
                   "3MP": "3 megapixels", "4MP": "4 megapixels"},
    "flux2_max": {"1MP": "1 megapixel", "2MP": "2 megapixels",
                  "3MP": "3 megapixels", "4MP": "4 megapixels"},
    "flux3_image": {"1K": "1024 x 1024", "1.5K": "1536 x 1536",
                    "2K": "2048 x 2048", "4K": "4096 x 4096"},
    "flux3_klein": {"1K": "1024 x 1024", "1.5K": "1536 x 1536",
                    "2K": "2048 x 2048", "4K": "4096 x 4096"},
    "flux_kontext_pro": {"square": "Square"},
    "midjourney_v7": {"relax": "Relax speed", "fast": "Fast speed",
                      "turbo": "Turbo speed"},
    "midjourney_v82": {"fast": "Fast speed", "turbo": "Turbo speed"},
    "midjourney_v81": {"fast": "Fast speed", "turbo": "Turbo speed"},
    "midjourney_v61": {"fast": "Fast speed", "turbo": "Turbo speed"},
    "midjourney_niji7": {"fast": "Fast speed",
                         "turbo": "Turbo speed"},
    "qwen_image20": {"square": "Square"},
    "qwen_image20_pro": {"square": "Square"},
    "qwen_image30": {"1K": "1024 x 1024", "2K": "2048 x 2048"},
    "qwen_image30_pro": {"1K": "1024 x 1024", "2K": "2048 x 2048"},
    "wan27_image": {"square": "Square"},
    "wan27_image_pro": {"square": "Square"},
    "mai_image26": {"1K": "1024 x 1024", "2K": "2048 x 2048"},
    "mai_image26_flash": {"1K": "1024 x 1024", "2K": "2048 x 2048"},
    "hunyuan_image35": {"1K": "1024 x 1024", "2K": "2048 x 2048",
                        "4K": "4096 x 4096"},
    "grok_imagine_image": {"1K": "1024 x 1024", "2K": "2048 x 2048"},
    "grok_imagine_image20": {"1K": "1024 x 1024",
                             "1.5K": "1536 x 1536",
                             "2K": "2048 x 2048"},
    "grok_imagine_image20_quality": {"1K": "1024 x 1024",
                                     "2K": "2048 x 2048"},
    "grok_imagine15_edit": {"match": "Same size as your image"},
    "ugc_image1": {"square": "Square"},
}


# Aspect shapes on the tier priced lanes (provider research,
# 2026-11-19): BeatAPI's Nano Banana image docs and SpicyAPI's
# Seedream 5.0 Pro page publish an aspect ratio input beside
# the resolution tier, and both price by the tier alone (the
# ratio never changes the price), so every published shape at
# a tier sells at that tier's raw price. The size key names
# both halves: "1K-16:9" is the 1K tier at 16:9, and the
# submits split the key back into resolution + aspect_ratio.
# BeatAPI publishes the same fifteen ratios for Nano Banana
# 2.1 and 2; the Pro drops the four banner ratios (1:4, 4:1,
# 1:8, 8:1). SpicyAPI publishes fifteen ratios for Seedream
# 5.0 Pro. "auto" is not a size and is never offered. Exact
# pixels per shape are the model's own choice on these
# routes (BeatAPI says so in words), so shaped sizes are
# labeled by ratio and tier below, never invented dimensions.
# The lanes NOT here stay as they are, on their providers'
# own published facts: Wiro's Seedream 5.5 page names an
# aspect input but publishes neither its values nor a per
# tier price table; fal's GPT Image publishes token pricing
# and measured examples only, no per size price; the Z-Image
# lanes already sell real pixel shapes at one flat price; the
# Qwen edit endpoint publishes no size inputs at all.
IMAGE_ASPECTS = {
    "nano_banana_21": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                       "4:5", "5:4", "21:9", "4:1", "1:4", "8:1",
                       "1:8"),
    "nano_banana_2": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                      "4:5", "5:4", "21:9", "4:1", "1:4", "8:1",
                      "1:8"),
    # Nano Banana Pro rerouted to VidLab 2026-11-23: VidLab
    # publishes only these six non-square ratios for the
    # model, so the lane's set narrows from BeatAPI's nine.
    "nano_banana_pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "seedream5pro_unc": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                         "4:5", "5:4", "21:9", "9:21", "2:1", "1:2",
                         "3:1", "1:3"),
    # VidLab image lanes (2026-11-23): every VidLab image
    # model publishes the same seven aspect ratios (1:1 plus
    # these six), except Seedream 5.5, which adds 21:9, 2:1
    # and 1:2. The edit lane takes no aspects.
    "ugc_image1": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "nano_banana": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "nano_banana2_lite": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "seedream55": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                   "21:9", "2:1", "1:2"),
    "seedream5pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "seedream50_lite": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "seedream50_flash": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "seedream45": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "seedream40": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "gpt_image2": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "gpt_image25_flare": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "gpt_image25_sunburst": ("16:9", "9:16", "4:3", "3:4", "3:2",
                             "2:3"),
    "flux2_pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "flux2_flex": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "flux2_max": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "flux3_image": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "flux3_klein": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "flux_kontext_pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "midjourney_v7": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    # CometAPI Midjourney lanes (2026-11-27): Midjourney
    # takes the same six non-square ratios through its
    # native --ar flag, and the relay prices by speed
    # mode alone, so every shape sells at its mode's raw
    # price, the V7 lane's shape on its new route.
    "midjourney_v82": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "midjourney_v81": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "midjourney_v61": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "midjourney_niji7": ("16:9", "9:16", "4:3", "3:4", "3:2",
                         "2:3"),
    "qwen_image20": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "qwen_image20_pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "qwen_image30": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "qwen_image30_pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "wan27_image": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "wan27_image_pro": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "mai_image26": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "mai_image26_flash": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "hunyuan_image35": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "grok_imagine_image": ("16:9", "9:16", "4:3", "3:4", "3:2", "2:3"),
    "grok_imagine_image20": ("16:9", "9:16", "4:3", "3:4", "3:2",
                             "2:3"),
    "grok_imagine_image20_quality": ("16:9", "9:16", "4:3", "3:4",
                                     "3:2", "2:3"),
}

for _lane_id, _aspects in IMAGE_ASPECTS.items():
    _lane = IMAGE_LANES[_lane_id]
    _expanded = {}
    for _tier, _raw in list(_lane["raw_cents"].items()):
        _expanded[_tier] = _raw
        for _aspect in _aspects:
            _expanded[f"{_tier}-{_aspect}"] = _raw
    _lane["raw_cents"] = _expanded
del _lane_id, _aspects, _lane, _expanded, _tier, _raw, _aspect


def image_size_label(lane_id: str, size: str) -> str:
    """The picker label for one size: real pixel dimensions
    where the provider publishes them; a shaped tier size is
    labeled by its ratio and tier, the terms its provider
    actually publishes for it."""
    labels = IMAGE_SIZE_LABELS.get(lane_id) or {}
    if size in labels:
        return labels[size]
    if "-" in size and ":" in size:  # a tier-aspect key
        tier, aspect = size.split("-", 1)
        return f"{aspect} · {tier}"
    if "x" in size:  # pixel-keyed sizes (GPT Image, Z-Image)
        return size.replace("x", " x ")
    return size


def image_lane_price(model: str, size: str, owner: bool = False):
    """(raw provider cost, customer debit) in cents per image."""
    raw = float(IMAGE_LANES[model]["raw_cents"][size])
    if owner and IMAGE_LANES[model]["provider"] == "kaggle-comfy":
        # A lane on the house Kaggle worker (Krea 2 Verite)
        # costs the platform $0 on free compute, so the
        # owner's at-cost price is $0; the entry's raw figure
        # is the customer price basis, set to match the
        # comparable uncensored lanes.
        return 0.0, 0.0
    return raw, (raw if owner else raw * FRONTIER_MIN_MARKUP)


def image_lane_charge_cents(model: str, size: str,
                            owner: bool = False) -> int:
    """The final integer cents one shelf image debits, through
    the one shared rounding path (Jack, 2026-11-23): the image
    quote endpoint returns this figure and image creation
    charges this figure, so the displayed price and the charge
    cannot disagree. A customer pays the 1.25x figure rounded
    UP whenever it lands on a fraction, never a cent short;
    the owner pays the provider's raw price exactly as
    image_lane_price returns it (whole cents on every lane,
    so his rounding is the identity it has always been)."""
    _raw, debit = image_lane_price(model, size, owner=owner)
    if owner:
        return int(round(debit))
    return customer_debit_cents(debit)


def image_quote(owner: bool = False) -> dict:
    return {
        "price_cents": image_price_cents(owner),
        "measured_cost_cents": IMAGE_MEASURED_COST_CENTS,
    }


# Voice cloning (2026-11-21): CosyVoice zero-shot cloning on
# the house Kaggle voice worker, wired as the "Voice cloning"
# pick in the voice picker. Same economics as the Krea 2
# Verite lane above: the run itself costs $0 on Kaggle's free
# compute, and the nominal raw basis matches the comparable
# uncensored lanes' 1.9 cents, so the customer price is that
# basis marked up once by the frontier minimum, debited once
# per job that uses cloning, never per line. The owner pays
# the route's true raw cost, $0.
VOICE_CLONE_RAW_CENTS = 1.9


def voice_clone_price_cents(owner: bool = False) -> int:
    """The integer cents one job using voice cloning debits:
    the nominal basis marked up once by the frontier minimum
    (2 cents), or the true raw cost for the owner ($0 on the
    Kaggle route)."""
    if owner:
        return 0
    return int(round(VOICE_CLONE_RAW_CENTS * FRONTIER_MIN_MARKUP))
