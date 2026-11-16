# Freefield inventory for Moonfactory

Written 2026-11-16. Maps every app in neuregex/freefield (MIT, cloned to
`vendor/freefield/`, 210 MB working tree) against Moonfactory's parity gaps
(`HIGGSFIELD-PARITY.md`), with a port / skip verdict for each.

Method: every verdict is grounded in the app's own manifest
(`apps/<id>/app.json`: models, pack, inputs), its workflow graph
(`apps/<id>/workflow.api.json`), Freefield's measured lab data
(`docs/vram-map.json` peak VRAM per app, measured on an L40S, and
`docs/lab-runs.md`), and Moonfactory's actual Volume contents below.

## What Moonfactory already holds (sinclip-models Volume, verified from the worker's download manifests)

- Wan 2.2 TI2V-5B (Draft lane) + Wan 2.2 I2V A14B high/low pair (Production lane)
- umt5_xxl_fp8 text encoder, wan_2.1_vae + wan2.2_vae, clip_vision_h
- LightX2V 4-step LoRAs, high + low noise (the distilled recipe)
- SeedVR2 3B fp16 (upscale stage of the post chain)
- ACE-Step runs locally for music; RIFE for frame interpolation

"In Volume" below means every file the app needs is already there.

## Lab data note

Freefield publishes measured **peak VRAM per app** (vram-map, L40S) and
per-case wall times in its lab log. Headline numbers used below:
Cinema Studio video cases ran ~100 s/case on an L40S at 44.4 GB peak;
the LTX-2.5 apps peak 40–50 GB; the Wan effects/camera apps peak 31–42 GB.
Freefield's harness rule is worth copying later: every example in its app
is a real GPU run with its time and peak VRAM attached.

## Video apps (45)

### Ported in this run

| App | Higgsfield feature | Models needed | Download | Peak VRAM | Verdict |
|---|---|---|---|---|---|
| cinema-video | Cinema Studio (video) | LTX-2.5 + Gemma 4 (its own render path) | 55.9 GB | 44.3 GB | **PORTED as recipes.** Its 33 camera moves and full look vocabulary are prompt text, now Moonfactory's `workflows/camera-presets/` library, running on both Wan 2.2 lanes today. Its LTX render path is skipped (below). |
| camera-moves | Camera Controls | Wan 2.2 Fun Camera high/low UNETs (15.3 GB each) | 40.1 GB pack; only the 2 UNETs (30.6 GB) are missing for us | 41.3 GB | **PORTED as workflow.** `workflows/fun_camera_i2v.json`, structurally validated, placeholder-ready. Status needs-model: everything else it needs is already in the Volume. True camera conditioning, the strongest parity item in the whole repo for us. |

### Already covered by what we hold

| App | Feature | Models | Peak | Verdict |
|---|---|---|---|---|
| upscale-video | Video Upscale | SeedVR2 3B | 26.1 GB | **Equivalent held.** SeedVR2 is already in the Volume and in the post chain (ours is the fp16 EMA file, theirs an int8 repack; same model). No port needed. |
| smooth | (frame interpolation) | FILM net, 0.07 GB | 27.7 GB | **Equivalent held.** We interpolate with RIFE in the post chain. |

### Queued: port next, needs a model download

| App | Feature | Models needed | Download | Peak | Why |
|---|---|---|---|---|---|
| effects | Effects | Wan 2.1 I2V 480p 14B base (16.4 GB) + 24 effect LoRAs (~0.36 GB each) | 34.0 GB pack | 41.9 GB | One-tap effects are core Higgsfield (Cakeify, Explode, 360° orbit, Crane down, Dolly zoom...). Native-node graph, same shape as our Wan workflows. Catch: the LoRAs are Wan **2.1**-only, so the 2.1 base must come with them; they cannot ride our 2.2 lanes. |
| bullet-time-scene | Bullet Time Scene | Same Wan 2.1 base + BulletTime LoRA (0.36 GB) + LightX2V 2.1 LoRA | 25.8 GB | 30.8 GB | The signature Higgsfield effect; cheapest of the three bullet-time variants and shares the effects app's base model, so the pair is one download decision. (splash/white variants add a Flux Klein helper, 36.8 GB.) |
| motion-transfer | Character swap / motion | Wan-Animate set | 24.9 GB | 37.3 GB | Drive a character image with a reference video's motion. A headline Higgsfield feature we have no equivalent for. Needs a video-input path in the worker, so it is a bigger port than the two above. |
| video-remove-bg | Video Background Remover | BiRefNet | 0.4 GB | 6.7 GB | Tiny and cheap; useful for compositing and gallery-safe thumbnails later. Not user-facing parity, just a good utility pickup. |

### Skipped, with reasons

