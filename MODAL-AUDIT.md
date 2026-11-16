# Modal usage audit — Moonfactory (2026-11-16)

Ordered by Jack: "make sure you're not running stuff on the Modal that
you could be running in Muse browser or with cloudflare." Conducted
the same day he raised the build/test cap from $5 to $10 (cumulative
spend at audit time ~$4.03-$4.27).

Method: full read of `modal-worker/app.py`, `modal-worker/audio_app.py`,
and `platform/app.py`; a live `modal app list` check on 2026-11-16
confirmed exactly THREE deployed apps (`moonfactory`,
`sinclip-comfyui`, `sinclip-audio`). Everything else in the workspace
account list is a stopped, ephemeral `modal run` invocation. No
scheduled/cron functions exist anywhere in the codebase (grep for
`schedule=` / `modal.Cron` / `Period(` across all three files: zero
hits). No GPU ran for this audit.

## Headline verdict

Modal usage is already disciplined. Every GPU item genuinely needs a
GPU. Every CPU item on Modal is either (a) the live platform service
itself, (b) sub-second glue physically bound to the Modal Volume that
holds the models and job files, or (c) one-time provisioning. The
dev/test work the local-first rule targets (stitching, muxing,
loudness, frame QA, test-image generation, the animated-shorts lane)
already runs on Muse's machine, with evidence below. Nothing was
found that is both clearly misplaced and safe to move without
touching live behavior, so this run moves nothing; the one real
hosting question (the platform's CPU home) is costed below as Jack's
call, with a recommendation to keep it where it is for now.

## Inventory

### App `sinclip-comfyui` (modal-worker/app.py) — the generation worker

| Item | Computes | GPU/CPU | Why on Modal | Verdict |
|---|---|---|---|---|
| `ComfyUI` class, `run_workflow` / `probe_nodes` (app.py:128-132, 202, 215) | Wan 2.2 draft (5B) and production (A14B, distilled 4-step) renders; RIFE interpolation and SeedVR2 upscale run as nodes inside the same ComfyUI graph | GPU, L40S, scaledown 300 s | Diffusion sampling plus the RIFE/SeedVR2 post stages are GPU work; models (55+ GB) live on the `sinclip-models` Volume the class mounts | KEEP |
| `api` ASGI app (app.py:556-557) | The worker's private HTTP front door: POST /jobs, GET status/file, X-API-Key auth | CPU, default 60 s scaledown | It is the dispatch surface for the GPU class and the `sinclip-jobs` Dict; per-request cost is milliseconds of CPU | KEEP |
| `run_job` (app.py:498) | Builds the lane workflow JSON (prompt/seed/prefix patching), decodes the start image, calls the GPU class, records the outcome | CPU, seconds per job | Orchestration glue inside the worker app; the platform talks to the worker's HTTP API, and this sits behind it. Moving it means redesigning the worker's API contract | KEEP |
| `store_job_file` / `read_job_file` (app.py:539, 548) | Write/read a finished mp4 to/from the Volume (`api_jobs/`) | CPU, sub-second | The Volume is Modal-side storage; these exist so job files survive containers. A few MB per job | KEEP |
| `fetch_output` (app.py:410) | Fallback retrieval of a persisted output from the Volume | CPU, sub-second | Same Volume binding; it is the recovery path that saved run 1's render when result delivery hung | KEEP |
| `download_models`, `download_prod_models`, `download_loras`, `download_seedvr2` (app.py:61, 311, 347, 382) | One-time pulls of model weights from Hugging Face into the Volume (~55 GB total across sets) | CPU, one-time | The destination is the Volume. The local alternative (download 55 GB to Muse's VM, then upload it back into the Volume through the same egress proxy) is strictly worse on time and bandwidth and buys nothing; these run once per model set, for pennies | KEEP |

### App `sinclip-audio` (modal-worker/audio_app.py) — the music stage

| Item | Computes | GPU/CPU | Why on Modal | Verdict |
|---|---|---|---|---|
| `generate_bed` (audio_app.py:110-112) | ACE-Step 1.5 (DiT-only) generation of a 10 s instrumental bed per (caption, seed); results cached on the Volume so repeats are free | GPU, A10G | ACE-Step is a diffusion model; on CPU it would take minutes per bed and Muse's VM has ~1.5 shared cores. GPU generation is exactly what Modal is reserved for under the standing rule. The platform calls it remotely only for Creator custom-audio jobs (platform/app.py:752); the house bed was generated once and is read from the Volume | KEEP |

### App `moonfactory` (platform/app.py) — the live platform

| Item | Computes | GPU/CPU | Why on Modal | Verdict |
|---|---|---|---|---|
| `api` ASGI app (platform/app.py:275-286) | The whole public service: landing/studio/gallery pages, accounts, sessions, quota and pricing enforcement, Stripe webhooks, job dispatch to the worker, and file delivery | CPU only. Settings: `max_containers=1`, `scaledown_window=240`, no `min_containers` (scale-to-zero), timeout 1200 | It is a live service for users, not dev compute; it cannot be hosted on Muse's local VM (shared ~1.5 CPU, no public ingress for user traffic). Full Cloudflare analysis below | JACK'S CALL (recommendation: KEEP on Modal now) |
| `_process_file` (platform/app.py:1082), called from `ensure_processed` (762) | Delivery-time ffmpeg: mux the music bed, loudnorm to -14 LUFS / -1.5 TP (line 1092), and for free-tier files a libx264 re-encode to burn the watermark | CPU, seconds per delivered file (the watermark re-encode is the heavy case) | This is the platform's own request path: it runs where the service runs, at download time, once per file (result cached on the `moonfactory-data` Volume). It is not dev work that drifted onto Modal; the local-first rule's muxing/stitching items refer to the test pipeline, which is local (evidence below) | KEEP (part of the platform hosting question, not separable from it) |

### Not deployed / not on Modal (checked so the inventory is complete)

- `platform/smoke_app.py` (app `moonfactory-smoke`), `platform/gmail_smoke_app.py`
  (`moonfactory-gmail-smoke`), `modal-worker/smoke_app.py` (`sinclip-api-smoke`):
  ephemeral `modal run` test clients, never deployed. They run inside
  Modal for one documented reason, stated in each file's docstring:
  this sandbox's egress proxy stalls POSTs to *.modal.run while GETs
  pass, so a local client cannot drive the public API end to end. Each
  run is pennies of CPU and happens only when someone invokes it.
  Muse's browser cannot substitute: the smokes need scripted HTTP,
  proof-of-work solving, operator-outbox reads, and ffprobe checks.
  Verdict: KEEP as-is (dev tooling with a real environment constraint,
  zero idle cost).
- `animated-lane/`: the animated-shorts pipeline (Kokoro TTS in its own
  local venv, local audio/video work). No Modal imports anywhere in
  the lane. Already local, per the standing rule.
- `worker/handler.py`: the pre-Modal RunPod handler skeleton. Not
  deployed anywhere; historical.
- `modal-worker/make_test_image.py`: PIL test-image generation. Runs
  locally.
- `modal_auth_flow.py`: runs locally; only drives Modal's token flow.

## Local-first compliance evidence (the rule is already holding)

- Run 3's chain test (BUILD-LOG.md): the two A14B segments rendered on
  Modal; the stitch, seam QA, and all file handling ran locally, and
  the log says so explicitly.
- The first audio proof (run 2): ACE-Step generation on Modal; the
  mux and two-pass loudnorm onto the draft clip were done locally
  (`outputs/first-draft-test-audio.mp4`).
- The platform's per-delivery ffmpeg (above) is the one CPU workload
  that runs in a Modal container by design, because it is part of
  serving live users, not part of building or testing.

## The platform hosting question, costed (Jack's call)

**What Modal costs for the platform today.** The function
(platform/app.py:275-286) has no minimum containers and a 240 s
scaledown window, so at zero traffic it scales to zero and costs
**$0 idle**. Modal bills CPU/memory per second of actual use
(derived from Modal's own published FAQ example: 0.5 core + 256 MiB
for 1 h/day = $0.0257/day, i.e. roughly $0.047 per core-hour and
$0.008 per GiB-hour), and the workspace is on Starter, whose $30/mo
compute credit currently absorbs all of it. Realistic bounds:

- Quiet day (a few signups, jobs, downloads): a few cents of CPU,
  dominated by the delivery ffmpeg passes, not the API calls.
- Worst realistic case, one container held warm 24/7 at its minimum
  reservation (0.125 core, ~0.5 GiB): roughly $7/mo, still inside the
  Starter credit.
- The real cost of Modal hosting today is not dollars; it is cold
  starts (a few seconds on the first request after 240 s idle).

**What a Cloudflare port would actually require.** This is a rewrite,
not a move:

1. Workers and Pages Functions run JavaScript/TypeScript (Python
   Workers exist via Pyodide, but with no subprocess, no local
   filesystem, and a restricted stdlib). The FastAPI app, the Stripe
   Python SDK calls, and the `requests`-based worker polling all get
   rewritten against fetch and Stripe's REST API.
2. **ffmpeg does not exist on Cloudflare.** The delivery step
   (music mux + loudnorm + watermark re-encode) has no Workers
   equivalent. It would have to move back into the Modal worker
   (putting the CPU back on Modal), onto Muse's local machine
   (cannot serve live user downloads), or be redesigned away (for
   example, burning the watermark inside the ComfyUI graph). Any of
   these is a product change, not a hosting change.
3. State moves: the four Modal Dicts (users, jobs, meta, outbox) map
   naturally to D1 (SQLite) with some query rewriting; the
   `moonfactory-data` Volume file cache maps to R2; the house bed
   read from the `sinclip-models` Volume becomes an R2 object; the
   three Modal Secrets become Worker secrets.
4. Cost on Cloudflare: Workers Paid starts at $5/mo before any
   usage (includes 10M requests and 30M CPU-ms; D1 and R2 have
   their own free tiers). At today's traffic the port would cost
   about $5/mo flat to replace about $0 of marginal Modal cost.

**Recommendation: KEEP the platform on Modal.** There is no cost win
today (scale-to-zero plus the Starter credit beats a $5/mo floor),
and the port's ffmpeg blocker forces a redesign of the delivery step.
Revisit when one of these becomes true: traffic grows past what one
container serves well, a custom domain is wanted (Modal custom
domains are a Team-plan feature; note Cloudflare could also front
the existing Modal URL as a proxy later without moving any compute),
or the delivery pipeline is redesigned for other reasons.

## What this run moved

Nothing, deliberately. Every candidate was examined: the downloads
(their destination is the Volume), the Volume glue functions
(sub-second, storage-bound), the smoke clients (blocked locally by
the egress proxy, a constraint documented in their own files), the
dispatcher (worker API contract), and the platform's ffmpeg (the
live delivery path, owned by the platform run and inseparable from
the hosting question above). Forcing a move for its own sake would
either break live behavior or spend more than it saves. The audit's
actionable output is the inventory itself plus the platform verdict:
GPU work is on Modal because it is GPU work, and the only CPU of any
size on Modal is the live service, at ~$0 idle and cents per day.
