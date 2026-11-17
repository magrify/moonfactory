# Higgsfield parity matrix, RE-SCORED 2026-11-17 (post scope build)

Supersedes the scoring in HIGGSFIELD-PARITY-MATRIX-2026-11-17.md
(the morning matrix). This is an independent verification pass,
not the builder's report: every Moonfactory fact below was
re-read live (/api/config, /api/pricing, the served pages) or
re-derived locally (the three scratch check suites, a numeric
quote demonstration, code reads) on 2026-11-17, after the scope
build shipped and after this pass's own fixes were deployed.
Higgsfield facts are unchanged from the morning matrix
(Higgsfield's own public pages, read 2026-11-17).

Strict rule applied: a feature that is wired but
payload-unverified does not count as Better or Equal on quality
grounds. Rows whose surface is shipped but whose function only
a funded generation can prove sit in Unverified, however good
the wiring looks. "Unverified" is not done, and this matrix
still does not call Moonfactory finished.

## Verdict counts (27 rows)

- Better: 7 (unchanged)
- Equal: 7 (was 6; reference count moved up)
- Worse: 8 (was 12; three rows moved to Unverified, one to Equal)
- Unverified: 5 (was 2; motion transfer, character consistency,
  and lipsync/dubbing moved here from Worse: shipped, unproven)

## Better (7)

| Row | Evidence |
|---|---|
| Video model breadth | 40 video models served live in /api/config (re-read this pass), more than Higgsfield's video shelf inside its 35+ models-and-tools total. Includes a dedicated motion transfer lane and two long-form house lanes (dark until their weights are staged). Named exceptions sit in the exclusives note below. |
| Max duration | Higgsfield caps at 30 s per generation. Moonfactory: API lanes generate natively to each model's real maximum (30 s on Kling 4.0, Seedance 2.5, Wan 3.0) and chain exact native segments to 45/60/90/120 s; the house lane chains to about 60 s; FramePack (wired, dark) makes up to 120 s in a single job. Length lists re-verified this pass against maker documentation. |
| Free plan | Unchanged: Higgsfield free carries 0 credits and no video generation; Moonfactory free is 5 real video clips per account, lifetime. |
| Per-clip price on compared lanes | Unchanged policy, re-audited 2026-11-16 and spot-re-verified this pass in /api/pricing: every compared lane under Higgsfield's effective price or at raw provider cost (at-cost set unchanged). The new image shelf follows the same rule (Nano Banana Pro at $0.0625 per image vs about $0.098 on Higgsfield's Plus plan). Exact price sits on the Generate button; Higgsfield prices in expiring credits. |
| Web UI simplicity | Create is still five things; the scope additions (image mode, characters, motion transfer) went behind the same mode toggle and More options, verified in the served studio HTML this pass. Subjective row, as before. |
| Uncensored models | 9 uncensored-flagged video lanes served live, plus 3 uncensored image lanes on the new shelf. Higgsfield sells none. Payload-unverified until funded. |
| Long-video clipping | Unchanged: Clip cuts long videos into captioned vertical clips with AI moment picking (proven 2026-11-16). No Higgsfield equivalent. |

## Equal (7)

| Row | Evidence |
|---|---|
| Aspect ratios | Every wired submit carries the job's aspect (9:16, 16:9, 1:1); the studio picker shows for every model; delivery ships the picked shape. Wired 2026-11-17, payload-unverified like every API lane. Exotic ratios (for example 21:9) are not matched. |
| Start and end keyframes | End image on the house Production lane (proven in proof renders); end-frame fields wired on the fal-routed lanes, Wiro Pro, and the SpicyAPI lanes. API side payload-unverified. |
| Camera controls and presets | 42 camera presets including conditioned Fun Camera moves with user-picked speed, plus 10 look slots composed into the prompt (tempo, genre, era, camera body, lens, focal, aperture, lighting, palette, emotion). Different mechanism, same decisions offered. |
| Native audio and voiceover | Both platforms generate dialogue and sound natively on flagship lanes. Moonfactory also mixes a generated music bed and a Kokoro voiceover in parallel on every job, including silent lanes (proven 2026-11-16 at about -15 LUFS). Speech jobs on non-native lanes now also get the built-in lip sync stage (see the lipsync row). |
| Gallery and community | Both run public community galleries. Moonfactory shows handle only, with Report, admin removal, and a paid opt-out. |
| Commercial use rights | Moonfactory's terms (live at /terms) leave users their rights in uploads and outputs with no plan-tier carve-out. Higgsfield excludes commercial use on free and reserves its strongest terms for Enterprise. |
| Reference count | Moved up from Worse. Per-lane maker caps are now served live and enforced in the job path (re-read this pass): Seedance 2.5 takes 30 images / 10 videos / 10 audios plus a 30 s combined reference-video cap, a 50-reference total matching Cinema Studio's 50; the Seedance 2.0 family takes 9/3/3; Veo 3.1 and Fast take 3 images and no reference video or audio, per Google; Kling 4.0 takes 10/5/3 and Kling 3.0 Omni 10/5/5, per Kling's published limits. One open item: Veo 3.1 Lite's advertised caps (see the verification checkpoint, defect-open A). Acceptance of references at these caps is payload-unverified like every API lane. |

## Worse (8)

