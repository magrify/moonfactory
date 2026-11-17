# Moonfactory, Finish State, 2026-11-17

Supersedes FINISH-STATE-2026-11-16.md. Written under MODAL SPEND:
NONE AUTHORIZED: this run deployed code and read live state only;
no generation of any kind was dispatched.

Platform: https://magrify--moonfactory-api.us-east.modal.run
Repo: github.com/magrify/moonfactory

## The parity verdict (Jack's bar, applied literally)

Moonfactory is NOT called finished. The feature-by-feature matrix
against Higgsfield's own current public pages
(HIGGSFIELD-PARITY-MATRIX-2026-11-17.md) reads: 27 rows, 7 better,
6 equal, 12 worse, 2 unverified. The 2 unverified rows (API-lane
output quality, API-lane speed) are blocked on provider funding
only, with a ready-to-run packet. The 12 worse rows are named in
the matrix with their closest shipped equivalents; three of them
(API access, trained character identity, team/enterprise plans)
need Jack's scope or business decisions, and the rest are product
builds, not configuration.

## What this run fixed (all verified live 2026-11-17)

1. Legal and support surfaces: /terms, /privacy, /support live
   (were 404), in the Direction C design, linked from every
   footer. Copy is accurate to the code (scrypt-hashed passwords,
   Stripe processing, gallery rules, credit refund policy from
   BUSINESS-RULES Rule 8). No em dashes anywhere in user-facing
   copy.
2. Internal registry out of public view: /api/config no longer
   serves llm_providers. The Clip picking fallback (Gemini, then
   free OpenRouter models, then loudness) is internal plumbing;
   customers pick video models, never text models.
3. Discoverability and basics: meta descriptions and Open Graph
   tags on /, /app, /gallery; /robots.txt, /sitemap.xml, and
   /favicon.ico live (were 404); baseline security headers
   (nosniff, same-origin referrer policy, SAMEORIGIN framing) on
   every response.
4. Aspect ratios on API lanes: every wired submit now carries the
   job's aspect (9:16, 16:9, 1:1) instead of a hardcoded 9:16,
   including CometAPI pixel-size and Runway ratio maps and the
   Wiro ratio parameter. The studio aspect picker is visible for
   every model. Delivery ships the picked shape. Payload-unverified
   like every API lane.
5. Emotion look slot: the camera library's look system now carries
   10 slots (tempo, genre, era, camera body, lens, focal,
   aperture, lighting, palette, emotion with 8 options), served
   live at /api/camera-presets and composed into prompts; verified
   locally (validation plus composition) and live.
6. 4K and 2K tiers wired where a published rate exists: LTX-2.5
   4K via fal ($0.34/s cost, $0.42625/s customer), Wan 3.0 Pro
   Uncensored 2K ($0.20/s cost) and 4K ($0.23/s cost) via Wiro,
   sold at the standard markup with can_undercut honestly
   unanswered. Local pricing invariants re-checked: no lane sells
   below cost, no standard-basis lane sells above its Higgsfield
   figure.
7. Studio bug: the 8 uncensored models had no entry in the
   studio's duration/resolution table, so their pickers inherited
   the previous model's options (including lengths the server
   would refuse). Entries added, matching server bounds.

## Verified working (carried from the Nov 16 audit, still true)

- 37 models live, sorted cheapest first, 9 uncensored-flagged,
  4 price-paused lanes (Vidu Q2, Vidu Q4, Seedance 2.5 Flash,
  PixVerse V6) shown unavailable at the end.
- House pipeline proven end to end (proof renders 2026-11-16):
  Production and Draft with parallel music and voiceover at about
  -15 LUFS, camera presets with user-picked speed, settled cost
  matching quotes.
- Access model, free tier (5 clips lifetime), paid gates, hard
  cost ceiling, owner pricing, Stripe live (Creator $20/mo,
  top-ups $5/$10/$20), gallery with report and opt-out, Clip
  upload path with AI picking: all as audited Nov 16, unchanged
  except the fixes above.
- Kling at-cost lanes stay at provider cost (Jack's decision,
  Nov 16). Kaggle token stored at ~/.kaggle/access_token; models
  live only in Modal (Jack's single-copy decision). Camera speed
  is the user's choice (Jack's correction, Nov 16).
- Rate freshness: the scheduled refresh is working; Seedance
  2.5's live 1080p rate (48.365c) differs from the seed (48.1c)
  because the BeatAPI page re-read moved it. By design.

## Blocked, with exact blockers

- Provider funding (Jack's money): every API lane is
  payload-unverified at $0 balances. The funded verification
  packet (funded-verification-packet-2026-11-17.md) is ready:
  32 priced lanes, $10.22 provider cost for one standard test
  each (worst case about $11.10), top-ups of $90 across 9
  providers plus 3 uncensored suppliers at unknown checkout
  minimums, funding can land provider by provider. OpenRouter
  needs $0 (balance $8.32). Each test also needs Jack's named
  Modal approval under the spend gate.
- Scope decisions (Jack): a customer API, a trained-identity
  (Soul ID class) feature, an image model shelf, a video editing
  surface, team/enterprise plans. Each is a product build, not a
  fix.
- Final name, domain, trademark (Jack's call, unchanged).
- Proven impossibilities: Sora 2 (OpenAI shut the Videos API
  2026-09-24), Cinema Studio 4.0 and DoP and Soul ID as products
  (Higgsfield-proprietary; functional equivalents shipped),
  Kling 4.0 Flash (fal early access only).
