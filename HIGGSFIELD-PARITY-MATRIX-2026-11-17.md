# Higgsfield parity matrix, 2026-11-17

Built under Jack's tightened bar: "as good or better than Higgsfield
in every way, or not done." Higgsfield facts are from Higgsfield's
own public pages read 2026-11-17 (their all-in-one subscription
guide, their pricing explainer, their best-models and camera-control
guides; plan figures verified October-November 2026 on those
pages). Moonfactory facts are live read-backs and local tests from
2026-11-17. "Unverified" is not done, and this matrix does not call
Moonfactory finished.

## Verdict counts (27 rows)

- Better: 7
- Equal: 6
- Worse: 12
- Unverified: 2 (both blocked on provider funding, packet ready)

## Better (7)

| Row | Evidence |
|---|---|
| Video model breadth | Moonfactory serves 37 video models live (/api/config, 2026-11-17), more video models than Higgsfield's video shelf inside its 35+ models-and-tools total. Named exceptions sit in the exclusives row below. |
| Max duration | Higgsfield caps at 30 s per generation. Moonfactory house lane chains to about 60 s (12 segments); API lanes reach 30 s. |
| Free plan | Higgsfield's free plan carries 0 credits, no video generation, and no commercial use (their pricing page, read via their own guides). Moonfactory free is 5 real video clips per account, lifetime, watermarked. |
| Per-clip price on compared lanes | Moonfactory's pricing policy, audited three rounds on 2026-11-16: every compared lane is under Higgsfield's effective price or at raw provider cost; the at-cost set is Wan 3.0 480p/720p, Kling 3.0 720p, Kling 4.0 720p/1080p (Jack approved at-cost for the Kling lanes). Exact price sits on the Generate button; Higgsfield prices in credits that expire monthly with no rollover. |
| Web UI simplicity | Moonfactory Create is five things (image, prompt, model, length, one priced Generate button) with everything else behind one More options. Higgsfield is a multi-studio suite. Subjective row, evidence is the shipped Direction C screen vs their suite structure. |
| Uncensored models | Higgsfield sells none and its own guide notes content protection on Seedance 2.5. Moonfactory serves 9 uncensored-flagged video lanes (8 wired 2026-11-17 plus the house Wan 2.2). Payload-unverified until funded. |
| Long-video clipping | Moonfactory Clip cuts a long video or upload into captioned vertical clips with AI moment picking (proven end to end 2026-11-16). Higgsfield has no equivalent surface; its agent (Supercomputer) is a different shape. |

## Equal (6)

| Row | Evidence |
|---|---|
| Aspect ratios | Higgsfield models run vertical, wide, and square. Moonfactory ran 9:16 only on API lanes until today: every wired submit now carries the job's aspect at 9:16, 16:9, or 1:1 (house aspects), the studio picker shows for every model, and the delivery stage ships the picked shape. Wired 2026-11-17, payload-unverified like every API lane. Exotic ratios some Higgsfield models offer (for example 21:9) are not matched. |
| Start and end keyframes | Higgsfield sells Start & End Frame on Plus and Ultra. Moonfactory: end image on the house Production lane (proven in proof renders), end-frame fields wired on the fal-routed lanes, Wiro Pro, and the SpicyAPI lanes. API side payload-unverified. |
| Camera controls and presets | Higgsfield Cinema Studio: Director's Panel (camera body, lens, aperture, lighting, genre, era, tempo, palette), 30+ movement presets, an emotion wheel. Moonfactory: 42 camera presets including conditioned Fun Camera moves with a user-picked speed, plus 10 look slots composed into the prompt (tempo, genre, era, camera body, lens, focal, aperture, lighting, palette, and emotion, the last added 2026-11-17 with 8 options, verified locally and live at /api/camera-presets). Different mechanism (prompt composition plus conditioned presets vs a proprietary panel), same decisions offered. |
| Native audio and voiceover | Both platforms generate dialogue and sound natively on flagship lanes. Moonfactory also mixes a generated music bed and a Kokoro voiceover in parallel on every job, including silent lanes, proven in the 2026-11-16 proof renders at about -15 LUFS. |
| Gallery and community | Both run public community galleries. Moonfactory shows handle only, with a Report button, admin removal, and a paid opt-out. |
| Commercial use rights | Moonfactory's terms (live 2026-11-17 at /terms) leave users their rights in uploads and outputs with no plan-tier carve-out. Higgsfield excludes commercial use on its free plan and reserves its strongest terms (training exclusion, IP indemnification) for Enterprise contracts. |

## Worse (12)