- **video, video-t2v** (Create Video, LTX-2.5 + Gemma, 44.9 GB, peak 41.8/48.4 GB): LTX-2.5 is a genuinely different lane (native audio, fast), but it is a 45 GB second video stack duplicating what our Wan 2.2 lanes already do. Revisit only as a deliberate third-lane decision.
- **extend-video, h3-video, h3-photo, reshoot** (MiniMax H3, 51.5–54.8 GB, peak 44–51 GB): a third model family; our chaining already covers long-form. Skip.
- **transitions, morph, chameleon, cloud-surf, roller-coaster, volcano-ad** (LTX-2.5 + Transition LoRA): LTX-only effect family; our chain crossfade covers basic transitions.
- **speak, talking, dub** (LTX-2.5 AV/dub LoRAs): lipsync/voice features. Moonfactory has no voice feature yet and the voice-cloning license question is already open (F5-TTS note). Revisit with the audio roadmap, not before.
- **genjutsu** (LTX-2.5 ingredients LoRA): overlaps motion-transfer; LTX-only.
- **reframe** (LTX-2.5 in/outpaint LoRA): aspect reframing; our delivery format is fixed 9:16 today.
- **sdr-hdr, vfx-clean-plate, vfx-day-to-night, vfx-water** (LTX-2.5 IC-LoRAs): niche VFX, LTX-only.
- **camera-copy** (LTX-2.3 Cameraman IC-LoRA): copies a reference video's camera move — clever, but LTX-only and needs a video-input path for a niche gain.
- **recast** (SCAIL-2, 28.6 GB): character swap via WanVideo **wrapper** nodes; our worker is native-node. motion-transfer covers the feature in our idiom.
- **asmr-host, asmr-product, asmr-whisper, idol, kick-ad, magic-button, melting-doodle, mukbang, rap-video, storm-creature, truck-ad, behind-the-scenes** (all LTX-2.5): vertical content *templates*, not capabilities — their value is prompt recipes for one niche each. Moonfactory can author its own templates later without the LTX stack.

## Audio apps (2)

- **music** (MiniMax Music 3, 14.3 GB, peak 37.1 GB): skip. Moonfactory's music bed is ACE-Step, run locally at $0.
- **sfx** (Stable Audio 3 + Qwen3.5, 15.0 GB, peak 9.3 GB): skip. No SFX feature yet.

## Utility apps (3)

- **smoke, smoke-invert**: Freefield's own test fixtures. Skip.

## Image apps (70) — video-first read

Moonfactory's only image-shaped parity gap is **start-image generation**
(P0): Higgsfield's "describe it, we make the first frame" button. The 70
image apps collapse into a few shared model packs:

- **z-image** (Create Image; z_image_turbo, 12.2 GB, peak 12.6 GB):
  **the start-image candidate.** One small turbo model, one job: text to
  image. Cheapest honest route to closing the P0 gap.
- **image-flux3 pack** (57 apps on ONE shared Flux Klein model, 11.0 GB,
  peaks ~11–13 GB): create-image, cinema-studio (image), headshot,
  face-swap, style templates, ad templates... One download would unlock
  the whole family if Moonfactory ever adds an image surface. For now:
  **cinema-studio's still-prompt template is already harvested** into the
  preset library's composition grammar; the rest are skipped as
  image-surface features, not video parity.
- **image-qwen-edit pack** (Qwen-Image-Edit, 31.2 GB): **angles, shots,
  zooms** generate camera-angle variants of a frame — genuinely
  camera-adjacent and worth a queue note behind z-image; **relight** and
  **whats-next** skip for now. 31 GB is a heavy ask for a variant tool.
- **image-krea2** (style-reference, styles; ~20–23 GB): skip.
- **moss-image**: skip.
- **depth-map, remove-bg** (upscale pack utilities): skip; no feature
  needs them today.

## What else is worth stealing (patterns, not apps)

- **App = manifest + workflow.** Freefield's unit of feature is a JSON
  manifest (name, what it does, inputs with bind templates, models with
  sizes) next to a ComfyUI API workflow. Moonfactory's preset library
  follows the same shape; lanes and features should keep doing so.
- **Lab-measured examples.** Every Freefield example carries its real GPU
  time and peak VRAM. Moonfactory's pricing already runs on measured
  costs; extend the same honesty to per-feature stats when the studio UI
  lands.

## Clone handling

- `vendor/freefield/` — 210 MB working tree. **Not committed** to the
  Moonfactory repo; the inventory and the ported artifacts carry the
  value. MIT license, attribution in the preset files.
- `vendor/open-higgsfield/` — 1.6 MB, small enough to vendor, but it ships
  no license file (Jack approved its use 2026-11-16 regardless). Its UX
  patterns are mined into `workflows/camera-presets/INDEX.md`; no code
  was copied. Recommend not committing the clone itself; parent's call.
