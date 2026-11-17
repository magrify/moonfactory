# Moonfactory funded verification packet, 2026-11-17

Ready to run the moment funding lands. Nothing in this packet has
been executed. MODAL SPEND: NONE AUTHORIZED by this document: each
test below also needs Jack's named approval for the platform-side
dispatch and post-processing run, per the spend gate, in addition
to the provider top-up.

## What this pass proves

Every priced API lane is payload-unverified: the request and
response shapes were built from provider docs and catalogs, never
from a live generation. One successful generation per lane proves
the payload contract, the polling path, the delivered file shape,
and the charged amount. That is the whole pass. Quality comparison
against the Higgsfield bar happens on the same outputs at no extra
provider cost.

## Standard test (same for every lane)

- Start image: the standard test image already used by the proof
  renders (flat illustration balloon scene), plus the prompt "a red
  hot air balloon rising over green hills, bright daylight" (the
  landing page example prompt), so outputs are comparable across
  lanes and against the house lane's proven renders.
- Shape: lowest resolution the lane sells, shortest duration the
  lane allows, sound on where the lane has a native audio mode,
  spoken line included only on speech-native lanes.
- The four SpicyAPI uncensored lanes accept image input only, so
  the standard test already fits them.

## Evidence captured per lane (pass/fail)

1. Sanitized request and response envelopes, provider task id.
2. The provider's own charged amount (credits or cost field), read
   back from the provider, never estimated.
3. Delivered file ffprobe: video codec, width, height, fps,
   duration, audio stream present or absent, integrated loudness.
4. Quote vs settled debit on the test account: must match to the
   cent, holds released on any failure.
5. Visual check: motion present, prompt followed, no corruption;
   one line against the Higgsfield bar (worse, comparable, better)
   with the file kept.
6. HotAPI only: the charged rate for MiniMax H3 uncensored settles
   whether the struck-through promo prices ($0.02/s at 480p) or the
   list prices ($0.08/s) are real. This lane does not light until
   that read exists.

A lane passes when 1 to 4 are clean and the file plays. A payload
mismatch is fixed in code, redeployed, and retested once on the
same funding; a second failure parks the lane with the exact error
recorded.

## Lane table (priority order)

Priority is by what the funding unlocks: providers already holding
a balance first, then the cheapest top-up covering the most lanes,
then the rest, uncensored suppliers last (their top-up minimums are
not yet known).

| # | Model (id) | Provider | Test shape | Provider cost per test |
|---|---|---|---|---|
| 1 | HappyHorse 1.1 (happyhorse) | OpenRouter | 720p, 3s | $0.296 |
| 2 | Grok Imagine Video 1.5 (grok) | Kie | 480p, 3s | $0.036 |
| 3 | Seedance 2.0 Mini (seedance2_mini) | Kie | 480p, 4s | $0.076 |
| 4 | Gemini Omni 1.2 Flash (omni12) | Kie | 720p, 3s | $0.135 |
| 5 | Hailuo 2.3 (hailuo23) | Kie | 720p, 6s | $0.150 |
| 6 | Seedance 2.0 Fast (seedance2_fast) | Kie | 480p, 4s | $0.236 |
| 7 | Kling 3.0 Omni (kling3_omni) | Kie | 720p, 3s | $0.303 |
| 8 | Kling 3.0 (kling3) | Kie | 720p, 3s | $0.340 |
| 9 | Kling 4.0 (kling4) | Kie | 720p, 3s | $0.427 |
| 10 | LTX-2.5 Fast (ltx25_fast) | fal | 720p, 6s | $0.540 |
| 11 | Veo 3.1 Fast (veo3_fast) | fal | 720p, 4s | $0.600 |
| 12 | Luma Ray 3.2 (luma_ray32) | fal | 480p, 5s | $0.500 |
| 13 | Pika 2.2 (pika22) | fal | 720p, 5s | $0.200 |
| 14 | LTX-2.5 (ltx25) | fal | 720p, 6s | $0.720 |
| 15 | MiniMax H3 (minimax_h3) | BeatAPI | 480p, 4s | $0.080 (see clip note) |
| 16 | Wan 3.0 (wan3) | BeatAPI | 480p, 3s | $0.106 (see clip note) |
| 17 | Gemini Omni 1.1 Flash (omni11) | BeatAPI | 720p, 3s | $0.180 (see clip note) |
| 18 | Seedance 2.0 (seedance2) | BeatAPI | 480p, 4s | $0.309 (see clip note) |
| 19 | Wan 3.0 Prime (wan3_prime) | CometAPI | 480p, 3s | $0.163 |
| 20 | Seedance 2.5 (seedance) | CometAPI at 480p/720p, BeatAPI at 1080p | 480p, 4s | $0.330 |
| 21 | Veo 3.1 (veo3) | PiAPI | 720p, 4s | $0.480 |
| 22 | Veo 3.1 Lite (veo3_lite) | Eachlabs | 720p, 4s | $0.120 |
| 23 | Vidu Q3 (vidu_q3) | Novita | 480p, 3s | $0.054 |
| 24 | Runway Gen-4.5 (runway_gen45) | Runway | 720p, 3s | $0.360 |
| 25 | Wan 2.2, Uncensored (wan22_unc) | SpicyAPI | 480p, 5s | $0.095 |
| 26 | Wan 3.0, Uncensored (wan3_unc) | SpicyAPI | 480p, 3s | $0.171 |
| 27 | Vidu Q3, Uncensored (vidu_q3_unc) | SpicyAPI | 480p, 3s | $0.200 |
| 28 | Seedance 2.5, Uncensored (seedance25_unc) | SpicyAPI | 480p, 4s | $0.864 |
| 29 | MiniMax H3, Uncensored (minimax_h3_unc) | HotAPI | 480p, 5s | $0.100 at promo, $0.400 at list |
| 30 | Wan 2.6, Uncensored (wan26_unc) | Wiro | 720p, 5s | $0.500 |
| 31 | Wan 2.7, Uncensored (wan27_unc) | Wiro | 720p, 5s | $0.650 |
| 32 | Wan 3.0 Pro, Uncensored (wan3pro_unc) | Wiro | 1080p, 5s | $0.900 |

