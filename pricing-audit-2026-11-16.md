# Moonfactory pricing audit — 2026-11-16

Jack's rule, restated 2026-11-16: every Moonfactory price must be
(a) cheaper than Higgsfield's equivalent price wherever a comparison
is possible, AND (b) still marked up from the actual API cost —
never sold at or below cost. The per-second-lane floor is cost x
1.25 (Amendment 3); self-hosted lanes keep their Rule 2 floor
(measured cost x 3, grossed up for Stripe fees). The owner account
(support@magrify.com) is exempt from markups by Jack's separate
order and is out of scope here; all figures below are the standard
public prices from the anonymous GET /api/pricing view, re-probed
live after the fix deploy.

## Method and comparison basis

- Our cost per second = the lane's live provider rate (the
  platform's refreshed rate table, seeded from pricing.py) + the
  0.1c/s measured local post cost. Markup = sell / cost.
- Higgsfield comparison basis, the platform's established one
  (PARITY-PROGRAM.md, pricing.py): Higgsfield's published credit
  count for the comparable clip, converted at Plus monthly credit
  rates ($59 / 1,200 credits = 4.9167c per credit). Plus is the
  cheapest Higgsfield plan that offers the full shelf (Starter,
  $19 / 270 credits = 7.04c/credit, excludes standard Seedance 2.0
  and 2.5 and carries only selected models), so the Plus basis is
  the conservative one: a lane that undercuts at Plus rates
  undercuts at Starter rates too. Ultra/annual rates would make
  Higgsfield cheaper per credit; no lane verdict below flips on
  that basis except the ones already flagged thin or OVER.
