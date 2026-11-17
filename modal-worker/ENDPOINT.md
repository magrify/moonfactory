# Sinclip generation API (Modal web endpoint)

The ComfyUI worker is deployed as the Modal app `sinclip-comfyui`
(workspace "magrify") with a FastAPI front door, so the Sinclip app can
dispatch real jobs instead of leaving them queued.

## Base URL

https://magrify--sinclip-comfyui-api.us-east.modal.run

Deployment dashboard: https://modal.com/apps/magrify/main/deployed/sinclip-comfyui

Redeploy after code changes:

    cd ~/workspace/sinclip/modal-worker
    ~/workspace/sinclip/modal-venv/bin/modal deploy app.py

## Auth

Every request needs the header:

    X-API-Key: <key>

The key is stored ONLY in:
- the Modal Secret named `sinclip-api-key` (env var `SINCLIP_API_KEY`), and
- `~/workspace/sinclip/modal-worker/api-key.txt` (mode 600) on this machine.

It is never printed in logs or docs. Requests with a missing or wrong key
get `401`.

## Endpoints

### POST /jobs — submit a generation job

Request JSON:

| Field | Type | Required | Notes |
|---|---|---|---|
| `lane` | string | yes | `"draft"` (Wan 2.2 TI2V-5B) or `"production"` (Wan 2.2 A14B, 720x1280) |
| `prompt` | string | yes | Positive prompt |
| `negative_prompt` | string | no | Defaults to the house negative prompt |
| `seed` | int | no | Defaults to the house seed |
| `start_image_png_b64` | string | no | Base64 PNG start image. On the draft lane, omit for a text-only job (the 5B model runs its text-to-video path). The production lane requires one (422 without). |
| `end_image_png_b64` | string | no | Base64 PNG end image, production lane only, and only with a start image (422 otherwise). The segment renders through the firstlast workflow (WanFirstLastFrameToVideo with the lane's distilled recipe), so the end image steers the render. |
| `aspect_ratio` | string | no | `9:16` (default), `16:9`, or `1:1` (422 otherwise). The worker renders natively at the chosen shape: draft 480x832 / 800x448 / 640x640, production 720x1280 / 1280x720 / 960x960. Nothing downstream crops or pads. |
| `segments` | int | no | 1 (default) to 12. Above 1 the job chains: each segment after the first starts from the previous segment's last frame, the final segment steers to the end image when one was given, and the segments are stitched (shared boundary frame dropped) into one file. If a segment fails after at least one completed, the completed segments deliver as one file with `partial: true` and `segments_done` set; segment 1 failing is a plain error. |
| `camera_pose` | string | no | One of `Static`, `Pan Up`, `Pan Down`, `Pan Left`, `Pan Right`, `Zoom In`, `Zoom Out`, `Anti Clockwise (ACW)`, `ClockWise (CW)`. Switches the render to the Fun Camera path (below). Production lane only, exactly 1 segment, no end image (422 otherwise). |
| `camera_speed` | number | no | Fun Camera move speed, 0.1 to 3.0, default 1.0 (422 outside). Only meaningful with `camera_pose`. |
| `effect` | string | no | One effect id from the effects table (below). Switches the render to the effects path: the record's lane becomes `"effects"`, a start image is required, exactly 1 segment, no end image, no `camera_pose` (422 otherwise). |
### Fun Camera path (camera conditioning, live 2026-11-16)

A job with `camera_pose` set renders through
`workflows/fun_camera_i2v.json` (Wan 2.2 Fun Camera UNET pair,
staged in the Volume as
`diffusion_models/wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors`
and the matching low-noise file, 15,301,408,048 bytes each). The
pose and speed patch the `WanCameraEmbedding` node; the move is
model conditioning, not prompt text, and the job record carries
`camera_pose`, `camera_speed`, and the measured `render_seconds`.

Recipe: the lane runs the LightX2V distilled patch, the same one
the firstlast workflow carries. The stock workflow is 20 steps at
cfg 3.5 (the long recipe, about $0.77 a segment on this model
set), far beyond a normal Production segment; the patch raises
the workflow's LightX2V LoRA pair from strength 0.0 to 1.0 and
sets the 4-step split (2 high-noise + 2 low-noise) at cfg 1.0.
Measured proof render (2026-11-16, Pan Left, balloon benchmark
scene, seed 12345): 177.8 s of worker wall, about $0.10 at the
L40S list rate, inside the Production lane's 15c worst case, so
the platform prices a Fun Camera segment as a Production segment.

Honest verification note: that proof render completed and
delivered a coherent clip, but frame measurement showed no
measurable pan (subject centroid and phase-correlation drift at
the first frame's values), weaker than the prompt-recipe Pan left
rendered alongside it. The loop-back test has since run
(2026-11-16, run 21): one stock-recipe render (20 steps, cfg
3.5, LoRA pair at strength 0.0, 1439.8 s of worker wall, about
$0.78) on the same benchmark measured the same way came out
indistinguishable from the distilled clip (centroid delta
+0.0006 of frame width, drift (0, 0)), and a debug pass over
the ported workflow found no wiring, pose-mapping, or speed
defect. Fun Camera conditioning strength is unproven on this
stack after both recipes; the distilled recipe above stays
shipped, because the stock recipe costs about $0.78 a segment
(about 24 minutes) and buys no measurable move.

### Effects path (one-tap effects, live 2026-11-16)

A job with `effect` set renders through `workflows/effects_i2v.json`
(built from the Freefield effects app definition, MIT) on the
Wan 2.1 I2V 480p 14B base
(`diffusion_models/wan2.1_i2v_480p_14B_fp8_scaled.safetensors`,
16,401,356,938 bytes in the Volume) plus ONE effect LoRA. The
job's prompt is the subject the effect acts on; the worker builds
the full prompt from the effect's model-card template (trigger
words included) and stores it on the record as `effect_prompt`.
The record also carries `effect` and the measured
`render_seconds`. Native render dimensions: 480x832 (9:16),
832x480 (16:9), 624x624 (1:1), 81 frames at 16 fps. The terminal
node is the house VHS combine the other lanes use, so output
handling is identical; the app's own CreateVideo/SaveVideo pair
was not carried over.

Effect ids (LoRA file in the Volume under `loras/`): angry,
crying, laughing, puppies, steam, money-rain, cakeify, crush,
deflate, electrify, explode, inflate, rotate, squish, orbit-360,
arc-shot, bullet-time, car-chase, crane-down, crane-over-head,
crane-up, crash-zoom-in, dolly-zoom, hero-run. The exact
id-to-LoRA mapping and prompt templates live in the `EFFECTS`
table in modal-worker/app.py.

Recipe: the effects app's fast preset. The stock recipe is
20 steps at cfg 6 (uni_pc); the fast preset raises the LightX2V
2.1 step-distill LoRA
(`loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors`,
738,005,744 bytes, sha256-verified against the Kijai/WanVideo_comfy
repo listing when staged) from strength 0.0 to 1.0 and runs
6 steps of euler at cfg 1.0. Measured proof renders
(2026-11-16, Inflate, balloon benchmark, seed 12345): 101.5 s
and 93.6 s of worker wall, about $0.05 at the L40S list rate,
inside the Production lane's 15c worst case, so the platform
prices an effect as a Production segment. The app's manifest
warns the fast preset can weaken an effect; on the proof render
the measured action was strong (subject area grew about 9.4x
first frame to last, centroid steady).

Response `202`:

    {"job_id": "026009aee5f54a4a", "status": "queued"}

Errors: `401` bad key, `422` unknown lane, missing prompt, bad
aspect, segments out of range, an end image without a start image
or on the draft lane, or a production job with no start image.

### GET /jobs/{job_id} — job status

Response `200`:

    {"job_id": "...", "lane": "draft", "prompt": "...",
     "status": "queued|running|done|error",
     "error": "...", "bytes": 12345, "created_at": 1763000000.0,
     "aspect_ratio": "9:16", "segments_total": 1, "segments_done": 1,
     "partial": false}

(`error` and `bytes` are null until relevant.) Unknown id: `404`.

### GET /jobs/{job_id}/file — download the finished video

`200` with `Content-Type: video/mp4` once the job is `done`.
`409` while queued/running/error, `404` for unknown ids.

Finished files are also persisted in the Modal Volume `sinclip-models`
under `api_jobs/<job_id>.mp4`.

### POST /image-jobs — submit a start-image generation job (Pass 3)

Text-to-image for a video's first frame. The workflow is
`workflows/z_image_t2i.json` (built from the Freefield z-image app
definition; model files staged in the Volume:
`diffusion_models/z_image_turbo_int8_convrot.safetensors`,
`text_encoders/qwen_3_4b_fp8_mixed.safetensors`, `vae/ae.safetensors`).

Request JSON:

| Field | Type | Required | Notes |
|---|---|---|---|
| `prompt` | string | yes | The frame description (422 when empty) |
| `aspect_ratio` | string | no | `9:16` (default), `16:9`, or `1:1` (422 otherwise). Rendered at the production house shape for the aspect: 720x1280, 1280x720, 960x960. |
| `seed` | int | no | Defaults to the house seed |

Response `202`: `{"job_id": "...", "status": "queued"}`. Same
`X-API-Key` auth and jobs Dict as the video endpoints. Errors:
`401` bad key, `422` missing prompt or bad aspect.

### GET /image-jobs/{job_id} — image job status

Response `200`:

    {"job_id": "...", "lane": "image", "prompt": "...",
     "status": "queued|running|done|error", "error": "...",
     "bytes": 12345, "created_at": 1763000000.0,
     "aspect_ratio": "9:16", "gpu_seconds": 64.3, "exec_seconds": 24.1}

`gpu_seconds` is the measured wall of the worker call (cold start
included, the convention the video benchmarks use); `exec_seconds`
is the ComfyUI execution alone. Unknown id (or a video job id):
`404`.

### GET /image-jobs/{job_id}/file — download the finished image

`200` with `Content-Type: image/png` once the job is `done`.
`409` while queued/running/error, `404` for unknown ids. Finished
images persist in the Volume under `api_jobs/<job_id>.png`.

Measured cost (2026-11-16, one proof render, 9:16): 64.3 s wall on
L40S = $0.0349 at the $0.000542/s list rate; the platform prices
the lane from this measure (see platform/pricing.py).

## How it works

`POST /jobs` records the job in the Modal Dict `sinclip-jobs` and spawns
the `run_job` function, which builds the lane workflow, calls the same
`ComfyUI.run_workflow` method the local test runs use, stores the mp4 in
the Volume, and updates the job record. GPU is the L40S class (see
STATUS.md for why A10G was dropped). A draft job takes roughly 6-13
minutes end to end; a production job roughly 25 minutes.

## Verification (2026-11-16)

Smoke test run from inside Modal (`modal-worker/smoke_app.py`), because
this sandbox's egress proxy stalls POST requests to `*.modal.run` (GETs
pass; POSTs hang at the transport level — an environment quirk, not an
endpoint fault). Results:

- POST without key: 401
- POST with key, missing prompt: 422
- POST with key, unknown lane: 422
- GET unknown job: 404
- POST valid job: 202 with a job_id; the job then transitioned
  queued -> running -> error, carrying exactly the expected error (the
  test deliberately sent an invalid base64 start image so the
  dispatcher failed before any GPU call — zero GPU spend), and
  GET /jobs/{id}/file on that job returned 409.

The one leg not exercised by the smoke test is the GPU render itself;
it is the identical `run_workflow` call proven by every local
`modal run` render in this project (draft, production, post chain).

## Known limits

- No job cancellation endpoint yet.
- One job at a time per GPU container; Modal scales containers under
  parallel load, each cold start re-loads models (~1-3 min).
- Job records live in a Modal Dict; finished files live in the Volume.
  Neither is a user-facing database — the Sinclip app should mirror job
  state into its own DB when it integrates.