Costs are the seeded provider rates (pricing.py, verified
2026-11-16/17 from each provider's own pages or catalog) times the
test duration. Total for one test on all 32 priced lanes: $10.22
at the promo HotAPI rate.

Clip note (BeatAPI lanes 15 to 18, plus Seedance 2.5 at 1080p if
tested there): BeatAPI publishes per 8-second clip prices, so a
short test may be charged as a full clip (Wan 3.0 $0.284, MiniMax
H3 $0.16, Omni 1.1 $0.48, Seedance 2.0 $0.618 at the carried
with-input rate). Worst case across the BeatAPI tests adds about
$0.90, for a worst-case total near $11.10. Settlement reads the
provider's reported charge either way.

## Top-ups needed (verified minimums, Nov 16/17)

| Provider | Account | Top-up | Covers lanes | Notes |
|---|---|---|---|---|
| OpenRouter | support@magrify.com | $0 | 1 | Balance $8.32 already there (Nov 16) |
| Kie | Google magrifywebsites@gmail.com, kie.ai | $10 | 2 to 9 | Credits never expire |
| fal | GitHub @magrify, fal.ai/dashboard | $10 (real minimum $1) | 10 to 14 | $10 covers tests plus retests |
| BeatAPI | Google magrifywebsites@gmail.com, beatapi.io | about $25 (as previously reported; confirm the minimum at checkout) | 15 to 18, 20 at 1080p | Prepaid, may be non-refundable |
| CometAPI | support@magrify.com, cometapi.com | $10 | 19, 20 | Minimum $10 |
| PiAPI | GitHub @magrify | $5 | 21 | Minimum $5; credits expire in 6 months |
| Eachlabs | GitHub @magrify | $10 | 22 | Presets start at $10 |
| Novita | support@magrify.com | $10 | 23 | Default top-up $10 |
| Runway | support@magrify.com | $10 | 24 | Minimum $10, autobilling off |
| SpicyAPI | Google magrifywebsites@gmail.com, spicyapi.ai/console | UNKNOWN minimum | 25 to 28 | Pay as you go, no subscription; read the minimum at checkout |
| HotAPI | Google magrifywebsites@gmail.com, hotapi.ai | UNKNOWN minimum | 29 | 1000 credits = $1 on their pricing page |
| Wiro | Google magrifywebsites@gmail.com, wiro.ai/panel | UNKNOWN minimum | 30 to 32 | Pay as you go |

Total known top-ups: $90 plus the three uncensored suppliers at
their checkout minimums. Funding can land provider by provider;
the pass runs per provider as its funding arrives, in the priority
order above.

## Not testable at any price yet

Vidu Q2, Vidu Q4, Seedance 2.5 Flash, and PixVerse V6 stay
price-paused: no published per-second rate exists for them, and the
platform does not invent rates. They join this packet the day a
published rate is recorded in pricing.py. WaveSpeed holds a $1
starting balance, but all three of its lanes are in this group.

## Sequence once funding exists

1. Jack funds one or more providers (table above) and names the
   Modal-side approval for the test runs.
2. Reuse an existing test fixture account with a paid balance (the
   72 fixtures stay; never create new test accounts). Confirm the
   hard cost ceiling admits the test cost.
3. Run lanes in priority order for the funded providers, one at a
   time, capturing the evidence list per lane.
4. Fix any payload mismatch in code, redeploy (deploy only), and
   retest that lane once.
5. For each passing lane, run one 720p test at the standard
   duration to check the main selling tier (provider cost: the
   lane's 720p rate times duration, from /api/pricing at run time).
6. Record charged vs seeded rate per lane; correct pricing.py where
   the provider's own charge disagrees, and note it in BUILD-LOG.
7. Record submit-to-delivered wall time per lane. This settles the
   speed row of the parity matrix, which is unverified until then.
8. Score each standard-test output against the house lane's proven
   renders on motion, prompt adherence, and artifacts. This settles
   the output-quality row of the parity matrix.
9. Spot-test the 4K tiers wired 2026-11-17: LTX-2.5 4K at 6 s
   ($2.04 at the seeded rate), Wan 3.0 Pro Uncensored 4K at 5 s
   ($1.15) and 2K at 5 s ($1.00). The aspect shapes wired the same
   day ride along free: run lane 2 (Grok, Kie) once extra at 16:9
   ($0.036) to prove the aspect path on a second shape.
10. Update the finish state: lanes move from payload-unverified to
    verified only with their evidence attached.
