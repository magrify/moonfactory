# Higgsfield parity map for Moonfactory

Written 2026-11-16. Jack's directive: "Moonfactory needs to do everything
Higgsfield does and more for videos. needs to look as good/better too."

Sources, in order of trust: Higgsfield's own pricing guide
(geo.higgsfield.ai, prices checked 2026-11-06), its pricing explainer blog
(higgsfield.ai, 2026-10-24), its help-center model guide (updated
2026-11-06), and its homepage (read 2026-11-16). Reviews (TechJournal,
Luma, paulbreit.com, kristian-larsen.com, Treza) fill in hands-on detail.
Higgsfield changes plans and model lineups constantly (several times in
2026 alone) and prices vary by country; its own two pages currently
disagree on Starter (blog: $15/mo, 200 credits; Nov 6 guide: $19/mo, 270
credits). The Nov 6 guide is treated as current. Anything below can move
within weeks.

Moonfactory status is read from platform/LAUNCH-NOTES.md,
BUSINESS-RULES.md, workflows/SHORTFORM.md, and modal-worker/STATUS.md,
all as of 2026-11-16.

## What Higgsfield is, in one paragraph

A browser-based creative suite that is mostly an aggregator: 35+ third-party
and in-house models behind one credit balance, wrapped in its own control
layer. The signature pieces are directed camera work (DoP preset engine and
Cinema Studio), Soul ID trained character identity, Marketing Studio
(URL-to-ad), and a public community feed. There is no mobile or desktop app
(its help center says store apps using its name are fakes); reach beyond the
browser is via official plugins (Premiere Pro, After Effects, Photoshop,
DaVinci Resolve, Figma, Blender), an MCP server, a CLI, and a developer API
with Python and Node SDKs.

## Headline capability inventory (video)

1. **Multi-model shelf.** Seedance 2.5 / 2.0, Kling 4.0 / 3.0, Veo 3.1,
   Wan 3.0 / 2.6, Grok Imagine, Gemini Omni Flash, MiniMax Hailuo, plus its
   own DoP and (for images) Soul 2.0. Sora 2 was listed through fall 2026;
   one source reports it was removed when OpenAI shut its API down on
   2026-09-24, others still list it. Fast-moving; treat as uncertain.
2. **Camera motion presets (the signature).** 50+ named moves on the Camera
   Controls page (reviewers count 65-70+; one skill doc says 100+ on DoP):
   Crash Zoom, Bullet Time, Dolly Zoom, 360 Orbit, Snorricam, Whip Pan,
   FPV Drone, crane, dolly, tracking. Pick a move instead of describing it.
3. **Cinema Studio.** A filmmaking layer over the models: genre, lens
   (Cooke, anamorphic, macro), focal length (8mm-75mm), aperture
   (f/1.4-f/11), sensor/film look, lighting, tempo, up to three camera moves
   in one shot, Cast and Locations for multi-shot sequences, clips up to
   30 s, up to 50 references. Always credit-paid, never in Unlimited windows.
4. **Generation modes.** Text-to-video, image-to-video, start & end frame
   control (Plus and up), multi-reference inputs (images, video, voice,
   Elements; Kling takes up to 10 keyframes and 15 combined references).
5. **Soul ID character consistency.** Train an identity once from 20+
   photos (25 credits, ~3-5 min), reuse the same face across images, clips,
   and models; characters can be saved as reusable Elements alongside
   products and locations. Higgsfield itself calls consistency "high, not
   absolute."
6. **Audio as a first-class feature.** Native audio in the frontier models
   (Veo 3.1 with audio, Seedance), Higgsfield Speak 2.0 and Wan 2.5 Speak
   lip sync, voice cloning, dubbing, multi-speaker dialogue and sound
   effects (Seed Audio / Higgsfield Audio, ElevenLabs models).
7. **Video-to-video editing.** Genjutsu: upload a finished clip, recast
   characters, swap objects or locations, restyle, keep the motion.
   Popcorn for storyboard/multi-frame editing and inpaint. Upscale,
   reframe, and background removal in-app and in the editor plugins.
8. **Marketing Studio and Shorts.** Paste a product link or photos, get an
   ad (1,500+ templates, UGC builder, native audio, lip sync). Shorts
   Studio builds vertical 9:16 videos with an AI avatar host and can
   recreate trending formats.
