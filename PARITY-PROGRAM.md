# Moonfactory vs Higgsfield: the parity program

Written 2026-11-16. Jack's readiness bar, set the same day: Moonfactory
is "ready" only when it is **as good or better than Higgsfield's video
generation in every way, and looks better**. Platform-live is not
"ready" by that bar. This document is the program for closing the gap:
an honest scorecard, the supplier facts the strategy rests on, and the
build passes in order, each with its spend and its proof.

Companion docs: `HIGGSFIELD-PARITY.md` (the capability map),
`vendor/FREEFIELD-INVENTORY.md` (what the open-source port already
harvested), `API-LANES-DESIGN.md` (the Wan 3.0 lane design and the
picker/pricing rules). Moonfactory state below is as of 2026-11-16
evening: Wan 3.0 / Prime lanes wired but dark (provider balance $0),
gallery live, preset library ported with its form wiring in progress,
payments live.

## 1. Dimension scorecard

Verdicts are for what a customer can actually do today, not what is
wired behind a zero balance or sitting in a library file.

| # | Dimension | Verdict | Evidence |
|---|---|---|---|
| 1 | Generation quality | **FAR BEHIND** | Higgsfield's best output is Seedance 2.5 / Veo 3.1 / Kling 4.0 output. Moonfactory's only runnable lane today is self-hosted Wan 2.2, a full generation behind on motion coherence, hands, faces, and prompt adherence. A user comparing clips notices in one viewing. The Wan 3.0 lanes that would narrow this are wired but refuse at dispatch (provider balance $0). |
| 2 | Generation speed | **BEHIND** | Higgsfield: ~30 s to ~5 min per generation. Moonfactory Production: ~3.6 min measured render for one 5 s segment (distilled recipe), inside their band for a single short clip. Draft: 6-13 min, outside it. A 30 s piece is six serial segments (~25 min end to end) against their single-pass 30 s models. |
| 3 | Model shelf breadth | **FAR BEHIND** | Higgsfield: 35+ models selectable today. Moonfactory: 3 names in the picker (Wan 2.2, Wan 3.0, Wan 3.0 Prime), and 2 of the 3 cannot run a job today. |
| 4 | Max length & resolutions | **BEHIND** | Higgsfield: single-pass clips to 30 s; 480p / 720p / 1080p / 4K; 16:9, 9:16, 1:1 and more. Moonfactory: one ~5 s segment per job on the house lanes; delivery fixed at 1080x1920, 9:16 only. The wired 3.0 lanes do 3-30 s and 480p-1080p but are dark. |
| 5 | Camera control | **BEHIND** | Higgsfield: 50+ named presets plus Cinema Studio, live in the product. Moonfactory: a 42-preset library is ported (33 prompt recipes, dolly-in render-verified) but the picker wiring into the form is still in progress, and true camera conditioning (Fun Camera) waits on a 30.6 GB model download. Today a user cannot pick a camera move in the product at all. |
| 6 | Character consistency | **FAR BEHIND** | Higgsfield: Soul ID trains an identity from 20+ photos (25 credits) and reuses it across clips and models. Moonfactory: nothing. Reusing a similar start image is the only lever, and faces drift between clips. |
| 7 | References / Elements | **BEHIND** | Higgsfield: saved characters, products, and locations as reusable Elements; top models take up to 15 combined references. Moonfactory: one start image per job in the form; the Wan 3.0 lane's reference mode exists at the API layer only; nothing is saved between jobs. |
| 8 | Native audio | **BEHIND** (one sub-point ahead) | Higgsfield's frontier models generate dialogue, SFX, and music in the same pass. Moonfactory has no in-model audio today. The sub-point: every Moonfactory clip ships with sound (a generated music bed mixed to a published ~-14 LUFS standard, free tier included), while Higgsfield's default output is silent unless the user picks and pays for an audio model. |
| 9 | Lip sync / voice | **FAR BEHIND** | Higgsfield: Speak 2.0 lip sync, voice cloning, dubbing, multi-speaker dialogue. Moonfactory: none of it exists in the product. A user with a talking-head use case has no path at all. |
| 10 | Video-to-video restyle | **FAR BEHIND** | Higgsfield: Genjutsu recasts characters, swaps objects and locations, and restyles finished clips today. Moonfactory: no video-input path exists in the product. |
| 11 | Editing / extend | **BEHIND** | Higgsfield: extend endpoints, storyboard editing (Popcorn), inpaint, reframe, background removal in-app. Moonfactory: segment chaining is proven in the worker (the embryo of extend) but is not in the form; no reframe, inpaint, or background-removal features. |
| 12 | Start-image generation | **BEHIND** | Higgsfield: Soul / Nano Banana / FLUX-class image models one click from the video form ("describe it, we make the first frame"). Moonfactory: bring your own start image or go without. The port candidate is identified (z-image, 12.2 GB) but not downloaded. |
| 13 | Gallery / community | **PAR** | Both have a public feed. Moonfactory's is public by default (paid plans can opt out), shows handles only, and has report + operator removal live; Higgsfield's is opt-in (free work is private by default). Higgsfield is deeper: open any project to see its prompts and settings, share presets and characters, run contests. Moonfactory has none of that depth yet. |
| 14 | Studio look & feel | **BEHIND** | Higgsfield is a polished creative suite: one composer, a model catalog, Cinema Studio. Moonfactory's studio is functional plain pages. The design patterns worth adopting (from the open-higgsfield repo: catalog-as-data, one composer, schema-generated setting pills, gallery-as-history) are written down in the preset library's INDEX.md and not yet applied. "Looks better" is half of Jack's bar, so this is a named pass below, not polish-when-idle. |
| 15 | API access | **FAR BEHIND** | Higgsfield: public REST API with its own balance, Python and Node SDKs, an MCP server, a CLI, editor plugins. Moonfactory: the only API is the internal worker API, key-gated to the platform itself. Customers get no API, SDK, MCP, or CLI. |
| 16 | Value at the $20 tier | **PAR, with a warning** | Moonfactory Creator ($20) includes ~214 s of house production as a finished 1080x1920 master with sound, plus a 637-cent generation budget usable on the 3.0 lanes. Higgsfield Starter ($19, 270 credits) buys roughly 150 s of Wan 3.0 at 720p, or only ~7 Seedance 2.5 clips, and credits expire monthly. On house lanes Moonfactory gives more finished seconds; on frontier lanes its 3x floor prices clips well above Higgsfield's effective prices (section 2, structural warning). |