- Credit counts are Higgsfield's own published numbers as read
  2026-11-03 to 2026-11-09 (its pricing-page comparison table and
  Generate-button reads, relayed by CamClo's and Aliteq's dated
  reads of higgsfield.ai/pricing, plus HIGGSFIELD-PARITY.md's read
  of Higgsfield's own 2026-11-06 pricing guide). Where two reads
  conflict, that is stated. Where no count is published, the lane
  is NOCOMP — no Higgsfield price was invented.
- Like for like: same model, same resolution class, per-second
  normalized. Our Kling 3.0 lane ships with sound on, so it is
  compared against Higgsfield's sound-on credit count.

## What was wrong and what changed (commit 83f1c87, deployed, verified live)

1. **Seedance 2.5 was OVER Higgsfield at 480p and 1080p.** The
   lane's Higgsfield figure was a single 720p number (35 credits)
   scaled to other resolutions by the provider-rate ratio (2.62x
   at 1080p). Higgsfield's own published counts step 35 -> 80
   credits at 1080p (2.29x) and 15 credits at 480p, so the scaled
   figures overstated Higgsfield (89.5-90.0c/s at 1080p against a
   true 78.666c/s; 17.9-18.0c/s at 480p against a true 14.75c/s)
   and the 0.97x undercut target landed above the real price.
   Fix: pricing.py gained
   HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP_BY_RESOLUTION with the
   published per-resolution figures (Seedance 2.5: 73.75c per 5 s
   clip at 480p, 393.33c at 1080p; Wan 3.0: 86.04c at 1080p from
   its published 17.5 credits), used by both frontier_lane paths.
   Live result: Seedance 2.5 sell 17.48 -> **14.308c/s at 480p**
   (Higgsfield 14.75; floor 12.125) and 87.329 -> **76.307c/s at
   1080p** (Higgsfield 78.666; floor 60.25-60.58). 720p unchanged
   at 33.368 vs 34.4.
2. **Published floor rates are now exact.** frontier_lane rounded
   the minimum/sell to 3 decimals with round(); Wan 3.0 480p's
   minimum is exactly 4.5625c/s and was published as 4.562, a sell
   rate technically 0.01% under the 1.25x floor (the per-job
   balance debit always ceils to whole cents, so no job ever
   actually charged under the floor). Minimum/sell now ceil to 3
   decimals (_ceil3). A few floored lanes moved +0.001c/s (Kling
   3.0 / Omni 9.562 -> 9.563, Seedance 2.0 720p 19.616 -> 19.617,
   Wan 3.0 Prime target 14.356 -> 14.357).
3. **Wan 3.0 1080p reclassified.** Against the scaled figure
   (16.856) it read OVER; against Higgsfield's published 1080p
   count (17.5 credits = 17.208c/s) the floor sell of 17.125 is
   UNDER, by 0.083c/s (0.5%). The 3%-undercut target (16.692) is
   below the floor, so the lane stays at the floor; can_undercut
   stays false in the payload because the full target is not
   reachable.

No other price changed. No route changed: every OVER lane left is
already on the cheapest verified route in the 2026-11-16 sweep.

## Lane table (live, post-fix; cents per second unless noted)

| Lane | Cost | Sell | Markup | Higgsfield | Verdict |
|---|---|---|---|---|---|
| Wan 3.0 480p | 3.650 | 4.563 | 1.250x | 4.400 (scaled est.; no published 480p count) | OVER — cannot undercut |
| Wan 3.0 720p | 7.200 | 9.000 | 1.250x | 8.800 (9 credits / 5 s) | OVER — cannot undercut |
| Wan 3.0 1080p | 13.700 | 17.125 | 1.250x | 17.208 (17.5 credits / 5 s) | UNDER (0.5%, at floor) |
| Wan 3.0 Prime 480p | 5.540 | 14.357 | 2.592x | 14.800 | UNDER |
| Wan 3.0 Prime 720p | 5.540 | 14.357 | 2.592x | 14.800 | UNDER |
| Wan 3.0 Prime 1080p | 5.540 | 14.357 | 2.592x | 14.800 | UNDER |
| Seedance 2.5 480p | 9.781 | 14.308 | 1.463x | 14.750 (15 credits / 5 s) | UNDER — fixed this audit |
| Seedance 2.5 720p | 18.580 | 33.368 | 1.796x | 34.400 (35 credits / 5 s) | UNDER |
| Seedance 2.5 1080p | 48.465 | 76.307 | 1.574x | 78.666 (80 credits / 5 s) | UNDER — fixed this audit |
| Veo 3.1 720p | 12.100 | 15.125 | 1.250x | 49.167 (~40 credits / 4 s) | UNDER |
| Veo 3.1 1080p | 12.100 | 15.125 | 1.250x | 49.167 | UNDER |
| Veo 3.1 Fast 720p | 15.100 | 18.875 | 1.250x | 19.667 (~16 credits / 4 s) | UNDER (thin: 4% under) |
| Veo 3.1 Fast 1080p | 15.100 | 18.875 | 1.250x | 19.667 | UNDER (thin: 4% under) |
| Kling 3.0 720p | 7.650 | 9.563 | 1.250x | 9.833 (10 credits / 5 s, sound on) | UNDER (thin: 2.8% under) |
| Kling 3.0 1080p | 7.650 | 9.563 | 1.250x | — (sound-on count unpublished; sound-off ~8 credits = 7.867) | NOCOMP |
| Kling 4.0 720p | 8.600 | 10.750 | 1.250x | 11.063 (11.25 credits / 5 s) | UNDER (thin: 2.8% under) |
| Kling 4.0 1080p | 21.350 | 26.688 | 1.250x | 15.979 (16.25 credits / 5 s) | OVER — cannot undercut |
| Kling 3.0 Omni 720p | 7.650 | 9.563 | 1.250x | — | NOCOMP |
| Kling 3.0 Omni 1080p | 7.650 | 9.563 | 1.250x | — | NOCOMP |
| Seedance 2.0 480p | 7.825 | 9.782 | 1.250x | — | NOCOMP |
| Seedance 2.0 720p | 15.693 | 19.617 | 1.250x | 21.633 (~22 credits / 5 s) | UNDER |
| Seedance 2.0 1080p | 38.942 | 48.678 | 1.250x | 59.000 (~60 credits / 5 s) — contested, see below | UNDER (contested) |
| Seedance 2.0 Fast 480p | 3.500 | 4.375 | 1.250x | — | NOCOMP |
| Seedance 2.0 Fast 720p | 3.500 | 4.375 | 1.250x | — | NOCOMP |
| Seedance 2.0 Mini 480p | 1.300 | 1.625 | 1.250x | — | NOCOMP |
| Seedance 2.0 Mini 720p | 1.300 | 1.625 | 1.250x | — | NOCOMP |
| Veo 3.1 Lite 720p | 3.100 | 3.875 | 1.250x | 7.375 (12 credits / 8 s) | UNDER |
| Veo 3.1 Lite 1080p | 8.100 | 10.125 | 1.250x | — | NOCOMP |
| Gemini Omni 1.2 Flash 720p | 2.100 | 2.625 | 1.250x | — | NOCOMP |
| Gemini Omni 1.2 Flash 1080p | 2.100 | 2.625 | 1.250x | — | NOCOMP |
| Gemini Omni 1.1 Flash 720p | 6.100 | 7.625 | 1.250x | — | NOCOMP |
| Gemini Omni 1.1 Flash 1080p | 9.100 | 11.375 | 1.250x | — | NOCOMP |
| Hailuo 2.3 720p | 2.600 | 3.250 | 1.250x | — | NOCOMP |
| MiniMax H3 480p | 2.100 | 2.625 | 1.250x | — (not on Higgsfield's shelf) | NOCOMP |
| MiniMax H3 720p | 3.100 | 3.875 | 1.250x | — | NOCOMP |
| MiniMax H3 1080p | 4.800 | 6.000 | 1.250x | — | NOCOMP |
| Grok Imagine 1.5 480p | 1.300 | 1.625 | 1.250x | — | NOCOMP |
| Grok Imagine 1.5 720p | 2.350 | 2.938 | 1.250x | 22.125 (22.5 credits / 5 s) | UNDER |
| Luma Ray 3.2 480p | 10.100 | 12.625 | 1.250x | — (not on Higgsfield's shelf) | NOCOMP |
| Luma Ray 3.2 720p | 20.100 | 25.125 | 1.250x | — | NOCOMP |
| Luma Ray 3.2 1080p | 40.100 | 50.125 | 1.250x | — | NOCOMP |
| LTX-2.5 720p | 12.100 | 15.125 | 1.250x | — (not on Higgsfield's shelf) | NOCOMP |
| LTX-2.5 1080p | 17.100 | 21.375 | 1.250x | — | NOCOMP |
| LTX-2.5 Fast 720p | 9.100 | 11.375 | 1.250x | — | NOCOMP |
| LTX-2.5 Fast 1080p | 13.100 | 16.375 | 1.250x | — | NOCOMP |
| HappyHorse 1.1 720p | 9.980 | 12.476 | 1.250x | — (not on Higgsfield's shelf) | NOCOMP |
| HappyHorse 1.1 1080p | 12.880 | 16.100 | 1.250x | — | NOCOMP |
| Pika 2.2 720p | 4.100 | 5.125 | 1.250x | — | NOCOMP |
| Pika 2.2 1080p | 9.100 | 11.375 | 1.250x | — | NOCOMP |
| Vidu Q3 480p | 1.890 | 2.363 | 1.250x | — (not on Higgsfield's shelf) | NOCOMP |
| Vidu Q3 720p | 2.780 | 3.476 | 1.250x | — | NOCOMP |
| Runway Gen-4.5 720p | 12.100 | 15.125 | 1.250x | — (not on Higgsfield's shelf) | NOCOMP |
| Runway Gen-4.5 1080p | 12.100 | 15.125 | 1.250x | — | NOCOMP |
| Vidu Q2 / Vidu Q4 / Seedance 2.5 Flash / PixVerse V6 | — | — | — | — | PAUSED (no published per-second rate exists; not sold) |

Self-hosted and flat products (per job / per minute; Higgsfield
sells no equivalent — its free tier has effectively no video and
its shelf has no Wan 2.2 lane — so all are NOCOMP, and all sit at
or above their Rule 2 floors):

| Product | Cost | Sell | Markup | Floor | Verdict |
|---|---|---|---|---|---|
| Draft job (Wan 2.2 5B) | 50c | 199c | 3.98x | 186c | NOCOMP |
| Production job (Wan 2.2 A14B) | 15c | 99c | 6.60x | 78c | NOCOMP |
| Effects job | 15c | 99c | 6.60x | 78c | NOCOMP |
| Start image | 5c | 47c | 9.40x | 47c | NOCOMP |
| Clipping (balance, per source minute) | 0.3c | 5.0c | 16.67x | 0.9c | NOCOMP |
| Creator plan | — | $20/mo | — | — | Plan-level; see PARITY-PROGRAM.md (PAR vs Higgsfield Starter $19) |

No THIN lanes: every per-second lane's sell is at or above cost x
1.25 exactly (post-fix, in published figures as well as in effect),
and every self-hosted product is at or above its Rule 2 floor.

## Cannot-undercut list (genuine, with the math)

1. **Wan 3.0, 720p.** Higgsfield effective 8.8c/s (9 credits per
   5 s clip). Cheapest verified route is BeatAPI at 7.1c/s; with
   the 0.1c/s post cost the floor is 7.2 x 1.25 = 9.0c/s > 8.8c/s.
   Even the 480p cell fails on the scaled figure (floor 4.563 vs
   4.4), and no published 480p credit count exists to overturn the
   estimate. Named next step, NOT taken: CometAPI publishes Wan
   3.0 at $0.04/s flat, which would give cost 4.1c/s and a 5.125c/s
   floor, under Higgsfield at every resolution — but CometAPI's
   own model page conflicts with its pricing page ($0.05-0.20/s by
   resolution, per the sweep), so the rate is unverified. Routing
   the lane on a self-contradicted price risks selling below true
   cost; it needs a console/live verification first, the same
   status as the Kie "from" prices already flagged in the
   BUILD-LOG. Kie lists Wan 3.0 "from $0.04/s" with no per-mode
   split published — same status.
2. **Kling 4.0, 1080p.** Higgsfield effective 15.979c/s (16.25
   credits per 5 s). Our cheapest verified route is Kie at
   21.25c/s (its published no-input 1080p price); raw cost with
   post is 21.35c/s, which exceeds Higgsfield's effective price
   before any markup — at a zero markup we would still be 34%
   above them. The floor is 26.688c/s. No cheaper route exists in
   the sweep: BeatAPI's displaced 1080p rate is 28.0c/s, and fal
   carries no standard Kling 4.0 route (early-access Flash only,
   per the model availability audit). At 720p the same lane IS
   under (10.75 vs 11.063).

## Contested and thin items, stated plainly

- **Seedance 2.0, 1080p.** Two dated reads of Higgsfield's own
  pricing table conflict: ~60 credits per 5 s (CamClo, read of
  higgsfield.ai/pricing, crawled 2026-11-13) gives 59.0c/s and we
  are UNDER at 48.678; ~45 credits (viralaitrends, October 2026
  page data) gives 44.25c/s, against which our sell — which is
  exactly the x1.25 floor — would be OVER and unfixable without a
  cheaper route (BeatAPI is already the sweep's cheapest). No
  price change either way: the lane sits at its floor. The 720p
  verdict is solid on both reads (~22 credits; the Generate-button
  read of 36 credits per 8 s agrees).
- **Thin UNDERs** (under Higgsfield, but by less than 5%, and all
  pinned at the x1.25 floor so they cannot go lower): Veo 3.1 Fast
  (18.875 vs 19.667), Kling 3.0 720p sound-on (9.563 vs 9.833),
  Kling 4.0 720p (10.75 vs 11.063), Wan 3.0 1080p (17.125 vs
  17.208). On an Ultra-annual credit basis (~3.3c/credit)
  Higgsfield's effective prices drop ~33% and these four flip
  OVER; that basis was not used, per the established Plus-monthly
  comparison, but Jack should know the undercut claim for these
  lanes is plan-basis-sensitive.
- **Kling 3.0 basis note.** Our lane includes sound; Higgsfield
  charges 10 credits with sound vs 7.5 without. Compared sound-off
  (7.375c/s) we would be OVER at both resolutions and at the
  floor (cannot undercut). The sound-on comparison is the honest
  like-for-like for the product we sell.

## Deliberately NOT done (decision for Jack, not an audit fix)

Several lanes with a published Higgsfield figure are priced at the
x1.25 floor, far below Higgsfield (Veo 3.1: 15.125 vs 49.167;
Grok: 2.938 vs 22.125; Veo 3.1 Lite 720p: 3.875 vs 7.375). The
Amendment 3 formula raises a lane toward 0.97x Higgsfield the
moment a figure is added to HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP.
This audit did not add figures for those lanes: raising public
prices toward Higgsfield is a pricing-policy choice, not an
undercut or floor fix, and Jack's restated rule stresses being
cheaper. The comparisons are documented above if he wants any
lane repriced upward.

## Round 2 — 2026-11-16 (Jack's strengthened policy; commit ba5d0b9, deployed, verified live)

Jack's new policy, which overrides the round-1 floor where they
conflict: EVERY public price must be cheaper than Higgsfield's,
no matter what — OR at raw provider cost where undercutting
with a markup is impossible but raw cost is still below
Higgsfield's price. Never sell below raw provider cost. A lane
whose raw cost is at/above Higgsfield's price even at cost is a
residual exception: priced at cost and flagged, never sold at a
loss. This section supersedes the round-1 verdicts where they
conflict; the round-1 table above is the pre-round-2 record.

### Mechanism (pricing.py)

- `HIGGSFIELD_POLICY_CENTS_PER_SECOND`: the published
  Higgsfield figures for the compared lanes outside the
  Amendment 3 target tables (Kling 3.0 720p, Kling 4.0,
  Seedance 2.0 720p/1080p, Veo 3.1, Veo 3.1 Fast, Veo 3.1 Lite
  720p, Grok 720p). Comparison-and-cap only — unlike the target
  tables it can never raise a price.
- `frontier_lane` at-cost cap: where a lane's sell rate would
  sit at or above its Higgsfield figure, the sell becomes raw
  cost, and the payload gains `pricing_basis`:
  `at_cost_undercut` (cost < Higgsfield, now under) or
  `at_cost_residual` (cost >= Higgsfield, the flagged
  exception). Standard lanes report `standard`.
- `/api/pricing`'s frontier_lanes now covers the policy lanes
  as well as the target-table lanes.

### 1. CometAPI Wan 3.0 — VERIFIED, does not beat BeatAPI

Evidence is CometAPI's own authenticated models API
(GET api.cometapi.com/api/models, key from the
moonfactory-providers secret; probe app kept at
scratch/cometapi_price_probe_app.py), read 2026-11-16:

- The `wan3.0` catalog entry reports pricing.per_second = 0.05
  (the official Alibaba rate) with ratio 0.8, and its
  pricing_content charge table bills per resolution: 480P
  $0.05 / 720P $0.10 / 1080P $0.20 per generated second —
  "Final charge = applicable resolution price x successfully
  generated video duration." Unlike the Seedance 2.5 entry
  (below), the Wan 3.0 entry has NO "actual price" column
  applying the 0.8 ratio; the $0.04/s figure is pricing-page
  marketing (official $0.05 x 0.8).
- Even crediting the marketing reading ($0.04 / $0.08 / $0.16
  per second), CometAPI loses to BeatAPI at every tier
  (3.55 / 7.1 / 13.6). **Verdict: BeatAPI stays the Wan 3.0
  primary; no re-route.** The at-cost rule is applied instead
  (below). This closes round 1's named follow-up.

### 2. Seedance 2.5 cheapest-route hunt

Published or API-verified prices only, per second, no
reference video:

| Source | 480p | 720p | 1080p | Note |
|---|---|---|---|---|
| **CometAPI (API catalog)** | **$0.0824** | **$0.1848** | not offered | "Actual price / second" column, 80% of official |
| BeatAPI (incumbent) | $0.096 seed (~$0.0968 live) | $0.194 (Nov 15 FX raise) | **$0.481** | cheapest verified 1080p among wireable routes |
| BytePlus official (token-billed) | ~$0.1028 equiv. | ~$0.2312 equiv. | ~$0.569 equiv. | per-second figures are conversions of its token examples |
| Replicate | $0.1028 | $0.2312 | not offered | official-rate pass-through |
| Kie | $0.14 | $0.315 | $0.79 | with-ref rates are per billed second over input+output — worse per output second |
| WaveSpeed | $0.18 | $0.36 | $0.90 | |
| fal | ~$0.2205 | ~$0.473 | ~$1.164 | token-billed |
| OpenRouter | official rate + funding fees | | no 1080p | |
| APIMart (via Yir roundup) | $0.0961 | — | — | above CometAPI; not a keyed provider |
| reAPI (its own blog) | $0.1186 | $0.2668 | $0.4619 | below BeatAPI at 1080p but NOT wireable: no account/key exists; noted, not adopted |
| PiAPI / Eachlabs / Eden AI / AIMLAPI / Novita | no published per-second Seedance 2.5 rate beats the above (Eachlabs' I2V range starts at $0.135; Eden resells the BytePlus token rate) | | | |

**Change made: the 480p tier re-routed BeatAPI -> CometAPI**
(route override added; seed and live stored rate 9.6 ->
8.24c/s; the scheduled BeatAPI refresh now excludes 480p as
well as 720p so it cannot shadow the CometAPI rate; the
CometAPI submit sends the 480p size, 480x854, on that tier —
payload remains payload-unverified like the rest of that
lane). 720p stays CometAPI (cheapest verified), 1080p stays
BeatAPI. Sell rates unchanged (14.308 / 33.368 / 76.307, all
UNDER); the 480p margin rises 1.463x -> 1.716x.

**Seedance 2.5 Flash is a different model, not this lane.**
CometAPI's catalog lists seedance-2-5-flash as upcoming:true
and token-priced ($75/M tokens); Kie's own Seedance 2.5 page
links Flash as a separate faster/cheaper variant for 480p/720p
quick iteration. WaveSpeed's Flash listing is that variant.
The platform already carries it as its own price-paused lane;
no action.

### 3. Cost-basis corrections (Kie "from" prices)

Re-checking the thin lanes against Kie's own published
matrices found two seeds taken from the wrong cell of Kie's
pricing tables. Both lanes' submits were verified in code
first (Kie submit sends sound=true for the Kling family;
Kling 4.0's standard shapes are text/keyframe):

- **Kling 3.0 (kie.ai/kling-3-0):** 720p $0.0755/s is the
  NO-audio rate; with audio it is $0.1135/s, and 1080p with
  audio is $0.151/s. The lane ships sound on, so the seed
  moves 7.55 flat -> **11.35 / 15.1**. The round-1 "thin
  UNDER" at 720p rested on the understated cost: at the true
  cost the lane was in fact selling below cost (9.563 sell vs
  11.45 true cost). BeatAPI's displaced sound-included rates
  (11.0 / 13.0) are marginally cheaper at 720p and cheaper at
  1080p; Kie stays primary (the follow-up pass chose it for
  reference mode; the 720p lane is a residual exception on
  either route — see below).
- **Kling 4.0 (kie.ai/kling-4-0):** the 720p seed $0.085/s is
  the WITH-reference-video rate, billed per second over
  input + output seconds. No-video-input jobs bill 28.5
  credits = **$0.1425/s** at 720p (1080p $0.2125 confirmed).
  Seed 720p 8.5 -> **14.25**. Round 1's "thin UNDER" at 720p
  likewise rested on the wrong cell.
- Live stored rates were updated to the corrected seeds the
  same hour (scratch/round2_rates_app.py); the seeds and the
  stored table now agree.

**Kling 4.0 1080p route hunt (the known case):** no wireable
pay-as-you-go route beats Higgsfield's 15.979c/s, or even
Kie's $0.2125/s. Checked: Kling official API ($0.231-0.2355/s
at 1080p; its one-time first-buy trial-pack math, ~$0.212/s,
is not a standing rate and Kling direct is not a wired
provider); fal / Replicate / WaveSpeed / EvoLink at Kling list
($0.235/s); AIMLAPI (from $0.157/s is the 720p figure);
CometAPI (no Kling 4.0 at all). Two non-wireable figures were
noted and not adopted: Yir ~$0.1872/s (channel-routed, price
not firm, no key) and useapi.net ~$0.152/s (subscription
account proxy, not a PAYG API). **Kie stays; the tier is a
residual exception at cost.**

**Kling 3.0 Omni — flagged, not re-based.** The submit sends
sound=true for Omni too, and third-party reads of Kie's Omni
matrix (a maintained Kie MCP wrapper's live-probed changelog;
a provider roundup listing Kie at 20 credits/s) put its
sound-on 720p rate at roughly $0.09-0.10/s against the flat
7.55 seed carried from the Kling 3.0 no-audio from-price. The
lane has no Higgsfield figure, and those reads are estimated
third-party figures rather than Kie's own published page, so
per this audit's own standard the seed is left as-is and the
lane stays flagged for a Kie console confirmation — the same
status it already carried.

### 4. Final lane table (live, post-round-2; cents per second)

All lanes not listed here are unchanged from the round-1 table
(NOCOMP lanes still price at the x1.25 floor; self-hosted
products unchanged).

| Lane | Cost | Sell | Markup | Higgsfield | Verdict |
|---|---|---|---|---|---|
| Wan 3.0 480p | 3.650 | 3.650 | at cost | 4.400 (scaled) | UNDER — at-cost (was OVER) |
| Wan 3.0 720p | 7.200 | 7.200 | at cost | 8.800 | UNDER — at-cost (was OVER) |
| Wan 3.0 1080p | 13.700 | 17.125 | 1.250x | 17.208 | UNDER (thin, at floor) |
| Wan 3.0 Prime 480p/720p/1080p | 5.540 | 14.357 | 2.592x | 14.800 | UNDER |
| Seedance 2.5 480p | 8.340 | 14.308 | 1.716x | 14.750 | UNDER (route: CometAPI, new) |
| Seedance 2.5 720p | 18.580 | 33.368 | 1.796x | 34.400 | UNDER |
| Seedance 2.5 1080p | 48.465 live (48.200 seed) | 76.307 | 1.574x | 78.666 | UNDER |
| Veo 3.1 720p/1080p | 12.100 | 15.125 | 1.250x | 49.167 | UNDER |
| Veo 3.1 Fast 720p/1080p | 15.100 | 18.875 | 1.250x | 19.667 | UNDER (thin) |
| Kling 3.0 720p | 11.450 | 11.450 | at cost | 9.833 | **RESIDUAL — at cost, above Higgsfield** |
| Kling 3.0 1080p | 15.200 | 19.000 | 1.250x | — (sound-on count unpublished) | NOCOMP (sell was below true cost; corrected) |
| Kling 4.0 720p | 14.350 | 14.350 | at cost | 11.063 | **RESIDUAL — at cost, above Higgsfield** |
| Kling 4.0 1080p | 21.350 | 21.350 | at cost | 15.979 | **RESIDUAL — at cost, above Higgsfield** (was OVER at 26.688) |
| Seedance 2.0 720p | 15.693 | 19.617 | 1.250x | 21.633 | UNDER |
| Seedance 2.0 1080p | 38.942 | 48.678 | 1.250x | 59.000 (CamClo 2026-11-13 read; contested) | UNDER (contested) |
| Veo 3.1 Lite 720p | 3.100 | 3.875 | 1.250x | 7.375 | UNDER |
| Grok Imagine 720p | 2.350 | 2.938 | 1.250x | 22.125 | UNDER |

### Answer to "which ones are we still charging more for than Higgsfield?"

**None above raw cost. Exactly three lanes remain above
Higgsfield's effective price, all priced AT raw cost with zero
markup** (the residual exceptions the policy names):

1. **Kling 3.0, 720p** — sell = cost 11.45c/s vs Higgsfield
   9.833c/s. Cheapest verified sound-on route (Kie $0.1135/s;
   Kling official $0.126/s; BeatAPI $0.11/s would still leave
   cost 11.1 > 9.833).
2. **Kling 4.0, 720p** — sell = cost 14.35c/s vs Higgsfield
   11.063c/s. Cheapest verified route is Kie no-input
   $0.1425/s.
3. **Kling 4.0, 1080p** — sell = cost 21.35c/s vs Higgsfield
   15.979c/s. Cheapest verified route is Kie no-input
   $0.2125/s (hunt detail above).

Every other compared lane is under Higgsfield, including the
two round-1 OVER lanes (Wan 3.0 480p/720p), which now sell at
raw cost under the new policy. No lane sells below raw cost.
The three residuals exist because Higgsfield's bundled-credit
effective price for the Kling family sits below every
provider's per-second API rate; they clear only if a provider
cuts Kling rates or Higgsfield raises credit prices.

### Contested item, per the round-2 brief

Seedance 2.0 1080p uses the more recent CamClo read
(2026-11-13, ~60 credits -> 59.0c/s): UNDER at 48.678, no
change. The October ~45-credit read (44.25c/s) is noted as
conflicting; against that figure the floor sell would sit
above Higgsfield, and under the new policy the lane would
become an at-cost residual at 38.942 — still above 44.25. The
governing read is the newer one; no price change either way in
this round.

## Sources

- Live platform: GET /api/pricing and /api/config,
  magrify--moonfactory-api.us-east.modal.run, 2026-11-16, before
  and after the fix deploy.
- Round 2: CometAPI models API (authenticated catalog read,
  2026-11-16, via scratch/cometapi_price_probe_app.py);
  kie.ai/kling-3-0 and kie.ai/kling-4-0 (Kie's own published
  matrices); kie.ai/seedance-2-5; WaveSpeedAI Seedance 2.5
  model pages; Replicate / BytePlus / fal figures via dated
  provider comparisons (8frame, kinovi.ai, BeatAPI's 9-provider
  comparison, reapi.ai, yir.ai, aivideocosts.fyi, useapi.net,
  argolink.io — all read 2026-11-16); live /api/pricing
  re-probed after the ba5d0b9 deploy.
- platform/pricing.py (rate seeds, Amendment 3 mechanics).
- ~/workspace/sinclip/provider-sweep-2026-11-16.md (cheapest-route
  verification for every lane; CometAPI Wan 3.0 conflict flag).
- ~/workspace/sinclip/HIGGSFIELD-PARITY.md (Higgsfield plan table
  and credit samples from its own 2026-11-06 pricing guide).
- Higgsfield credit counts as published and read 2026-11-03 to
  2026-11-09: higgsfield.ai/pricing comparison table and Generate
  button (CamClo pricing read, crawled 2026-11-13; Aliteq credits
  explainer and audio-generator comparison, reads dated
  2026-11-03 to 2026-11-09; viralaitrends review, October 2026
  page data).

## Round 3 — finish run, 2026-11-16: Kie catalog verification

Method: Kie's own authenticated catalog API (GET
https://api.kie.ai/api/v1/models, Bearer key from the platform
secret store; 254 models). Prices live in each entry's
pricingDesc; 1 Kie credit = $0.005. The flagged "from" prices
were checked against the catalog, and Kling 3.0 / 4.0 were
independently confirmed on Kie's public model pages.

Confirmed as seeded (no change): Kling 3.0 (720p $0.0755
no-audio / $0.1135 with audio; 1080p $0.101 / $0.151), Kling 4.0
(720p $0.1425 no-input, 1080p $0.2125; with-reference $0.085 /
$0.1275), Grok Imagine (480p $0.012, 720p $0.0225), Hailuo 2.3
Standard (768P 6s $0.15/clip = 2.5c/s; 10s clips $0.26 =
2.6c/s, immaterial, noted).

CORRECTED (seed + live stored table both updated; all four are
NOCOMP lanes with no Higgsfield figure, so the corrections
restore honest cost floors; customer sell = (rate + 0.1 post)
x 1.25, ceiled to 3 decimals):

| Lane (price in $/s) | Tier | Provider | Owner | Customer | Higgsfield |
|---|---|---|---|---|---|
| Kling 3.0 Omni | 720p | 0.101 | 0.102 | 0.1275 | none |
| Kling 3.0 Omni | 1080p | 0.126 | 0.127 | 0.15875 | none |
| Seedance 2.0 Fast | 480p | 0.059 | 0.060 | 0.075 | none |
| Seedance 2.0 Fast | 720p | 0.124 | 0.125 | 0.15625 | none |
| Seedance 2.0 Mini | 480p | 0.019 | 0.020 | 0.025 | none |
| Seedance 2.0 Mini | 720p | 0.041 | 0.042 | 0.0525 | none |
| Gemini Omni 1.2 Flash | 720p | 0.045 | 0.046 | 0.0575 | none |
| Gemini Omni 1.2 Flash | 1080p | 0.050 | 0.051 | 0.06375 | none |

Why each was wrong: Kling 3.0 Omni's old flat 7.55c seed was
the no-audio 720p rate; the platform submits sound=true, and
the catalog's with-audio rates are 10.1c (720p) / 12.6c
(1080p) — the lane was priced BELOW true cost. Seedance 2.0
Fast/Mini old seeds were the with-video-input rates; the wired
text/image modes carry no video input, so the no-video rates
apply (Fast 5.9c/12.4c, Mini 1.9c/4.1c). Note: Seedance 2.0
Fast in reference mode (video input present) bills the
with-video rate over input+output seconds (3.4c/7.5c basis);
not separately seeded — flagged here. Gemini Omni 1.2 Flash's
old 2.0c seed was the catalog's 360P rate; 720P is 4.5c and
1080P is 5.0c.

Cross-checks that changed nothing: Seedance 2.5 via Kie
(no-video $0.140/$0.315/$0.790 per s) confirms the BeatAPI /
CometAPI primaries are cheaper; MiniMax H3 via Kie (768P
$0.04/s) confirms the BeatAPI primary. Live /api/pricing
re-verified after the stored-table update: all four lanes
serve the corrected rates and sells.