| Row | Gap and closest equivalent |
|---|---|
| Image generation breadth | Higgsfield runs a full image shelf (Nano Banana Pro, Flux.2, GPT Image, Seedream, Soul). Moonfactory generates a start image with one model inside the studio. Closest equivalent shipped: that start-image lane. Closing the gap means wiring an image shelf, a product build, not a config change. |
| Resolutions and 4K breadth | Higgsfield sells 4K across several models (Seedance 2.0 4K, Kling 3.0 4K). Moonfactory wired 4K today where a published rate exists (LTX-2.5 via fal at $0.34/s cost, Wan 3.0 Pro Uncensored 2K and 4K via Wiro), but most lanes still top out at 1080p. SpicyAPI's Seedance 2.5 4K rate ($2.16/s) is recorded, but its schema's resolution value was not in what was read, so that tier is not wired. Payload-unverified. |
| Reference count | Cinema Studio takes up to 50 references; Kling on Higgsfield takes up to 10 keyframes and 15 references. Moonfactory caps at 10 reference images, 5 reference videos, 5 reference audios, set to the widest limits the wired providers publish in common. Raising caps blind would send jobs providers reject. |
| Motion transfer | Higgsfield sells a dedicated Motion Control tool (movement and expression from a reference video onto a character). Moonfactory has reference-video modes on several lanes but no dedicated transfer surface. Payload-unverified. |
| Character and element consistency | Soul ID trains a reusable identity from 20+ photos and applies it across image and video. It is Higgsfield-proprietary; no provider Moonfactory uses sells an equivalent trained-identity product. Closest shipped equivalent: reference images and a start image per job. A training pipeline is a product build, blocked on Jack's scope decision. |
| Editing (video-to-video, extend, region edit) | Higgsfield sells Genjutsu (video-to-video), Forward/Backward Extend, and Region Edit. Moonfactory has Clip (cutting) and one-tap effects, no video-to-video edit or extend surface. Closest equivalent: effects plus Clip. An edit surface is a product build. |
| Upscale and enhance | Higgsfield sells a 4K upscale pipeline. Moonfactory's house delivery upscales vertical output to 1080x1920 with SeedVR2 and RIFE interpolation built in; API lanes ship at native resolution and there is no standalone upscale tool or 4K upscale. Partial equivalent built into the house lane only. |
| Lipsync and dubbing | Higgsfield sells Lipsync Studio and multilingual dubbing in Higgsfield Audio. Moonfactory has speech-native lanes and a spoken-line voiceover, no lipsync or dubbing tool. |
| Plan ladder | Higgsfield: Starter $15/mo (200 credits), Plus $49, Ultra $129, annual discounts, Team seats, Enterprise. Moonfactory: Creator $20/mo plus pay-per-video, no annual, team, or enterprise tier. Entry price is $5 higher; the per-clip row above is where Moonfactory wins. Team and enterprise are business decisions for Jack. |
| API and agent access | Higgsfield sells a public API (50+ models, $5 minimum top-up), an MCP server, and a CLI. Moonfactory's API is internal only; customers get the web studio. The internal REST API exists, so a customer API is a product build (keys, quotas, docs), blocked on Jack's scope decision. |
| Support and help depth | Email support now matches (support page live, same-business-day standard), but Higgsfield runs a full help center and Moonfactory has three pages. Worse on self-serve depth. |
| Native mobile app | Higgsfield has an iOS app. Moonfactory is mobile-fluid web only, verified on the studio and gallery. Closest equivalent shipped: the responsive web app. |

## Unverified (2), blocked on Jack funding

| Row | What settles it |
|---|---|
| Output quality on API lanes | The funded verification packet (funded-verification-packet-2026-11-17.md): one standard test per priced lane, 32 lanes, $10.22 in provider cost at seeded rates (worst case about $11.10 with BeatAPI clip billing), plus 4K spot tests (LTX-2.5 4K 6 s $2.04, Wan 3.0 Pro Uncensored 4K 5 s $1.15, 2K 5 s $1.00). The house lane is proven only on flat-illustration and balloon scenes. |
| Generation speed and queue on API lanes | The same packet, recording submit-to-delivered wall time per lane. House lane times are measured (Draft roughly 6 to 13 minutes, Production about 25, per the studio's own copy) and are worse than hosted API models are expected to be; that is exactly what the packet measures. Concurrency is free 1, paid 4 (Higgsfield: 2 video on Starter, unlimited paid parallel on Ultra web). |

## Blocked on external impossibility (named, not counted as rows)

- Sora 2: OpenAI shut the Videos API down on 2026-09-24. No platform
  can wire it; Higgsfield's shelf listing is legacy. Not available
  at any price.
- Cinema Studio 4.0 and DoP as products: Higgsfield-proprietary.
  The functional equivalents (camera looks, emotion slot, presets,
  Fun Camera) are shipped and counted in the camera row.
- Soul ID as a product: proprietary trained identity. See the
  character consistency row.
- Kling 4.0 Flash: fal early access only, per the Nov 16 sweep.

## What changed today toward parity (2026-11-17)

1. Aspect ratios on every API lane (was 9:16 only).
2. Emotion look slot (Director's Panel parity on the last slot).
3. 4K and 2K tiers wired where published rates exist.
4. Studio bug fixed: the 8 uncensored models had no duration and
   resolution table entry, so their pickers showed the previous
   model's options.
5. Terms, privacy, and support pages shipped (commercial terms row
   depended on them), plus meta descriptions, robots, sitemap,
   favicon, and baseline security headers.
6. The internal Clip picking registry (llm_providers) removed from
   the public /api/config response.
