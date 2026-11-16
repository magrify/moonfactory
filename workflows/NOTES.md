# Workflow template notes

These are ComfyUI **API format** workflow templates (the format the `/prompt` endpoint accepts), built from the node structure in the local studio guide. They are templates, not tested exports. The worker (`../worker/handler.py`) loads one, replaces the placeholder values with the job's real values, and submits it.

Nothing here has been run yet. Every uncertain field is listed under UNVERIFIED below rather than silently invented.

## Patch points (what the worker replaces per job)

| Placeholder / field | Where | Replaced with |
|---|---|---|
| `PLACEHOLDER_START_IMAGE.png` | LoadImage node | Filename of the job's start image, after the worker copies it into ComfyUI's input folder |
| `PLACEHOLDER_END_IMAGE.png` | LoadImage node (firstlast_chain only) | Filename of the job's end keyframe |
| `PLACEHOLDER_POSITIVE_PROMPT` | CLIPTextEncode node | The job's motion prompt |
| `PLACEHOLDER_NEGATIVE_PROMPT` | CLIPTextEncode node | The job's negative prompt, or the house default below |
| `seed` / `noise_seed` = 0 | KSampler / KSamplerAdvanced | The job's seed |
| `width`, `height`, `length` | Wan node | The job's size and frame count, within tier limits |
| `filename_prefix` | VHS_VideoCombine | A per job output prefix so outputs never collide |

House default negative prompt (from the guide):
`blurry, distorted face, extra fingers, morphing, flicker, text, watermark`

## The three templates (short-form presets)

All three are vertical first since the 2026-11-15 retune: 9:16 canvas, one segment is 81 frames at 16 fps (about 5 seconds), and output prefixes live under `sinclip/shortform_*`. The delivery spec they feed is in SHORTFORM.md in this folder. File names did not change, because the worker loads templates by file stem; "short-form preset" is carried in the defaults below, the output prefixes, and this documentation.

- **draft_i2v_5b.json** - Single stage. Wan 2.2 TI2V-5B, one KSampler pass, 480x832 at 81 frames and 16 fps (about 5 seconds). Used for free tier clips and for cheap previews of paid work. The 5B needs no CLIP Vision step.
- **prod_i2v_a14b.json** - Production. Wan 2.2 I2V A14B as two sampler stages: the high noise model runs steps 0 to 10, the low noise model runs steps 10 to 20, with leftover noise passed between them. CLIP Vision encodes the start image. 720x1280, 81 frames at 16 fps (about 5 seconds per segment).
- **firstlast_chain.json** - One chained segment. Same A14B two stage tail as production, but the conditioning node is WanFirstLastFrameToVideo with a start keyframe and an end keyframe, each CLIP Vision encoded. 720x1280, 81 frames at 16 fps. The chain itself is a job level loop run by the worker/app layer: render segment 1, take its last frame (or the next authored keyframe) as the next segment's start, repeat, then stitch. Extracting the last frame is a small ffmpeg step in the stitch stage, not a node in this file.

## What is NOT in these JSONs

The post chain from the product spec (frame interpolation, upscale, color match), the audio generation (music and TTS), and the final stitch are separate pipeline stages that run after a segment renders. They are TODOs in the worker, not nodes here. Keeping them out of the generation graph means a segment can be re-rendered without redoing post, and post can be redone without re-rendering.

## UNVERIFIED - check against a live ComfyUI before first real run

- **5B filename.** The guide lists `wan2.2_ti2v_5B_fp16.safetensors`. Whether the Comfy-Org repackaged repo ships that exact name, or a bf16/fp8 variant, is unverified offline. The loader will fail loudly if the name is wrong, which is the safe failure mode.
- **Sampler recipes.** Steps, cfg, sampler and scheduler values (5B: 20 steps, cfg 5.0, uni_pc/simple, shift 8.0. A14B: 20 steps split 10/10, cfg 3.5, euler/simple, shift 5.0) come from memory of the official ComfyUI templates. They are plausible starting points, not verified values. First live session: load the official template library versions side by side and diff every field.
- **WanFirstLastFrameToVideo input names.** `clip_vision_start_image` and `clip_vision_end_image` are the expected input names for the two CLIP Vision feeds. Verify against the live node's input list.
- **VHS_VideoCombine field set.** Field names (`frame_rate`, `filename_prefix`, `format`, `pingpong`, `loop_count`, `save_output`) follow the VideoHelperSuite convention. The node's output shape (a filename list) also needs confirming when the worker's output lookup is wired.
- **Frame rates after the retune.** All three templates now render at 16 fps and 81 frames, and the short-form post chain interpolates to 30 fps for delivery (see SHORTFORM.md). The draft was retuned from 121 frames at 24 fps to match that segment standard. Whether the TI2V-5B actually prefers its native 24 fps rate (121 frames for the same about 5 seconds) over 16 fps is UNVERIFIED; if the draft looks off in the first session, that is the first knob to A/B.
- **Resolution behavior, now vertical.** Both models are picky about dimensions. The retune uses 480x832 (5B) and 720x1280 (A14B): the same pixel counts as the guide's landscape sizes (1280x704 class and 1280x720), rotated to 9:16, and both divide cleanly by 16. Portrait orientation itself is UNVERIFIED on both models, since Wan demos and templates are usually landscape. The first test session renders vertical on purpose to settle it (see ../FIRST-TEST-RUNBOOK.md, pass criterion 6). Any other size a tier allows later must be tested, not assumed.