| Row | Gap and closest equivalent |
|---|---|
| Image generation breadth | Narrowed, still Worse. A curated shelf now exists (Nano Banana 2.1 / Pro / 2, GPT Image 2.6 Flare, Seedream 5.5 Uncensored, Seedream 5.0 Pro Uncensored, Z-Image Uncensored, plus the house lane), priced per image under Higgsfield's comparable lane. Higgsfield's shelf is still broader (Flux.2, Soul, more sizes; the GPT lane here sells one size until the funded pass measures its token billing). The small shelf was Jack's explicit curation call. Payload-unverified (packet item 11). |
| Resolutions and 4K breadth | Unchanged in substance. Re-verified this pass in /api/pricing: 4K rates exist on exactly two lanes (LTX-2.5 via fal, Wan 3.0 Pro Uncensored via Wiro, which also sells 2K); Seedance 2.0's supplier 4K tier stays deliberately unsold. Higgsfield sells 4K across more models (Seedance 2.0 4K, Kling 3.0 4K). Payload-unverified. |
| Editing (video-to-video, extend, region edit) | Unchanged. Higgsfield sells Genjutsu, Forward/Backward Extend, Region Edit. Moonfactory has Clip and one-tap effects. Deferred by Jack's scope decision (no editing tools for now). |
| Upscale and enhance | Unchanged. Higgsfield sells a 4K upscale pipeline. Moonfactory's house delivery upscales vertical output to 1080x1920 built in; no standalone upscale tool. |
| Plan ladder | Unchanged. Higgsfield: Starter $15/mo, Plus $49, Ultra $129, annual discounts, Team seats, Enterprise. Moonfactory: Creator $20/mo plus pay-per-video; no annual, team, or enterprise tier (team/enterprise deferred by Jack). |
| API and agent access | Unchanged. Higgsfield sells a public API, an MCP server, and a CLI. Moonfactory's API is internal only. Deferred by Jack (public API comes later). |
| Support and help depth | Narrowed, still Worse on depth. Moonfactory now runs a real help center (/help plus eight guides: getting started, pricing, lengths, audio, characters, motion transfer, images, FAQ), all live and in the sitemap, with a contact form that emails support@magrify.com through Resend (endpoint verified this pass without sending a test message). Higgsfield's help library is still larger. |
| Native mobile app | Narrowed, still Worse. Moonfactory is now an installable PWA (manifest, Play Hollow icons, service worker, standalone display, app-like touch behavior; all verified live this pass), which meets Jack's "works like an app on the web" bar. Higgsfield has a native iOS app in the App Store; Moonfactory has no store app. |

## Unverified (5), blocked on provider funding

| Row | State and what settles it |
|---|---|
| Motion transfer | Moved from Worse. A dedicated lane ships: Wan Motion on fal ($0.06/s at 720p), subject image plus one driving video, output follows the driving length (2 to 30 s), wired through quote, create, and the fal payload, with the studio's Motion transfer group in More options. This pass fixed its served length list, which had advertised chained lengths the lane cannot make. Function is payload-unverified; packet item 12 settles it. |
| Character and element consistency | Moved from Worse. Saved character profiles ship (up to 5 characters, 10 images each, reused as start image plus references), and the Soul ID functional equivalent is wired: fal's FLUX portrait LoRA trainer at an honest $7.50 (raw $6.00 at the standard markup; owner pays $6.00; full refund on failure, in code and on the studio button). Trained-identity quality cannot be scored until it runs; packet items 14 and 15 settle it. |
| Lipsync and dubbing | Moved from Worse. Built-in lip sync ships as a product rule, not a tool: any job with a spoken line on a lane that does not speak natively gets fal Sync Lipsync v2 ($0.05/s) folded into its quote, hold, ceiling, and settlement, verified numerically this pass (a 10 s Wan 3.0 speech job: +$0.50 raw, +$0.625 at the customer markup; speech-native lanes and scriptless jobs correctly get none). Failure still delivers the video, flagged. Higgsfield sells lipsync and multilingual dubbing as a separate studio; Moonfactory's is automatic, and its dubbing is the same mechanism (the job's own voice matched to the talker), with no multilingual dubbing catalog. Packet item 13 settles it. |
| Output quality on API lanes | Unchanged. The funded verification packet (funded-verification-packet-2026-11-17.md), now 16 items with the scope proofs: one standard test per priced lane (32 lanes, $10.22 provider cost at seeded rates, worst case about $11.10), 4K spot tests, and items 11 to 16 (image proofs, motion proof, lip-sync proof, character reuse and training proofs, one chained 45 s proof). |
| Generation speed and queue on API lanes | Unchanged. The same packet, recording submit-to-delivered wall time per lane. House lane times are measured and slower than hosted API models are expected to be; concurrency is free 1, paid 4. |

## Blocked on external impossibility (named, not counted as rows)

- Sora 2: OpenAI shut the Videos API down on 2026-09-24.
  Higgsfield's shelf listing is legacy. Not available at any price.
- Cinema Studio 4.0 and DoP as products: Higgsfield-proprietary.
  Functional equivalents (camera looks, emotion slot, presets,
  Fun Camera) are shipped and counted in the camera row.
- Soul ID as a product: proprietary. The functional equivalent
  (reference profiles plus fal LoRA training) is counted in the
  character row, in Unverified.
- Kling 4.0 Flash: fal early access only.

## What changed in this re-score

1. Reference count: Worse to Equal (per-lane maker caps live
   and enforced; flagship totals now match).
2. Motion transfer, character consistency, lipsync/dubbing:
   Worse to Unverified (surfaces shipped and verified as wired;
   function waits on the funded packet).
3. Image breadth, support depth, native app: still Worse, each
   narrowed by a shipped build (curated shelf, real help center,
   installable PWA).
4. Defects found and fixed by this pass (details in
   verification-rescore-2026-11-17.md): Vidu Q2's length list,
   Wan Motion's length list, a crash in the long-form quote
   path, and two stale comments describing the removed trim
   system. One defect left open: Veo 3.1 Lite's advertised
   reference caps exceed every documented figure for that
   model and wait on the funded pass to settle.