Score today: 1 PAR-with-warning, 1 PAR, 6 BEHIND, 5 FAR BEHIND, and
the rest split. Nothing on this list is AHEAD outright except the
sound-on-everything sub-point in row 8 and the pricing-transparency
mechanics (exact dollar quote and hard cap before dispatch, which
Higgsfield's credit system does not offer). That is the honest
distance to the bar.

## 2. Supplier facts (primary sources, read 2026-11-16)

Higgsfield's top shelf is metered third-party API product. The
strategic question was whether Moonfactory can offer the same named
models through fal.ai, the provider it already has an account and a
wired integration with. Answer first: **yes, for every named model
except Higgsfield's own.** One key, the same queue/webhook shape the
Wan 3.0 lanes already use; each new model is a picker entry, a rate
table entry, and an endpoint mapping inside the existing quote, hold,
settle, and cost-ceiling machinery. No new provider, no new key, no
rules change.

### What fal carries, at fal's own published prices

| Model | On fal (API) | fal price (per second unless noted) | Notes |
|---|---|---|---|
| Veo 3.1 Standard | Yes | $0.20 silent / **$0.40 with audio** (720p, 1080p); 4K $0.40 / $0.60 | Text, image, first+last frame, reference modes, plus an extend endpoint; up to 8 s per generation. Fast tier: $0.10 / $0.15 (4K $0.30 / $0.35). Lite: $0.05 with audio at 720p. |
| Kling 3.0 (V3 + O3, Standard / Pro) | Yes | Standard (720p) $0.084 silent / **$0.126 with audio**; Pro (1080p) $0.112 / **$0.168**; voice control $0.154 / $0.196 | Multi-shot storyboarding, element referencing, start+end frames, native audio in five languages. See the rate caveat below. |
| Kling 4.0 | Listed, **API not open yet** | $0.157 (720p) / $0.235 (1080p) | 3-30 s, up to 15 references. fal's own comparison article says API access is "coming soon"; today it is playground and fal Agent only. Not wireable yet (section 4). |
| Seedance 2.5 | Yes (US-hosted endpoints listed) | Token billing: $0.0214 per 1K tokens (480p, 720p), ~$0.0234 at 1080p; fal's own conversion is ~$0.473/s at 720p, so **~$2.37 per 5 s 720p clip**; jobs with a reference video are multiplied by 0.6 | Native 30 s single-shot with sound. Seedance 2.0 is also listed (~$0.24-0.30/s at 720p). |
| Wan 3.0 / Wan 3.0 Prime | Yes, **already wired** | $0.10 / $0.14 at 720p | Live in the picker today; dark only for the $0 balance. |
| MiniMax Hailuo family, MiniMax H3 | Yes | Hailuo 2.3 per-generation (e.g. $0.28 per 6 s at 768p); H3 $0.05/s (480p) to $0.16/s (4K) | Shelf depth if wanted; not needed for the top-shelf comparison. |
| Grok Imagine Video 1.5 | Yes | $0.08 / $0.14 / $0.25 (480p / 720p / 1080p); Lite $0.02 / $0.03 / $0.14 | Native audio, reference-to-video (up to 7 images), up to 15 s. |
| Gemini Omni Flash | **Not confirmed on fal** in this pass | Direct from Google: ~$0.07/s effective at 720p (see below) | On Higgsfield's shelf. A possible direct-Google lane later; not required for the top-shelf answer. |
| Soul 2.0 / Soul ID, DoP, Speak 2.0, Cinema Studio | **No, at any price** | n/a | These are Higgsfield's own models and engine. No supplier sells or licenses them. Section 4. |

Rate caveat, stated plainly: fal's own pages disagree about Kling 3.0
by tier and mode (its marketing FAQ quotes higher worked examples,
e.g. $1.12 for a 5 s O3 Standard clip with audio, than its endpoint
rate cards imply). This is the same failure mode the API-lane design
already guards against: the wiring pass must pin each endpoint's live
rate before the lane sells, and the platform already re-reads the
rate table every six hours and pauses a lane rather than quote from
stale prices.

### The direct-Google route for Veo

There is no price advantage to going direct, and the route changed
underneath everyone this fall:

- The Gemini Developer API no longer lists Veo at all (its pricing
  page, read today, has no Veo section; the Veo 3.1 preview model IDs
  were shut down on 2026-10-22). Veo 3.1 direct now means a Vertex AI
  / Gemini Enterprise account.
- Google's last published API rate for Veo 3.1 is the same $0.40/s
  with audio at 720p/1080p that fal charges. Going direct buys a
  second account, a second key, and a second integration for $0.00/s
  of savings. Verdict: stay on fal for Veo.
- What Google does sell direct now is Gemini Omni 1.2 Flash (GA on
  the Gemini API): video output at $12.00 per 1M tokens, 5,792
  tokens per second of 720p, which Google itself converts to ~$0.07/s
  at 720p ($0.104/s at 1080p, upsampled), up to 15 s, native audio,
  generation and editing. That is the cheapest frontier-grade second
  in this whole survey after Wan. Worth a lane of its own if fal has
  not listed it by the time Pass 2 lands.

### Terms, read plainly

- **fal**: its Terms of Service define a "Customer Solution" (your
  product, your end users, fal's APIs behind it) and license exactly
  that pattern, and its model pages state that outputs may be used
  commercially. Two plain limits: you may not expose fal's own APIs
  directly to end users, and you may not resell or sublicense the
  Services themselves or run a service bureau on them. Moonfactory's
  shape (its own product, its own key held server-side, users buying
  Moonfactory jobs) is the Customer Solution pattern, not raw resale.
- **Google**: the Gemini API paid tier does not use your content to
  improve its products (its pricing page says so). Nothing in the
  Google pages read for this document plainly forbids serving end
  users through your own product; the direct route's cost is
  integration, not permission.
- **BytePlus (Seedance first-party)**: ModelArk is about half of fal
  (~$0.2312/s at 720p), but BytePlus states it is not available in
  the United States. Not a lawful route for this business; fal's
  US-hosted Seedance endpoints are the compliant one.
- **MiniMax H3 open weights**: its Community License excludes the
  US, EU, UK, and South Korea for local deployment. Do not self-host
  H3 (this is also why the Freefield H3 apps stay skipped); hosted
  access via fal is unaffected.

### The math: a 5-second 720p clip, with audio, per model

Moonfactory is sound-on by default, so the with-audio rate is the
operative one. "Sell" is the balance debit at the standing 3x floor
(cost x 3); spending included budget debits the raw cost instead.

| Model (via fal) | Cost per 5 s | Sell at 3x floor | Creator budget (637c) buys |
|---|---|---|---|
| Wan 3.0 (already wired) | $0.50 | $1.50 | ~12 clips |
| Kling 3.0 Standard | $0.63 | $1.89 | ~10 clips |
| Grok Imagine Video 1.5 | $0.70 | $2.10 | ~9 clips |
| Veo 3.1 Fast | $0.75 | $2.25 | ~8 clips |
| Kling 3.0 Pro (1080p) | $0.84 | $2.52 | ~7 clips |
| Veo 3.1 Standard | $2.00 | $6.00 | ~3 clips |
| Seedance 2.5 | ~$2.37 | ~$7.10 | ~2.5 clips |

**Structural warning, not hidden:** Higgsfield's effective user
prices at Plus credit rates are roughly $0.44 for the Wan 3.0 clip
and $1.72 for the Seedance 2.5 clip. At a 3x floor over fal list
prices, Moonfactory sells the same clips for $1.50 and ~$7.10.
Capability parity through fal: yes, completely. Frontier *price*
parity at the current floor: no. Higgsfield blends a ~2-4x markup
over cheaper supply routes (including BytePlus at half of fal,
which a US business cannot lawfully use). Matching its frontier
prices would mean multipliers well under 1x on those lanes, i.e.
selling below the floor. That is a pricing-policy decision for
Jack, flagged here, not a build item and not something this
program quietly assumes away.

## 3. The program

Ordered by user-visible impact per dollar. Spend figures are
external spend only (Modal GPU/downloads, provider credit); every
pass also costs build time, which is not priced here.

### Pass 0 — Fund the provider account (the gate)

- **Ships:** nothing new. It turns the already-wired Wan 3.0 and
  Wan 3.0 Prime lanes from dark to live and unlocks every fal pass
  below.
- **Touches:** no files. One billing action.
- **Spend:** $0 build. The top-up itself is Pass 2's test budget
  plus operating float (below).
- **Needs from Jack:** one top-up at fal.ai/dashboard/billing.
  **Size: $50.** The Pass 2 verification matrix costs ~$21-22
  (below); $50 covers it, the later passes' tests (~$10), and
  leaves the account above the $25 operating floor the lane design
  specifies, so the lanes do not run dry mid-test.
- **Verification:** one real Wan 3.0 job succeeds end to end through
  the public form, down the exact path that today returns "this
  model is temporarily unavailable" with the hold released.

### Pass 1 — Finish the form (breadth that is already proven in the worker)

- **Ships:** (a) the camera-preset picker wired from
  `presets-index.json` (already in progress as its own platform
  pass; listed here so the program is complete); (b) chaining in
  the form: multi-segment jobs to 30 s and 60 s at per-segment
  pricing; (c) start & end frame fields (the worker's firstlast
  chain is seam-verified already); (d) a text-only mode, no start
  image required; (e) aspect choice (16:9, 1:1) alongside 9:16 on
  the house lanes; (f) queue position display and per-plan
  parallel dispatch.
- **Touches:** the platform form and job dispatch; the worker's
  chain endpoints already exist. Coordinates with the in-flight
  platform pass rather than colliding with it.
- **Spend:** ~$0.50 of Modal verification renders. Nothing else.
- **Needs from Jack:** nothing.
- **Verification (user-visible):** from the public form, produce
  (i) a 30 s chained video, (ii) a start/end-frame clip whose last
  frame lands on the chosen end image, and (iii) a text-only clip;
  each timed, each posted to the public gallery next to the
  single-segment baseline it improves on.
- **Closes:** rows 4 (length, on house lanes), 5 (presets usable),
  11 (extend embryo), and the text-mode part of row 12's workflow.

### Pass 2 — The frontier shelf (the quality pass)

- **Ships:** the picker gains **Veo 3.1** (Standard and Fast),
  **Kling 3.0** (V3 and O3), and **Seedance 2.5**, names only, per
  the standing picker rule. Each runs through the existing quote,
  hold, settle, and per-account cost-ceiling machinery; each
  endpoint's live rate is pinned into the rate table at wiring
  time (the platform already refreshes it every six hours and
  auto-pauses on staleness). Native audio from these models stays
  in the delivered file, normalized to the house loudness standard,
  like the Wan 3.0 lanes.
- **Touches:** platform lane configuration and fal endpoint
  mappings, the same surface the Wan 3.0 wiring touched. No worker
  change, no new provider, no new key.
- **Spend:** $0 Modal. ~$21-22 of the Pass 0 fal credit for the
  verification matrix: 3 fixed prompts x (Wan 3.0 $0.50 + Veo Fast
  $0.75 + Veo Standard $2.00 + Kling Standard $0.63 + Kling Pro
  $0.84 + Seedance 2.5 $2.37) = 3 x $7.09 = $21.27.
- **Needs from Jack:** Pass 0 done first. Nothing else.
- **Verification (user-visible):** the 3-prompt comparison set
  rendered through Moonfactory and published in the public gallery
  with model names shown. These are the same models Higgsfield
  sells, so any user can compare clip-for-clip against Higgsfield's
  own showcase output for the same model. Plus a stream-level check
  that delivered files keep the model's native audio at the house
  loudness standard.
- **Closes:** row 1 (quality, on these lanes: same models, same raw
  ceiling), row 3 (shelf, from 3 names to 8+ frontier families),
  row 4 (single-pass 30 s, 1080p, 4K on Veo), row 8 (native audio),
  and the dialogue half of row 9.

### Pass 3 — Start-image generation

- **Ships:** "describe it, we make the first frame" in the form:
  the z-image turbo model (12.2 GB, one small model, from the
  Freefield inventory) runs in the worker; an image job flows one
  click into an image-to-video job.
- **Touches:** modal-worker (a new image endpoint), the model
  Volume (one download), the platform form.
- **Spend:** the download costs pennies on Modal (earlier full
  model sets cost $0.02-0.03); verification renders ~$0.30.
- **Needs from Jack:** nothing.
- **Verification (user-visible):** one session that starts from an
  empty form, text only, and ends with a delivered video; timings
  recorded in the run log.
- **Closes:** row 12.

### Pass 4 — True camera conditioning

- **Ships:** the Fun Camera presets go live. The workflow is
  already ported and structurally validated; this pass is the
  30.6 GB two-UNET download, the lane wiring, and the picker state
  change from needs-model to live. Camera moves become model
  conditioning instead of prompt suggestions.
- **Touches:** the Volume, modal-worker, the presets index, the
  platform picker.
- **Spend:** download pennies; ~$0.45 of verification renders.
- **Needs from Jack:** nothing.
- **Verification (user-visible):** same start image, same scene
  text, three ways: no preset, the prompt-recipe Pan Left, and the
  Fun Camera Pan Left, measured with the framing/subject-scale
  method the dolly-in test established, published side by side.
- **Closes:** row 5's remaining half (conditioning-grade moves).

### Pass 5 — Characters and Elements

- **Ships:** an asset library: save a character, product, or
  location once and reuse it across jobs. Reference slots appear
  in the form on the lanes that support them (the Wan 3.0 lane's
  reference mode, already wired at the API layer, gets its form;
  the Kling O3 lane from Pass 2 gets the element treatment it was
  built for).
- **Touches:** platform (assets, form), lane configurations.
- **Spend:** ~$3-4 of fal credit for the verification set.
- **Needs from Jack:** nothing. Note what this pass is not:
  Soul-style identity training is not in it (section 4). This is
  reference-driven consistency, which is the strongest form of the
  feature any supplier sells.
- **Verification (user-visible):** one character across three
  different clips with the same face, published next to
  Higgsfield's own Soul consistency examples. Drift is visible or
  it is not.
- **Closes:** rows 6 and 7, to the level the supplier market
  supports.

### Pass 6 — Voice and lip sync

- **Ships, stage A ($0 beyond Pass 2):** the frontier lanes'
  native dialogue becomes a product feature: a spoken-line field
  on Veo and Kling jobs. The models already generate dialogue
  with lip sync in the same pass; this is form and prompt
  plumbing.
- **Ships, stage B (stack):** the Kokoro TTS voiceover hook from
  the SHORTFORM spec (runs on CPU, local-first) and a lip-sync
  stage in the post chain for house-lane clips (LatentSync /
  MuseTalk class). License check before any model download, per
  the traps already catalogued in OPEN-SOURCE-STACK.md.
- **Touches:** platform form, platform post chain; modal-worker
  only if the lip-sync model needs GPU time.
- **Spend:** stage A $0; stage B ~$1 of Modal verification.
- **Needs from Jack:** one standing decision, already open: voice
  *cloning* is not in this pass. The known shippable cloning
  model's weights are non-commercial (F5-TTS), so the pass ships
  TTS plus lip sync without cloning unless Jack rules otherwise.
- **Verification (user-visible):** a talking-head clip made on the
  Wan 2.2 lane from a typed line, lips synced, next to the same
  line delivered through Veo's native dialogue.
- **Closes:** row 9 (lip sync and voiceover; cloning excluded,
  section 4).

### Pass 7 — Video-to-video

- **Ships:** upload a finished clip and restyle or recast it, on
  the Kling O3 edit endpoints and Seedance reference jobs (Pass 2
  machinery; video input bills per the design's input-seconds
  rule, and Seedance reference jobs carry the 0.6 multiplier).
  Later, the stack option from the Freefield queue: the
  motion-transfer port (Wan-Animate, 24.9 GB) for
  reference-motion character work.
- **Touches:** platform (a video-upload path), lane
  configurations; modal-worker for the stack stage.
- **Spend:** ~$3 of fal credit for verification; the stack stage's
  download is pennies plus ~$0.50 of renders.
- **Needs from Jack:** nothing.
- **Verification (user-visible):** one uploaded clip and its
  restyled output, posted as a before/after pair in the gallery,
  motion visibly preserved.
- **Closes:** row 10.

### Pass 8 — The studio look (the "looks better" pass)

- **Ships:** the studio rebuilt on the open-higgsfield pattern
  notes already mined into `workflows/camera-presets/INDEX.md`:
  a catalog-as-data model picker, one composer bar, setting pills
  generated from the preset library's Cinema vocabulary (lens,
  focal, aperture, lighting, palette become real controls, not
  prompt folklore), gallery-as-history with attachable inputs,
  and job cards that show model, presets, and exact cost.
- **Touches:** the platform front end only.
- **Spend:** $0 external.
- **Needs from Jack:** one look approval at the end, from a
  screenshot set. Nothing up front.
- **Verification (user-visible):** a fixed screenshot checklist,
  Moonfactory vs higgsfield.ai side by side: first screen,
  composer with a model picked, gallery, a finished job card.
  Judged on: fewer clicks from landing to a running job than
  Higgsfield, every price visible before dispatch, no dead or
  decorative controls.
- **Closes:** row 14, and the depth half of row 13 (project pages
  with settings are part of gallery-as-history).

### Pass 9 — Breadth finishers (after the bar is met on the core)

- **Ships:** the effects pack port (Wan 2.1 I2V base, 16.4 GB,
  plus 24 effect LoRAs at ~0.36 GB each; the signature Bullet
  Time rides the same base), a 4K delivery option priced per
  Rule 2, a public API with an MCP wrapper over it, Stripe
  auto-refill, and queue/parallel polish.
- **Touches:** modal-worker and the Volume (effects), platform
  (API surface, billing), pricing config (4K).
- **Spend:** downloads pennies; renders ~$1; 4K test generations
  on Veo ~$3 of fal credit (4K with audio is $0.60/s).
- **Needs from Jack:** nothing.
- **Verification (user-visible):** per feature, the same pattern:
  a gallery artifact a user can open, not a code check.
- **Closes:** row 15 (API), the effects row of the parity map,
  and the 4K half of row 4.

## 4. Impossible or impractical, named plainly

- **Soul ID, Soul 2.0, DoP, and Speak 2.0 as themselves:
  IMPOSSIBLE to close literally.** They are Higgsfield's
  proprietary models and camera engine; no supplier sells or
  licenses them at any price. Passes 4-6 close the user-visible
  functions (conditioned camera moves, a persistent character,
  synced speech) by other routes. Any plan that promises the
  branded features themselves is fiction.
- **Kling 4.0 by API: IMPRACTICAL today.** fal lists it
  ($0.157/s at 720p) but its own article says API access is
  "coming soon"; it runs in fal's playground and Agent only.
  Higgsfield lists Kling 4.0, so a literal shelf-name gap
  persists until an aggregator opens the API. Watch item: the
  day it opens, it is a Pass 2-style lane addition.
- **Frontier price parity inside the 3x floor: IMPOSSIBLE
  while fal is the lawful supply.** Section 2's warning.
  Capability parity yes; matching Higgsfield's ~$1.72 effective
  Seedance clip price would take a multiplier under 1x on that
  lane. A pricing-policy call for Jack, not a build.
- **Cinema Studio's multi-shot directed-scene rig at DoP
  fidelity: IMPRACTICAL on this roadmap.** It is a product build
  measured in months at Higgsfield's team scale. Passes 1 and 5
  deliver the embryo (chains, presets, references); the polished
  rig is not promised here.
- **Editor plugins (Premiere, Resolve, Photoshop, Figma,
  Blender): IMPRACTICAL, deliberately skipped.** Real per-host
  builds, team-months each, for a surface almost no early user
  touches.
- **Self-hosted frontier quality: IMPOSSIBLE by construction.**
  Wan 3.0 has no open weights; there is nothing to download.
  The API lane is the route, which is why Pass 2 exists.
- **Team workspaces, pooled credits, contests: out of scope.**
  Business programs and marketing spend, not generation-product
  gaps a build closes. Listed so nobody mistakes them for
  forgotten items.

## 5. What it costs, and what "ready" looks like at the end

**Total external spend to close every closable item: about $55.**

- fal credit: one **$50** top-up (Pass 0). Tests consume ~$31-33
  of it across Passes 2, 5, 7, and 9; the rest is operating float,
  topped back to the $25 floor as real usage draws it down.
- Modal: all the model downloads (z-image 12.2 GB, Fun Camera
  30.6 GB, Wan-Animate 24.9 GB, the effects base and LoRAs
  ~25 GB) at the established pennies-per-set rate, plus every
  verification render in Passes 1, 3, 4, 6, and 9: **~$3-5
  total**.
- Everything else is code and design: $0 external.

When Passes 0-8 have landed, the scorecard reads: the same
frontier models in the picker that Higgsfield's shelf is built on
(minus its two proprietary pieces and Kling 4.0's pending API),
single-pass 30-second clips, a preset picker plus true camera
conditioning, characters that persist across clips, native
dialogue, sound on every clip at a published standard, a finished
1080x1920 master included instead of charged as a multiplier,
exact dollar prices before anything runs, and a studio that
passes the Pass 8 screenshot checklist against higgsfield.ai.
The deltas that would remain are named in section 4: Higgsfield's
proprietary engine polish, and its frontier prices. Neither is
hidden, and neither is fixable by building harder.
