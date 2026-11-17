# Moonfactory finish run, 2026-11-17 (checkpoint)

MODAL SPEND: NONE AUTHORIZED. Deploy only, no generation.

## Live baseline (verified 2026-11-17 ~12:53 UTC)
- HEAD 64872ab, local == origin at start.
- /api/config: 37 models, sorted cheapest-first by lowest customer sell rate, 9 uncensored-flagged (8 new lanes + house Wan 2.2), 4 price-paused lanes at end (Vidu Q2, Vidu Q4, Seedance 2.5 Flash, PixVerse V6), default wan2.2, payments live.
- /api/pricing: 33 priced per-second lanes in api_lanes; seedance 1080p live rate 48.365c vs seed 48.1c is the scheduled BeatAPI refresh working as designed, not drift.
- Pages live: / 200, /app 200, /gallery 200, /healthz 200, /api/gallery 200, /api/camera-presets 200, /api/effects 200.
- Compile OK on all platform modules.

## Gap list (evidence-based)
- G1 FIXING: /terms, /privacy, /support all 404. Paid platform with public gallery and uncensored lanes has no legal or support pages. Footer links only to Gallery.
- G2 FIXING: /api/config publicly exposes llm_providers (internal Clip picking fallback registry, incl. uncensored chat model list). Jack's ruling: internal only, customers never see them. Studio JS never reads it.
- G3 FIXING: No meta description or Open Graph tags on /, /app, /gallery. /robots.txt, /sitemap.xml, /favicon.ico all 404.
- G4 FIXING: Em dashes in user-facing copy (404 title, landing pricing line, one studio string). Standing rule: no em dashes in user-facing copy.
- G5 FIXING: No baseline security headers (nosniff, referrer policy, frame protection) on responses.
- G6 DOCS: FINISH-STATE-2026-11-16 stale (Kling decision made, Kaggle token stored, camera choice shipped, 37 models not 29). LAUNCH-NOTES Stripe section stale (secret was the one missing value; Stripe is live and verified since).
- G7 PACKET: Funded verification packet not yet written as a single ready-to-run document.
- NOT A BUG: seedance 1080p live vs seed rate (refresh). Duplicate display labels for uncensored lanes are by design (dropdown appends the Uncensored suffix). HotAPI H3 promo pricing flag stands, already in code and BUILD-LOG.
- BLOCKED ON JACK: provider funding for payload verification; final name/domain/trademark.

## Progress
- [x] F1 terms/privacy/support pages + footer links
- [x] F2 remove llm_providers from public config
- [x] F3 meta/OG + robots/sitemap/favicon
- [x] F4 em dash cleanup in user-facing strings
- [x] F5 security headers middleware
- [x] Local pricing invariant checks (NONE failed, incl. new 4K tiers)
- [x] Deploy + live verification (fresh pass)
- [x] FINISH-STATE-2026-11-17 + funded packet + BUILD-LOG + BUILD-LOG (LAUNCH-NOTES checked: its Stripe section already carries the RESOLVED note, so it stands as dated history)
- [x] Commit + push

## Parity round (Jack's tightened bar, 2026-11-17)
- P1 FIXED: API lanes were 9:16 only. All wired submits now carry the job's aspect (9:16 / 16:9 / 1:1); studio aspect picker visible for every model; CometAPI size map and Runway ratio map added; Wiro ratio wired.
- P2 FIXED: emotion look slot added (8 emotions) beside the existing 9 Director's Panel style slots (tempo, genre, era, camera body, lens, focal, aperture, lighting, palette) and 42 camera presets. Verified locally: validation + prompt composition.
- P3 FIXED: 4K wired where a published rate exists: LTX-2.5 4K via fal ($0.34/s), Wan 3.0 Pro Uncensored 2K/4K via Wiro ($0.20/$0.23 per s). SpicyAPI Seedance 2.5 4K rate ($2.16/s) recorded but its schema resolution value is not published in what was read, so not wired.
- P4 FIXED (bug): the 8 uncensored models had no MODEL_OPTS entry in the studio, so their duration/resolution pickers showed the previous model's options. Entries added matching server bounds.
- Parity matrix: see HIGGSFIELD-PARITY-MATRIX-2026-11-17.md (in sinclip and repo).

## Outcome
- Deployed and verified live twice (post-deploy readback + independent fresh pass): all routes 200, 37 models sorted, 9 uncensored flags, 4K/2K sells live, emotion slot live, no llm_providers in public config, no 'Spicy' in config/pricing payloads.
- Parity matrix: 27 rows, 7 better, 6 equal, 12 worse, 2 unverified (funding-blocked, packet ready).
