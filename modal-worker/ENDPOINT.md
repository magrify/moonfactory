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
| `start_image_png_b64` | string | no | Base64 PNG start image. Omit to use the lane's built-in test image |

Response `202`:

    {"job_id": "026009aee5f54a4a", "status": "queued"}

Errors: `401` bad key, `422` unknown lane or missing prompt.

### GET /jobs/{job_id} — job status

Response `200`:

    {"job_id": "...", "lane": "draft", "prompt": "...",
     "status": "queued|running|done|error",
     "error": "...", "bytes": 12345, "created_at": 1763000000.0}

(`error` and `bytes` are null until relevant.) Unknown id: `404`.

### GET /jobs/{job_id}/file — download the finished video

`200` with `Content-Type: video/mp4` once the job is `done`.
`409` while queued/running/error, `404` for unknown ids.

Finished files are also persisted in the Modal Volume `sinclip-models`
under `api_jobs/<job_id>.mp4`.

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
