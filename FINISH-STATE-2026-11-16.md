# Moonfactory — Finish State, 2026-11-16

The definitive completion audit. Written under MODAL SPEND: NONE
AUTHORIZED — this audit ran no generations and no proof renders; every
claim below rests on the smoke run, the approved proof renders, the
pricing audits, or a live read-back taken today.

Platform: https://magrify--moonfactory-api.us-east.modal.run
Repo: github.com/magrify/moonfactory

## Verified working

1. Landing, session-aware, Direction C dark design — paid members get
   an "Open Create" hero with zero Try It framing anywhere; guests and
   free accounts get the Try It hero. (Finish-run smoke, 39 checks;
   guest variant read back live today.)
2. Studio (/app): Try It / Create / Clip surfaces; Create is the
   five-thing screen (image drop, prompt, model dropdown, length,
   Generate with the exact price on the button); everything else
   behind "More options." (Finish-run smoke 39/39.)
3. Model shelf: 29 models served by /api/config, names only, default
   Wan 2.2, dropdown fed at runtime so it cannot go stale. (Live
   read-back today: exactly 29 models, default wan2.2.)
4. Access model: sign-in + verified email required on every
   generation path (401 anonymous / 403 unverified, audited); free
   accounts land on Try It with paid fields walled off; paid accounts
   have no Try It tab and open into Create. (Access-model pass +
   finish-run smoke.)
5. Free tier: 5 clips per account, lifetime, platform ceiling
   enforced; rejected probes leave free_jobs_used at 0. (Finish-run
   smoke.)
6. Paid gates: model switching, custom music prompt, spoken line,
   gallery opt-out all gated to paid accounts and verified on both
   sides (free 403 before dispatch; owner passes to the same 422
   validation as any dispatch). (Finish-run smoke.)
7. House generation pipeline end to end on Wan 2.2 (Draft and
   Production lanes), settled cost matching the quote to the cent.
   (Proof renders 2a/2b: settled 15c Production / 50c Draft on the
   owner account.)
8. Audio pipeline: music bed and Kokoro voiceover spawn in parallel
   at dispatch and land mixed, voice ducked over the bed. (Proof
   renders 2a/2b: mix fit R-squared 0.997 / 0.987, voice ~3x the bed
   gain, -15.18 / -15.0 LUFS, no audio fallback.)
9. Voiceover as a capability: single approved test call produced
   clean speech; the voice model is Volume-cached platform-wide, so
   every account's voiceover starts warm. (Approved test,
   2026-11-16.)
10. Camera presets: prompt-recipe presets live (42 presets served);
    Fun Camera presets now resolve in exactly one place
    (platform/fun_camera.py) to the only combination measured to
    move the camera — distilled recipe, speed 3.0 — whatever speed
    the client sends. (Proof render 3a: real pan, -8.89% of frame
    width, correct direction; speed 1.0 measured zero in every
    recipe. Camera-fix probe 23/23 including live read-back today.)
11. Gallery: public dark grid, handle only (never email or real
    name), Report button with admin visibility and removal, paid
    opt-out honored. (Finish-run smoke: 26 items, file serving,
    report flow; /api/gallery read back live today.)
12. Clip: upload path proven end to end twice — transcription, AI
    moment picking (pick_mode "ai" after the Gemini model fix),
    rendered child clip serving 200 video/mp4. YouTube link fetch is
    conclusively impossible from Modal egress (every route tested);
    the product says so plainly and routes users to upload, which
    works. (Finish-run item 4.)
13. Pricing policy: every compared lane is under Higgsfield's price
    or at raw provider cost; no lane sells below cost; the at-cost
    set is exactly Wan 3.0 480p/720p, Kling 3.0 720p, Kling 4.0
    720p/1080p. (Pricing audits rounds 1-3; live /api/pricing
    read back today: 28 API lanes, at-cost and residual flags match
    the audit.)
14. Owner pricing: support@magrify.com is the only markup-exempt
    account, keyed by an explicit is_owner flag audited across all
    accounts; owner quotes equal raw cheapest-route cost on every
    surface. (Owner-pricing pass + live probes.)
15. Stripe, live mode: Creator $20/mo and $5/$10/$20 top-ups,
    webhook wired, restricted key held only in the Modal secret
    store; payments verified live. (Live read-back today:
    payments_mode "live", configured; finish-run smoke created and
    expired checkout sessions with no charge.)
16. Hard cost ceiling: no account can be served more generation cost
    than it has paid, net of Stripe fees, enforced before dispatch.
    (Platform gate, exercised throughout the smoke run.)
17. Provider lanes wired dark-safe across the swept provider field
    under the cheapest-route rule (Kie primaries, CometAPI Seedance
    2.5 480p re-route, per-resolution Higgsfield overrides); $0
    balances fail clean with holds released. (Wiring pass +
    provider sweep, 2026-11-16.)
18. Kie prices verified against Kie's own authenticated catalog;
    four lanes that were priced below their true sound-on cost were
    corrected. (Pricing audit round 3.)
19. Polish: Play Hollow logo and favicon on all surfaces, on-brand
    dark 404 for browsers (JSON kept for /api/*), empty states,
    mobile-fluid studio. (Finish-run item 6; 404 re-verified live
    today.)
20. Compute scaffolding: Kaggle free-GPU batch lane scaffolded
    (private kernel, empty queue), Vast.ai dormant fallback
    recorded, Modal production serving only. (Compute distribution
    plan, 2026-11-16.)

## Not done — Jack's steps only

- Provider funding + funded lane verification (Jack's money):
  provider accounts sit at $0, so the API lanes are payload-
  unverified until funded. fal's own minimum top-up is $1 and $10
  covers verification tests; the other amounts stand as previously
  reported. Once funded, the funded verification pass (a real
  generation per primary lane against the Higgsfield bar) can run —
  each generation still needs Jack's spend approval under the gate.
- Kling at-cost residuals decision (Jack's call: sell below cost or
  pause 3 lanes): Kling 3.0 720p, Kling 4.0 720p, and Kling 4.0
  1080p are priced at raw provider cost, which is still above
  Higgsfield's bundled-credit price; no cheaper verified Kling
  route exists. They stay at cost until Jack decides.
- Final name/domain (Jack's call): the platform runs as Moonfactory
  on the Modal URL; a final name, custom domain, and trademark check
  are undecided.
- Kaggle API token (Jack's step, sign-in Google
  magrifywebsites@gmail.com): Kaggle lane is scaffolded at 0
  sessions until Jack creates the API token (Kaggle > Settings >
  API > Create New Token).

## Parity statement

The Higgsfield quality-parity bar is NOT claimed for the provider
lanes: they remain payload-unverified until funded. The house lane,
audio pipeline, camera behavior, pricing policy, and every product
surface above are verified on evidence, not assertion.
