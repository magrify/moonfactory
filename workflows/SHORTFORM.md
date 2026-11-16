# Short-form delivery spec (TikTok / Reels / X)

Sinclip's default output is a vertical short built for TikTok, Instagram Reels, and X. This file is the target every generation and post step aims at. The workflow templates in this folder are already set to the render sizes below; this spec covers what happens between render and upload.

## Platform targets

| Platform | Aspect | Delivery size | Notes |
|---|---|---|---|
| TikTok | 9:16 vertical | 1080x1920 | The primary target. Full screen vertical is the native format. |
| Instagram Reels | 9:16 vertical | 1080x1920 | Same file as TikTok works. Keep key subjects and any text out of the bottom strip, where the caption and UI sit, and out of the top strip, where the header sits. |
| X | 9:16 or 16:9 | 1080x1920 or 1920x1080 | X accepts vertical and landscape. Sinclip defaults to the same 9:16 file; a 16:9 variant is a later option, not part of the first pipeline. |

One master file serves all three platforms. We do not render per platform variants in v1.

## Render size vs delivery size

We never render at delivery size. Rendering is the expensive step, so it happens at the model's comfortable native pixel count, in portrait orientation, and the post chain upscales to delivery size.

| Stage | Draft (5B) | Production (A14B) |
|---|---|---|
| Render | 480x832 | 720x1280 |
| Frame rate at render | 16 fps | 16 fps |
| Post: interpolation | to 30 fps | to 30 fps |
| Post: upscale | to 1080x1920 | to 1080x1920 |
| Delivery file | 1080x1920, 30 fps, H.264 mp4 | 1080x1920, 30 fps, H.264 mp4 |

Why this split: 720x1280 is the same pixel count as the A14B's native 1280x720 landscape size, just rotated, so quality and speed should match the model's known good operating point. The upscale to 1080x1920 happens once, in post, where it is cheap and can be redone without re-rendering. Draft renders at 480x832 (same 9:16 shape, roughly half the pixels per side) because drafts exist to judge motion and framing, not final quality.

UNVERIFIED: both Wan models are usually demonstrated in landscape. Portrait behavior at these exact sizes is expected to work (the pixel counts match the native landscape sizes) but has not been confirmed on a live run. The first test session (see ../FIRST-TEST-RUNBOOK.md) renders vertical on purpose to settle this.

## Durations

The unit of generation is one segment: 81 frames at 16 fps, about 5 seconds. Finished shorts are chains of segments, joined at authored keyframes with the first frame / last frame workflow, so cuts land on frames a human picked.

| Finished short | Segments | Use |
|---|---|---|
| About 5 s | 1 | Single beat clips, free tier clips, previews |
| About 15 s | 3 | The standard short: hook, beat, payoff |
| About 30 s | 6 | Story and product shorts |
| About 60 s | 12 | The long end of short-form; the practical ceiling for this pipeline in v1 |

Segment joins share the boundary keyframe, so a chain of N segments runs about N times 5 seconds. Exact final durations are set in the stitch step, not by the model.

Longer paid videos (the Creator plan's up to 3 minutes) use the same chaining at larger segment counts. Shorts are the proving ground because they exercise the identical pipeline at the smallest cost.

## Audio defaults for shorts

Every short ships with audio. Silent output is never a deliverable.

- **Music bed on everything.** A generated music track (ACE-Step class model) matched to the short's mood and length, laid under the whole video from frame zero. Free tier clips get the music bed too; audio is not a paid upsell.
- **Optional TTS hook line.** For shorts that need a spoken hook, one short narration line (Kokoro / F5-TTS class) placed in the first 1 to 2 seconds, while attention is decided. The music bed ducks under the voice and comes back up after it.
- **Loudness normalized for social platforms.** House target: about -14 LUFS integrated loudness for the final mix, true peak at or below -1 dBTP. Social platforms normalize playback loudness themselves; delivering at this target means the platform leaves our mix roughly as authored instead of turning it up or down hard. This is a house default, to be confirmed against platform playback during the sample gallery stage.
- **Format.** Audio is mixed into the single delivery mp4 (AAC) in the stitch step. There is no separate audio file deliverable in v1.

## What this spec deliberately leaves out

- Captions and burned-in text: a product layer decision for the app stage, not the generation pipeline.
- Per platform re-edits (different hooks per platform): creators can re-run a chain with a different first segment later; v1 ships one master.
- Lip sync: as stated in the README, close up lip synced dialogue is the known gap. Shorts in v1 lean on music and voice over formats, which this pipeline does well.
