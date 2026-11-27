# Route audit and Modal cost pass, 2026-11-26

Real-spend reductions only. Customer shelf prices did not
move on any lane (frozen through SHELF_PROVIDER_RATES where
the shelf was cost-built; check_post_cost_cut pins every
sell rate and debit). Owner price follows the new true
cost through the standard machinery.

## 1. Off Modal

- word_check + transcribe (the last live calls on the
  sinclip-audio Modal app) moved to the Kaggle audio
  worker as wordcheck / transcribe stages on the existing
  child-job seam. The Modal app is now fully dormant; its
  functions bill $0 uncalled and the file stays as the
  spare. Measured volume (jobs Dict, last 30 days): 14
  word checks, 2 captioned jobs, 20 spoken of 131 jobs.
  Saving at today's volume: about $0.02/mo of Modal CPU
  (a word check cost ~120 core-seconds at $0.047/core-hour
  = ~$0.0016; transcribe ~$0.0005). At scale: ~$1.60 per
  1,000 spoken jobs. The structural win is that no
  per-job Modal worker remains in the delivery path.
- LTX-2.5 Uncensored (ltx_unc, the sinclip-comfyui L40S
  app) STAYS on Modal: Jack's explicit 2026-11-20 ruling
  ("this model does not go on Kaggle"). The lane is dark
  and near-zero traffic, so near-zero spend.
- FLUX.2 Klein fallback: already on Kaggle since
  2026-11-20 (primary is Cloudflare's free tier). Nothing
  to move.
- Sweeps: refresh_provider_rates (6h) lost its last
  required page read with the Wan 3.0 move; both sweeps
  together cost under ~$0.10/mo. Trimming the refresh to
  12h would save ~$0.04/mo while delaying VidLab price
  tracking for 50+ lanes: assessed a bad trade, left as
  is. legal_hold_destruction_sweep (daily, seconds) is
  already lean. The web app stays on Modal (scales to
  zero), per the brief.

## 2. Route swaps wired (before -> after, cents/second)

| lane | from | to | 480p | 720p | 1080p | 4K |
|---|---|---|---|---|---|---|
| wan3 | BeatAPI | FrameHush | 3.55 -> 3.0 (-15%) | 7.1 -> 6.0 (-15%) | 13.6 -> 9.7 (-29%) | |
| minimax_h3 | BeatAPI | FrameHush | 2.0 -> 1.5 (-25%) | 3.0 -> 1.5 (-50%) | 4.7 -> 1.5 (-68%) | |
| ltx25 | fal | SpicyAPI | | 12.0 -> 9.0 (-25%) | 17.0 -> 13.0 (-24%) | 34.0 -> 30.0 (-12%) |
| seedance2_fast | VidLab | SpicyAPI | 5.0 -> 4.5 (-10%) | 11.0 -> 9.68 (-12%) | | |
| vidu_q4 | VidLab | Kie | 6.0 -> 3.6 (-40%) | 12.0 -> 7.6 (-37%) | 13.0 -> 9.6 (-26%) | |

Mean tier saving across the 13 priced tiers: ~27%.
ltx25 additionally leaves fal's $0-balance account, where
the lane could not run at all. All five are
payload-unverified like every reroute before them: the
first live job on each is the envelope proof, failures
refund clean. FrameHush prices are its estimate endpoint
read on the platform's own account (Wan 3.0 $0.15/$0.30/
$0.485 per 5 s; MiniMax H3 a flat $0.075 per 5 s at every
tier, carried flat). Stored live rate cells were reset
to the new seeds (fix_route_audit_stored_rates.py) and
the deploy was live-verified on /api/pricing and
/api/config: new rates in place, sell rates unchanged,
all five lanes available.

## 3. Listed, not wired

- kling3 (Kie 11.35/15.1/37.75 with audio): SpicyAPI is
  ~11% cheaper (10.08/13.44/33.6) but publishes no
  reference-to-video endpoint for Kling 3.0, and the
  lane sells reference mode. Wiring would break a sold
  mode.
- vidu_q3_pro (VidLab 7.0/15.0/16.0): SpicyAPI is far
  cheaper (5.4/6.5/7.8) but publishes only an
  image-to-video endpoint; the lane sells text and
  reference modes.
- seedance15pro (VidLab 3.0/6.0/14.0): SpicyAPI cheaper
  (with-audio 2.33/5.18/11.66; no-audio 1.17/2.59/5.83)
  but publishes no reference-to-video endpoint; the
  lane sells reference mode.
- seedance2_mini (Kie 1.9/4.1) and seedance25_flash
  (VidLab 8.5/18.5): BeatAPI is cheaper only for jobs
  without video input (1.7/3.6 and 6.3/13.55) and MORE
  expensive with it (2.6/5.5 and 9.55/20.55); both lanes
  take reference videos, so the blend is not a clear
  win. FrameHush's doubao-seedance-2-5-flash-261115
  estimate (4.21/6.53) is cheaper still but the dated
  variant's identity as the same model is unverified.
- seedance2 (BeatAPI): FrameHush's seedance_2.0 is
  under provider maintenance (estimate returns 503);
  VidLab is mixed. No move.
- kling3_omni, omni11, wan26_flash, pixverse_v6_max:
  partial or single-digit wins only (per-tier or 4-14%),
  not clear wins.
- wan_motion, grok15_lite, luma_ray32, pika22,
  ltx25_fast: priced only on fal (unfunded) with no
  same-model alternative in any live catalog (the
  "motion control" entries elsewhere are Kling's, a
  different model). They stay listed and dark.
- Image lanes: clear wins exist (nano_banana via
  BeatAPI 1.5c vs VidLab 3.0c; seedream55 via BeatAPI
  1.4/1.4/2.4c vs 1.6/1.6/2.8c; mai_image26 via SpicyAPI
  flat 3.35c vs 4.0/9.0c; mai_image26_flash via SpicyAPI
  flat 1.65c vs 2.0/5.0c; flux3_image via SpicyAPI
  4.08/5.95/8.5/51.6c vs 5.0/7.0/10.0/61.0c) but image
  customer prices are computed as raw x 1.25 with no
  shelf-freeze mechanism, so wiring any of them would
  cut customer prices, which the owner's freeze rule
  reserves to his say-so. Wiring them needs either his
  ruling to let image prices follow cost down, or an
  image shelf freeze like SHELF_PROVIDER_RATES.

## Commits

be81b08 wan3 + minimax_h3 to FrameHush; 96a1eb2 rate
refresh follow-up; 4bcda57 ltx25 to SpicyAPI; 74c055f
seedance2_fast to SpicyAPI; 194cef0 vidu_q4 to Kie;
e98dac1 word check move (platform routing landed in
ced42f0, shared working tree); 3029dba audit evidence.