9. **Format range.** 480p / 720p / 1080p / 4K (Seedance 2.0 at 4K on Plus),
   16:9, 9:16, 1:1 and other aspects per model, clips from ~3 s to 30 s
   depending on model. Resolution is a price multiplier: 1080p costs
   roughly 2-3x the credits of 720p, 4K about 5x.
10. **Speed and parallelism.** Generations land in ~30 s to ~5 min.
    Parallel video jobs scale by plan: 1 free / 2 Starter / 6 Plus / 8 Ultra.
11. **Community and gallery.** A public feed of user projects where you can
    open any project and see its prompts, assets, and settings; publishing
    is opt-in (free-plan generations are private by default); creator
    contests (a $1M Talent Contest is running now); community preset and
    character sharing.
12. **Developer and agent surfaces.** REST API with its own dollar balance
    (open.higgsfield.ai, docs.higgsfield.ai), Python and Node SDKs, an MCP
    server (OAuth, no key handling), an npm CLI, agent Skills, a ChatGPT
    plugin, Canvas (node workspace), and Supercomputer (agentic workspace
    with Slack / Drive / Notion / Figma connectors).

## Pricing shape (Nov 6, 2026, US, Higgsfield's own guide)

| Plan | Price/mo | Credits/mo | Notes |
|---|---|---|---|
| Free | $0 | 0 | A look around: images on 2 models, effectively no video, no commercial use, watermark can appear |
| Starter | $19 | 270 | Selected models only, 2 parallel videos, commercial use included |
| Plus | $59 ($47 annual) | 1,200 | Every model, Start & End Frame, Speak, UGC builder, 6 parallel videos |
| Ultra | $129 ($99 annual) | 3,000 (6,000 / 9,000 options) | 8 parallel videos, lowest cost per credit |
| Team / Scale | $79 / $215 per seat | 1,000 / 2,500 per seat, pooled | Scale adds SSO and spend control |

Mechanics that matter: credits are priced per model, resolution, and
duration, and the cost per credit changes with your plan ($0.070 on Starter
down to $0.043 on Ultra, ~$0.033 on Ultra annual). Subscription credits
expire at each cycle, no rollover. Top-up packs and auto-refill expire
after 90 days. Sample generation costs: Wan 3.0, 9 credits per 5 s at 720p;
Kling 4.0, 16.25 credits per 5 s at 1080p; Seedance 2.5, 35 credits per 5 s
at 720p (70 for 10 s, 210 for 30 s in Cinema Studio terms); Soul ID
training, 25 credits once. At Plus's ~$0.049/credit, a 5 s 720p Wan 3.0
clip effectively costs the user about $0.44, a Seedance 2.5 clip about
$1.72. "Unlimited" windows on named models are a recurring promotion layer
(web app only, never Cinema Studio, never via API/MCP/CLI). Terms note,
from TechJournal's read of the ToS: Higgsfield may train on your inputs
and outputs unless you are on Enterprise.

## Parity table

Status key: **have** = shipped and usable by customers today.
**partial** = exists in the worker, the spec, or one lane, but not as a
finished customer feature. **missing** = not built.
Closing cost key: **stack** = buildable on the current self-hosted Wan 2.2
stack; **API lane** = needs the Wan 3.0 / Prime API lane (or another
provider API) wired; **hard** = research-grade or a big product build.

| Higgsfield capability | Moonfactory status | What closing the gap takes | Priority |
|---|---|---|---|
| Image-to-video | have (Draft 5B + Production A14B lanes live) | n/a | done |
| Text-to-video | partial: the 5B is a text+image model, but the public form is start-image first; no pure text entry | stack: expose a text-only mode in the form | P1 |
| Start & end frame control | partial: proven in the worker (firstlast chain test, seam-verified); not in the public form | stack: form fields + dispatch wiring; the pipeline already does it | P0 |
| Multi-segment chaining / longer videos | partial: chaining proven in the worker (10 s stitched, spec to 60 s and 3 min); the public form sells one ~5 s segment per job | stack: wire chains into the job form with per-segment pricing from pricing.py | P0 |
| Camera motion presets (50+) | **ported, unverified 2026-11-16**: preset library shipped in `workflows/camera-presets/` — 33 Cinema Studio prompt-recipe moves (ported from Freefield, MIT) running on both Wan 2.2 lanes, 13 with deterministic crop-window end-keyframe recipes for the chain lane; plus 9 true-conditioning presets on the ported Wan 2.2 Fun Camera workflow (`workflows/fun_camera_i2v.json`, structurally validated) which is **needs-model** (the 2 Fun Camera UNETs, 30.6 GB, are not in the Volume yet). Not yet wired into the generation form (platform pass) | stack: platform pass renders the picker from `presets-index.json`; Fun Camera goes live with one model download | P0 for the recipe library — library done, form wiring remains |
| Cinema Studio (lens, aperture, lighting, genre, multi-shot rig) | partial: the lens / aperture / lighting / genre / era / palette **vocabulary and composition grammar are ported** into `workflows/camera-presets/` (from Freefield's Cinema Studio) as prompt recipes; the multi-shot directed-scene rig is still missing; chaining gives multi-shot in embryo only | hard: a directed-scene UI over chains + presets + references; pieces are stack, the polish is a product build | P2 |
| Start-image generation (Soul, Nano Banana, FLUX class) | missing: users must bring their own start image | stack or API lane: add an image model to the worker (open image models run on the same Modal setup) or an image API | P0 |
| Character consistency (Soul ID) | missing; a consistent start image gives weak per-clip consistency only | hard on stack (identity LoRA training for Wan is real work); API lane models with character references get partway there | P1 |
| Reusable references (Elements: characters, products, locations) | missing; single start image only | stack: an asset library + reference slots in the form; multi-reference conditioning quality depends on the model (Wan 3.0 API takes up to 10 images / 5 videos / 5 audio) | P1 |
| Lip sync (Speak) | missing; named as the known gap in SHORTFORM.md | stack: open lip-sync models (LatentSync / MuseTalk class) in the post chain; licensing needs care per model | P1 |
| Native dialogue / SFX audio | missing in-model (Wan 2.2 is silent); platform muxes a generated music bed onto every file | API lane: Wan 3.0 generates native audio in the same pass; stack: add SFX/TTS stages to the post chain | P1 |
| Per-prompt custom music | partial: ACE-Step bed exists but the audio app takes a fixed caption today; Jack has ordered per-prompt audio as a paid-only feature | stack: pass the user's prompt/caption through to ACE-Step, gate to paid plans | P0 (already ordered) |
| TTS voiceover hook | partial: spec'd in SHORTFORM.md (Kokoro class, first 1-2 s, ducking), not wired | stack: Kokoro runs on CPU locally per the local-first rule | P1 |
| Video-to-video restyle / recast (Genjutsu) | missing | hard on stack (video-to-video Wan workflows exist but quality and identity swap are research-y); API lane models with video references get partway | P2 |
| Upscale to delivery size | have: RIFE to 30 fps + SeedVR2 to exactly 1080x1920 in the post chain, included in the job | n/a | done |
| Editing basics (reframe, background removal, inpaint) | missing; delivery is 9:16 only | stack: reframe/crop variants are cheap post steps; background removal is another model in the chain | P2 |
| Aspect ratios beyond 9:16 | partial: spec allows 16:9 for X as a later option; pipeline is portrait-only today | stack: render sizes are config; landscape pixel counts match the models' native sizes | P1 |
| 4K delivery | missing (1080x1920 is the ceiling) | stack: SeedVR2 can target higher, at real GPU cost; price it per Rule 2 before offering | P2 |
| Durations to 30 s in one action | partial: 30 s = a 6-segment chain, worker-proven pattern, not a one-click job | stack: same wiring as chaining | P0 (with chaining) |
| Model choice / switching | partial: two self-hosted Wan 2.2 lanes live; Wan 3.0 and Wan 3.0 Prime lanes are designed in BUSINESS-RULES (est. $0.10/s and $0.14/s at 720p) but not wired (no provider keys). Jack has ordered model switching as a paid-only feature | API lane: wire the provider, per-use pricing and spend caps already specified in Rule 3 | P0 |
| Frontier-model quality (Seedance 2.5, Veo 3.1, Kling 4.0) | missing; no route to these models today | API lane via aggregators (fal, reAPI class) at per-second prices; Rule 3 already governs how they may be sold | P1 |
| Generation speed | missing by a wide margin: Draft ~6-13 min, Production ~25 min vs Higgsfield's ~0.5-5 min | stack: render-cost/speed work is Jack's chosen lever for the pricing problem too (fewer steps, better GPU class, caching); API lane is fast by construction | P0 |
| Parallel jobs | missing: effectively one at a time, no queue position display | stack: Modal scales out; needs queue UI and per-plan concurrency in code | P1 |
| Marketing Studio (URL-to-ad, templates, UGC builder) | missing | hard: a template/product pipeline on top of chaining + lip sync + TTS; valuable but a second product | P2 |
| Shorts Studio (avatar host, trend recreation) | missing as a product; the animated-shorts lane and SHORTFORM spec cover the format, not an avatar host | hard: depends on character consistency + lip sync landing first | P2 |
| Effects library (45+ one-click VFX) | missing; **port mapped 2026-11-16**: Freefield's effects app is a native-node Wan 2.1 graph with 24 effect LoRAs — the route is one base-model download (Wan 2.1 I2V 14B, 16.4 GB) plus the LoRA set; see `vendor/FREEFIELD-INVENTORY.md` | stack: model download + workflow port, same recipe approach as camera presets | P2 |
| Public gallery / community | missing but decided (Jack, 2026-11-16): public by default, paid plans get a small checkbox to opt out, and the public gallery must be made safe for users | stack: gallery pages, publishing defaults, moderation/reporting basics, opt-out flag per account | P0 (already ordered) |
| Watermark policy | have: free tier watermarked, paid clean (matches Higgsfield) | n/a | done |
| Commercial use on paid plans | partial: nothing in the launch notes grants or restricts it; Higgsfield includes it on every paid plan and excludes it on free | paperwork: state it in terms/pricing copy | P1 |
| Credits / top-ups / auto-refill | partial: prepaid top-ups exist ($5/$10/$20, Stripe pending live keys); no auto-refill; included seconds expire at cycle end per Rule 4 (same as Higgsfield's no-rollover) | stack: auto-refill is a small Stripe feature once live keys land | P2 |
| Public API / SDKs | missing for customers: the worker API is internal and key-gated to the platform only | stack: expose a scoped public API on the platform once lanes stabilize | P2 |
| MCP / CLI / agent access | missing | stack: an MCP wrapper over the public API is cheap once the API exists | P2 |
| Editor plugins (Premiere, Resolve, etc.) | missing | hard: real plugin builds per host app; last, if ever | P2 |
| Mobile app | missing, but so is Higgsfield's (web-only, by their own help center) | n/a: keep the web studio phone-friendly instead | not a gap |
| Team workspaces / pooled credits | missing | stack: accounts work; only if a business tier is ever sold | P2 |

## The honest quality section

Higgsfield's best-looking output does not come from Higgsfield. It comes
from the frontier models on its shelf (Seedance 2.5, Veo 3.1, Kling 4.0,
Wan 3.0) plus two pieces of its own tuning: the DoP camera-preset engine
and the Soul image/identity models. Moonfactory's self-hosted Wan 2.2
A14B is a full generation behind Wan 3.0 and further behind Seedance 2.5
and Veo 3.1 on motion coherence, prompt adherence, hands and faces, and
human realism. On a clip-for-clip basis against those models, Moonfactory
cannot match Higgsfield today, and no amount of post chain changes that:
the post chain makes a clip bigger, smoother, and louder, not smarter.

What is closable, in order of leverage:

1. **The API lane closes most of the raw-quality gap in one wiring job.**
   Wan 3.0 at 720p lists at $0.10/s with native audio and rich reference
   inputs; Prime at ~$0.14/s. BUSINESS-RULES Rule 3 already specifies
   exactly how these lanes may be sold (per use, exact estimate shown,
   hard spend cap, never bundled). What it does not close: Seedance, Veo,
   and Kling, which need an aggregator API and their own per-second
   prices, and DoP/Soul, which are Higgsfield's own.
2. **Breadth gaps are product work, not research.** Chaining in the form,
   start/end frames in the form, preset recipes, start-image generation,
   aspect ratios, the gallery: all buildable on the current stack, most
   of them already proven in the worker.
3. **Speed is a real gap with a real cost.** ~25 minutes for a production
   segment against their ~1-5 minutes is the difference between a tool
   people iterate in and a tool people check back on. It is also the same
   work as Jack's cheaper-renders lever: every minute cut from a render
   is margin and usability at once.
4. **Not closable soon:** DoP-grade preset fidelity baked into a model,
   Soul ID-grade trained identity, Cinema Studio's multi-shot polish,
   and a 35-model shelf. Those are team-years at Higgsfield's scale.
   Moonfactory should not promise them; it should route around them
   (see the "and more" section).

One more honest note on cost: Moonfactory's measured self-hosted
production cost (~$0.77 per 5 s segment, about $0.154/s) is currently
HIGHER per second than Wan 3.0's API list price at 720p ($0.10/s).
Higgsfield's pricing works because it resells metered APIs at roughly
2-4x markup inside credits that expire monthly. Until render costs fall,
the API lane is not just the quality lever, it is also the cheaper
second of video Moonfactory can sell.

## And more: where Moonfactory can credibly be better

1. **Sound on every video, always, at a published standard.** Most of
   Higgsfield's shelf is silent unless you pick an audio model and pay
   its rate. Moonfactory already muxes a generated music bed onto every
   delivered file, free tier included, mixed to about -14 LUFS / -1 dBTP.
   With per-prompt custom audio (paid, ordered) this becomes "describe
   the sound, get the sound" as a default, not an upgrade path.
2. **A finished delivery master is the default, not a multiplier.**
   Every production job includes the post chain to exactly 1080x1920 at
   30 fps. Higgsfield charges roughly 2-3x the credits for 1080p over
   720p and about 5x for 4K. Moonfactory's flat "this is the file you
   post" is simpler and, for vertical short-form, arguably better.
3. **Prices in dollars, per job, before anything runs.** Higgsfield
   makes users convert credits at a rate that changes by plan
   ($0.033-$0.070 per credit) and charges for every completed generation,
   liked or not, inside a balance that expires. Moonfactory's rules
   already require the exact charge and a hard spend cap before an
   API-lane job runs, and a per-job profit record after. "You know what
   this clip costs before it exists" is a real differentiator.
4. **Your videos are never training data.** Higgsfield's terms allow
   training on inputs and outputs for everyone below Enterprise.
   Moonfactory can promise the opposite to every user at zero marginal
   cost, and pair it with Jack's gallery rule (public by default to
   market creators' work, one-click opt-out on paid plans) so the
   privacy story and the growth story do not fight.
5. **Stories, not just shots: authored-keyframe chaining.** Higgsfield
   sells the single beautiful clip; its multi-shot story lives in the
   expensive Cinema Studio tier. Moonfactory's chain joins segments at
   keyframes a human picked, so cuts land where the creator wanted them,
   at a flat per-segment price known upfront, for shorts up to minutes.
   As render costs fall this gets cheaper automatically; per-second API
   metering at Higgsfield does not fall the same way for the user,
   because the credit price of a 30 s clip stays 6x the 5 s price.

## What this means for the $20 Creator plan

- Higgsfield's nearest tier is Starter at $19/mo, and its 270 credits buy
  roughly 30 five-second Wan 3.0 clips at 720p (about 150 s of video), or
  ~38 Kling 3.0 clips, or only ~7 Seedance 2.5 clips. The headline number
  hides a 5-10x spread by model, and the credits expire monthly.
- Moonfactory's $20 Creator currently includes about 40 s of production
  video at measured costs under the 3x floor. Against Starter's ~150 s of
  mid-tier clips, that is the gap Jack's cheaper-renders decision has to
  close; the API lane at $0.10/s is the fastest route to included time
  that does not bleed margin, but Rule 3 currently forbids bundling API
  lanes into included minutes, so closing it that way is a rules change
  for Jack, not a build detail.
- Where Moonfactory already matches or beats the $19 tier on paper:
  delivery is a 1080x1920/30 fps master with sound on every clip
  (Higgsfield's 720p silent-by-default clips at that tier are neither),
  free tier is 5 real video clips lifetime (Higgsfield's free tier has
  effectively no video at all), and pricing is in dollars, not credits.
