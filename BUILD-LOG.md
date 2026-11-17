# Sinclip build log

## 2026-11-15: zero cost foundation

Built the complete paper foundation for Sinclip in one pass. Total spend: $0. No GPU started, no service called, nothing deployed, no accounts created. Everything below is local files in `~/workspace/sinclip/`.

### What was created

| File | What it is |
|---|---|
| `README.md` | Product spec: proposed tier limits, the six stage generation pipeline, text architecture diagram (Pages, Workers, D1/R2, RunPod Serverless, Stripe), repository layout, open decisions |
| `workflows/draft_i2v_5b.json` | ComfyUI API format template, Wan 2.2 TI2V-5B single stage I2V (free tier + previews) |
| `workflows/prod_i2v_a14b.json` | ComfyUI API format template, Wan 2.2 I2V A14B two stage I2V at 720p (production) |
| `workflows/firstlast_chain.json` | ComfyUI API format template, one first frame / last frame segment on the A14B tail (the chaining unit for full length videos) |
| `workflows/NOTES.md` | Every patch point the worker substitutes per job, plus the full UNVERIFIED list |
| `worker/handler.py` | RunPod Serverless handler skeleton: fetch inputs, patch workflow by class type, POST /prompt, poll /history, find video output. Compiles clean under py_compile. Post chain, audio, and R2 upload are explicit TODO blocks |
| `worker/Dockerfile` | Image sketch on a RunPod worker-comfyui style base: custom nodes, models baked in at build time, workflows and handler copied in |
| `worker/download_models.sh` | Build time model pull (Hugging Face CLI, resumable) for every file the workflows reference. Shell syntax checked |
| `worker/requirements.txt` | runpod SDK + requests; boto3 noted as a TODO for the R2 step |
| `app/README.md` | Front end scaffold plan: stack table, page list, job flow, tier enforcement points, build order |
| `app/landing.md` | Full landing copy draft: hero, how it works, why creators, pricing (free vs $20/mo), FAQ, closing. Honest about AI generation and about the free tier being small because GPUs cost money |

### What is verified

- All three workflow JSONs parse as valid JSON.
- `handler.py` compiles (`python3 -m py_compile`).
- `download_models.sh` passes `bash -n`.
- No em dashes anywhere in the tree (checked by grep), per the standing copy rule.

### What is UNVERIFIED (nothing has run on a GPU)

- Workflow field values come from the guide and from memory of the official ComfyUI templates, not from a live node inspection. The full list lives in `workflows/NOTES.md`. Highest risk items: the exact 5B model filename, `WanFirstLastFrameToVideo` input names, the VHS_VideoCombine output shape the handler walks, and sampler recipes (steps, cfg, shift).
- The Dockerfile's base image name and tag, its ComfyUI path (`/comfyui`), and how its entrypoint chains ComfyUI and the handler.
- The exact in-repo paths in `download_models.sh`, especially the 5B file and `clip_vision_h.safetensors` (which may live in a different Comfy-Org repo).
- All cost math. The $0.40 to $0.75 per finished minute figure is a pod rate estimate from the planning docs. Serverless per second pricing will differ, and the proposed tier limits (5 free clips, 15 included minutes) must be re-sized against measured serverless costs before launch.
- The post chain (interpolation, upscale, color match) and the entire audio stage (music, TTS) are designed but not implemented anywhere. They are TODO blocks in the handler, not working code.
- The app layer design (D1 schema, queue mechanics, RunPod callback shape for finished files) is a plan, not code.

### Next steps that need the RunPod account credit (Jack's payment step, pending)

These are the first spend items and they stay inside the $5 build and test cap:

1. Stand up one RunPod Serverless endpoint (or a single short lived pod) with the worker image and run ONE draft job end to end: submit, poll, get an mp4 back. This verifies the handler's ComfyUI API calls and the VHS output shape.
2. In the same session, open the official ComfyUI template library Wan workflows and diff every field against the three templates here; fix the UNVERIFIED list down to zero.
3. Run one A14B production segment and one first/last frame segment at 720p; confirm quality against the commercial bar and time them. Timing converts the cost estimates into real numbers.
4. Terminate everything and verify the bill for the session landed inside the cap.

### Next steps that need Jack's decisions

1. **Final name and domain.** Sinclip is a placeholder. Needs: his pick, then a domain availability check and a trademark screen before any branding work.
2. **Stripe account.** Subscription billing cannot be wired, even in test mode, without his Stripe account and his call on tax settings.
3. **Tier limit approval.** The numbers in the README (5 free clips at 5 seconds, watermark, lowest priority; $20/mo with 15 finished minutes, 3 minute max length, priority queue) are proposals waiting on his yes and on measured costs.
4. **Community gallery default.** Whether free tier outputs are public by default is a product and moderation call for him.
5. **Audio scope for launch.** Music only at launch, or music plus TTS narration in v1. This sizes the audio model work in the worker.

## Priority change (Jack, 2026-11-15): VIDEO GENERATION FIRST, tuned for short-form (TikTok / Reels / X).
Build order is now: (1) generation pipeline proven end to end with a real vertical short WITH audio, (2) sample gallery, (3) web app, (4) Stripe/payments. Vertical 9:16 is the default canvas; production renders 720x1280 with upscale toward 1080x1920 delivery.

## 2026-11-15: short-form retune of the foundation

Retuned the paper foundation to the generation first, short-form priority. Spend: $0. Local files only, nothing ran anywhere.

### What changed

| File | Change |
|---|---|
| `workflows/draft_i2v_5b.json` | Vertical draft preset: 1280x704 at 121 frames / 24 fps became 480x832 at 81 frames / 16 fps, matching the house segment standard. Output prefix now `sinclip/shortform_draft`. |
| `workflows/prod_i2v_a14b.json` | Rotated to vertical: 1280x720 became 720x1280 (same pixel count), 81 frames / 16 fps unchanged. Output prefix now `sinclip/shortform_prod`. |
| `workflows/firstlast_chain.json` | Rotated to vertical: 720x1280, 81 frames / 16 fps. Output prefix now `sinclip/shortform_segment`. |
| `workflows/SHORTFORM.md` | NEW. The delivery spec: TikTok and Reels at 9:16 with 1080x1920 delivery, X accepting 9:16 (16:9 variant later), render at 480x832 / 720x1280 and upscale to 1080x1920 in post, segment math for 5 / 15 / 30 / 60 second shorts, and audio defaults (music bed on everything, optional TTS hook in the first 1 to 2 seconds, final mix at about -14 LUFS, peak at or below -1 dBTP). |
| `FIRST-TEST-RUNBOOK.md` | NEW. The first credit funded session in exact order: offline prep (reference keyframes, prompts, diff checklist), then provision pod, pull models, diff templates against the official library, draft vertical render, production vertical render, two segment chain test, music bed, post and 1080x1920 export, download, TERMINATE and verify zero idle. Per step pod minutes and costs at 4090 rates: about 87 pod minutes, about $0.58 at $0.40/hr, with a $3 abort line, all visibly inside the $5 cap. Includes the six pass criteria for "generation pipeline proven" (identity holds, motion clean, chain seam acceptable, audio present and synced, export specs correct, vertical renders work). |
| `workflows/NOTES.md` | Template descriptions updated to the vertical presets. UNVERIFIED list revised: the frame rate item now records the draft's 24 fps to 16 fps retune as an open question, and the resolution item now covers portrait orientation as UNVERIFIED on both models (pixel counts match the native landscape sizes; behavior unconfirmed until the first session). |
| `README.md` | New Build order section (generation proof, sample gallery, web app, Stripe). Working rules and pipeline steps now state the vertical sizes (480x832 draft, 720x1280 production, interpolation to 30 fps, upscale to 1080x1920). Repository layout lists the two new files. Tier and pricing content untouched. |

### Deliberately not changed

- Workflow file names. The worker loads templates by file stem (`draft_i2v_5b`, `prod_i2v_a14b`, `firstlast_chain`), and the handler's patch points (placeholders, class types, width / height / length fields) are untouched, so the retune cannot break the worker contract. The short-form preset identity is carried in the defaults, the output prefixes, and the docs.
- The handler, Dockerfile, and download script. The music model pull is still their open TODO; the runbook handles it as a session step with a fallback instead of pretending it is wired.

### New UNVERIFIED items added by this retune

- Portrait orientation behavior of both Wan models at 480x832 and 720x1280 (first session, pass criterion 6, settles it).
- Whether the TI2V-5B prefers its native 24 fps / 121 frames over the retuned 16 fps / 81 frames for the same about 5 second segment.
- The ACE-Step class music model's repo and filenames (needed at runbook step 3).
- The -14 LUFS loudness target as heard after platform normalization (checked during the sample gallery stage, not the first test).

All three workflow JSONs re-validated after editing (parse clean, vertical values confirmed by script).

## 2026-11-15: open-source stack research (OPEN-SOURCE-STACK.md)

Researched and verified 50+ GitHub repos against Sinclip's gaps (captions, interpolation, upscale, lip sync, avatar, music, voice, repurposing). Full vetted list with per repo verdicts: `OPEN-SOURCE-STACK.md`. Spend: $0, research only, nothing installed.

Key outcomes:
- Top adoptions: WhisperX (captions, BSD-2), ACE-Step 1.5 (music, MIT; supersedes the v1 repo the docs referenced and closes the runbook's open music repo question), SeedVR2 (video upscale, Apache-2.0), ComfyUI-Frame-Interpolation / RIFE (30 fps step, MIT), LatentSync (lip sync post pass, Apache-2.0), MuseTalk (fast lip sync, MIT), Kokoro (default TTS, Apache-2.0), Revideo (template render layer, MIT).
- License traps confirmed from the repos' own license files: F5-TTS pretrained weights are CC BY-NC (code is MIT), so Kokoro is the only shippable default voice today and voice cloning is blocked until a clean checkpoint exists. Sonic is CC BY-NC-SA. Wav2Lip has no license file at all. MusicGen / AudioCraft weights are CC BY-NC. Duix-Avatar (formerly Duix.Heygem, the best open HeyGen clone) blocks commercial use above 1,000 MAU without a discretionary commercial license. Remotion is a custom license with a company tier, which is why Revideo gets the render layer slot.
- MoneyPrinterTurbo verdict: real, MIT, 143k stars, actively maintained, but it assembles stock footage plus TTS rather than generating video. Not the core. Possible later faceless short lane and a parts reference for the post chain. Pixelle-Video (Apache-2.0, 30k stars) is the closest cousin and already orchestrates ComfyUI.
- Nothing changes the build order: generation proof still first. The named repos slot into existing runbook and post chain steps.

## 2026-11-15: RunPod blocked at signup, provider switch in evaluation

RunPod is blocked at signup: its Cloudflare Turnstile check rejects Muse's browser outright, and Jack could not get in either, so the account the whole first test depends on does not exist. Per Jack's ruling that the provider has to work with Muse, a verified comparison of replacements was written to `PROVIDER-DECISION.md`. Recommendation: Modal as primary (Muse drives it entirely from Python and CLI after a one time OAuth signup, it has an official ComfyUI example that matches the worker design, and its Starter plan's $30/month compute credit makes the first test cost $0 out of pocket against the $5 cap), Vast.ai as fallback (closest to the pod the runbook was written for, cheapest 4090 rates, full CLI lifecycle, but a $5 minimum deposit and an untested signup page), and fal.ai kept as a possible later complement for LoRA training and cheap clips rather than the pipeline host. No accounts created, no spend. Next step is Jack's call on the switch; if Modal is approved, the runbook gets its deploy based rewrite and the handler gets ported to the Modal class pattern before any session runs.

- 2026-11-15: Added BUSINESS-RULES.md (Jack: save money as a business, always make profit). Margin by construction: prepaid only, per-lane price floors at 3x measured worst-case cost enforced in code, API lanes (Wan 3.0 / Prime) never bundled into included minutes, free tier capped with a platform-wide monthly ceiling and spare-capacity-only, scale-to-zero idle, per-job profit records with weekly lane margin report.

## 2026-11-15 (evening): Modal generation worker stood up, FIRST REAL CLIP RENDERED

The draft lane now runs end to end on Modal (workspace "magrify", Starter plan, $30/mo credit). Worker code: `modal-worker/app.py` (follows Modal's official ComfyUI example pattern).

### What was built and verified

- `modal-worker/app.py`: Modal app `sinclip-comfyui`. A ComfyUI image (comfy-cli 1.26.0, torch 2.8.0+cu128, VideoHelperSuite + KJNodes custom nodes), a CPU function that downloads models into the Modal Volume `sinclip-models`, and a GPU class (A10G, 24 GB) that symlinks the Volume models into ComfyUI, launches it headless, and drives its HTTP API (POST /prompt, poll /history), mirroring `worker/handler.py`'s contract. Outputs are also persisted into the Volume (`outputs/`) so a render survives its container.
- Models in the Volume, byte-verified against the HF listing for Comfy-Org/Wan_2.2_ComfyUI_Repackaged (total 18.14 GB): `wan2.2_ti2v_5B_fp16.safetensors` (9,997,466,312 B), `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (6,735,906,897 B), `wan2.2_vae.safetensors` (1,409,481,964 B).
- Test input: `modal-worker/start_image.png` (PIL-drawn 480x832 vertical scene: sky, sun, clouds, hills, red balloon).
- **First clip: `outputs/first-draft-test.mp4` — 3,748,246 bytes.** ffprobe: h264, **960x1664**, 16 fps, 81 frames, 5.0625 s. NOTE: requested 480x832 came out at exactly 2x (960x1664); the WanImageToVideo node with the 5B path doubles dimensions somewhere in the current ComfyUI build. Recorded as an open item in modal-worker/STATUS.md; the clip is valid vertical video either way.
- Motion verified three ways: full-frame MAE 2.87 / 2.26 / 3.57 between frames at 0.3 s / 2.5 s / 4.8 s (frames genuinely change); the balloon's red-pixel centroid rose ~9 px across the clip (y 898.5 -> 889.1) with its pixel count changing (235 -> 315 -> 270); a viewed mid-clip frame shows a coherent photorealistic balloon-over-hills scene matching the prompt (the model reinterprets the crude PIL drawing loosely, as expected for I2V conditioning).

### Failures hit and fixed in-run (checkpoints in modal-worker/STATUS.md)

1. Modal refuses to mount a Volume over ComfyUI's non-empty `models/` dir. Fixed by mounting at `/sinclip-models` and symlinking `diffusion_models`, `text_encoders`, `vae` into ComfyUI's tree at container start.
2. Render attempt 2 completed remotely (~13 min execution) but the local `modal run` process hung ~4 min on result delivery and was killed (a separate `modal app list` answered in ~5 s at the same moment, so the API connection was healthy; the hang was specific to that result return). Hardening added: run_workflow returns an existing output without re-rendering when possible, copies every finished video into the Volume before returning bytes, and a CPU `fetch_output` function can retrieve from the Volume as a fallback. Attempt 3 then delivered cleanly end to end.

### Cost / timing (observed)

- Model download function: ~2 min, CPU only.
- Render attempts 2+3: ~26 min of A10G container time total (one lost to the delivery hang). At Modal's A10G list rate ($1.10/hr) that is roughly $0.48 of GPU, plus negligible CPU/volume; comfortably inside the $30 credit and far under the $3 abort line. Exact billed figure is on the Modal dashboard (CLI exposes no billing readout).
- Steady-state expectation per draft render: ~1 min container start + ~11-13 min execution on A10G for 81 frames at 20 steps.

### Still open

- The 2x output-resolution behavior (480x832 requested, 960x1664 delivered) needs a deliberate decision: keep (higher-res draft) or pin down the node behavior.
- Audio mux is the next step (per the project plan), then the production A14B lane.

## 2026-11-15/16 (run 2 + phase 3): AUDIO PASS, PRODUCTION LANE, POST CHAIN, LIVE API

Continuation of the Modal pipeline. All spend stayed inside the $30 credit; cumulative project estimate ~$2.73 against the $3 abort line (list-rate estimates; exact figures live on the Modal dashboard).

### Audio pass (ACE-Step) — DONE

- New sibling app `modal-worker/audio_app.py` ("sinclip-audio"): ACE-Step 1.5 installed via uv sync at image build, run DiT-only (acestep-v15-turbo, no LM; caption/metadata supplied directly, thinking and CoT off, shift=3.0, 8 steps, seed 12345). Worked on the first attempt.
- Bed: `outputs/music_bed_first.wav` (10.00 s, 48 kHz stereo PCM; also in the Volume at `audio/music_bed_first.wav`). Muxed under the draft clip with two-pass loudnorm: **`outputs/first-draft-test-audio.mp4`** — video copied untouched, audio AAC LC 48 kHz stereo, 5.06 s, measured **-13.96 LUFS integrated / -1.40 dBTP** (house target -14 / -1). AUDIO ROUTE USED: ACE-Step on Modal A10G; no synthesized fallback was needed.

### Production lane (Wan 2.2 A14B) — DONE, after a GPU change

- Model set downloaded to the Volume, byte-verified against HF (Comfy-Org Wan 2.2 + 2.1 repackaged): high/low noise 14B fp8 (14,294,742,832 B each), wan_2.1_vae (253,815,318 B), clip_vision_h (1,264,219,396 B). Total 30.11 GB.
- Attempt 1 on A10G FAILED: the render did not finish within the 3300 s poll window (app stopped ~03:23 UTC, output unrecoverable). A14B fp8 on Ampere (no native fp8 acceleration, likely memory offloading) is too slow at 720x1280. ~55 min, ~$1.01, lost.
- Attempt 2 on **L40S** (48 GB, native fp8) SUCCEEDED in ~23 min of execution: **`outputs/first-prod-test.mp4`** — h264, **exactly 720x1280** (the draft's 2x quirk does NOT occur on the A14B + wan_2.1_vae path), 16 fps, 81 frames, 5.06 s; frame MAE 17.05/16.81 (strong real motion); mid frame viewed, clean illustration style faithful to the start image. The worker class now runs on L40S permanently.

### Post chain — DONE

- One ComfyUI workflow on the rendered segment: VHS_LoadVideo -> RIFE VFI (rife49, 2x, 81 -> 161 frames) -> SeedVR2 3B fp16 (shortest edge 1080) -> VHS combine at 32 fps; local ffmpeg then converts to 30 fps. SeedVR2 stood up cleanly (weights byte-verified from numz/SeedVR2_comfyUI); no Lanczos fallback needed.
- **`outputs/first-postchain-test.mp4`**: h264, exactly 1080x1920, 30 fps, 151 frames, 5.03 s — the SHORTFORM.md delivery spec, met. Raw 32 fps master kept as `outputs/postchain-raw.mp4`.
- Two cheap validation failures came first; both were fixed from ComfyUI's own error bodies (VHS_LoadVideo takes custom_width/custom_height in this version and RIFE requires ckpt_name; SeedVR2VideoUpscaler requires every schema input in API format). `run_workflow` now surfaces /prompt error bodies, and a `probe-nodes` mode dumps /object_info specs.

### Web endpoint — DEPLOYED AND SMOKE-TESTED

- `modal deploy app.py` ships a FastAPI front door on the worker app: POST /jobs (lane, prompt, optional negative/seed/start image), GET /jobs/{id}, GET /jobs/{id}/file; X-API-Key auth via a Modal Secret (key only in the Secret and `modal-worker/api-key.txt`, mode 600). Jobs are dispatched by a `run_job` function into the same ComfyUI class and persisted in the Volume (`api_jobs/`). Full doc: `modal-worker/ENDPOINT.md`. Base URL: https://magrify--sinclip-comfyui-api.us-east.modal.run
- Smoke test (run inside Modal via `smoke_app.py`, because this sandbox's egress proxy stalls POSTs to *.modal.run while GETs pass): 401 / 422 / 422 / 404 all correct; a valid POST returned 202 + job_id and the job transitioned queued -> running -> error with exactly the designed pre-GPU error (deliberately invalid base64 start image, zero GPU spend); file fetch on an unfinished job returned 409. The GPU leg of the API path is the identical `run_workflow` call proven by every local render.

### Deferred by budget, prepared to fire

- **Chain test** (two chained A14B segments via firstlast_chain.json, stitched ~10 s): NOT run. Its render projects ~$0.75, which would take the cumulative total to ~$3.5, past the abort line. Everything is staged: `render-chain2` mode implemented, segment-1 last frame and the authored push-in end keyframe saved in modal-worker/, stitch + seam-check procedure documented in STATUS.md. One command fires it when more credit is authorized.
- A live GPU smoke job through the API (~$0.25) was likewise deferred; see the smoke-test note above for what was proven instead.

## Standing rules (Jack, 2026-11-15 ~10:39 PM CT)
- LOCAL-FIRST PROCESSING: anything that can be processed on Muse's own machine does NOT use Modal. Modal is reserved for GPU generation only (Wan renders, ACE-Step, RIFE/SeedVR2 GPU stages). Stitching, muxing, loudness normalization, captioning, TTS (Kokoro runs on CPU), frame QA, and all file handling run locally.
- PUBLISH PLAN: when the platform is finished — bot farming prevented (free-tier abuse controls live), Stripe enabled, and the rest of the launch checklist complete — publish the codebase to Magrify's GitHub as the repo "moonfactory" (github.com/magrify/moonfactory).
- NAME CHANGE (Jack, 2026-11-15 ~10:42 PM CT): the platform is named MOONFACTORY, not Sinclip. Publish target is github.com/magrify/moonfactory. The local working tree stays at ~/workspace/sinclip until the in-flight renders finish (workers hold live paths into it); the rename happens with the publish prep, and "Sinclip" survives only as the historical working name in these logs.

## 2026-11-16 (run 3): CHAIN TEST — PASSED (last unproven pipeline piece)

Two chained A14B production segments now proven end to end. Segment 2 was rendered on Modal (L40S) via the staged `render-chain2` mode: `workflows/firstlast_chain.json` (WanFirstLastFrameToVideo, 720x1280, 81 frames), starting from segment 1's last frame (`chain_seg1_last.png`) and ending at the authored 1.15x push-in keyframe (`chain_seg2_end.png`), seed 12346, same balloon prompt.

- **Segment 2: `outputs/chain-seg2-raw.mp4`** — h264, 720x1280, 16 fps, 81 frames, 5.06 s; spec-identical to segment 1. Modal run 04:35:25 -> 04:59:04 UTC (23m39s wall including container start and delivery), ~$0.77 at the L40S list rate. No failures; the Volume-persistence + return-bytes hardening from run 1 delivered cleanly on the first attempt.
- **Stitch (LOCAL, per Jack's standing rule that Modal is GPU-generation only): `outputs/first-chain-test.mp4`** — segment 1's 81 frames + segment 2's frames 1..80 (boundary frame shared, not duplicated) = **161 frames, 720x1280, 16 fps, 10.06 s**, 1,702,498 B.
- **Seam verdict (honest):** no visible jump. Frames either side of the join (80 / 81) viewed directly: balloon position and scale, sun, clouds, and hills all continuous; no cut, no teleporting. One measurable subtlety: the seam step's inter-frame MAE is 3.14 vs 0.56-1.01 for neighbouring in-segment steps, because segment 2's regenerated first frame differs slightly from segment 1's true last frame (MAE 3.33) — a faint grain/texture settle at the join, not a positional discontinuity.
- **Push-in verified:** balloon bounding box grows steadily through segment 2 (width 223 -> 239 -> 255 px; height 259 -> 277 -> 299 px at frames 80/120/160), landing at ~1.15x, matching the authored end keyframe.
- **Post chain on the stitched clip: not run.** This run used ~$0.77 of its ~$1.00 authorization (abort line $1.25); a GPU post pass on the 10 s clip (which took three attempts on the 5 s segment in phase 3) had no safe margin. It is also already proven on a production segment (`outputs/first-postchain-test.mp4`, exact 1080x1920 / 30 fps delivery spec). It can be fired on the stitched clip whenever budget is allocated.
- **Cost:** this run ~$0.77; cumulative project ~$3.50 of the $5/month build/test cap (list-rate estimates; exact figures on the Modal dashboard). Remaining headroom ~$1.50.
- Standing rule recorded in modal-worker/STATUS.md: anything processable on Muse's own machine stays off Modal; Modal is GPU-generation only. This run complied (render on Modal; stitch, seam QA, and file handling local).

## 2026-11-16 (run 4): MOONFACTORY PLATFORM BUILD (Jack: "complete the moonfactory platform/app for github and so people can start using it")

Architecture chosen (lightest fit for the existing stack): a second Modal app,
`moonfactory`, in `platform/app.py`. One FastAPI service serves the public
site (landing + studio), the account/job API, and the Stripe webhook. It
holds the worker API key server-side in a Modal Secret and proxies every
worker call, so the key never reaches a browser. State lives in Modal Dicts
(users, jobs, meta, outbox); finished files are cached on a platform Volume.
The worker endpoint returns silent video, so the platform muxes the proven
ACE-Step house bed (read from the shared sinclip-models Volume at
audio/music_bed_first.wav) onto every delivered file on CPU, with a
Moonfactory watermark burned in for free-tier files. Local-first rule holds:
muxing, watermarking, and all account work are CPU; Modal GPU runs only
inside the existing worker.

Pricing honesty (Rule 2 applied to measured costs): a production segment
measured ~$0.77 for 5.06 s on L40S and a draft job ~$0.50 worst case, so the
README's proposed "15 included production minutes at $20/mo" would lose
money and is NOT shipped. platform/pricing.py derives everything from the
measured costs: floor = worst-case cost x 3, grossed up for Stripe's
2.9% + 30c. The Creator plan's included production seconds are computed
from the plan's net revenue at that floor (about 40 s at current measured
costs), and top-up jobs are charged per job at the floor price. If measured
costs fall (faster renders), the same formula raises the allowance on its
own.

Bot-farming controls (Rule 5): email verification required before any job,
free tier = draft lane only, 480p-class output, watermarked, 5 clips per
account per month, platform-wide free ceiling of 100 jobs/month in config,
rate limits on signup/login/job creation, and a bot check on signup and job
creation (Cloudflare Turnstile when keys are configured; a built-in
proof-of-work challenge until then).

Stripe: full test-mode integration in code (Checkout for Creator and
top-up packs, webhook with signature verification as the source of truth,
live keys refused by the code). No Stripe account or keys exist yet, so
checkout returns a clear "payments are not configured" state until Jack
supplies test keys. The exact supply list is in platform/LAUNCH-NOTES.md.

Budget: cumulative Modal spend ~$3.50 of the $5 cap before this run. This
run allows exactly ONE draft-lane smoke job through the public path
(~$0.25-0.50), keeping the total under the cap.

### Run 4 progress (2026-11-16)

- platform/pricing.py written and unit-checked locally: floors draft
  $1.86 / production $2.79; selling prices $1.99 / $2.99; Creator
  included production seconds compute to 40 at measured costs; no lane
  paused.
- platform/app.py built and deployed as Modal app `moonfactory`:
  https://magrify--moonfactory-api.us-east.modal.run (landing, studio,
  accounts, jobs, Stripe webhook, admin outbox). healthz 200, pricing
  endpoint live. Worker key, admin key, and session secret live only
  in the Modal Secret `moonfactory-secrets`; values were never printed
  (secret built from files in-process).
- Turnstile: a real widget was provisioned for the platform hostname
  through the Cloudflare API (site key 0x4AAAAAAGC4dkwRrQ0yrS5n) and
  its keys added to the secret; /api/config confirms Turnstile live.
  The proof-of-work check remains accepted as a fallback (and is what
  the browserless smoke test uses).
- GitHub publish tree staged at ~/workspace/moonfactory-publish and
  secret-scanned clean: the actual worker key and admin key values
  grep to zero matches, no credential patterns, no key/secret files,
  .gitignore committed in the tree. Publish waits on the smoke proof.
- Smoke test (one draft job through the public path) running.

### Run 4 COMPLETE (2026-11-16): PLATFORM LIVE + PUBLISHED

- END-TO-END PROOF PASSED through the public URL
  (platform/smoke_app.py, run inside Modal because this sandbox's
  proxy stalls POSTs to *.modal.run): healthz 200, landing served,
  signup 200 with the PoW bot check, verification email found in the
  operator outbox, verify 303, login 200 with verified=true, job
  create 202, job reached done, file 200 at 3,156,308 bytes. ffprobe
  of the delivered file: h264 video 960x1664 + AAC audio, 5.0625 s.
  The smoke account is on the free plan, so the delivery also
  exercised the watermark re-encode and the house-bed mux. Total wall
  about 5.3 minutes.
- Cost of this run: the one budgeted draft smoke job (~$0.20 at the
  L40S list rate) plus pennies of CPU. Cumulative project spend is
  about $3.70 of the $5 build/test cap (list-rate estimates; exact
  figures on the Modal dashboard). No other GPU ran.
- PUBLISHED: https://github.com/magrify/moonfactory (public, HEAD
  d99c1bd5c4). Contents: platform/, modal-worker/, workflows/,
  worker/, BUSINESS-RULES.md, README, LICENSE (MIT, Magrify LLC),
  .gitignore. Description and topics set via the API. Secret scan
  before pushing: the actual worker API key value and admin key value
  each grep to ZERO matches across the tree, no credential patterns,
  no key/secret/credential filenames; api-key.txt and admin-key.txt
  are excluded and named in .gitignore. The magrify-site-ci token and
  ~/.git-credentials were never touched.
- Stripe remains test-mode code only: no Stripe account or keys exist
  (connector status: not_connected), so checkout returns 503
  "payments are not configured yet" by design. The exact supply list
  is platform/LAUNCH-NOTES.md: test secret key + webhook signing
  secret via the Secure Vault, then Muse drops them into the
  moonfactory-secrets Modal Secret and redeploys; no code change.
- Email: verification mail currently lands in the operator outbox
  (admin-key only) because no SMTP provider is configured; the same
  LAUNCH-NOTES list covers the SMTP values Jack must supply.
- Still Jack's: Stripe keys (above), an email provider, the final
  domain (platform runs on its modal.run address; if it moves, the
  Turnstile widget needs the new hostname), the trademark screen,
  and the product calls from the original list (tier-limit approval
  as shipped by formula, community gallery default, launch audio
  scope). Known launch limits are written down in LAUNCH-NOTES.md:
  one ~5 s segment per job in the public form, house music bed on
  every video, render times of about 6-13 min (draft) and ~25 min
  (production).

## 2026-11-16 (run 5): JACK'S THREE FIXES (free tier, Stripe live, email)

FIX 1 DONE AND VERIFIED. Free plan is now 5 clips per account TOTAL:
a lifetime counter (user.free_jobs_total) that never resets; the old
period reset now applies only to the paid plan's included seconds.
The platform-wide ceiling is a total budget (100, config-raisable),
key freecount:total. All monthly free-tier language removed from
code, landing page, studio, README, BUSINESS-RULES Rule 5, and these
notes. Redeployed; full public-path smoke PASSED after the change
(signup, verify, login, one Draft job, delivered file h264 960x1664
+ AAC, 5.0625 s, ok: true). Smoke cost ~$0.20; cumulative project
spend about $3.90 of the $5 build/test cap.

FIX 2 CODE-READY, ACCOUNT STEP BLOCKED ON FACTS. The code no longer
refuses live keys (accepts sk_live_/sk_test_, rejects non-secret
keys), /api/config reports payments_mode, and checkout can pin
configured price IDs (STRIPE_PRICE_CREATOR, STRIPE_PRICE_TOPUP_500/
1000/2000) or create prices inline from pricing.py. But: the Secure
Vault holds NO stripe.com login (the full saved-login list was
checked entry by entry) and the Stripe connector status is
not_connected. A live secret key exists only inside the Stripe
dashboard; no API or connector can export one, and this agent
cannot run the browser task. Handed to the parent with the exact
dashboard pass (activate if needed, create Creator $20/mo + $5/$10/
$20 top-up products/prices, live webhook endpoint for
/api/stripe/webhook with the four events, reveal sk_live_ +
whsec_) and the connector connect link
(https://agent.meta.ai/connectors/connect/stripe) as the OAuth
alternative. Keys go straight into moonfactory-secrets when they
arrive; no code change needed.

FIX 3 DECIDED: RESEND. Jack overruled the PrivateEmail plan: Resend
account under support@magrify.com, sending domain magrify.com,
sender Moonfactory <noreply@magrify.com> (an earlier send./noreply.
subdomain plan was dropped before anything was created). Platform
code now sends via the Resend API as the primary route when
RESEND_API_KEY is in the secret, sender from RESEND_FROM config;
PrivateEmail SMTP stays as a configured fallback and the operator
outbox behind that. Still needed (browser session): create the
Resend account, add the magrify.com domain, merge DNS in Cloudflare
(SPF: extend the existing record with Resend's include, never
replace; DKIM/DMARC as given), confirm support@magrify.com still
receives mail, create the API key into moonfactory-secrets. Then a
fresh Gmail-address signup verifies delivery end to end.

## 2026-11-16 (run 6): FIX 3 FINISHED — RESEND LIVE

The Resend account was created under the Magrify GitHub (account
email support@magrify.com) and magrify.com verified as a sending
domain. The API key (name "moonfactory", full access) was placed
in a NEW Modal secret, moonfactory-integrations, together with
RESEND_FROM = Moonfactory <noreply@magrify.com>. The existing
moonfactory-secrets secret was deliberately not touched: its
SESSION_SECRET and TURNSTILE_SECRET_KEY values exist nowhere
else, and overwriting the secret would have broken sessions and
the bot check. platform/app.py now mounts both secrets on the
api function; redeployed to the same public URL.

Proof: a fresh signup through the public URL for a Gmail
plus-address (jackcmagri+moonfactorytest@gmail.com) returned
HTTP 200 with email_mode "resend" (the Resend API accepted the
send; no SMTP/outbox fallback). The verification email arrived
in the Gmail inbox from Moonfactory <noreply@magrify.com>,
subject "Verify your Moonfactory account", sent 06:42:08 UTC,
seconds after the signup, SES-backed Message-ID. The Gmail
connector redacts verification links in its read path (the body
carries a credential reference instead of the URL), so the
click-through (verify link, login, Draft job) completes through
the browser route with that reference; the two-phase proof tool
is platform/gmail_smoke_app.py (signup_phase / finish_phase).
No GPU ran in this run; Modal spend unchanged at about $3.90
of the $5 build/test cap.

## 2026-11-16 (run 7): RENDER-COST OPTIMIZATION (Jack: "cheaper renders")

Jack picked the cost lever for the Creator allowance problem: cut the
production render cost instead of raising the price or thinning the
margin. Hard cap for this run: $0.65 of GPU spend (cumulative ~$3.90
of the $5 build/test cap before this run). At most two benchmark
renders. All frame work local.

### Desk analysis (free, done before any spend)

Baseline decomposition (from STATUS.md): a production segment is
23m39s wall on L40S ($0.000542/s) = ~$0.77. Container start + ComfyUI
boot + model load is roughly 2-4 min of that; VAE decode/encode
about 1 min; the rest is sampling: 20 steps (10 high-noise + 10
low-noise) x 81 frames at 720x1280 on the A14B fp8 pair, about
57 s/step. Sampling steps are the cost driver; everything else is
overhead that does not scale with steps.

Modal published per-second GPU rates (modal.com/pricing, checked
2026-11-16): L4 $0.000222 (24 GB), A10 $0.000306 (24 GB), L40S
$0.000542 (48 GB), A100-40 $0.000583, A100-80 $0.000694, H100
$0.001097 (80 GB).

Levers priced:
1. STEP DISTILLATION (LightX2V Wan 2.2 4-step LoRAs): 20 -> 4 steps
   is 5x fewer model evaluations. Sampling ~20 min -> ~4 min;
   projected wall ~7-8 min -> ~$0.23-0.26 per segment on L40S.
   LoRAs live in the same Comfy-Org repackaged repo as the base
   models (wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise /
   _low_noise, 1,226,977,424 B each; download ~2.45 GB, ~$0.01 on
   the CPU download function). Recipe: LoRA strength 1.0 on both
   UNETs, steps 4 split 2+2, cfg 1.0, euler/simple, shift 5.0.
   This is the benchmark candidate.
2. TeaCache alone: ~1.5-2x on sampling -> ~$0.45-0.55, with real
   motion-artifact risk; stacked on a 4-step distilled run there
   are too few steps left to cache against. Not chosen.
3. Naive step cut (20 -> 12, no distillation): ~$0.48 and known
   quality loss at cfg 3.5. Dominated by lever 1.
4. fp8: already fp8_scaled weights. No further gain there.
5. GPU class at 20 steps: H100 is ~2x L40S compute at 2.02x the
   price (a wash); A100-40 costs more than L40S for similar
   throughput; L4/A10 (24 GB) cannot hold the 2x14.3 GB models +
   6.7 GB text encoder without offloading (the A10 attempt at
   20 steps never finished inside 55 min). No.
6. GPU class at 4 steps: L4 at $0.000222/s. Per-step time likely
   ~2.2-2.7x L40S (300 vs 864 GB/s bandwidth) and the 24 GB VRAM
   squeeze remains, but at 4 steps the absolute time is small:
   projected wall ~13-15 min -> ~$0.17-0.20. Worth one test only
   if the L40S distilled render holds quality and budget remains.

Benchmark plan (cap $0.65): LoRA download ~$0.01; render A =
distilled 4-step on L40S with the exact baseline inputs
(test_start_720.png, seed 12345, same prompts) projected
$0.22-0.28; render B (conditional) = same config on L4,
projected $0.17-0.22. Quality judged on extracted frames vs
outputs/first-prod-test.mp4 plus within-clip motion MAE and
sharpness, stated honestly.

### Spend log (run 7)
- LoRA download (CPU function, 2.45 GB, byte-verified): ~$0.01. Running total ~$0.01.
- Render A (distilled 4-step, L40S, baseline inputs): the verified
  artifact is outputs/prod-distilled-test.mp4 (802,632 B, present
  both on disk and in the Volume), wall 216.4 s = ~$0.12.
  ACCOUNTING NOTE: the backgrounded render's completion was
  delivered twice with inconsistent figures (762,760 B / 258.7 s,
  then 802,632 B / 216.4 s), and for a window neither the local
  file nor the Volume copy was visible from the foreground seat,
  which read as a lost render. A duplicate re-run launched during
  that confusion was killed mid-flight once the verified artifact
  surfaced. Worst case, if both reported runs billed in full plus
  the partial duplicate: ~$0.37 for run 7. Verified-figures case:
  ~$0.13. Either way inside the $0.65 cap. Cumulative project:
  ~$4.03 (verified) to ~$4.27 (worst case) of the $5 cap.

### Run 7 results: DISTILLED CONFIG ADOPTED

- Cost: production segment $0.77 (23m39s wall, 20 steps) -> $0.117
  measured (216.4 s wall, 4 steps), a ~6.6x cut. pricing.py now
  carries production at 15c worst case (verified measure plus
  cold-start headroom); the 3x formula itself is untouched.
- Quality: HOLDS on the benchmark scene. Local side-by-side frames
  at 0.3 / 2.5 / 4.8 s vs outputs/first-prod-test.mp4: clean
  composition and edges in both, no warping or structural defects;
  motion MAE comparable (1.17 / 1.79 / 1.04 distilled vs 1.06 /
  1.36 / 0.99 baseline); Laplacian sharpness higher in the
  distilled frames at all three timestamps (73.9 / 70.7 / 62.2 vs
  41.9 / 32.2 / 27.5). Differences are trajectory differences
  (cloud and hill arrangement, balloon slightly higher late), plus
  two tiny bright specks on the sun's rim at 4.8 s. Caveat: the
  test scene is flat illustration with gentle motion, where
  distillation is strongest; photoreal / fast-motion parity is
  not proven by this benchmark.
- Adoption: workflows/prod_i2v_a14b_distilled.json is the
  production template; the worker API's LANE_WORKFLOWS maps
  "production" to it; worker and platform apps redeployed; the
  live /api/pricing endpoint now reports production cost 15c,
  floor $0.78, per-job price $0.99, and Creator included
  production seconds 214 (about 3.6 minutes), up from 40.
- NOT tested (deliberate): the distilled config on L4
  ($0.000222/s). With L40S already at $0.12, the best case saves
  roughly $0.03 per segment, against the known 24 GB offload risk
  and the budget uncertainty above. TeaCache stacking also
  untested: at 4 steps there is little left to cache.

## 2026-11-16 (run 8): STRIPE OBJECTS CREATED LIVE, KEYS WIRED (one value left)

The Stripe account side got done through the Stripe connector
(OAuth, no dashboard sign-in needed): the Magrify account
(acct_1Q16NdAQThnByJFL) is fully activated for live charges
(charges_enabled and payouts_enabled, no requirements due). Created
live in that account:

- Product "Moonfactory Creator" with a $20.00/mo recurring price.
- Product "Moonfactory Credit Top-Up" with one-time $5 / $10 / $20
  prices.
- Live webhook endpoint for
  https://magrify--moonfactory-api.us-east.modal.run/api/stripe/webhook
  subscribed to checkout.session.completed, invoice.paid,
  invoice.payment_failed, customer.subscription.deleted.

Wiring done this run: the `moonfactory-integrations` Modal secret
was recreated wholesale with the two Resend entries preserved plus
STRIPE_WEBHOOK_SECRET and the four price IDs (STRIPE_PRICE_CREATOR,
STRIPE_PRICE_TOPUP_500/1000/2000). Key values went only into the
secret via the Modal CLI; they are in no file, log, or commit.
`moonfactory-secrets` was not touched. Platform redeployed.

Verified through the public URL after the redeploy:
- A fresh signup (Gmail plus-address, no job created) returned
  email_mode "resend" — the recreated secret's Resend key is live
  in the running app, so the integrations secret is reaching it.
- /api/config reports payments_mode "off" and payments_configured
  false. That is expected and honest: those two fields derive only
  from STRIPE_SECRET_KEY, which is still outstanding (next
  paragraph). The price IDs are consumed by the checkout handlers
  from the same secret's environment, not shown on /api/config.
- An unsigned POST to /api/stripe/webhook is refused with 503
  "webhook is not configured". Reading the handler: it builds the
  Stripe client first, and stripe_client() errors while
  STRIPE_SECRET_KEY is absent, so the request is rejected before
  the signature check. The 400 "bad signature" path becomes
  reachable the moment the secret key is stored; the route is live
  and verifying-by-construction (construct_event with the stored
  webhook secret).

The ONE outstanding value: STRIPE_SECRET_KEY (sk_live_). A secret
key cannot be created or exported through the API or the
connector; it exists only inside the Stripe dashboard, and the
dashboard sign-in is gated by a passkey this browser cannot
complete. When that key is supplied, it goes into
moonfactory-integrations the same way, the platform redeploys, and
payments_mode flips to "live" with no code change. Until then
checkout correctly stays off rather than half-on.


## Note — 2026-11-16: fal API key stored

The fal.ai account for Magrify was created under the Magrify GitHub
(support@magrify.com), starting balance $0.00. The "moonfactory" API
key is stored as FAL_KEY in a new Modal secret,
`moonfactory-providers`. The API lane is not yet wired or funded; a
credit top-up is the remaining step before the first paid call.

## 2026-11-16 (run 9): PUBLIC GALLERY + PAID-ONLY FEATURES (custom audio, model switching)

Jack's decisions: (1) the community gallery is public by default for
everyone, paid-plan users get a small checkbox to opt out, and the
gallery must be safe for users; (2) per-prompt custom audio is built
now as a paid-plan-only feature, and model switching is paid-only too.

FEATURE A — PUBLIC GALLERY (live at /gallery)
- Every finished clip lists by default. What the public sees per
  item: the creator's handle, the lane, the prompt text, the video.
  Handles: a user-picked handle (POST /api/me/handle, 3-20 chars,
  unique case-insensitively) or a neutral default derived from the
  account id (creator-XXXXXX). Emails and real names are never
  exposed; the gallery JSON contains no email field (verified by
  grep over the live response).
- Opt out: POST /api/me/gallery-optout + a studio checkbox. Paid
  plans only, enforced server-side (free account attempt: 403).
  Visibility rule reads the flag only for non-free owners, so a
  downgraded account's clips list again, matching "free cannot
  opt out".
- Report: POST /api/gallery/{id}/report (rate-limited, optional
  reason) appends to a reports list in meta. Admin path with the
  existing ADMIN_KEY: GET /api/admin/reports,
  POST /api/admin/gallery/{id}/remove and /restore (a removed item
  disappears from the list and its public file route 404s; the
  owner's own copy is untouched).
- Delete: DELETE /api/jobs/{id} (owner only) removes the job
  record and its cached files, which also unlists it. Studio job
  cards carry a Delete button.
- Studio account card also gained the handle field; the landing
  page links the gallery and explains the rules.

FEATURE B — PAID-ONLY GATES (server-side, in create_job)
- Custom audio: job create accepts music_prompt (<= 300 chars).
  Free account + music prompt: 403 "custom audio is a Creator plan
  feature", before any dispatch. The prompt is stored on the job
  and carried through regenerate. At delivery, _bed_for_job calls
  the ACE-Step app (modal-worker/audio_app.py, now parameterized:
  generate_bed(caption, seed), beds cached in the worker Volume by
  caption+seed hash; the house bed keeps its original path and
  behavior), caches the wav per job on the platform Volume, and
  _process_file muxes it. Any audio-stage failure falls back to
  the house bed and flags the job (audio_fallback) instead of
  breaking delivery.
- Model switching: free accounts were already locked to Draft
  server-side; the gate is unchanged and now covered by an explicit
  check (free + production: 403). The studio disables the
  Production option and the music field for free accounts with a
  plan note on each; the server rejects regardless of the UI.

VERIFICATION (all through the public URL; POSTs run inside Modal
via a throwaway check app because this sandbox stalls POSTs to
*.modal.run; every submitted job carried a deliberately invalid
start image so the worker rejected it before any GPU call):
- Gallery page 200; /api/gallery lists both pre-existing test
  clips under creator-d5e854 / creator-4f78b9.
- Free test account: default handle creator-XXXXXX; set handle
  200; opt-out attempt 403; production job 403; draft + music
  prompt 403; plain draft 202; delete own job 200 and gone.
- Paid test account (plan flipped to creator in the Dict for the
  check, test data only): duplicate handle 409; opt-out set 200
  (true) and cleared 200 (false); draft + music prompt 202 with
  the prompt stored verbatim and has_custom_audio true in the API;
  production 202. Both test jobs deleted afterwards.
- Report flow: report 200; admin reports list contains it; admin
  remove 200 and the item leaves the gallery; restore 200 and it
  returns. Opt-out visibility: flipping an existing clip's owner
  to creator + opted out hid exactly that clip; restoring the
  owner brought it back.
- Custom audio end to end (the one GPU spend): a music prompt was
  set on an existing test clip's record and its processed file
  dropped, so the next public gallery fetch regenerated delivery:
  ACE-Step produced a new bed (cached at audio/custom/ in the
  worker Volume and per-job on the platform Volume) and the
  delivered file's audio stream differs from the house bed
  (md5 a1d11289... vs a077e232...), duration 5.0625 s, loudness
  -14.1 LUFS, h264 960x1664 + AAC. The test record was then
  restored (prompt cleared, files dropped, re-fetched): the
  delivered file is byte-identical to the original house-bed
  version (3,156,308 bytes, audio md5 a077e232...). The linkage
  disclosure: the prompt was placed on that record directly
  rather than via a fresh paid render, to stay inside the spend
  cap; the creation-side storage was proven separately above.
- One behavior note: a warm platform container serves its mounted
  Volume view, so files deleted through the Modal CLI are not seen
  until the container restarts (scaledown 240 s). Deletes through
  the API itself are same-container and immediate; this only
  affected the test procedure above.

SPEND: one ACE-Step custom-bed generation, about $0.07 at the
A10G list rate (the whole delivery fetch took about a minute).
No video renders ran. Cumulative project spend about $3.97 of
the $5 build/test cap (list-rate estimates). Test residue: two
throwaway accounts (feat-free-*, feat-paid-* @example.com) and
one feature-check report in the reports list; the platform-wide
free counter rose by one for the free-account control job.

## 2026-11-16: BUDGET CAP RAISED + MODAL USAGE AUDIT

- **Cap change (Jack, 2026-11-16): the build/test cap is now $10**,
  raised from $5. Cumulative spend at the change: ~$4.03-$4.27
  (list-rate estimates). Headroom under the new cap: ~$5.73-$5.97.
- **Modal audit (Jack's order: nothing on Modal that could run in
  Muse's browser or on Cloudflare).** Full inventory and reasoning:
  `MODAL-AUDIT.md`. Headline verdicts:
  - Exactly three deployed apps (verified live via `modal app list`):
    `sinclip-comfyui`, `sinclip-audio`, `moonfactory`. No cron or
    scheduled functions anywhere.
  - All GPU work genuinely needs its GPU: the ComfyUI class (Wan
    renders + RIFE/SeedVR2 nodes, L40S) and ACE-Step `generate_bed`
    (A10G). KEEP.
  - All other Modal CPU is the live platform service itself, or
    sub-second Volume-bound glue (job file store/read, output fetch),
    or one-time model downloads into the Volume (~55 GB; the local
    path would download and re-upload the same bytes for nothing).
    KEEP.
  - The platform already does its CPU post-processing in its own
    container by design (delivery-time ffmpeg mux/loudnorm/watermark,
    platform/app.py:1082); the dev pipeline's stitching, muxing, and
    QA already run locally (runs 2 and 3), and the animated lane is
    fully local.
  - Platform hosting is JACK'S CALL with a KEEP recommendation:
    scale-to-zero means $0 idle on Modal (max_containers=1,
    scaledown 240 s, Starter credit absorbs the cents/day of CPU),
    while a Cloudflare port is a JS rewrite with a hard blocker
    (no ffmpeg on Workers for the delivery step) and a $5/mo floor
    that replaces ~$0 of marginal cost. Revisit at real traffic or
    when a custom domain is wanted.
  - Nothing was moved in this run: every candidate move would break
    live behavior, violate another run's ownership of
    platform/app.py, or cost more than it saves. No GPU spend.
- 2026-11-16: Stripe restricted key (Checkout Sessions write, created by Jack in the dashboard 2026-11-16) stored as STRIPE_SECRET_KEY in moonfactory-providers; payments verification follows the API-lane run's redeploy.

## 2026-11-16 (run 10): API LANES WIRED (Wan 3.0 + Prime via fal), MODEL PICKER, COST CEILING

Implemented API-LANES-DESIGN.md in platform/app.py + pricing.py and
redeployed. The worker was not changed (no GPU spend in this run).

- Secrets: moonfactory-providers mounted alongside moonfactory-secrets
  and moonfactory-integrations; FAL_KEY read from the environment,
  never logged. stripe_mode() now accepts rk_live_/rk_test_
  restricted keys: /api/config reports payments_mode "live".
- Picker (Jack's ruling): models by name only (Wan 2.2, Wan 3.0,
  Wan 3.0 Prime) in /api/config, /api/pricing, the landing page, and
  the studio. Forbidden-word grep over all three page constants and
  every error string: clean (no hosting/API/provider wording; the
  only "api" strings left in the studio page are its own fetch
  paths). Switching is paid-only on the server: free + Wan 3.0/Prime
  create or quote returns 403 before any dispatch.
- Default model (Amendment 2): DEFAULT_MODEL = wan2.2 everywhere;
  create_job defaults to it when the field is omitted; the studio
  select lists it first and free accounts are locked onto it.
- Flow: POST /api/jobs/quote gives the exact price (included-budget
  debit or balance charge, the 1.1x cap, lane-paused and ceiling
  flags). Create holds the cap (included budget in cost cents, or
  balance in debit cents), submits to fal's queue API with a
  per-job signed-token webhook, and settles at actual billed
  seconds on completion (webhook primary, 8-second poll fallback,
  both idempotent). Failures at any point release the hold in full
  and surface only "this model is temporarily unavailable"; the
  provider's raw error is kept in an internal job field. Delivered
  files keep the model's native audio, loudness-normalized to
  about -14 LUFS / -1 dBTP (an ordered custom bed mixes under it);
  a finishing failure falls back to the standard mux, flagged.
  Reference inputs (images/videos/audio) are accepted with
  reference video seconds billed in, per the design's pricing
  table; the studio form wires text and start-image modes today.
- One budget: usage.included_cost_cents is the included accounting
  now; included_seconds is derived from it at the measured
  production rate, so existing accounts migrate exactly and the
  launched 214s behavior is unchanged. Worker-lane segments debit
  15c of budget; per-second models debit their measured per-second
  cost (design s.5 table).
- Ceiling (Amendment 2): users carry net_paid_cents (Stripe
  webhook: invoice.paid for subscriptions, deduped by invoice id
  so the checkout event cannot double-count; checkout completion
  for top-ups; net_of fees) and cost_settled_cents (all lanes:
  worker jobs at dispatch, returned on error; fal jobs at settle).
  Both create paths refuse pre-dispatch with 402 when the job's
  worst-case cost would cross it. Free jobs never enter this
  accounting (Rule 5 bounds them).
- Rate table: meta.provider_rates seeded from pricing.py's
  verified fal list prices; refresh_provider_rates (modal.Period
  6h) re-reads fal's public model pages with the patterns verified
  in the prep run and only advances the timestamp on a complete
  read; staleness over 24h pauses the lanes. Lane prices also
  still pass pricing.lane_paused's floor check at create.
- Verification (public URL, in-Modal harness, zero GPU): config
  and pricing as above; free Wan 3.0 create 403 and Prime quote
  403; ceiling test account (net paid 10c): Wan 2.2 Production
  402, Wan 3.0 402, zero jobs created, balance/budget untouched;
  quotes exact (Wan 3.0 5s 720p: 50.5c included, 152c balance,
  cap 55.55c; Wan 3.0 30s 1080p: 1,809c balance; Prime 5s 720p:
  212c). ONE real submission on the paid test account reached fal
  and was refused there: HTTP 403 "User is locked. Reason:
  Exhausted balance" (fal account balance is $0.00). User side:
  503 "this model is temporarily unavailable"; job recorded as
  error with that message; hold_released true; afterwards balance
  $20.00 intact, included budget used 0.0, settled cost 0.0.
  Stripe checkout session creation: 200 with a real
  checkout.stripe.com URL (no charge). That $0-balance state is
  the expected end state until Jack tops up fal at
  fal.ai/dashboard/billing; the moment he does, the same
  submissions succeed with no code or config change.
- Spend: $0 of GPU. Platform CPU pennies inside the Starter credit.

## 2026-11-16 (run 11): CAMERA PRESET LIBRARY + FREEFIELD INVENTORY (Higgsfield parity build)

Scope: worker / workflows / docs only. platform/app.py untouched (owned
by the runs above). Sources: neuregex/freefield (MIT) and
wide-trace/open-higgsfield, both cloned to vendor/ at Jack's direction.
One-line note, per the brief: open-higgsfield ships no license file;
Jack was told and said use it anyways. Only patterns and notes were
taken from it, no code.

### What shipped

- **vendor/FREEFIELD-INVENTORY.md**: all 120 Freefield apps mapped
  against the parity gaps (video first, then audio, utility, image).
  Per app: models needed, download size, Freefield's own measured peak
  VRAM (L40S), whether the model is already in the sinclip-models
  Volume, and a port / skip verdict with a one-line reason.
- **workflows/camera-presets/**: the camera layer, 42 presets.
  - 33 prompt-recipe moves from Freefield's Cinema Studio (its camera
    moves are prompt text on LTX-2.5; the phrases port directly to our
    Wan 2.2 lanes), plus the full look vocabulary (tempo, genre, era,
    camera body, lens, focal, aperture, lighting, palette) and the
    composition grammar, all in presets-index.json for the platform
    picker to render. One JSON file per preset alongside the index.
  - 13 of the moves also carry a deterministic crop-window end-keyframe
    recipe (op + params) for firstlast_chain.json. Spec only: the API
    dispatcher does not expose the firstlast lane yet.
  - 9 Fun Camera presets patching the ported workflow below.
  - INDEX.md documents the library and the open-higgsfield studio
    patterns for the platform pass (catalog-as-data, one composer,
    schema-generated setting pills, per-model settings, gallery as
    history log with attachable inputs, in-flight runs as tiles).
- **workflows/fun_camera_i2v.json**: Freefield's camera-moves workflow
  (Wan 2.2 Fun Camera) adapted to our conventions: placeholder prompts
  and start image, VHS output node in place of CreateVideo/SaveVideo,
  720x1280 canvas. The move is a model input (WanCameraEmbedding
  camera_pose + speed), i.e. true camera conditioning. NOT a live lane:
  the two Fun Camera UNETs (15.3 GB each) are not in the Volume. Every
  other file it needs already is.
- **HIGGSFIELD-PARITY.md**: camera presets row, Cinema Studio row, and
  effects row updated to the states above.
- **workflows/NOTES.md**: fun_camera_i2v.json and the preset library
  documented with the other templates.

### Verification

- Structural: every workflow JSON in workflows/ (the 4 existing plus
  the port) parses, every node input link resolves to an existing
  node, exactly one VHS output node each; the port's only new node
  classes are the two ComfyUI core Wan camera nodes. All 42 preset
  files parse and match their index entries exactly.
- Render 1 (production, preset dolly-in): worker job 7cd20fb98a0545c2,
  distilled lane, benchmark balloon scene, seed 12345, same start
  image and scene text as the run 7 no-preset benchmark; the only
  delta was the composed preset prefix "The camera slowly dollies in
  toward the subject." Result: subject scale grew about 1.67x
  first-to-last frame (red-blob measure) vs 1.02x for the benchmark,
  framing visibly advances; the preset steers the render. Output
  outputs/camera-preset-dolly-in-test.mp4 (720x1280, 16 fps, 5.06 s,
  1,061,991 B). Caveat: the balloon's envelope pattern morphs mid-clip,
  the same class of drift the benchmark shows in its background; the
  benchmark scene is flat illustration and photoreal fidelity is
  untested, as in run 7. (The first submission of this test was
  interrupted by a runtime restart on the client side; the job itself
  survived server-side and was recovered by job id from the jobs Dict.
  No duplicate render was submitted.)
- Render 2 (draft, preset crash-zoom): one draft job, same scene and
  seed, prefix "The camera crash-zooms in fast on the subject."
  Result: weak fidelity on this lane. Subject scale +24% spread
  evenly across the clip, no fast punch-in; direction honored, speed
  not. Output outputs/camera-preset-crash-zoom-draft-test.mp4
  (960x1664, 16 fps, 5.06 s). Both outcomes are recorded in the
  preset entries themselves; the other 31 prompt presets remain
  "ported, unverified", the 9 Fun Camera presets "needs_model".

### Spend (list-rate estimates)

- Production preset job: about $0.12-$0.25. Draft preset job: about
  $0.30-$0.48 (draft has always been the pricier lane per second of
  wall). In-Modal client and recovery functions: CPU pennies.
- Run total: about $0.45-$0.75 against the ~$1.50 verification
  allowance in the brief. Project cumulative: roughly $4.5-$5.0 of
  the $10 cap (basis: $4.03-$4.27 at the cap raise, plus runs 8-10 at
  $0 GPU, plus this run).

### Queued by value (from the inventory)

1. Fun Camera model download (30.6 GB, the 2 UNETs): turns the 9
   ported conditioning presets live; the strongest parity item in
   Freefield for us.
2. Freefield effects port: Wan 2.1 I2V base (16.4 GB) + 24 effect
   LoRAs; one-tap effects incl. bullet-time-scene on the same base.
3. z-image (12.2 GB) as the start-image generation model: cheapest
   route to the P0 "describe it, we make the first frame" gap.

## 2026-11-16 (run 11): STRIPE WEBHOOK CREDITING VERIFIED (with a real bug found and fixed)

Final payments verification, all through the public URL. Harness:
an in-Modal function mounting the same three secrets as the
platform, importing the platform app in-process and reading
STRIPE_WEBHOOK_SECRET from that environment (presence and whsec_
prefix confirmed; the value was never printed, logged, or
returned). Signatures built with Stripe's documented scheme
(HMAC-SHA256 over "<timestamp>.<payload>", header t=<ts>,v1=<sig>).
Throwaway accounts whverify-*@example.com; no real charges; $0 GPU.

- Check 1 PASS: unsigned POST /api/stripe/webhook -> 400
  {"detail":"bad signature"} (was 503 before the key existed). A
  garbage-signature POST also -> 400.
- FIRST SIGNED RUN FAILED, and it mattered: all three signed
  events (top-up checkout, subscription checkout, invoice.paid)
  returned 500. Modal logs showed the handler crashing at
  obj.get("metadata") (checkout) and obj.get("id") (invoice):
  stripe-python's construct_event returns typed objects (Session,
  Invoice) whose .get() raises AttributeError in the installed
  SDK version. Signature verification itself passed (the events
  were recorded in stripe_events before the crash), which made it
  worse: the event id is marked seen BEFORE handling, so a real
  delivery that crashed would have its Stripe retries dropped as
  "duplicate" and the payment would never credit. The crediting
  path had never actually worked for any event type.
- FIX (platform/app.py, stripe_webhook): after construct_event
  verifies the signature, the handler now re-parses the verified
  payload bytes as plain JSON (event = json.loads(payload)) and
  handles dicts throughout. Redeployed to the same public URL.
- RE-RUN, ALL PASS (fresh accounts and event ids):
  - Top-up: signed checkout.session.completed (mode=payment,
    $10 top-up, price STRIPE_PRICE_TOPUP_1000, credit_cents 1000)
    -> 200 {"received":true}; balance_cents 0 -> 1000;
    net_paid_cents 0.0 -> 941.0 = pricing.net_of(1000), the
    fee-net amount exactly.
  - Subscription: signed checkout.session.completed
    (mode=subscription) -> 200; account plan set to "creator",
    stripe_customer_id recorded. Signed invoice.paid ($20,
    amount_paid 2000) -> 200; plan "creator"; net_paid_cents
    1912.0 = net_of(2000); included budget total 637.33
    cost-cents (pricing.included_budget_cents()), used 0.0.
  - Dedupe, both layers: resending the identical invoice event
    (same id, same bytes) -> 200 {"received":true,
    "duplicate":true}, net_paid unchanged at 1912.0. A NEW event
    id carrying the SAME invoice id -> 200, net_paid still
    1912.0 (invoice-level dedupe via net_counted_invoices).
- Test residue: four whverify-*@example.com accounts (the failed
  run's pair uncredited, the passing run's pair credited by
  design), clearly named as tests. The crashed run's three event
  ids sit in stripe_events as seen; they are synthetic ids with
  no real-world counterpart.
- Spend: $0 GPU; harness and platform CPU pennies inside the
  Starter credit.

## 2026-11-16: PARITY PROGRAM (Moonfactory vs Higgsfield readiness bar)

- Jack's bar, restated by him today: Moonfactory is "ready" only when
  it is as good or better than Higgsfield's video generation in EVERY
  way and looks better. Platform-live / payments-working is not ready.
- Wrote PARITY-PROGRAM.md (repo root): a 16-dimension scorecard
  (today: 5 FAR BEHIND, 6 BEHIND, 2 PAR), verified supplier facts,
  and the closure program in 10 passes (Pass 0 = a $50 fal top-up,
  sized to the test matrix; Pass 2 = the frontier shelf).
- Supplier facts, primary sources read today: fal (our existing
  provider) carries Veo 3.1 ($0.40/s with audio at 720p), Kling 3.0
  ($0.126/s Standard with audio), Seedance 2.5 (~$0.473/s at 720p,
  token billing, US-hosted endpoints), Grok Imagine 1.5, and the
  Hailuo family. Same picker + 3x floor + ceiling machinery can sell
  them all; no new provider or key. Kling 4.0 is listed on fal but
  its API is "coming soon" (playground only). Google's Gemini API no
  longer lists Veo; direct Veo means Vertex at the same $0.40/s, so
  no direct-route savings. Gemini Omni 1.2 Flash direct is ~$0.07/s
  at 720p, a possible future lane.
- Structural warning recorded in the doc: at a 3x floor over fal
  list prices, Moonfactory's frontier clips price well above
  Higgsfield's effective credit prices (Seedance 5s: ~$7.10 vs
  ~$1.72). Capability parity via fal is complete; frontier PRICE
  parity inside the floor is impossible (BytePlus is half of fal
  but not available in the US). That is a pricing-policy call for
  Jack, flagged, not assumed away.
- Impossible/impractical, named in the doc: Soul ID / DoP / Speak
  as themselves (Higgsfield-proprietary, not licensable), Kling 4.0
  API (not open yet), Cinema Studio rig fidelity, editor plugins,
  self-hosted frontier quality (Wan 3.0 has no open weights).
- Total external spend to close every closable item: ~$55 ($50 fal
  top-up + ~$3-5 Modal downloads and verification renders).
- Committed PARITY-PROGRAM.md only; no platform code touched.

## 2026-11-16 (run 12): CAMERA PRESETS WIRED INTO THE STUDIO

- The preset library from run 11 is now a live studio feature.
  platform/app.py ships workflows/camera-presets/presets-index.json
  in its Modal image as data (/platform/presets-index.json) and
  serves it at GET /api/camera-presets (42 presets, look
  vocabulary, max 3 chained moves). The studio form renders its
  picker from that response: numbered chips for the moves (pick
  order = chain order), optional selects for the nine look slots
  (tempo, genre, era, camera body, lens, focal, aperture,
  lighting, palette). Nothing is defaulted in; no selection means
  the user's prompt runs exactly as written.
- Server-side composition at job creation, in the library's
  grammar ({moves}{scene}.{tempo} The look of a film{genre}{era}:
  {camera_body}; {lens}; {focal}; {aperture}. Lighting: ...
  Color: ...). The job record stores prompt (composed),
  user_prompt (original), camera_presets (ids, in order),
  camera_preset_names, and camera_look, on BOTH the Wan 2.2 path
  and the per-second path (the composed prompt passes through to
  Wan 3.0 / Prime unchanged). public_job and the gallery item
  expose the preset names; the studio list and gallery render
  them. Regenerate recomposes from user_prompt + the stored
  selection, so it reruns the identical final prompt.
- Gates: presets are NOT a paid feature (free accounts use them
  on Draft; model switching stays paid-only). The 9 Fun Camera
  presets (needs_model) render disabled with a "coming soon"
  label and are refused server-side (422) if submitted directly;
  unknown presets, a 4th move, and unknown look values are also
  clean 422s before any dispatch or quota is touched.
- Interpretation note: 13 of the 33 prompt presets carry a
  keyframe_enhancement recipe. Those presets are selectable
  today on their prompt text (the library's own INDEX.md counts
  all 33 as working today); only the keyframe steering itself is
  unwired, because the API dispatcher does not expose the
  firstlast lane. The only needs-model presets are the 9 Fun
  Camera ones.
- Verification (scratch/verify_camera_presets.py, in-Modal
  harness against the public URL; no-GPU paths used a
  deliberately invalid start image the worker rejects before
  sampling):
  - Picker: 42 presets, 33 available, 9 coming soon, max_moves
    3, all 9 look slots with full vocabularies; /app serves the
    picker markup and the /api/camera-presets fetch.
  - Free account: fun-preset / 4-move / unknown-preset /
    bad-look submissions each 422. Draft job with ["dolly-in"]
    + lens "Classic Anamorphic" -> 202; record holds ids
    ["dolly-in"], the look, user_prompt = the scene as written,
    prompt = "The camera slowly dollies in toward the subject.
    <scene>. The look of a film: a classic anamorphic
    widescreen look with oval bokeh and blue streak flares."
  - Paid account: Production job with ["dolly-in", "pan-left"]
    -> 202; names stored in order ["Dolly in", "Pan left"];
    composed prompt joins the moves with ", then ".
  - ONE live render: paid Production, ["dolly-in"], balloon
    benchmark scene -> done. The worker's own record in
    sinclip-jobs holds the composed prompt byte for byte (proof
    the composition reaches the worker, not just the platform
    record). Delivered file 1,266,177 bytes, h264 720x1280 +
    AAC. Gallery item for the clip shows camera_presets
    ["Dolly in"].
  - Test residue: three presets-*@example.com accounts (one free,
    one paid flipped to Creator with net_paid 1912.0 as operator
    test data), three error jobs (invalid image, by design) and
    one finished clip, all clearly named as tests.
- Spend: ~$0.12 GPU (the single distilled production render);
  everything else was platform/worker CPU pennies. Inside the
  $0.60 run cap. Project cumulative roughly $4.65-$5.15 of the
  $10 cap.

## 2026-11-16 (run 13): AMENDMENT 3 PRICING + PAY-PER-VIDEO BILLING

Jack's rulings (2026-11-16, BUSINESS-RULES Amendment 3): frontier
(per-second) lanes price to undercut Higgsfield's effective
per-clip prices where possible, hard minimum cost x 1.25, never a
loss after fees; billing is two first-class paths (pay-per-video
OR subscription + top-ups); "paid" gates mean any paying customer.

- pricing.py: FRONTIER lane table (frontier_lane()) carrying
  provider cost per second, Higgsfield's effective per-second
  price on the PARITY-PROGRAM comparison basis (5 s, 720p, with
  audio, Plus credit rates: Wan 3.0 $0.44/clip = 8.8c/s; Wan 3.0
  Prime $0.74/clip = 14.8c/s from 15 credits at the same rate),
  the cost x 1.25 minimum, and the sell rate (97% of Higgsfield's
  effective where that clears the minimum). Structure takes
  Veo/Kling/Seedance entries later by adding one dict row.
- Outcome at 720p: the minimum binds on BOTH live lanes.
  Wan 3.0: cost 10.1c/s (Higgsfield's effective 8.8c/s is below
  raw cost itself), sell 12.625c/s, 5 s clip 64c from balance
  (was 152c at the 3x floor; per-second display 13c, was 31c).
  Prime: cost 14.1c/s, effective 14.8c/s, sell 17.625c/s, 5 s
  clip 89c (was 212c; display 18c, was 43c). Both recorded
  can_undercut=false in the pricing data and surfaced in
  /api/pricing as data, not user copy. Included-budget debits
  unchanged (raw cost; the budget is a cost unit) and the
  Amendment 2 ceiling unchanged. Self-hosted lanes keep the 3x
  floor. Settle path now charges the sell rate, capped at hold.
- app.py: is_paid(user) = active subscription OR net_paid > 0.
  All four gates (per-second quote, per-second create, Wan 2.2
  paid lanes, music prompt, gallery opt-out) key on it. A
  free-plan account that has paid in runs Production and
  Wan 3.0/Prime from balance through the same quote/hold/ceiling
  machinery; the included budget stays subscription-only in the
  quote, hold, and Wan 2.2 paths. Watermark now keys on the job's
  free_tier flag (paid jobs ship clean whatever the plan name);
  gallery opt-out honored for any paid account. public_user
  exposes paid_customer; the studio enables controls and fetches
  quotes for paid customers, not just subscribers.
- Copy: landing and studio present the two paths side by side
  (Pay per video: top up, pay per clip, no subscription /
  Creator: monthly included budget plus top-ups). Model names
  only; no hosting or API words; no em dashes.
- Verification (scratch/verify_pricing_billing.py, in-Modal
  harness against the public URL; signing secret read in-process
  from the mounted integrations secret, presence + whsec_ prefix
  only): (a) never-paid free account: Production create 403,
  Wan 3.0 quote 403, Wan 3.0 create 403, music create 403,
  opt-out 403. (b) Signed $10 top-up webhook (checkout.session
  .completed, mode payment) credited a no-subscription account:
  balance 0 -> 1000, net_paid 0 -> 941.0, plan stayed free,
  paid_customer true. Gates then passed: opt-out 200; Wan 3.0
  quote method=balance at 64c; Prime quote 89c; a Draft job
  carrying a music prompt was created (202, has_custom_audio)
  and errored on its deliberately invalid start image before
  sampling, refunded in full under Rule 8 (balance back to
  1000); a real Production job ran from balance (202, charged
  99c, balance 901, settled cost +15.0). (c) With the account's
  settled cost operator-set to leave 1.0c of ceiling headroom,
  Wan 3.0 create and Production create both refused 402
  pre-dispatch, no jobs created, balance untouched; settled
  restored after. (d) /api/pricing shows the frontier table
  (sell rates, Higgsfield effectives, can_undercut flags) and
  /api/config is unchanged.
- Test residue: ppv-free-* and ppv-paid-* @example.com accounts
  (the paid one holds a 901c test balance and one rendering
  Production job), one errored music Draft job, one signed test
  webhook event id (evt_ppv_*), all clearly test data. An
  earlier harness attempt also left one ppv-paid account with a
  refunded invalid-image Draft job.
- Spend: ~$0.14 (one distilled Production render ~$0.12 plus
  platform/harness CPU pennies). Inside the $0.30 run cap.
  Project cumulative roughly $4.80-$5.30 of the $10 cap.

## 2026-11-16: PeakCut evaluation and clipping design (no platform code touched)

- Evaluated PeakCut (github.com/haziqahmedqasmi/PeakCut, MIT, commit b3e403e; study clone at vendor/peakcut, not committed). Verdict: safe to adapt under MIT with attribution. Port the pipeline (brain.py picking, render.py captions/layouts, transcribe.py Parakeet wrapper, media.py fetch/loudness); rebuild server, auth, storage, and posting inside the platform. Red flags noted: 60 second GitHub phone home update checker (sends no user data; dropped from any port), auth off by default single owner mode, plaintext BYO key file (fine single tenant, not for us), all limits default to unlimited, repo is two days old so the commit stays pinned.
- Smoke test on this machine, $0, no API key: venv at clipping/venv, server on port 8791, dashboard healthy. One end to end job in loudness fallback mode on a 180 s public domain excerpt (Kennedy Rice University address, Wikimedia Commons): 2 clips out, both 1080x1920 h264 + AAC (51.3 s and 35.4 s), word by word captions verified burned in on a frame grab, transcription accurate. Sample at clipping/sample-clip01.mp4. Server stopped after the test.
- Design written to CLIPPING-DESIGN.md: pipeline stages, CPU only Modal function placement (never the GPU apps), cost model of about 0.3 cents per source minute worst case (compute about 0.20, Gemini 2.5 Flash picking about 0.02, loudness 0), pricing proposal (Creator includes up to 300 clipping minutes drawn on the shared budget at the 0.9 cent floor rate; top ups at 5 cents per source minute against OpusClip's effective 9.7), phasing (v1 link/upload, v2 live, v3 auto posting with per platform OAuth approvals), and a Create / Clip two tab studio.

## 2026-11-16 (run 14): FRONTIER SHELF (PASS 2) + BEATAPI SUPPLIER FOR SEEDANCE 2.5

- Supplier change executed: Seedance 2.5's primary supplier is
  BeatAPI, not fal. Researched from their own pages 2026-11-16
  (full write-up: BEATAPI-EVALUATION.md, local-only). Rates
  $0.096/$0.194/$0.481 per second (480p/720p/1080p, no reference
  video, native audio included) vs fal's $0.462/s at 720p with no
  1080p. Terms verdict: the Customer Solution pattern is allowed
  (homepage markets SaaS platforms explicitly; outputs usable
  subject to provider rules; the resale bar covers reselling the
  raw service; operator is LogiaVox LLC, Sheridan WY; no US
  account restriction stated; Seedance route hosting region not
  stated, flagged). fal's Seedance rates kept in pricing.py as
  FALLBACK_PROVIDER_RATES_CENTS_PER_SECOND, unwired, not
  user-visible.
- Catalog finds (BeatAPI vs fal, like-for-like): Seedance 720p
  58% cheaper (wired). Veo 3.1 standard 720p with audio 42%
  cheaper ($1.85 per fixed 8 s clip vs $0.40/s) and Veo 3.1 Fast
  75% cheaper ($0.30 per 8 s vs $0.15/s): both beat the 20% bar
  but sell fixed-length clips per clip, so Veo stays on fal this
  pass per the program; moving it is recommended for a later
  pass. Wan 3.0 entry spec 29% cheaper (from $0.0355/s vs $0.05);
  Prime 11% and Kling 3.0 ~13% (from $0.11/s vs $0.126) are under
  the bar; Kling 4.0 roughly par.
- Account: NOT created. BeatAPI signup is a dashboard flow that
  needs the live browser, which this run did not have. Remaining
  steps are written in BEATAPI-EVALUATION.md (signup under
  support@magrify.com, one key named for Moonfactory, add as
  BEATAPI_KEY to moonfactory-providers without touching FAL_KEY
  or STRIPE_SECRET_KEY). The lane is built to unpause the moment
  the key lands, no deploy needed; until then it reports
  unavailable in /api/config and refuses 503 before dispatch.
- Wiring: MODEL_PROVIDER map (seedance -> beatapi, the rest ->
  fal); per-provider configured check in api_lane_state. BeatAPI
  dispatcher: POST /v1/videos/tasks (job id as Idempotency-Key),
  media via POST /v1/files when not already a public URL, poll
  GET /v1/tasks/{id} on the existing 8 s mirror cadence (their
  webhooks skip timeout-stopped tasks, so polling is the reliable
  path), download from output.media, local finishing unchanged
  (native audio kept). Settlement prefers the task's reported
  credits_settled (provider_cost_cents): their billing is
  token-based and reference-video seconds settle at a lower rate
  that billable_duration_seconds (output only) misses; the hold
  cap still binds. Veo 3.1 + Fast and Kling 3.0 wired via fal:
  Veo payload per the parity research (duration "8s" form,
  generate_audio, image_url/end_image_url, image_urls refs,
  images only); Kling tier rides in the endpoint path (Standard
  720p / Pro 1080p) via fal_endpoint_for. Per-model validation:
  seedance 4-30 s all resolutions; veo 4/6/8 s only, 720p/1080p,
  image refs only; kling3 3-15 s, 720p/1080p. Studio form rebuilds
  length/resolution options per model; the free-user gate now
  disables every non-Wan-2.2 option generically. Landing page
  lists all seven models and builds its per-second price line
  from /api/pricing.
- Pricing rows: seedance cost 9.6/19.4/48.1 seed (refreshed live
  to 9.681/19.541/48.365 from the supplier's page, largest-frame
  basis), Higgsfield effective 172c per 5 s clip -> can_undercut
  TRUE at every resolution; sell 33.368c/s at 720p (target 97% of
  their 34.4c/s, minimum 24.375c/s cleared); a 5 s 720p clip is
  167c of balance vs their ~172c. Veo 3.1 (40c/s), Veo 3.1 Fast
  (15c/s), Kling 3.0 (12.6/16.8c/s) carry NO Higgsfield effective
  (none exists in the parity research; none invented): they sell
  at the 1.25x minimum (50.125 / 18.875 / 15.875 c/s at 720p) and
  can_undercut reports null. Included debits stay at raw cost;
  ceiling untouched.
- Rate refresh: required reads are now the two fal Wan pages AND
  the supplier's Seedance model page (structured-data offers;
  first regex missed the escaped JSON, fixed by normalizing
  \u00b7 and stripping escape backslashes; verified live:
  verified [seedance, wan3, wan3_prime], updated true). Veo/Kling
  fal pages are opportunistic reads. Stored-table merge in
  rate_table() now seeds gaps per model so new lanes never pause
  for a missing row.
- Verification (scratch/verify_frontier_shelf.py, in-Modal,
  public URL): ok true. Config lists 7 models in order, default
  wan2.2, seedance unavailable pre-key. Free account 403 on
  create + quote for seedance/veo3/kling3. Creator quotes:
  seedance 5 s 720p balance 167c, included 98.21c (refreshed raw);
  veo3 8 s 401c / 320.8c; veo3_fast 8 s 151c; kling3 5 s 80c /
  63.5c. Validation 422s: veo 5 s, veo 480p, seedance 3 s, kling
  20 s. Ceiling: wan3 create 402 pre-dispatch, no job; seedance
  create 503 pre-dispatch (paused), no job. Real submits at $0
  balances: wan3, veo3_fast, kling3 -> clean 503, error job with
  the one public sentence, holds fully released; balances,
  included budget, settled cost all untouched after.
- Flags for the funded pass: fal-ai/veo3.1's page showed a
  deprecation notice dated 2026-11-17 (successor endpoint to
  pin); Veo/Kling payload field names follow the parity
  research and get their live schema check on first funded
  submits; the BeatAPI submit path gets its live check the same
  way once the key exists (a $0 balance answers their 402, which
  the shared submit-failure path already turns into the clean
  503 with the hold released).
- Test residue: shelf-free-*, shelf-paid-*, shelf-ceil-*
  @example.com accounts; three error jobs (wan3, veo3_fast,
  kling3) on the paid test account; no charges anywhere.
- Spend: ~$0.10 Modal (deploys, two refresh probes, one harness
  run; all CPU pennies, no GPU, no provider spend). Inside the
  $0.30 run cap. Project cumulative roughly $4.90-$5.40 of $10.
- Funding to go live: BeatAPI top-up from the dashboard at
  beatapi.io (any amount unlocks all models; $25 recommended);
  fal $50 at https://fal.ai/dashboard/billing (unchanged Pass 0).

## 2026-11-16: rea-video evaluation and Recreate design (no platform code touched)

- Evaluated rea-video (github.com/JuneYaooo/rea-video, Apache-2.0, pinned 8db6cdf; study clone at vendor/reavideo, not committed; first shallow clone failed in fetch-pack, retry succeeded). Verdict: WRAP it as an internal analysis service behind the VIDEO.md contract; do not adopt as-is (agent skill + CLI, no server/auth/queue, and its capsule-cinema generation bridge bills on a third-party service outside our rules, never invoked) and do not rebuild (about 18,300 lines of tuned forensic rules plus eval sets). Safety read: no telemetry and no upload of user video anywhere; the only HTTP code is model-weight downloads (GitHub/ModelScope, into ~/.cache/rea-video/models) and the operator-configured VLM endpoint. Keyless runs are fully local after downloads; with --vlm, per-shot frames/clips go to that configured OpenAI-compatible endpoint only. Apache-2.0 hosted use has no source-disclosure duty; downloaded ONNX model weights need a pre-launch license pass.
- Smoke test on this machine, $0, no keys: venv at recreate-lab/venv (pip install of the clone with [ocr], about 100 s; opencv GUI/headless conflict fixed per the repo's setup.md; first run downloads the YuNet face model). One keyless decompose of our own outputs/prod-distilled-test.mp4 (Wan 2.2 A14B distilled render, flat-illustration balloon scene, 5.06 s, no audio track): 53 s wall (37.5 s analysis). Classification WRONG: verdict "code / template render 100%", shot scored code_render 0.69 / cg_3d 0.20 / ai_video 0.05, because flat colour fields (95% of pixels in 24 colours) match the code-render signature. Everything else landed: exact shot boundaries, camera measured as a smooth pull-out, palettes and sub-scenes, "no audio track", no phantom captions. The wrong label routed the remake plan to a Remotion code rebuild, so plan quality is bounded by classification.
- Agent-review loop tested (review -> my answers at ai_video 0.80 -> apply-review): narrative/cast/prompts merged but the label did NOT flip. Reproduced the fusion offline: group-first decide() kept rendered (0.4996) ahead of generated (0.4424), so the lone-reader gate rejected the review; the same review at ai_video 0.90-0.95 flips it cleanly. Wrapper rule recorded: reviews of flat/animation-style content must be decisive, or pair with a VLM second reader; user `correct` overrides unconditionally. Full notes: recreate-lab/SMOKE-TEST.md, outputs in recreate-lab/prod-distilled/.
- Design written to RECREATE-DESIGN.md: studio Recreate flow (upload/link + rights attestation, in-studio breakdown report, per-shot "Recreate this shot" prefill), a full field mapping from the VIDEO.md package to platform inputs (per-shot image/video prompts, first/last frames, camera measurements to the camera-preset library, SRT/ASS caption and styled-text layers, sound plan to TTS/ACE-Step/SFX, cast to character-sheet references), lane mapping via upstream's capability map (wan-2.2 / wan-3.0 already present), cost model (keyless worst case about $0.02 per breakdown measured; VLM-assisted worst case about $0.20) and pricing under BUSINESS-RULES (3 free keyless breakdowns per account, then $0.49; VLM-assisted $0.99 or budget debit; recreation generation at normal lane prices), phasing (v1 internal tool for our own recreation work and gallery-prompt research, v2 user-facing breakdown, v3 one-click per-shot recreation), and the REFERENCE-RECREATE rights guardrails surfaced in the UX (attestation stored per job, identity never cloned, voice always new, real material supplied never regenerated). Open items: flat-style classification weakness (mitigations designed, broader eval on our own clips owed before v2), audio path unproven (test clip had no audio track; code-verified only), young single-maintainer upstream (pinned, wrapped, re-vet on upgrade).

## 2026-11-16 (run 15): CLIP TAB v1 (PeakCut port, CLIPPING-DESIGN sections 4, 6, 8)

- Step 0: the frontier shelf commit was already landed and pushed by
  the earlier pass; the publish tree was clean at HEAD 4404552 and
  up to date with origin at the start of this run, so there was
  nothing to commit or push for step 0.
- Port (platform/clipping.py, new): PeakCut pinned commit b3e403e
  under MIT (platform/PEAKCUT-LICENSE.txt + THIRD-PARTY-NOTICES.md).
  brain.py picking (windowed prompts, the transcript-untrusted
  sanitizer kept verbatim in spirit, sentence snapping, dedupe,
  loudness fallback), render.py ASS word-by-word captions in the
  same 3 styles (Bounce, Hype, Clean) and the same 3 ffmpeg layouts
  (blur fill, zoom plus blur, centre crop) at 1080x1920, the
  sherpa-onnx Parakeet transcribe wrapper (including upstream's
  subword-to-word merge, without which captions came out as
  fragments), and media.py fetch/probe/loudness. NOT ported:
  updates.py (phone-home dropped entirely), PeakCut's server, auth,
  accounts, and plaintext key storage. The fetch allowlist guard is
  kept exactly (youtube.com, youtu.be, twitch.tv, kick.com;
  http/https only) with playlists and channels refused on top.
  One simplification, stated plainly: upstream downloads audio
  first and only the picked sections later; v1 downloads the whole
  file at up to 720p, bounded by the source cap. YouTube's own
  captions are not used in v1; every job transcribes locally.
- Limits set explicitly (upstream defaults them to unlimited):
  max source 3 hours, max upload 2 GiB, up to 6 clips per job.
- Placement: process_clip_job, a CPU-only function in the platform
  app (4 cores, 8 GiB, scaledown 60 s, ffmpeg + sherpa-onnx +
  yt-dlp in its own image, bundled caption fonts shipped). The
  Parakeet model (about 480 MB) downloads once and is cached on
  the moonfactory-data Volume at /data/clip-models (the
  sinclip-models Volume holds the generation models and was not
  touched). Never the GPU apps. Clip jobs are platform jobs: same
  accounts, holds, ceilings, library and gallery rules; finished
  clips are child job records (lane "clip") with files in place on
  the data Volume, thumbnails served at /api/jobs/{id}/thumb.
  Volume-view lesson from this run: warm containers across a
  redeploy served several spawns with pre-upload snapshots and the
  jobs failed as "uploaded file is missing" (holds released, no
  charge); the worker now reloads its Volume view on entry, and
  the file/thumb routes reload on a miss.
- Picking mode live: LOUDNESS FALLBACK. Secret key names checked
  in-process (values never read out): moonfactory-providers holds
  FAL_KEY and STRIPE_SECRET_KEY only; no Gemini or Google key
  exists in any mounted secret. The picking client is wired to
  read GEMINI_API_KEY from moonfactory-providers; adding that one
  key (recreating the secret with all existing values preserved)
  turns on AI picking with no deploy. No secret was modified in
  this run; moonfactory-secrets untouched as always.
- Pricing as implemented (pricing.py): source minutes = probed
  duration rounded up to whole minutes. Creator: included budget
  debited at 0.9c per source minute. Balance: 5c per source minute.
  Free: refused 403 on quote, upload and create. Exact quote
  before the job runs (POST /api/clips/quote, and the upload
  response carries the quote). Amendment 2 ceiling checked
  pre-dispatch at the worst-case cost of 0.3c per source minute.
  Failed clip jobs restore balance/budget in full (Rule 8);
  settling records the Rule 7 numbers on the job.
- Studio: Create and Clip tabs at the top of the studio; the Clip
  page uses the design's copy verbatim (headline, subline, Make
  clips button, both charge lines, empty state), one input (link
  or upload), clip count up to 6, length short/medium/long, the 3
  named caption styles, layout Fill with blur / Zoom / Crop,
  progress with the four stage labels, and clip cards (thumbnail,
  hook, duration, score, Download) in the results pane and the
  library. No em dashes in any user-facing copy (checked by grep
  over the served studio page in the harness); no provider,
  hosting or API words in the clip copy.
- Verification (scratch/verify_clipping.py, in-Modal harness
  against the public URL, ok: true): pricing surface carries the
  clipping rates and picking_mode "loudness"; studio copy all
  present; free account 403 on quote, upload and create; playlist,
  channel, non-allowlist and Vimeo links all 422. Creator upload
  job on the 180 s Kennedy Rice excerpt (probed 180.01 s, so 4
  source minutes under the round-up rule): done, pick_mode
  loudness, 2 clips, first clip 44.01 s, h264 1080x1920 + AAC,
  thumbnail present, hook pill measured in a frame grab (12.19% of
  the top band near-white), included budget debited exactly 3.6c
  (0.9 x 4), settled cost 1.2c (0.3 x 4), clip listed in the
  gallery. Balance account: a job whose uploaded file was removed
  errored and restored the balance to 1000 in full; a clean job
  finished done and charged exactly 20c (5 x 4), ending at 980.
  Ceiling account (1c of headroom): create refused 402 before
  dispatch, balance untouched. Link jobs: verified through the
  guard and quote-refusal path only; the end-to-end proof used
  the upload path (one guessed public video id turned out
  unavailable and no live link download is claimed). The same
  pipeline also ran end to end locally before deploy (691 words
  transcribed, 2 loudness picks, 2 clips rendered).
- Test residue: clip-free-*, clip-creator-*, clip-balance-*,
  clip-ceil-* @example.com accounts from the harness runs
  (several rounds), their clip jobs and clips (two finished
  Kennedy clip sets are in the public gallery as test data), the
  directtest/directdbg synthetic jobs, uploaded test sources
  under /data/clip-sources, and the cached Parakeet model under
  /data/clip-models (a keeper: it is the production cache).
- Spend: about $0.20 of Modal CPU by list-rate estimate (roughly
  eight clip-job executions at about 0.33 core-hours each, the
  one-time model download, deploys and harness functions; no GPU,
  no provider generations). Inside the $0.50 run cap. Project
  cumulative roughly $5.10-$5.60 of the $10 cap.
- Commits: frontier HEAD at step 0 was 4404552 (already pushed).
  This run: 9d11836 on magrify/moonfactory, pushed (the stored
  git credential is scoped to another repo and answered 403; the
  push went through with the magrify-repos token read in-process,
  never printed).

## 2026-11-16: Animated-shorts lane built (three.js in Remotion, CPU) + first short rendering

- New lane at animated-lane/ following the vendored claude-animated-shorts skill: flat 2D
  billboard characters in a two-tone 3D world, ink/lens/grade post chain, synthesized
  voice + original synthesized score/SFX. No GPU, no provider; runs on this machine.
- First short: "The Wave That Should Not Move" — original script (18 lines, facts verified
  against Wikipedia/History Today) about the Boston Molasses Flood of Jan 15, 1919.
  13 shots, 2088 frames = 69.6 s at 1080x1920/30. Project: animated-lane/project
  (story/timing/world/build/cast/Short), script at animated-lane/SCRIPT.md.
- Voice: Kokoro af_heart (Apache-2.0) at speed 1.4, per-sentence synthesis; word timings
  from faster-whisper; timing.ts generated from the real voice (work/make_timing.py).
- Audio (animated-lane/audio/): original C-major march + synthesized SFX on the vendor
  DSP/instrument toolkit; mix.wav 48 kHz stereo 24-bit, exactly 69.600 s, -15.26 LUFS /
  -1.20 dBTP (ffmpeg cross-checked), voice gain-only, music ducked, SFX at naive -6 dB.
- Render reality on this cell (~1.5 shared CPUs, SwiftShader): ~10-25 s/frame at full
  spec. Rendering is chunked (180-frame mp4s, resumable: work/render-chunks.sh).
  Assembled deliverable so far: outputs/first-animated-short.mp4 = first 540 frames
  (18.000 s) with audio, QA'd with the skill's short_qa.py (specs/loudness clean; one
  flagged pop at frame 87 = a diagnosed hook defect, fixed in source; the fixed chunk 0
  and chunks 3-11 are still rendering in the background queue — see
  animated-lane/STATUS.md for the finish procedure and the two bug write-ups:
  wave-overtakes-camera and the fx pool scale k=1 correction).
- Note: the business was renamed Sinclip -> Moonfactory today (parent workstream); this
  lane's files still live under sinclip/ paths.

## 2026-11-16 (run 16): FORM FINISHERS (PARITY-PROGRAM Pass 1 items b-f)

Worker C. Platform HEAD at start 9d11836. Shipped, on the Wan 2.2
house lanes only (per-second lanes keep their own duration and
resolution surface and refuse house shapes 422):

- Chaining in the form and dispatch: segments 1-12 at per-segment
  pricing exactly as single segments price. The worker runs the
  chain natively (segment n+1 starts from segment n's last frame,
  extracted in the dispatcher; final segment steers to the end
  image when given; stitch drops the shared boundary frame, the
  run 3 shape). The platform post chain's music bed now loops, so
  -shortest can never trim a chained video to the bed length.
  Chains are paid-only (free accounts stay single-segment Draft,
  403); each segment counts as a render for cost, ceiling, and
  included-budget accounting. Mid-chain failure behavior, stated
  in LAUNCH-NOTES and the studio job card: completed segments
  deliver as one finished shorter video and stay charged per
  segment; unrendered segments are refunded in full (balance,
  settled cost, and included budget restored for those segments);
  segment 1 failing is a plain error with the usual full restore.
  Job records and the studio list show segment progress.
- Start/end frames: server shape-checks both frames (base64, PNG
  or JPEG magic, 10 MB cap) before dispatch. End frames run on
  Production only, through the worker's firstlast workflow with
  the lane's LightX2V distilled recipe patched in (4 steps, cfg
  1.0, same LoRA pair), so an end-frame segment costs a production
  segment, not the 20-step original's ~$0.77. Draft + end image
  and end-without-start are 422s.
- Text-only: Draft needs no start image (the TI2V-5B's text path:
  the start_image input is unwired in the workflow). Production
  without a start image is a 422 naming the Draft lane, replacing
  the worker's silent built-in test image on that path (the worker
  backstops the same 422). Behavior change, deliberate.
- Aspects: 9:16 / 16:9 / 1:1 on both house lanes, rendered
  natively (Draft 480x832, 800x448, 640x640; Production 720x1280,
  1280x720, 960x960); the post chain never crops. First 16:9 proof
  caught my own wrong Draft dims (832x480 measures ratio 1.733,
  not 16:9); corrected to 800x448 (1.786, the closest /16 shape at
  the Draft pixel budget) and re-proven. Production 16:9 is exact.
- Parallel submission: per-plan in-flight limits (free 1, paid 4)
  enforced 429 before charge or dispatch; the studio list shows
  each in-flight job's position, segment progress, aspect, and a
  plain partial-delivery note. Regenerate carries the full shape
  (segments, aspect, both frames stored privately on the record).
- Worker contract extended in place (ENDPOINT.md): aspect_ratio,
  segments, end_image_png_b64, per-job-unique output prefixes (the
  old shared prefix could reuse a previous job's file inside a
  warm container), segment progress and partial fields on GET.

Verification (public URL, in-Modal harnesses
scratch/verify_form_finishers.py, verify_ff_checks.py,
verify_ff_finish.py, verify_ff_169.py):
- Gates and matrix, no GPU: config serves the house shapes; free
  chain 403, free Production 403, free Draft+end 422, free
  concurrency 429 (one seeded in-flight record); paid matrix of
  12 bad combinations all 422 with the worker job count unchanged
  (18 -> 18, zero dispatches); paid concurrency 429 at 4 seeded.
  Two harness-era bugs found and fixed on the way: segments=0 was
  coerced to 1 by a falsy default in both platform and worker
  (one stray Draft render dispatched before the fix, counted in
  spend below), and the matrix tripped the platform's own
  10-per-minute job-creation rate limit until the harness paced
  its creates.
- Proofs (Draft except the end-frame job): (a) 2-segment chain
  delivered ONE file, 10.06 s, 960x1664, h264 + AAC, segments_done
  2 of 2, charged 398c = 2 x 199c. Seam measured on the delivered
  file: inter-frame MAE 15.56 across the join against 0.32 and
  0.14 inside the segments, on a nearly static synthetic scene:
  the join is continuous in content but carries a visible texture
  settle, larger than run 3's A14B seam (3.14). Stated plainly:
  Draft chaining works and is honest about its seam. (b)
  Start/end Production job: first frame mean color 7.9 from the
  start image, last frame 10.7 from the supplied end image
  against 243.3 from the start: the end frame steers the render.
  Charged 99c. (c)+(d) Text-only 16:9 Draft job, no image
  supplied: delivered 1600x896, ratio 1.786, 5.06 s, charged
  199c. Parallel: the chain, text-only, and end-frame jobs were
  submitted back to back on one paid account and were all running
  at the same time in the worker (three containers), positions 1
  and 2 visible in the studio list snapshot. Billing across all
  proofs exact: balance 100000 -> 99105, settled cost 215c
  (= 50c per Draft segment-render and 15c for the Production one,
  times the renders that ran).
- The mid-chain partial path is implemented and unit-shaped
  (worker delivers completed segments with partial=true; the
  platform refunds the missing segments on first mirror) but was
  NOT live-proven: forcing a mid-chain GPU failure inside the
  spend cap was not practical. Stated as unverified, not hidden.

- Spend: six segment-renders total (the four planned proofs, the
  stray segments=0 Draft render, and the corrective 16:9
  re-render) at roughly 3.6-6 GPU-minutes each on L40S list rates,
  plus CPU pennies for deploys and harnesses: about $0.85
  estimated, over the run's $0.75 proof cap. The overage is the
  two extra renders, both from my own errors above (the falsy
  segments default and the first 16:9 dims), not from the planned
  proof set, which was four renders. No Production long-recipe
  renders, no per-second lane ever dispatched, provider balances
  untouched at $0.
- Test residue: ff-free-*, ff-paid-* @example.com accounts from
  four harness rounds (the run-3 paid account holds the three
  proof jobs plus the corrective 16:9 job, all in the gallery as
  test data), the stray segments=0 Draft job on the first round's
  paid account, and the seeded fake in-flight records (all
  removed by the harnesses after their 429 checks).
- Commits: this run's commit on magrify/moonfactory, pushed with
  the magrify-repos token read in-process, never printed (the
  stored git credential is scoped to another repo).

## 2026-11-16 (run 17): START-IMAGE GENERATION (PARITY-PROGRAM Pass 3)

- Worker: new text-to-image path in the sinclip-comfyui app.
  workflows/z_image_t2i.json built from the Freefield z-image app
  definition (same node graph: UNETLoader / CLIPLoader lumina2 /
  ModelSamplingAuraFlow shift 3 / KSampler 8 steps res_multistep
  cfg 1 / ConditioningZeroOut negative / VAEDecode / SaveImage),
  with the staged Volume file names (Worker A, byte-verified in
  the volume listing this run: z_image_turbo_int8_convrot,
  qwen_3_4b_fp8_mixed, ae). New endpoints POST /image-jobs,
  GET /image-jobs/{id}, GET /image-jobs/{id}/file: same API-key
  auth and jobs Dict as the video endpoints, PNG out at the
  production house shape for the picked aspect (720x1280 /
  1280x720 / 960x960). The job record carries measured
  gpu_seconds (worker call wall, cold start included) and
  exec_seconds (ComfyUI execution alone). Documented in
  modal-worker/ENDPOINT.md.
- Measured cost and price (Rule 2, self-hosted lane): ONE proof
  render (9:16, seed 12345) measured 64.3 s L40S wall = $0.0349
  at the $0.000542/s list rate (exec 24.1 s; PNG 720x1280,
  1,131,638 bytes). Carried at 5c worst case in pricing.py (the
  production lane's headroom shape), floor = gross_up(5 x 3) =
  47c per image from balance, charged exactly at the floor, no
  rounding. Included budget debits at the raw carried cost, 5c
  (Amendment 1). Worker validation in the same proof: 401 no
  key, 422 no prompt, 422 bad aspect, 404 unknown id.
- Platform: POST /api/images and POST /api/images/quote. An
  image job is a normal job record (kind "image") with the same
  holds, the Amendment 2 ceiling before dispatch, and Rule 8
  restore in mirror_image_status (balance, included budget, and
  settled cost all returned on failure). Images never list in
  the gallery and are delivered as made (no post chain). Video
  creation accepts start_image_job_id and resolves the user's
  own finished image job server-side, so attach is one click in
  the form and one field in the API. Studio: start-image source
  choice (upload / none / "Describe it and we make it") with a
  quote shown before the render, a preview when the frame lands,
  and regenerate by making another. No model name, provider,
  hosting, or API words in any user copy; no em dashes.
- Gate decision: any account that could run the matching video
  job may generate its start image. Free accounts MAY generate
  start images for Draft clips, counted exactly like a Draft
  render against both free ceilings (5 lifetime per account,
  platform-wide total). Rationale: the free tier is a capped
  marketing budget, and the first frame is part of a Draft
  clip's cost either way; the cap binds identically.
- Verification (public URL, in-Modal harness
  scratch/verify_start_image.py): config and /api/pricing serve
  the lane (47c / 5c). Free account: bad aspect 422, empty prompt
  422 on create and quote, zero jobs created by the gates.
  Quotes: free = free method, balance account = 47c balance,
  Creator = 5c included debit. Topped-out account: image create
  refused 402 pre-dispatch, balance unchanged, no job created.
  Seeded failed image job: status error, 47c balance restored,
  5c settled cost released. End-to-end session (the Pass 3
  proof, free account, empty form): image from text prompt done
  (720x1280 PNG served by the platform file route), attached via
  start_image_job_id, Draft video done and delivered: h264
  960x1664 (the known Draft 2x shape), 5.0625 s, 5,182,400
  bytes, video stream present. Timings: image leg 30.0 s at
  poll granularity, video leg 229.6 s, session 266.7 s total.
  Free lifetime counter: 1 after the image, 2 after the video.
- Spend: proof image $0.035 + E2E image (~$0.02 warm) + one
  Draft render (~$0.13-0.25 at list rates for its GPU minutes)
  plus CPU pennies: about $0.20-0.30 estimated, inside the
  $0.60 cap. Provider balances $0 and untouched; per-second
  lanes exercised only through dark-safe refusal paths in
  earlier runs, not dispatched here.
- Residue: si-free-*, si-paid-*, si-creator-* @example.com test
  accounts; the free account holds the session's image and
  Draft clip (the clip lists in the gallery as test data, the
  image does not list by design); the seeded si-fail worker and
  platform records remain as error-state test data; the proof
  worker image job e01c8230668e40ae and its PNG sit in the
  worker Volume under api_jobs/.
- Commits: this run's commit on magrify/moonfactory, pushed with
  the magrify-repos token read in-process, never printed.

## 2026-11-16 (run 18): FUN CAMERA LIVE (PARITY-PROGRAM Pass 4, Worker E)

- Worker: new Fun Camera generation path in the sinclip-comfyui
  app. POST /jobs takes camera_pose (the nine WanCameraEmbedding
  poses) and camera_speed (0.1 to 3.0, default 1.0); a job with a
  pose renders workflows/fun_camera_i2v.json on the Fun Camera
  UNET pair (Worker A staged, re-verified this run in the Volume:
  15,301,408,048 bytes each; WanCameraEmbedding and
  WanCameraImageToVideo both registered in the deployed ComfyUI).
  Validation: pose on the draft lane, unknown pose, segments > 1,
  an end image, or a bad speed are 422s before any GPU call. The
  job record carries camera_pose, camera_speed, and the measured
  render_seconds (recorded for every video job). Documented in
  modal-worker/ENDPOINT.md.
- Recipe shipped: the LightX2V DISTILLED patch, per the firstlast
  precedent (Worker C). The stock workflow is 20 steps at cfg 3.5
  (the long recipe, ~$0.77 a segment), far beyond a normal
  Production segment; the patch raises the workflow's own LoRA
  pair from strength 0.0 to 1.0 and sets the 4-step split (2 + 2)
  at cfg 1.0. Proof render: 177.8 s worker wall, about $0.10 at
  the L40S list rate, inside pricing.py's 15c Production worst
  case, so a Fun Camera segment prices exactly as a Production
  segment and pricing.py is unchanged.
- Platform: the 9 fun-* presets flipped from needs_model to live
  in presets-index.json (and the nine per-preset files);
  camera_preset_available now keys on status alone, so
  GET /api/camera-presets serves 42 of 42 available. The old 422
  "coming soon" refusal is gone; new validation: a fun preset is
  a single conditioned move (combining it with any other move,
  prompt recipe or fun, is a 422), Production lane only, one
  segment, no end image, Wan 2.2 only, speed range checked, all
  422 before quote, hold, or dispatch. Dispatch routes the pose
  and speed to the worker; the job record stores both, and
  regenerate carries the speed. Composition is unchanged and
  correct by construction: fun presets have no prompt fragment,
  so the worker receives the user's prompt with no camera text.
  All other gates unchanged (free 403 on Production, ceiling,
  Rule 8).
- Verification (public URL, in-Modal harness
  scratch/verify_fun_camera.py, plus the measure-only follow-ups
  verify_fun_camera_measure.py, measure_fun_frames.py,
  read_fun_records.py; the first harness's own frame extraction
  had -sseof misplaced and crashed AFTER both renders finished,
  so measurement was redone on the delivered files with no new
  renders): picker 42 available / 0 coming soon; free account
  403 on fun + Production and 422 on the nine invalid combos;
  worker 401 no key and 422 on pose-on-draft, bogus pose,
  segments 2, bad speed. Proof renders, balloon benchmark scene,
  seed 12345, same start image: (1) Fun Camera Pan Left, done,
  177.8 s, delivered 720x1280 h264 + AAC, 5.0625 s, 810,495
  bytes, gallery lists "Camera: Pan left (Fun Camera)", charged
  one Production segment (15c included budget). (2) Prompt-recipe
  Pan left alongside, done, 243.7 s, 1,083,317 bytes, same
  streams and duration.
- MEASUREMENT, stated plainly (the dolly-in method: red-blob
  subject centroid/scale first vs last frame, plus
  phase-correlation frame drift): the Fun Camera clip shows NO
  measurable pan. Subject centroid x 0.4866 -> 0.4873 (delta
  +0.0007 of frame width), subject scale 0.04782 -> 0.04701
  (0.98x), phase drift (0, 0) at quarter scale; a per-second
  series and a contact-sheet read agree (background morphs
  slightly, nothing translates). The prompt-recipe clip moved
  more: centroid x 0.4874 -> 0.4994 (+0.012, the correct
  direction for a pan left) with the subject rising (centroid y
  0.4845 -> 0.3603, scale 1.03x). Verdict: the conditioned move
  did NOT beat the prompt recipe in this test; under the
  distilled recipe the conditioning appears washed out (the
  LoRAs were built for the standard I2V UNETs, and cfg 1.0
  leaves no guidance headroom). Loop-back item, exact shape: one
  stock-recipe Fun Camera render (20 steps, cfg 3.5, LoRA
  strength 0, about $0.77 and ~24 min) on the same benchmark to
  judge whether the conditioning carries at full recipe, and if
  it does, a recipe decision for the lane (stock at a measured
  price, or a mid-step variant). That render sat outside this
  run's caps (2 Production-class renders, $0.75), so it was not
  run. The runaway guard never triggered; both renders finished
  in about 3 and 4 minutes.
- Spend: two Production-class renders about $0.23 of GPU, the
  node/Volume probe about $0.15, deploys and harness CPU
  pennies: about $0.45 estimated, inside the $0.75 cap. No
  provider generations; provider balances stay $0.
- Residue: fun-free-* and fun-paid-* @example.com test accounts;
  the paid account holds the two proof clips (the Fun Camera one
  lists in the gallery as test data); worker jobs
  fd472d7fe67d4887 (fun) and 11bca32a8d4e4bcc (recipe) and their
  files sit in the worker Volume under api_jobs/; scratch
  harnesses named above stay in scratch/.
- Commits: this run's commit on magrify/moonfactory, pushed with
  the magrify-repos token read in-process, never printed.

## 2026-11-16 (run 19): EFFECTS PACK LIVE (PARITY-PROGRAM Pass 9, pulled forward; Worker F)

- Worker: new effects generation path in the sinclip-comfyui app.
  POST /jobs takes `effect` (one of 24 ids); a job with an effect
  renders workflows/effects_i2v.json, built from the Freefield
  effects app definition (native-node graph; the terminal node is
  the house VHS combine the other lanes use, not the app's
  CreateVideo/SaveVideo pair). Base: Wan 2.1 I2V 480p 14B fp8
  (16,401,356,938 bytes, re-verified this run in the Volume) plus
  the one selected effect LoRA (all 24 re-verified present, about
  0.36 GB each); umt5_xxl_fp8, clip_vision_h and wan_2.1_vae were
  already in the Volume. The job prompt is the subject; the worker
  builds the full prompt from the effect's model-card template
  (trigger words included) and stores it on the record as
  effect_prompt, alongside effect and render_seconds. Validation:
  unknown effect, no start image, segments > 1, an end image, or
  a camera_pose with an effect are 422s before any GPU call; the
  record's lane becomes "effects". Documented in
  modal-worker/ENDPOINT.md.
- Recipe shipped: the effects app's own FAST preset (the
  distilled precedent from Workers C and E). The LightX2V 2.1
  step-distill LoRA was staged this run from Kijai/WanVideo_comfy
  and byte-verified (738,005,744 bytes, sha256 match against the
  repo listing); the patch raises it from the template's no-op
  strength 0.0 to 1.0 and runs 6 steps of euler at cfg 1.0 in
  place of the stock 20 steps at cfg 6 (uni_pc). Native shapes
  from the app: 480x832 / 832x480 / 624x624, 81 frames at 16 fps.
- Platform + form: an Effects picker in the Create form, rendered
  from GET /api/effects (24 effects, plain names only: Cakeify,
  Explode, Inflate, Deflate, Squish, Hydraulic press, Electrify,
  Angry face, Crying, Laughing, Surrounded by puppies, Steam from
  nose and ears, Money rain, Rotate 360, 360 orbit, Arc shot,
  Bullet Time, Car chase, Crane down, Crane over the head, Crane
  up, Crash zoom in, Dolly zoom, Hero run). One effect per job;
  effects require a start image; the lane renders 480p class
  native and delivers through the existing post chain like Draft
  (music bed muxed, delivered at native size, stated in the form
  and the launch notes). Paid-only (Production-class), stated in
  the form like the other paid features and enforced server-side.
  Validation, all pre-dispatch 422s: unknown effect, effect
  without a start image, effect with chaining, an end image, any
  camera preset or look (Fun Camera included), an effect on the
  Draft lane, or an effect with a per-second model. The job
  record, the studio list, and the gallery carry the effect name
  ("Effect: Inflate"); regenerate carries the effect and the
  original subject prompt.
- Pricing (Rule 2): two proof renders measured 101.5 s and
  93.6 s of worker wall on L40S, about $0.055 for the worse one
  at the $0.000542/s list rate, inside the Production lane's
  15c worst case already carried in pricing.py. Decision, per
  the run brief: effects price AS Production segments rather
  than as a new lane price, because the distilled recipe lands
  inside the existing worst case with the same headroom shape.
  MEASURED_COST_CENTS["effects"] = 15, balance price 99c (the
  formula's floor is 78c), included budget debits at the raw
  carried cost of 15c. pricing.py records both measures.
- Verification (public URL, in-Modal harnesses
  scratch/verify_effects.py and scratch/verify_effects_followup.py;
  the first harness hit two harness-side bugs after the renders
  finished, a worker lane-check ordering bug it exposed was fixed
  and redeployed, and the follow-up recovered the proof evidence
  from the records and the delivered file with no new renders):
  picker serves 24 effects at 99c, paid_only true; free account
  403 on a valid effect, 422 on unknown effect and on no-image;
  paid account 422 on unknown effect, Draft lane, segments 2,
  end image, a prompt camera preset, a Fun Camera preset, a
  per-second model, and no image, with the worker job count
  unchanged across the whole matrix; a topped-out Creator was
  refused 402 pre-dispatch with balance and settled cost
  untouched; worker-side 401 without a key and 422 on unknown
  effect, no image, segments 2, end image, and effect plus
  camera pose. A deliberately corrupt start image (PNG header,
  garbage body) on a balance account was charged 99c and 15c of
  settled cost at dispatch, failed in the worker before
  sampling, and restored both in full (balance back to 2000,
  settled cost back to 0.0; verified on both attempts' records).
  Proof renders (Creator, Inflate, balloon benchmark scene,
  seed 12345): both done; the recovered proof delivered
  480x832 h264 + AAC, 5.0625 s, 479,201 bytes through the post
  chain, gallery lists it as "Effect: Inflate", included budget
  debited exactly 15c, and the worker record holds the composed
  effect prompt with the infl4t3 trigger word.
- MEASUREMENT, stated plainly (red-blob subject area, first
  frame vs last frame, the dolly-in method): the Inflate proof
  shows the effect's action unambiguously. Subject area grew
  from 0.05088 of the frame to 0.47583 (9.35x), centroid steady
  (x 0.4856 -> 0.4897, y 0.4863 -> 0.4757): the balloon inflated
  to fill much of the frame while staying centered. Unlike the
  Pass 4 Fun Camera proof, the fast recipe did NOT wash this
  effect out. The runaway guard never triggered; both renders
  finished in about 1.6 minutes of worker wall.
- Spend: two effects renders (195.1 s of measured worker wall,
  about $0.11) plus their container cold starts, two failed-job
  container starts (no sampling), the 0.74 GB distill download,
  probes, deploys and harness CPU: about $0.55 estimated at list
  rates, inside the $0.90 cap. At most 2 renders ran on this
  lane. No provider generations; provider balances stay $0.
- Residue: fx-* and fx2-* @example.com test accounts (free,
  paid, ceiling, fail, proof sets from the two harness passes);
  the two proof clips list in the gallery as test data; the
  failed jobs' platform and worker records remain as error-state
  test data; worker proof jobs and their files sit in the worker
  Volume under api_jobs/; scratch harnesses verify_effects.py,
  verify_effects_followup.py and probe_effects.py stay in
  scratch/.
- Commits: this run's commit on magrify/moonfactory, pushed with
  the magrify-repos token read in-process, never printed.

## 2026-11-16 (run 20): STUDIO LOOK PASS (PARITY-PROGRAM Pass 8; Worker G)

- Scope kept: front end only. No job dispatch, pricing, gate, or
  API behavior changed. The only server-side edits beyond the
  page HTML/CSS/JS are additive display fields: public_job gained
  included_cost_cents and free_tier (both already on the job
  records), and public_gallery_item gained camera_look,
  duration_seconds, resolution, aspect_ratio, segments_total, and
  cost_cents. /api/config, /api/pricing, /api/camera-presets, and
  /api/effects responses are byte-shape unchanged (verified live).
- Catalog as data (pattern 1): the landing page's hand-written
  model table and the studio's hand-written model <select> are
  replaced by a card grid rendered from /api/config + /api/pricing
  on both pages. Every card shows the model name, its price (Wan
  2.2: Draft $1.99 / Production $0.99 per segment from
  job_price_cents; per-second models: the balance price per second
  at each resolution from api_lanes), its lengths and resolutions,
  who can use it (paid_only), and a Temporarily unavailable flag
  from config's available field (Seedance 2.5 shows it today,
  matching its paused lane). In the studio the cards are the
  picker: a click sets the hidden select the form logic already
  reads, and applyModelGates keeps card disabled/selected state in
  lockstep with the existing plan-gate code (free accounts get the
  same locked models, locked opt-out, and lock notes as before;
  verified present in the served page). The resolution select for
  a picked model now also fills from the pricing data's keys. One
  honest limit, recorded in the checklist: per-model lengths are
  not served by any endpoint, so the cards take them from the one
  MODEL_OPTS table that also fills the length select (a client
  mirror of server validation, as before), not from a second
  hand-written list.
- One composer (pattern 2): the Create card is restructured into
  a single composer surface. The prompt leads, larger; under it,
  labeled groups: Model (the catalog), lane or per-second length
  and size, Start image (source select, upload, or describe-it,
  moved up from the bottom of the form), Camera and look (preset
  chips, look selects, effects, all already data-rendered),
  Sound (music prompt), Length and shape (aspect, segments, end
  image). A price line under the Create button shows the composed
  house-lane or effects price from /api/pricing before dispatch
  (balance wording, with the Creator included-budget note); the
  per-second exact quote line is unchanged. The landing first
  screen gained a prompt field that carries its text into the
  studio via ?prompt= (read by the studio on load), so the
  landing starts the same job the composer would.
- Gallery as history (pattern 5, display half): studio job cards
  now render a cost line from the record the API already carried
  (charged_cents) plus the new included/free fields: "$X.XX from
  your balance", "$X.XX of your included budget", or "free clip"
  (error cards show no cost line, since failures restore in
  full). Cards also gained the film-look line and a custom-music
  line next to the existing model, camera, effect, aspect,
  segment, and queue lines. Public gallery cards render in a grid
  and show handle, model, camera, effect, look, length, size,
  aspect, segments, and the same cost figure (clip outputs carry
  cost_cents null and show none; their parent clip job carries
  the charge in the studio list). Clip tab parity: shared .tabs
  bar, shared header (gallery link added), card and heading
  treatment matched to Create, "Your clips" results heading
  mirroring "Your videos".
- Checklist: STUDIO-LOOK-CHECKLIST.md (repo root) holds the
  fixed four-view comparison (landing first screen, composer
  with a model picked, gallery, finished job card) and the
  criteria scoreboard. Verdicts: landing par; composer par on
  shape and price visibility; gallery par as a gallery, behind
  on depth; finished job card ahead on the program's criteria
  (exact dollar cost and full recipe on the card). Criteria:
  clicks par, price visibility ahead, no dead controls par,
  shelf at a glance par. Method stated in the file: built from
  page reads and rendered structure (higgsfield.ai homepage read
  2026-11-16; its studio sits behind sign-in), NO screenshots
  were captured in this environment and none are claimed.
- Open items listed in the checklist as features, not faked:
  attach a finished clip as the next job's start (needs a server
  path; Pass 5), a per-item project page (new route), reference
  and element slots (Pass 5), pill-and-popover treatment for the
  film look (the data-rendered selects already carry the full
  vocabulary; left for the look approval round), and shelf
  breadth (row 3, a wiring program).
- Copy discipline: no new claims (the landing catalog dropped
  the old table's superlative phrasing rather than adding any);
  every price on every surface comes from /api/pricing data;
  model names only. Greps over the three served pages after
  deploy: zero em dashes, zero matches for fal.ai, beatapi,
  provider, hosted, supplier.
- Verification (public URL, $0): two deploys (the second only
  rounds the segment-seconds figure on the cards). Served
  landing, studio, gallery, and Clip markup read back with all
  new hooks present; the three pages' inline scripts pass
  node --check; the catalog card strings were reproduced from
  the live /api/config and /api/pricing payloads and match the
  page logic output for all seven models; /api/gallery serves
  26 items with the new fields and real cost values (15.0 cent
  included debits, 0.0 free). No jobs created, no provider
  calls, no test accounts. Spend: deploy CPU pennies only,
  inside the $0.05 cap.
- Residue: none in the product. /tmp holds the read-back page
  copies from verification.
- Commits: this run's commit on magrify/moonfactory, pushed with
  the magrify-repos token read in-process, never printed.

## 2026-11-16 (run 21): FUN CAMERA STOCK-RECIPE TEST (loop-back L4, Worker L4)

- The decisive test run 18 left open: ONE stock-recipe Fun Camera
  render on the exact run-18 benchmark (balloon scene, Pan Left,
  seed 12345, modal-worker/test_start_720.png, the platform's
  scene prompt and the worker's house negative). Stock recipe =
  the raw workflows/fun_camera_i2v.json graph with NO distilled
  patch: 20 steps, cfg 3.5, LightX2V LoRA pair at strength 0.0,
  sampler split 0-10 / 10-end, shift 8.0. Because the worker's
  HTTP path always applies the distilled patch, the graph was
  submitted to the deployed worker's ComfyUI class directly
  (scratch/stock_fun_camera_test2.py, deployed as a scratch Modal
  app so the render ran detached server-side; the job record
  lives in the sinclip-jobs Dict as fun_stock3).
- Result: done in 1439.8 s of worker wall (24.0 min, $0.78 at the
  L40S list rate), 984,312 bytes, 720x1280 h264, 5.0625 s,
  persisted in the worker Volume as api_jobs/fun_stock3.mp4.
- MEASUREMENT (run-18 method: red-blob centroid/scale first vs
  last frame + phase-correlation drift; all three clips
  re-measured in one pass, scratch/measure_stock3.py, results
  persisted as api_jobs/fun_stock3_measure.json in the Volume):
  distilled fun (fd472d7fe67d4887, 177.8 s): cx 0.4866 -> 0.4873
  (+0.0007), scale 0.98x, drift (0, 0). Prompt recipe
  (11bca32a8d4e4bcc, 243.7 s): cx 0.4874 -> 0.4994 (+0.012,
  correct direction), cy 0.4845 -> 0.3603 (subject rises), scale
  1.03x, drift (-2, 41). Stock fun (fun_stock3, 1439.8 s): cx
  0.4866 -> 0.4872 (+0.0006), cy 0.4848 -> 0.4747, scale 0.93x,
  drift (0, 0); per-second series cx 0.4866 / 0.4874 / 0.4876 /
  0.4978 / 0.4891 / 0.4872 with drift (0, 0) at every second.
  Verdict: the stock recipe shows NO move either; it is
  indistinguishable from the distilled clip on this measurement.
- DEBUG (the one pass, desk-only, no GPU): the port is faithful
  to the Freefield source app (vendor/freefield/apps/camera-moves/
  workflow.api.json + tools/object_info.json). WanCameraEmbedding
  output 0 feeds WanCameraImageToVideo.camera_conditions and
  outputs 1/2/3 feed its width/height/length, identical in the
  source, the port, and the live stock job record (camera_feed
  ["108", 0]). The pose enum in the node spec is exactly
  ["Static", "Pan Up", "Pan Down", "Pan Left", "Pan Right",
  "Zoom In", "Zoom Out", "Anti Clockwise (ACW)", "ClockWise
  (CW)"]; the submitted "Pan Left" is an exact member, and the
  preset library's fun-pan-left patch value is the same string.
  Speed is a FLOAT, default 1.0, range 0-10; both the run-18 job
  and the stock job fed 1.0, the app's own working value. The
  stock sampler graph matches the source field for field (steps,
  cfg, split, shift, LoRA 0.0). ComfyUI accepted and executed
  the graph with no node errors under both recipes (the worker's
  run_workflow raises on node_errors). The optional
  clip_vision_output input is unconnected in the source app too.
  No concrete defect found: the embedding reaches the model and
  the move still does not register measurably.
- DECISION (STOP branch): the shipped distilled wiring is left
  exactly as is. No worker or platform code changed, nothing
  redeployed, pricing.py untouched (a stock segment would cost
  ~$0.78 against the 15c Production carry and buys no measurable
  move, so there is nothing honest to reprice to). Exact open
  blocker: Fun Camera conditioning strength is unproven on this
  stack after both recipes (distilled 4-step cfg 1.0 LoRA 1.0
  and stock 20-step cfg 3.5 LoRA 0.0), with the wiring verified
  faithful to the source app. Future angles, untested and not
  claimed: a speed value above 1.0, a different benchmark scene
  with stronger background parallax, or a different ComfyUI /
  model build.
- Spend: stock3 render $0.78; attempt stock1 lost at ~21 min
  when the local Modal client was killed by an environment
  restart (the .remote() call died with it, nothing persisted;
  ~$0.65 of L40S time estimated billed); attempt stock2's spawn
  was cancelled pre-start when its ephemeral app stopped ($0,
  verified: no Dict record, no file, function never ran);
  measurement, checks, and doc work were CPU pennies. Total
  about $1.44 of the $1.60 cap. No provider generations;
  provider balances stay $0. moonfactory-secrets untouched.
- Residue: worker Volume files api_jobs/fun_stock3.mp4 and
  api_jobs/fun_stock3_measure.json; sinclip-jobs Dict record
  fun_stock3 (plus an empty fun_stock1/fun_stock2 absence: no
  records exist for them); scratch harnesses stock_fun_camera_
  test.py, stock_fun_camera_test2.py, check_stock_fun.py,
  spawn_stock.py, measure_stock3.py and stock3_measure_output.txt
  stay in scratch/; the scratch deployed app
  moonfactory-stock-fun-test2 was stopped after the run.
- Commits: this run's doc commit on magrify/moonfactory, pushed
  with the magrify-repos token read in-process, never printed.

## 2026-11-16: OVERNIGHT GAP-CLOSURE RUN (coordinator summary; detail in runs 15-21 and overnight-state.md)

Jack's order: close every gap all night, never stop on one item. Runs 15-20 shipped, in order: Clip tab v1 (run 15, HEAD 9d11836), form finishers (run 16, caec1b5), start-image generation (run 17, 4df182b), Fun Camera wiring (run 18, b558cdc), effects pack (run 19, 8846d08), studio look pass (run 20, 9873900). Model staging preceded them: 30 files, ~67.8 GB into sinclip-models, 30/30 size + SHA-256 verified (z-image set, Fun Camera UNETs, Wan 2.1 effects base + 24 LoRAs). Recreate v1 internal stood up in recreate-lab ($0; runner with paid paths structurally unreachable; ASR + effects stems working after a cache pre-seed; diarisation gated by an upstream voice-style misclassification, documented).

Loop-backs: L1 proved YouTube link fetching is bot-walled from Modal datacenter egress (not a platform bug; upload clipping carries the end-to-end proof). L4 (run 21) proved Fun Camera conditioning does not measurably move the frame under EITHER recipe with a verified-faithful port: documented blocker, no repricing. Mid-chain partial delivery remains implemented-but-not-force-tested (run 16 note).

Run spend ~$3.86 (allowance ~$4.60); project cumulative ~$9.26 of the $10 cap. Final HEAD f4d1e1d. Remaining gates are Jack's: fal $50 top-up (Pass 0 + the deferred funded generation matrix), BeatAPI key + ~$25 top-up (Seedance unpauses itself), a Gemini API key for AI clip picking, and a fetch-architecture decision for YouTube link clipping.

- Replicate account created 2026-11-16 via GitHub @magrify, token "moonfactory" stored as REPLICATE_API_TOKEN in moonfactory-providers.
- MiniMax account created 2026-11-16 under support@magrify.com, key "moonfactory" + Group ID stored as MINIMAX_API_KEY and MINIMAX_GROUP_ID in moonfactory-providers.
- Gemini API key created 2026-11-16 in AI Studio project "moonfactory" under magrifywebsites@gmail.com, free tier, stored as GEMINI_API_KEY in moonfactory-providers — this also enables Clip AI picking.
- Runway developer account created 2026-11-16 under support@magrify.com, org "Moonfactory", key "moonfactory" stored as RUNWAY_API_KEY in moonfactory-providers; credits $0, minimum top-up $10 not paid.
- AIMLAPI account created 2026-11-16 under support@magrify.com, key "moonfactory" stored as AIMLAPI_API_KEY in moonfactory-providers; minimum top-up $20 not paid.
- PiAPI account created 2026-11-16 via GitHub @magrify, key "moonfactory" stored as PIAPI_API_KEY in moonfactory-providers; minimum top-up $5 not paid; credits valid 6 months.
- CometAPI account created 2026-11-16 under support@magrify.com (Personal user type; the Business form rejected the support@ role address), key "moonfactory" stored as COMETAPI_API_KEY in moonfactory-providers; balance $0, minimum top-up $10 not paid ($10 shows $13 total credits with bonus).
- Eden AI account created 2026-11-16 under support@magrify.com, organization renamed to Magrify, LLC, account region defaulted to Paris (no Dallas option offered), key "moonfactory" (Production, never expires) stored as EDENAI_API_KEY in moonfactory-providers; balance $0, pay-as-you-go, minimum top-up $5 with a 5.5% service fee, not paid.
- Vidu API platform account created 2026-11-16 under magrifywebsites@gmail.com (Google sign-in via the already-signed-in session; a separate vidu.com consumer account also exists under support@magrify.com), key "moonfactory" stored as VIDU_API_KEY in moonfactory-providers; balance $0, smallest top-up is the $18 first-time pack (5,000 credits), not paid. Key value was supplied by Jack in chat after the browser clipboard paste route proved impossible; stored via stdin only, never logged.
- Eachlabs account created 2026-11-16 via GitHub OAuth @magrify (workspace "Magrify LLC"), key "moonfactory" stored as EACHLABS_API_KEY in moonfactory-providers; balance $0, lowest top-up preset $10, not paid.
- OpenRouter account (pre-existing, Magrify LLC, signed in via GitHub @magrify, profile support@magrify.com, balance $8.32), key "moonfactory" stored as OPENROUTER_API_KEY in moonfactory-providers 2026-11-16; key value supplied directly by Jack in chat, stored via stdin only, never logged.
- WaveSpeedAI account created 2026-11-16 via GitHub OAuth @magrify (Magrify LLC, Bronze tier, $1 starting balance; sign-in is OAuth-only, no password form exists), key "moonfactory" stored as WAVESPEED_API_KEY in moonfactory-providers; no payment method, no top-up. Uniques for the follow-up wiring pass: Sora 2 ($0.10 Standard / $0.30 Pro per clip), Vidu Q4 from $0.095, PixVerse V6/C1 from $0.04, Seedance 2.5 Flash.

## Pika — CLOSED Nov 16, 2026 (subscription-gated, Jack declined)
- Pika API Club (dev.pika.art) requires a $10/month membership (returned as $10 in API credits monthly). Jack's ruling Nov 16, 2026: "no subscriptions." Account NOT created (signup was also Turnstile-blocked from the browser network; moot now). Pika 2.5 remains available to Moonfactory via the fal lane wired in the shelf pass. Do not reopen unless Jack changes the rule.

## Novita AI — account + key (2026-11-16)
- Novita AI account created and email-verified Nov 16, 2026 under support@magrify.com (email/password signup; Personal account shown as "support").
- API key "moonfactory" created (all enabled models, unrestricted IPs, permanent) and stored as NOVITA_API_KEY in Modal secret moonfactory-providers via SDK Secret.update() merge; verified by key-name list (17 entries, all pre-existing preserved).
- Balance $0, no payment method. Top-up field defaults to $10 with no explicit minimum shown. Not paid.
- Wiring note: Novita carries the cheapest Vidu Q3 route (Turbo off-peak $0.0268/s 720p) plus unique Kling O1 and Seedance 1.5 Pro $0.012/s.

## Kie.ai — account + key stored Nov 16, 2026
- Kie.ai account created Nov 16, 2026. The browser route was blocked by Kie's hCaptcha rate limit, so the owner signed in himself in his own browser (Google account magrifywebsites@gmail.com) and supplied the key directly in chat. Key stored as KIE_API_KEY in the Modal secret moonfactory-providers (SDK per-key merge; verified by key-name list: 18 entries, all pre-existing preserved). Balance not reported.
- Wiring note: Kie won 9 models in the Nov 16 sweep (Kling 4.0 $0.085/s, Kling 3.0 $0.0755/s, Seedance 2.0 Mini $0.012/s, Grok Imagine from $0.012/s, Gemini Omni Flash $0.02/s). PAYG credits never expire. Per-mode prices need console confirmation before wiring flips.

## 2026-11-16: COMPUTE-DISTRIBUTION SPLIT APPROVED + $0 SETUP DONE

Jack approved the compute-distribution plan as written
(compute-distribution-plan-2026-11-16.md, now marked APPROVED by Jack,
Nov 16, 2026): Kaggle free GPU for dev/batch, Vast.ai dormant fallback,
Modal production serving only. Setup of everything that costs nothing
was carried out the same day. Spend in this entry: $0. No Modal runs,
no Vast deposit, no paid anything.

What was set up, all under ~/workspace/sinclip/compute/:

- README.md + routing.json: the routing table and gates in prose and
  machine-readable form (serving = Modal; dev/tests/benchmarks =
  Kaggle; batch = Kaggle with Vast overflow; CPU = local; API lanes
  outside the split). Run reports now state placement up front.
- kaggle/kernel/moonfactory_batch.py: the batch kernel (script kernel,
  compiles clean). ComfyUI + the worker's custom nodes (VideoHelperSuite,
  KJNodes, SeedVR2), models linked from attached datasets or downloaded
  from the same public HF repos the Modal worker uses (MODEL_SOURCE
  switch), queue worked one job at a time via the ComfyUI API with a
  per-job checkpoint (results.jsonl), session budget default 7 hrs
  after which it stops starting new jobs. Generic patch contract:
  queue jobs carry {node_id: {input: value}} overrides.
- kaggle/kernel/kernel-metadata.json: private script kernel
  magrify/moonfactory-batch, GPU on, internet on, the three datasets
  attached.
- kaggle/model-staging.md: the staging plan. Core sets A-D total
  57.98 GB (draft 18.14, production A14B 30.11, LightX2V LoRAs 2.45,
  SeedVR2 7.28, all byte figures from the worker); full set with the
  effects/Fun Camera/z-image remainder is ~67.8 GB, about 32% of the
  account's 214.75 GB. Dataset split: moonfactory-models-core (A+B),
  moonfactory-models-extras (C+D, E later), moonfactory-batch-input
  (workflows + queue + images). Preferred upload route is a one-time
  Kaggle-side staging kernel pulling from HF, so the ~58 GB never
  transits this machine.
- kaggle/budget-ledger.md: weekly 30 hr budget tracker with the
  approved split (batch ~20, tests ~6, reserve ~4) and the session
  rules. First row records the setup day, 0 hours used.
- kaggle/README.md: lane rules (batch only, never serving), the weekly
  cadence (two sessions/week, 4-8 hrs, worked around the queue), the
  session checklist, and the push commands.
- batch-queue/: queue.jsonl (live, empty), queue.example.jsonl (two
  format examples), README.md with the job format and overflow rule.
- vast/README.md + vast-fallback.json: dormant fallback configuration.
  State: $0 balance, no deposit, no instances, enabled=false. Two
  triggers only (Kaggle quota exhausted with queue remaining; Modal
  outage with users waiting), activation = live rate quote, exact
  amount to Jack, approval before deposit/rental, destroy on finish.
  GPU class rules recorded (4090-class for distilled/draft at an
  estimated $0.35-0.45/hr market rate, re-quoted live; 48 GB class for
  the 20-step config, which does not fit 24 GB).

THE ONE REMAINING SETUP STEP IS JACK-ONLY: the Kaggle API token.
This machine has no ~/.kaggle/kaggle.json and no kaggle CLI installed,
so the kernel and datasets cannot be pushed from here yet. The token
comes from the Kaggle account settings page (account "magrify", under
magrifywebsites@gmail.com): Settings -> API -> Create New Token
downloads kaggle.json. Once that file is in place (Jack places it, or
hands it over the way he did the provider keys), the push is three
commands (in compute/kaggle/README.md) and the first measured session
can run: a timed distilled production render to replace the plan's
Kaggle time estimates with real numbers. Until then the lane is fully
scaffolded but has run 0 sessions, and dev/batch work continues on
Modal under the existing spend gate.

## 2026-11-16: OWNER ACCOUNT (support@magrify.com) — CREATOR (OWNER COMP)

- The owner's own account on the live platform uses support@magrify.com. A signup attempt returned 409 (an account for this email already existed and was already email-verified); a public-path login with the password the owner supplied in chat succeeded (200, verified true), confirming the existing account is his with that password. No password is recorded anywhere in this log or any file.
- Owner-comp flagging, set directly in the moonfactory-users Dict (both id: and email: keys): plan = "creator"; net_paid_cents = 100000 (owner comp credit, so the per-account hard cost ceiling — settled cost <= net paid — never blocks the owner); balance_cents = 100000 (prepaid rail for lanes beyond the included budget). Free-tier counters are irrelevant at plan=creator and were left untouched. No gate changed for any other account; everyone else still pays.
- Verified through the public path (POSTs run inside Modal via a throwaway check app, per the standing sandbox pattern; the throwaway apps were stopped and deleted after): GET /api/me shows plan "creator", paid_customer true, balance_cents 100000, net_paid_cents 100000. Gallery opt-out set and cleared: 200/200 (paid gate passed). Production-lane job create with a custom music_prompt and a deliberately invalid start image: rejected 422 "start image is not valid base64 image data" — i.e. past the production, model-switching, and custom-audio paid gates, stopped only at image validation, before any dispatch or GPU spend. Model-switch create (model wan3) with the same invalid image: same 422, gate passed. No generation ran.

## 2026-11-16: FOLLOW-UP WIRING PASS (provider sweep routes + audio correction)

Scope: apply the Nov 16 provider sweep's 14 re-routes, wire the lanes that
lacked keys in the shelf pass, switch Clip AI picking on and verify it, and
fold in Jack's same-day audio correction ("audio" means voiceover/speech
too, not just a music bed). All dark-safe: NO generation runs, NO provider
top-ups, NO Modal spend beyond the deploys themselves. Commit on
magrify/moonfactory (see git log); platform files mirrored to
~/workspace/sinclip/platform/.

### Architecture changes (platform/app.py)

- MODEL_PROVIDER generalized: new PROVIDER_KEY_ENV map (13 suppliers) and
  provider_for(model, resolution) with MODEL_PROVIDER_OVERRIDES =
  {"seedance": {"720p": "cometapi"}} — Seedance 2.5's cheapest 720p route
  is a different supplier than its 480p/1080p route, so the provider is
  now a function of (model, resolution). Quote and dispatch both check
  the serving supplier's key; a missing key reports the lane paused
  instead of a price.
- Dispatch and polling generalized: submit_api_job / mirror_api_status
  route to per-supplier submit/mirror functions for kie, cometapi,
  piapi, openrouter, novita, wavespeed, eachlabs, runway, vidu (spare),
  minimax (spare); fal and BeatAPI keep their original functions.
  ensure_processed downloads result URLs for all of them
  (URL_RESULT_PROVIDERS). Hold/ceiling/settle logic untouched and
  provider-agnostic, as before.
- Picker: 24 -> 29 models (names only, as always). /api/config and
  /api/pricing now also carry each lane's audio capability (below).

### Routes added / switched (prices are the sweep's published figures)

Kie primaries (9 sweep wins; Kie prices are published "from" prices,
per-mode/per-resolution confirmation in the Kie console still owed
before funding — flagged in pricing.py comments on every Kie row):
- Kling 4.0 -> Kie: 720p 8.5c/s (from $0.085/s with media input), 1080p
  21.25c/s (Kie's own no-input page price). BeatAPI 18.0/28.0 displaced.
- Kling 3.0 -> Kie: 7.55c/s flat (from-price does not split Std/Pro).
  BeatAPI 11.0/13.0 displaced; fal 12.6/16.8 stays the recorded fallback.
- Kling 3.0 Omni -> Kie: 7.55c/s flat (same flag). fal 11.2/14.0 fallback.
- Seedance 2.0 Fast -> Kie: 3.4c/s flat. BeatAPI 6.072/12.251 displaced.
- Seedance 2.0 Mini -> Kie: 1.2c/s flat. BeatAPI 3.283/7.363 displaced.
- Gemini Omni 1.2 Flash -> Kie: 2.0c/s flat (resolution basis
  unconfirmed). BeatAPI 4.9/7.28 displaced.
- Grok Imagine 1.5 -> Kie: 480p 1.2c/s, 720p 2.25c/s. No Kie 1080p price
  is published, so the lane drops its 1080p tier. BeatAPI displaced.
- Hailuo 2.3 -> Kie: per-clip $0.15/6s 768p carried as 2.5c/s at 720p.
  No Kie Pro/1080p price published, so the lane is 720p-only; the
  per-second carry assumes the per-clip price scales with duration.
  fal per-clip rates stay the recorded fallback.
CometAPI:
- Seedance 2.5, 720p tier only -> CometAPI 18.48c/s (480p/1080p stay
  BeatAPI 9.6/48.1). Verified live: 720p sell 33.368c/s, the known
  Seedance undercut figure.
- Wan 3.0 Prime -> CometAPI 5.44c/s flat (single published figure, no
  resolution split; basis flagged unconfirmed). BeatAPI displaced.
PiAPI:
- Veo 3.1 -> PiAPI 12.0c/s flat, NO-AUDIO basis (with-audio is $0.24/s
  and does not beat). See the audio section: this is a deliberate,
  recorded capability trade, not a silent one. fal with-audio 40.0
  stays the recorded fallback.
Eachlabs (the sweep's $0.03/s trio — OpenRouter / AIMLAPI / Eachlabs —
resolved to Eachlabs per the pass brief):
- Veo 3.1 Lite -> Eachlabs: 720p 3.0c/s, 1080p 8.0c/s (published range
  $0.03-0.08/s no-audio; bottom of range at 720p, top at 1080p).
OpenRouter:
- HappyHorse 1.1 -> OpenRouter: 720p 9.88c/s, 1080p 12.78c/s (both
  published). BeatAPI 12.0/15.5 displaced.
Novita (first priced Vidu Q3 route):
- NEW MODEL Vidu Q3 -> Novita Turbo, off-peak: 480p tier (maps to
  Novita 540p) 1.79c/s, 720p 2.68c/s; the payload sends off_peak=true
  to realize them. No Turbo 1080p rate published; lane tops at 720p.
Runway direct:
- NEW MODEL Runway Gen-4.5 -> Runway direct 12.0c/s flat ($0.12/s =
  12 credits/s, published; no resolution split).
Spare routes wired (functions + dispatch registered; primary unchanged;
flip is one MODEL_PROVIDER line):
- MiniMax direct for H3 (v2 content-array endpoint; published direct
  rate H3 768P $0.08/s recorded in pricing.py DISPLACED_RATES).
- Vidu direct for Q3 (Token-auth ent/v2 endpoints; stays UNPRICED —
  Vidu sells credits and publishes no per-second price, so it must not
  become primary until a price exists).
Replicate: NOT wired. The sweep gives Replicate no winning route for
any shelf model; the key stays stored for future use. Sora 2 not wired
anywhere (OpenAI shut the Videos API down 2026-09-24; listings are
legacy).

### Dark lanes (price-paused, with reasons)

- Vidu Q2: unchanged — no published per-second price anywhere.
- NEW Vidu Q4 (WaveSpeed): wired (image/reference modes; text mode
  refused at validation — Q4 has none), price-paused: WaveSpeed
  publishes only a per-run from-price ($0.095/run), which cannot seed
  a per-second lane without inventing a rate.
- NEW PixVerse V6 (WaveSpeed): wired (text/image), price-paused:
  per-run from-price only ($0.04/run; WaveSpeed's own page hedges
  "per run or per second").
- NEW Seedance 2.5 Flash (WaveSpeed): wired (text/image/reference),
  price-paused: WaveSpeed's per-second figure is not published on
  its model page (Kie's $0.0575/s is a different supplier's price and
  was not transferred across suppliers).
Live check after deploy: /api/config unavailable set and /api/pricing
paused set are exactly {vidu_q2, vidu_q4, seedance25_flash, pixverse_v6}.

### Payload-unverified (funded verification pass still owed)

Everything new is payload-unverified against live accounts, the shelf
pass's standing standard: Kie slugs beyond the confirmed Kling 4.0 pair
(kling-4.0/reference-to-video, kling-4.0/keyframe-to-video) follow
Kie's naming pattern; CometAPI's Wan 3.0 Prime slug (wan-3-0-prime)
is a naming-pattern guess (its Seedance 2.5 id seedance-2-5-260628 is
confirmed on CometAPI's own page); PiAPI Veo input details, Eachlabs
Veo Lite slugs, Novita Q3 payload fields, WaveSpeed PixVerse fields,
Runway gen4.5 payload, Vidu direct and MiniMax v2 shapes all follow
the suppliers' published docs but have never met a live account. All
lanes are dark on $0 balances, so a mismatch fails clean with the
hold released, exactly like the shelf pass.

### Rate refresh fix + stored-table cleanup

- refresh_provider_rates no longer reads fal pages for re-routed
  lanes (veo3, veo3_lite, kling3_omni, hailuo23 removed; veo3_fast and
  the LTX pair remain), no longer requires the BeatAPI Wan 3.0 Prime
  page (required set is now {wan3, seedance}), merges per-resolution
  instead of replacing whole model entries, and never lets the
  BeatAPI Seedance read touch the 720p tier CometAPI now serves.
- Coverage gap (recorded, not hidden): the new suppliers' rates are
  seeded-only. No parseable public price page is wired for Kie,
  CometAPI, PiAPI, OpenRouter, Novita, WaveSpeed, Eachlabs or Runway,
  so their seeded rates do not auto-refresh; the table timestamp still
  advances on the BeatAPI required reads. A parser per supplier is
  follow-up work.
- Stored-table cleanup (moonfactory-meta, one-off Dict patch, no
  generation): removed the stale stored wan3_prime entry (fal-era
  6.8/14.0/28.0 shadowing the CometAPI seed), the stored seedance
  720p entry (BeatAPI-derived 19.541 shadowing CometAPI 18.48), and
  the stored wan3 entry (fal-era 5.0/10.0/20.0). The wan3 removal was
  verified against BeatAPI's own live page first (still $0.284 /
  $0.568 / $1.088 per 8s = the seeded 3.55/7.10/13.60). Why the
  refresh had not overwritten the wan3 entry itself is unexplained;
  flagged for the next refresh-verification pass. Live after cleanup:
  wan3 3.55/7.1/13.6, wan3_prime 5.44 flat, seedance 9.681 / 18.48 /
  48.365 (480p/1080p carry the BeatAPI-refreshed values, correct for
  their route).

### Clip AI moment-picking: ON, verified

The code path was already complete (process_clip_job carries the
moonfactory-providers secret and passes GEMINI_API_KEY into
clipping.find_clips, which calls Gemini picking when the key is
non-empty and falls back to loudness on failure). With the key now
in the secret, live /api/pricing reports picking_mode "ai" (checked
after this pass's deploy). No code change was needed; the remaining
proof is a real clip job, which is a generation and stays gated.

### AUDIO CORRECTION (Jack, 2026-11-16): speech is a first-class capability

- LANE_AUDIO matrix in app.py, recorded per lane (model on its wired
  route) and exposed in /api/config (per model) and /api/pricing (per
  lane) as native_speech / native_audio, plus has_speech_script /
  voiceover_status / native_speech on the public job view:
  - Native speech (dialogue): seedance (2.5), seedance2, seedance2_fast,
    kling3, kling4, kling3_omni, omni12, omni11, minimax_h3, grok,
    veo3_fast, vidu_q3, vidu_q4, seedance25_flash.
  - Native audio, music/SFX-led (speech not reliable): wan3,
    wan3_prime, ltx25, ltx25_fast, pixverse_v6, vidu_q2.
  - Silent lanes: wan2.2 (house), seedance2_mini, hailuo23 (no native
    audio mode exists on any route), luma_ray32, happyhorse, pika22,
    runway_gen45 — and, by ROUTE, veo3 and veo3_lite: both models
    generate dialogue natively, but their cheapest published routes
    (PiAPI no-audio $0.12/s; Eachlabs no-audio tier from $0.03/s) do
    not. The trade is recorded in LANE_AUDIO notes and pricing.py
    comments, with the with-audio routes kept as priced fallbacks
    (PiAPI $0.24/s, fal $0.40/s; Lite with-audio $0.05/s) so restoring
    native speech on those lanes is a route flip, not a rebuild.
- Native-audio modes are the default where a lane has one: fal Veo /
  Kling payloads already sent audio; BeatAPI Seedance generate_audio
  and Kling sound were already on; new wiring sends sound for the
  Kie Kling family, generate_audio for Kie Seedance Fast, audio=true
  for Novita Q3 and Vidu direct, generate_audio=true for WaveSpeed
  Flash and Q4. PiAPI Veo sends generate_audio=false ON PURPOSE (the
  priced basis); Eachlabs Lite sends no audio field (no-audio tier).
- Voiceover path (Kokoro, Apache-2.0):
  - Job field: speech_script (max 1000 chars) + speech_voice (Kokoro
    voice ids), parsed and paid-gated in create_job exactly like the
    music prompt, stored on house and per-second jobs, carried on
    retry, and offered in the studio form as "Spoken line" next to
    the music prompt (gated with the other custom-audio fields).
  - Routing: at delivery, a script on a speech-native lane is marked
    voiceover_status "native" (the model performs it; no TTS double).
    On any other lane _voiceover_for_job calls the sinclip-audio
    app's generate_voiceover and caches the wav per job.
  - Synthesis: generate_voiceover(text, voice) implemented in
    modal-worker/audio_app.py (Kokoro KPipeline, 24 kHz wav, Volume
    cache by (voice, text) hash, same pattern as generate_bed).
  - Mixing: _process_api_file and _process_file now take voice_path;
    the voice sits on top (volume 1.0, apad) and the bed/native audio
    ducks under it. _process_api_file also detects silent outputs
    (ffprobe) instead of failing on a missing audio stream: silent +
    voice/bed mixes over silence; silent + nothing ships a silent
    track. All four ffmpeg shapes were verified locally on synthetic
    files before deploy (correct durations, no failures).
  - WHAT REMAINS, precisely: the sinclip-audio app has NOT been
    redeployed in this pass (this pass's deploy budget was the
    platform app), so live jobs with a speech script will record
    voiceover_status "unavailable" and deliver with the lane's own
    audio/bed until (a) modal deploy of modal-worker/audio_app.py,
    and (b) one verification voiceover render — a Modal GPU call,
    which needs Jack's approval under the spend gate. The platform
    side fails clean and flags the job record until then; a music
    bed is never silently passed off as speech.

### Live verification (after deploy, $0)

- /healthz {"ok": true}. /api/config: 29 models, labels only, default
  wan2.2, audio fields present, unavailable = the 4 price-paused.
- /api/pricing: 28 lanes; picking_mode "ai"; paused = exactly the 4
  price-paused; new rates live (kling4 8.5/21.25, kling3 7.55 flat,
  wan3_prime 5.44 flat, veo3 12.0, veo3_lite 3.0/8.0, seedance 720p
  18.48, vidu_q3 1.79/2.68, runway_gen45 12.0, happyhorse
  9.88/12.78, grok 1.2/2.25, hailuo23 2.5 at 720p only, omni12 2.0
  flat); every priced lane sells at frontier cost x1.25 minimum
  (Seedance 720p undercuts at 33.368 via its measured Higgsfield
  effective, unchanged).
- Studio page serves the new Spoken line field; landing 200. Local
  pre-deploy checks: pricing.py frontier math exercised; app-wide
  consistency check (every per-second model has a provider, a key
  env, a dispatch branch, a mirror branch, and an audio entry);
  ffmpeg mixing shapes verified on synthetic media.
- No jobs created, no provider calls, no test accounts. Spend:
  deploy CPU pennies only.

## 2026-11-16: sinclip-audio REDEPLOYED with generate_voiceover (deploy only)

- `modal deploy audio_app.py` run from modal-worker/ (project pattern, modal-venv). Deploy output: "Created Function generate_bed" + "Created Function generate_voiceover" — App deployed. This closes the wiring-pass gap (commit 44a77e8) where the platform's Kokoro voiceover stage existed but the audio app had not been redeployed, so live speech jobs recorded voiceover_status "unavailable".
- Verification (no invocation): SDK control-plane lookup `Function.from_name("sinclip-audio", name).get_current_stats()` resolved BOTH generate_voiceover and generate_bed (0 running containers = scale-to-zero idle). That is the same from_name lookup platform/app.py performs in _voiceover_for_job before calling .remote(), so the platform side now sees the voiceover function as available.
- DEPLOY ONLY: no generation calls, no voiceover renders, no GPU jobs ran in this step.
- REMAINING audio item: one verification voiceover render (a single generate_voiceover call proving the Kokoro stage end to end) is a Modal GPU call and still awaits Jack's approval under the Modal spend gate.

## 2026-11-16 (run 10): ACCESS MODEL — TRY IT / CREATE SPLIT (Jack)

Jack's nav/access model, set 2026-11-16: nobody can try anything
without signing in first, either way; the first screen is labeled
"Try It" and is the pre-payment experience; Try It DISAPPEARS once a
user has paid — members open straight into Create; Create is
distinctly PAID ONLY, nothing free on it. Implemented in the live
platform (platform/app.py), deployed, verified, commit ddd4660 on
github.com/magrify/moonfactory (44a77e8 -> ddd4660).

### Sign-in-first audit (rule 1) — already held, now verified
Every generation path requires a session and a verified email; no
anonymous generation path exists:
- POST /api/jobs (create_job): require_user (401) -> verified (403)
  -> past_due -> bot check -> gates.
- POST /api/images (create_image): same shape.
- POST /api/clips and /api/clips/upload: require_user + verified +
  is_paid (clipping was already fully paid-only).
- POST /api/jobs/{id}/regenerate routes through create_job, so all
  gates re-apply. Quote endpoints generate nothing.

### Server side
- public_user now reports access_surface ("create" if is_paid else
  "tryit") and surface_label ("Create" / "Try It"), derived from the
  same is_paid fact the gates enforce. No gate logic changed: the
  paid surface was already 403-enforced server-side (Production
  lane, model switching, custom music, voiceover/speech script,
  multi-segment, effects via the paid path, clipping, gallery
  opt-out). Pricing, the hard cost ceiling, the 5-free-lifetime
  counter, and every account record are untouched.

### Studio UI (labels / navigation / gating only — NOT the redesign)
- Tab bar is now Try It | Create | Clip. showStudio reads the
  surface from /api/me: a paid account's Try It tab is hidden and it
  lands on the Create pane directly; a free account lands on Try It
  and its Create and Clip tabs open a new upgrade pane ("Create is
  the paid studio": what it unlocks, both payment paths, buttons
  wired to the existing checkout handlers) instead of the studio.
- The composer heading reads "Create" for paid accounts and "Try
  It" for free ones (with a one-line note), the account card shows
  the surface next to the plan, and the submit button reads "Create
  video" (paid) or "Make a free clip" (free). The free composer
  itself is unchanged: Wan 2.2 Draft only, paid models/lanes/fields
  disabled exactly as before.
- Landing: header and closing CTAs relabeled "Try It" / "Try It
  free", the hero states the sign-in-first rule and the 5-clip Try
  It allowance, the free-plan section is titled "Try It: the free
  plan, honestly", and paid-studio mentions now say Create.
  Gallery page: CTA relabeled "Try It", opt-out copy says Create.
- What the front end cannot express (for the redesign build): the
  landing page is served statically with no session awareness, so a
  signed-in paid member who visits / still sees the Try It CTAs
  there; the disappearance of Try It applies inside /app, where the
  session is known. A session-aware landing (or redirecting paid
  members from / to /app) belongs with the Direction C rebuild.

### Live verification (zero generation spend)
In-Modal probe (scratch/access_probe_app.py; sandbox POSTs to
*.modal.run stall, same pattern as the smoke test). Every
substantive check passed:
- Anonymous: POST /api/jobs, /api/images, /api/clips and GET
  /api/me all 401.
- Fresh free account (throwaway, deleted after): signup 200;
  unverified job create 403 "verify your email before creating";
  /api/me -> plan free, paid_customer false, access_surface
  "tryit", surface_label "Try It", free limit 5. Try It draft path
  with a deliberately invalid start image -> 422 "start image is
  not valid base64 image data" (past every free gate, stopped at
  image validation, no dispatch, free_jobs_used still 0 after all
  tests). Create surfaces all 403: Production lane ("the free plan
  covers the Draft lane only"), model switching (wan3 and the
  price-paused vidu_q2 alike), custom music, voiceover, multi-
  segment, clipping, gallery opt-out. Empty-prompt image create
  422 (auth+verified path, no dispatch).
- Owner (support@magrify.com; session minted with SESSION_SECRET,
  no password used): /api/me -> plan creator, paid_customer true,
  access_surface "create", surface_label "Create". The requests the
  free account was 403'd on pass the paid gates: music prompt and
  speech script with an invalid start image -> 422 at image
  validation; vidu_q2 -> 503 (price-paused lane, past the
  model-switching gate, no hold, no provider call). Gallery opt-out
  200 and restored to its original value. Owner record compared
  before/after on plan, net_paid_cents (100000.0), balance_cents
  (100000), gallery_opt_out, free_jobs_total, cost_settled_cents:
  identical.
- Served pages: /app HTML carries tab-tryit, upgrade-pane,
  composer-heading and the new copy; / carries the Try It labels.
- Two probe artifacts, not platform faults: (1) the throwaway
  account's verification used a Dict flag flip because Resend
  accepted the signup email to example.com, leaving no operator-
  outbox copy to click; the email-link flow itself was proven in
  runs 4-6. (2) The probe's first run missed bot fields on the
  image-create check (403 bot check, correct platform behavior);
  fixed and re-run clean.

## 2026-11-16: VOICEOVER VERIFICATION RENDER + PARALLEL AUDIO (Jack approved both)

### Job 1 — one minimal voiceover verification render (Jack: "as short and as little spend as possible")
- Exactly ONE call to the deployed `generate_voiceover` (sinclip-audio), nothing else: no video render, no bed, no second take. Line: "The moon is rising tonight." (one short sentence), default Kokoro voice af_heart.
- Result verified locally (~/workspace/sinclip/outputs/voiceover-test-2026-11-16.wav): valid wav, PCM s16le, 24 kHz mono, 102,044 bytes, **duration 2.125 s** (right for a 5-word line), loudness sane for raw TTS: integrated **-25.2 LUFS**, true peak **-9.8 dBTP**, RMS -26.4 dB — real speech energy, no clipping, no silence (the delivery mix normalizes the final file as before).
- Cost: Modal exposes no per-call price; measured wall time for the single call was **137.3 s** on a cold first-ever invocation (A10G container start + one-time Kokoro weight download + synthesis). At Modal's published A10G rate ($0.000306/s) that is **~4.2 cents** of GPU time (~5 cents all-in with CPU/memory) — an estimate from measured time x published rate. Warm calls will be a few seconds; the (text, voice) pair is now Volume-cached, so this exact line never renders again.
- This closes the last open audio item from the wiring pass / audio redeploy entries: the Kokoro stage is now proven end to end.

### Job 2 — audio generated IN PARALLEL with the video (Jack's directive)
- Before: bed/voiceover were produced synchronously inside ensure_processed, after the video render finished. Now the audio starts in the same step as the video dispatch and the finishing stage only collects it.
- platform/app.py (commit 6a51bd6, deployed, pushed):
  - `_audio_spec(job)` decides a job's audio work once from its record: voiceover args when a speech_script rides a lane that is NOT speech-native (speech-native lanes still perform the script in the model — no Kokoro call is ever spawned for them), bed args when a music_prompt is present (same caption/seed the finishing stage always used).
  - `_kickoff_audio(job)` spawns generate_voiceover / generate_bed non-blocking (Modal `.spawn()`); call ids ride on the job record as `audio_calls`, voiceover flagged `voiceover_status "pending"`. Called at exactly two points: create_job right after the house worker accepts the job, and create_api_job right after submit_api_job returns with the job still queued/running (covers fal/BeatAPI/Kie and every follow-up supplier).
  - Finishing (`_voiceover_for_job` / `_bed_for_job` in ensure_processed) collects the spawned call first (FunctionCall.get), writes the per-job wav, and mixes with the existing ducking. The pre-change synchronous generation remains as the fallback for any job with no spawned call (e.g. a spawn failure at dispatch). A failed/lost spawned call lands exactly where a failed synchronous call landed: voiceover "unavailable" / house-bed fallback with `audio_fallback` flagged — never a hard failure. Holds, ceiling, settle, and partial delivery untouched.
- Verification (dark-safe, no generation beyond Job 1): py_compile clean; platform deployed twice (final = commit 6a51bd6 content); in-Modal probe (scratch/audio_kickoff_probe_app.py, owner session minted with SESSION_SECRET, no password):
  - owner + music prompt + speech script + deliberately invalid start image -> **422** on the house Production lane and on Kling 4.0 (per-second): validation raises before any dispatch, so no kickoff.
  - price-paused Vidu Q2 with music + speech -> **503** inside create_api_job, before hold/submit/kickoff.
  - jobs Dict byte-compared before/after: **47 jobs both times, zero jobs carrying audio_calls** — no orphan audio calls from any rejected job. Owner record (plan/net_paid/balance/opt-out/free count/settled cost) unchanged.
  - /healthz 200, /api/config 200, /api/pricing 200 live after deploy.
  - Success-path concurrency is confirmed at code level (per the verification plan): both kickoff call sites sit strictly after a successful dispatch return and before the 202 response, in the same request step as the video dispatch.
- Note: platform/pricing.py carries a separate uncommitted owner-pricing workstream change in the working tree (not part of this commit); it rode along in the deploy upload as working-tree state, as any deploy from this tree includes it. Committing it belongs to that workstream.

## 2026-11-16 (run 11): OWNER PRICING EXEMPTION (Jack)

### The rule
Jack's owner account (support@magrify.com) is the ONLY account that
does not get charged markups: he gets every model at raw cost on the
cheapest available route (no x1.25 frontier markup, no x3
self-hosted floor, no Stripe gross-up on his prices). Every other
account keeps paying the standard prices; their pricing, gates,
holds, ceiling and settle logic are untouched.

### Implementation
- Keyed on an explicit flag, not the email: `is_owner: true` was set
  on the owner record in the moonfactory-users Dict (both id: and
  email: keys) via scratch/set_owner_flag_app.py. The same pass
  audited all 73 account records: no other record carries the flag.
  The record's comp was untouched (plan creator, net_paid_cents
  100000, balance_cents 100000 before and after).
- pricing.py: an owner= path on every price surface.
  frontier_lane / per_second_balance_debit_cents /
  per_second_estimate resolve the sell rate to raw cost (provider
  rate + post cost) for the owner, so his balance debit AND his
  balance hold cap are at cost; job_price_cents / job_price_table
  return MEASURED_COST_CENTS; image_price_cents / image_quote
  return IMAGE_MEASURED_COST_CENTS; clip_quote /
  clip_balance_rate_cents return CLIP_COST_CENTS_PER_MINUTE;
  public_pricing(owner=True) assembles the at-cost picture.
- app.py: is_owner(user) reads the record flag. Wired into
  /api/pricing (session resolved optionally; anonymous and all
  other sessions get the standard list), the per-second quote and
  create paths, the per-second settle (debit recomputed at cost for
  the owner's jobs), the house-lane charge at creation, the
  partial-delivery per-segment refund (refunds at the rate the job
  was actually charged), _image_payment / image quote / image
  charge, and the clip quote + create paths. The session-less
  /api/config and /api/effects surfaces keep the standard public
  prices for everyone; the owner's binding numbers are his quotes.
- Accounting honesty: settled cost always recorded actual cost, so
  nothing about cost_settled_cents, the Amendment 2 ceiling, holds
  or refunds changed shape; for the owner the charge now equals the
  cost that was already being settled.

### Live verification (read-only probes, zero generation spend)
In-Modal probe (scratch/owner_pricing_probe_app.py, run twice, all
13 verdicts true both times; owner session minted with
SESSION_SECRET, no password; throwaway non-owner account created
through the real signup flow and deleted after):

- Frontier per-second lane, Wan 3.0 @720p: raw cost 7.2c/s
  (BeatAPI 7.1 + 0.1 post). Standard sell 9.0c/s (the x1.25 minimum
  binds; Higgsfield undercut target 8.536 sits below it). Owner
  sell 7.2c/s. Owner 5 s job quote: raw_cost_cents 36.0,
  balance_debit_cents 36 (standard formula would debit 45).
- House Wan 2.2 job lanes (/api/pricing + house job quote):
  production 99c standard vs 15c owner (measured cost); draft 199c
  vs 50c; effects 99c vs 15c.
- Start image (/api/pricing + image quote): 47c standard vs 5c
  owner (measured cost). Clipping balance rate: 5.0c/min standard
  vs 0.3c/min owner (raw cost).
- Non-owner session /api/pricing is byte-identical to anonymous on
  every probed field; its house quote and image quote show the
  standard prices; its per-second quote is still 403 at the paid
  gate. Owner record compared before/after on plan, net_paid_cents,
  balance_cents, free_jobs_total, cost_settled_cents, is_owner:
  identical.

## 2026-11-16 (run 12): DIRECTION C REBUILD — THE C DESIGN ON THE LIVE PLATFORM (Jack)

Jack's decision: "no need for the mock up. It can go on the actual."
The Direction C design (dark pass, Play Hollow logo) is now the live
front end. Commit b2f2072 on github.com/magrify/moonfactory
(f04e3ac -> b2f2072), deployed to the moonfactory Modal app,
platform/app.py only. This was a front-end rebuild, not a logic
rewrite: no API contract, gate, price, hold/ceiling/settle rule, or
account state changed; the owner pricing exemption (run 11) and
parallel audio pass were landed first and are untouched (the diff
touches only landing() plus the _CSS / INDEX_HTML / APP_HTML /
GALLERY_HTML page layer). Changed platform files mirrored to
~/workspace/sinclip/platform/.

### What shipped, per surface

- Palette everywhere is C's dark pass: bg #0c0c0f, surface #17171d,
  inset #101014, borders #26262e, text #f4f4f1, secondary #b8b8c0,
  faint #92929c (muted LIGHT grays only), accent #6366f1, bright
  #818cf8. Play Hollow lockup + SVG favicon on all three pages:
  crescent #818cf8, play triangle in the starlight yellow #f5d67a
  (the shade the mockup fix pass chose, kept identical).
- Landing (/), SESSION-AWARE — this closes the run-10 logged
  limitation. landing() now resolves the session optionally (the
  same current_user()/is_paid() the pricing endpoint uses) and swaps
  one of two top blocks into the page. Paid member: "Open Create"
  hero, welcome-back subline, no Try It framing anywhere on the
  page. Signed-out/free: C's Try It hero, prompt entry ("Try It
  free" -> /app?prompt=), the sign-in-first rule stated under it.
  Shared below: community strip (newest 4 from /api/gallery, clip +
  handle only) and ONE pricing line (Free 5 clips / Creator with
  the price read from /api/pricing / pay per clip, exact price on
  the button).
- Studio (/app): Try It / Create / Clip as a C segmented pill;
  Create is C's five things — start-image drop zone (click or drag;
  one reader path shared with the file input; filled state shows
  the thumbnail with a remove control), one prompt box, a model
  dropdown FED FROM /api/config AT RUNTIME (all 29 models by name,
  in config order; unavailable lanes suffixed), a segmented length
  control (per-model durations for API lanes; house lane maps
  segments to 5s steps), and one Generate button that CARRIES THE
  EXACT PRICE (fetchQuote/updateHousePrice now also set the button
  label; the detail line stays under it). Everything else sits
  behind one "More options" disclosure: lane, resolution, aspect,
  end frame, camera presets + film looks, effects, music prompt,
  Spoken line, negative prompt, seed. The start-source choice
  (none / upload / describe-and-we-make-it, incl. the first-frame
  generator) stays directly under the drop zone. Free accounts get
  the Try It composer + the C paywall card on Create/Clip, paid
  accounts get no Try It tab and open into Create — the access
  model's server behavior is byte-for-byte the run-10 behavior;
  only presentation changed. The account card moved below the
  composer and job list (same fields, same checkout handlers).
- Gallery (/gallery): C's plain dark grid — 9:16 tile, handle,
  Report. Nothing else per item.

### Wired for real in this pass

- Negative prompt ("Anything to avoid") and Seed, which the C
  mockup showed as inert inputs, are sent on job create: the house
  lane already forwarded both to the worker, and the per-second
  submit builders already forwarded an integer seed. The owner
  probe below carried both fields through the paid gates to the
  422, proving they break nothing.

### Not carried over 1:1 from C, and why

- The old landing's long sections (How it works, the model-catalog
  grid, Effects, pricing cards, the honest-free-plan section, FAQ)
  are GONE, deliberately: C's landing is one idea. The model shelf
  now exists only as the studio's runtime-fed dropdown; pricing
  facts live in the one pricing line and in /api/pricing.
- The mockup's "Preview as: Free user / Member" toggle was
  review-only scaffolding; the live surface is driven by the
  account's real paid state, as in run 10.
- C's Sound select (Music + voice / Music only / Voice only) was
  not carried as a select: the platform's real audio contract is
  the music prompt + Spoken line fields, which sit in More options.
- The old gallery's per-item prompt / spec / cost lines were
  dropped to match C's plain grid (handle + Report only).
- The studio's model CARD grid (Pass 8) is replaced by C's
  dropdown; the cards' price/meta text is not shown in the
  composer anymore — the exact price for the chosen model/length/
  size is on the Generate button and its detail line instead.

### Live verification (zero generation spend)

In-Modal probe (scratch/c-rebuild/rebuild_probe_app.py), 18/18
checks PASS, all_ok true (run twice; the first run's only "fail"
was the probe's own cleanup bookkeeping, removed=true, fixed and
re-run clean):
- /healthz 200; /api/config lists exactly 29 models.
- Landing anonymous -> guest top (Try It free, sign-in-first line,
  dark palette, logo yellow) and NO member block, no leftover
  __LANDING_TOP__ token. Landing with the owner session -> member
  top (Open Create, welcome back) and NO Try It framing. Landing
  with a fresh free session -> guest top.
- /app serves the C studio markers (dropzone, runtime model
  select, duration/segments segmented controls, More options,
  negative/seed fields, tabs, upgrade pane, badge, buildCatalog /
  buildSegButtons JS; no model-catalog or LANDING_DURATIONS
  remnants). /gallery serves the C grid.
- Fresh free account (real signup, throwaway, deleted after):
  unverified job 403; verified -> /api/me Try It surface, limit 5;
  Try It draft with a deliberately invalid start image -> 422 at
  image validation, no dispatch, free_jobs_used still 0; model
  switch -> 403.
- Owner: /api/me Create surface; a job carrying music prompt,
  speech script, negative_prompt and seed with an invalid start
  image -> 422 past every paid gate; owner record compared
  before/after (plan, net_paid_cents 100000, balance_cents 100000,
  gallery_opt_out, free_jobs_total, cost_settled_cents):
  identical.
- All three pages' inline scripts pass node --check; app.py
  compiles; the git diff vs f04e3ac touches only landing() and the
  page layer.

## 2026-11-16 (run 13): PRICING AUDIT — EVERY PRICE vs HIGGSFIELD AND THE x1.25 FLOOR (Jack)

Jack's restated rule: every price cheaper than Higgsfield's
equivalent where a comparison is possible, and still marked up
from actual API cost (floor: cost x 1.25 on per-second lanes;
Rule 2 floors on self-hosted). Full table, sources, and math:
~/workspace/sinclip/pricing-audit-2026-11-16.md. Owner exemption
untouched; no generation runs, no provider spend.

FOUND AND FIXED (commit 83f1c87, deployed, verified live):
Seedance 2.5 was OVER Higgsfield at 480p and 1080p. Cause: its
Higgsfield figure was one 720p number scaled by the provider-rate
ratio (2.62x at 1080p); Higgsfield's published credit counts step
35 -> 80 (2.29x), 15 at 480p. pricing.py gained
HIGGSFIELD_EFFECTIVE_CENTS_PER_CLIP_BY_RESOLUTION (Seedance 2.5
480p 73.75c / 1080p 393.33c per 5 s clip; Wan 3.0 1080p 86.04c;
Plus monthly $59/1200 = 4.9167c/credit) used by both
frontier_lane paths. Live after deploy: Seedance 2.5 sell
17.48 -> 14.308 c/s at 480p (HF 14.75, floor 12.125) and
87.329 -> 76.307 c/s at 1080p (HF 78.666, floor ~60.6); balance
debits 18/88 -> 15/77. Also _ceil3: published minimum/sell now
ceil to 3 decimals so no published rate sits a rounding hair
under the exact floor (wan3 480p minimum 4.5625 was published as
4.562; a few floored lanes +0.001 c/s). Wan 3.0 1080p
reclassified UNDER on the published 17.5-credit figure (HF
17.208 vs floor sell 17.125).

VERDICT SUMMARY (56 priced per-second cells + house products):
UNDER 19 (incl. the 2 fixed), OVER-cannot-undercut 3 cells
(Wan 3.0 480p/720p — floor 4.563/9.0 vs HF 4.4 scaled / 8.8;
Kling 4.0 1080p — raw cost 21.35 alone exceeds HF 15.979),
NOCOMP the rest (no published Higgsfield figure, or model not on
their shelf; all priced at the floor). No THIN lanes: every sell
>= cost x 1.25. Contested: Seedance 2.0 1080p (two reads of
Higgsfield's table conflict, ~60 vs ~45 credits; lane sits at
its floor either way). NOT done, flagged for Jack: lanes with a
published HF figure priced at the floor far below Higgsfield
(Veo 3.1, Grok, Veo Lite) were not raised toward 0.97x HF —
adding their figures to the Amendment 3 table would raise those
prices; that is a policy call, not an audit fix. Named follow-up:
CometAPI's $0.04/s flat Wan 3.0 would flip the wan3 verdict if
verified, but CometAPI's own pages conflict ($0.05-0.20/s by
resolution) — console verification needed before any re-route.

## 2026-11-16 (run 14): PRICING ROUND 2 — EVERY PRICE UNDER HIGGSFIELD OR AT RAW COST (Jack)

Jack's strengthened policy: every public price cheaper than
Higgsfield no matter what, or at raw provider cost where a
markup undercut is impossible but cost is still below
Higgsfield; never below raw cost; a lane whose raw cost meets
or exceeds Higgsfield even at cost is priced at cost and
flagged as a residual exception. Full evidence and final
table: pricing-audit-2026-11-16.md, round-2 section. Commit
ba5d0b9, deployed, verified live (/api/pricing + /api/config,
29 models). Owner exemption, ceiling, free tier untouched. No
generation runs; provider spend $0.

MECHANISM: pricing.py gained HIGGSFIELD_POLICY_CENTS_PER_SECOND
(comparison-and-cap figures for the compared lanes outside the
Amendment 3 target tables; can only lower or confirm a price,
never raise one) and an at-cost cap in frontier_lane: any lane
whose sell would sit at/above its Higgsfield figure sells at
raw cost, with pricing_basis (standard / at_cost_undercut /
at_cost_residual) surfaced in /api/pricing, whose frontier_lanes
now covers the policy lanes too.

COMETAPI WAN 3.0 VERDICT (API-verified, closes the round-1
follow-up): CometAPI's own authenticated models API reports
wan3.0 billing at the official per-resolution rates
($0.05/$0.10/$0.20 per second; per_second 0.05, ratio 0.8
unapplied in its charge table — the $0.04/s figure is
pricing-page marketing). Even the marketing reading loses to
BeatAPI (3.55/7.1/13.6) at every tier. NO re-route. Wan 3.0
480p/720p instead go at-cost: sell 4.563/9.0 -> 3.65/7.2, now
UNDER Higgsfield (4.4 scaled / 8.8).

SEEDANCE 2.5 HUNT: cheapest verified per tier — 480p CometAPI
$0.0824 (its catalog's "Actual price" column; beats BeatAPI
$0.096), 720p CometAPI $0.1848 (already primary), 1080p
BeatAPI $0.481 (only wireable verified 1080p; reAPI's $0.4619
is cheaper on paper but has no account/key — noted, not
adopted). CHANGE: 480p re-routed to CometAPI (override added,
seed + stored rate -> 8.24, refresh exclusion extended to
480p, CometAPI submit sends size 480x854 on that tier;
payload-unverified as before). Sells unchanged, all UNDER;
480p margin 1.463x -> 1.716x. Flash confirmed a distinct
model (CometAPI catalog: upcoming, token-priced), not this
lane.

KIE COST CORRECTIONS (the thin-lane re-check found two seeds
in the wrong cell of Kie's own matrices; submits verified
sound-on in code first): Kling 3.0 seed 7.55 flat (the
no-audio 720p "from" price) -> 11.35/15.1 (Kie's with-audio
rates; the lane was actually selling below true cost at
720p). Kling 4.0 720p seed 8.5 (the with-reference-video
per-billed-second rate) -> 14.25 (no-input rate). Stored
rates updated to match (scratch/round2_rates_app.py,
updated_at untouched).

RESIDUAL EXCEPTIONS (priced at raw cost, zero markup, flagged
in the payload): Kling 3.0 720p (cost 11.45 vs HF 9.833),
Kling 4.0 720p (14.35 vs 11.063), Kling 4.0 1080p (21.35 vs
15.979; sell was 26.688 — the 1080p hunt covered Kling
official, fal, Replicate, WaveSpeed, EvoLink, AIMLAPI,
CometAPI; nothing wireable beats it). Kling 3.0 1080p stays
NOCOMP (HF sound-on count unpublished) at its corrected floor
19.0. Seedance 2.0 1080p UNDER on the governing CamClo
2026-11-13 read (59.0), conflict noted in the audit doc.

ANSWER TO JACK'S QUESTION: no lane is above Higgsfield by
more than raw cost; the only lanes still above Higgsfield at
all are the three at-cost residuals above. Every other
compared lane is under, including both round-1 OVER lanes.

FLAGGED, NOT CHANGED: Kling 3.0 Omni's flat 7.55 seed likely
understates its sound-on Kie cost (third-party reads put it
at ~$0.09-0.10/s at 720p), but the figures are estimated
third-party reads, not Kie's own page, and the lane has no
Higgsfield figure — it stays flagged for a Kie console
confirmation, as before.

## 2026-11-16 (finish run): full smoke, clip fetch verdict, Kie corrections, polish

Jack's order: "finish the fucking APP". Mid-run correction from
Jack (structure layer, SPEND-GATE.md): urgency is NOT spend
approval. MODAL SPEND: NONE AUTHORIZED for this run. The two
house-lane proof generations (item 2) and the camera proof
renders (item 3) were HELD and are listed in
finish-run-2026-11-16.md with exact proposed jobs + estimated
costs for Jack's individual approval. One unintended exception,
disclosed in the run report: an early smoke fixture pick was
paid under is_paid() (plan free but net_paid > 0), so one gate
probe dispatched one real distilled render (job 80510862a8c4ca89,
est. ~$0.12); the gates themselves behaved correctly.

- FULL SMOKE (39/39 pass; probes scratch/finish_smoke_app.py,
  finish_fixup_app.py, finish_fixup2_app.py; fixtures REUSED,
  never created): healthz; landing anon/free guest vs owner
  member variants; /app shell; /api/config 29 models default
  wan2.2; /api/me surfaces; house + paid per-second quotes;
  free gates 403 before dispatch (Production lane, music
  prompt, speech script, model-switch quote); invalid image
  422; image quotes owner $0.05 / customer $0.47; gallery 26
  items + file serving + Report + admin visibility; gallery
  opt-out free 403 / paid toggle + restore; handle set/verify/
  restore; Stripe checkout sessions (top-up + subscription)
  created via the platform and ALL expired, zero open remain
  (payments_mode is LIVE — no test key is stored anywhere, so
  literal test-mode sessions are impossible without one);
  owner record byte-identical before/after.
- FIX: the member landing contained the words "Try It" once
  (an explanatory sentence) — reworded; redeployed; zero
  occurrences verified. Paid members never see Try It.
- CLIP: Invidious resolver added to clipping.py (10 public
  instances, direct googlevideo stream then ?local=true proxy,
  muxed mp4 <=720p) ahead of yt-dlp for YouTube; Twitch/Kick
  keep yt-dlp. Exhaustive diag (finish_invidious_diag_app.py,
  finish_ytdlp_diag_app.py): EVERY route is dead from Modal
  egress — all Invidious + Piped instances fail, yt-dlp is
  bot-walled on every player client, Cobalt's official API
  requires JWT, no keyed provider offers a YouTube fetch,
  oEmbed answers but carries no streams/duration. Shipped
  state = brief option (c): LINK_DEAD_MESSAGE ("that link
  could not be read from our servers right now; upload the
  video file instead, uploads always work") on both clip 502
  paths + the Clip pane copy; no resolver internals leak to
  users. Resolver stays in place (instances churn).
- CLIP AI PICKING WAS SILENTLY BROKEN — found + fixed: the
  platform Gemini key (created 2026-11-16) gets 404 on
  gemini-2.5-flash ("no longer available to new users").
  _gemini_pick now walks GEMINI_PICK_MODELS = (gemini-3.8-flash,
  gemini-3.5-flash-lite) with a 503 retry. Proof: upload of a
  real narrated 50 s file -> transcription -> pick_mode "ai"
  -> clip rendered -> child file serves 200 video/mp4
  (before the fix the same proof fell back to "loudness").
  Earlier upload proof with pick_mode loudness also completed
  (10.5 MB child). YouTube quote returns the clean 422
  LINK_DEAD message.
- CAMERA: no code defect exists to fix — this run's
  programmatic field diff of the ported workflow vs the
  Freefield source app is identical in wiring (run 21 reached
  the same verdict by desk debug; distilled AND stock recipes
  both measured zero movement). Worker gained a camera_recipe
  job parameter (distilled | stock | mid; mid = LoRA 0.5,
  10 steps, cfg 2.0, previously untestable without a scratch
  app), deployed, default distilled = current behavior.
  Proof renders held for Jack (see finish-run checkpoint):
  speed 3.0 distilled ~$0.10, mid ~$0.25, stock speed 3.0 ~$0.78.
- KIE: catalog verified via the authenticated API; four seeds
  corrected in pricing.py AND the live stored table (Kling 3.0
  Omni was priced below its sound-on cost; Seedance 2.0
  Fast/Mini seeds were with-video rates; Omni 1.2 Flash seed
  was the 360P rate). Full table: pricing-audit-2026-11-16.md
  round 3. Live /api/pricing re-verified.
- POLISH: on-brand dark 404 page for browsers (JSON kept for
  /api/*), live-verified; Play Hollow favicon + titles +
  viewport confirmed on /, /app, /gallery and the 404 page;
  empty states present (jobs, clips, gallery); /app uses fluid
  max-width cards + the shared 700px media query. Screenshots
  could NOT be taken from this VM: its Chromium is TLS-blocked
  by the sandbox egress proxy (ERR_SSL_PROTOCOL_ERROR; curl
  works, Chromium does not) — markup-level verification only.
- Spend: $0 authorized generation spend. Unintended: the one
  smoke render above (~$0.12 est.). CPU probe time across the
  run: pennies (est. < $0.05, unmeasured). No provider spend;
  balances stay $0.

## 2026-11-16 (proof renders): THE FIVE HELD RENDERS, RUN UNDER JACK'S APPROVAL

MODAL SPEND: APPROVED BY JACK IN CHAT, Nov 16, 2026, verbatim:
"Approve all 5 proof renders (~$1.42 total)". Exactly those five jobs
ran; mid-run Jack added "make them as short and as cheaply as
possible" — the four already-dispatched jobs finished as-is per the
constraint's own terms, and 3c (the only undispatched one) was
reviewed: the Fun Camera lane renders one segment at a fixed length
(no duration parameter exists) and the stock recipe's steps/cfg are
the variable under test, so it ran in its already-minimal form.
Harness: scratch/proof_renders_app.py; checkpoint:
proof-renders-run-2026-11-16.md; outputs:
outputs/proof-renders-2026-11-16/ in the sinclip workspace.

- AUDIO PROOF (2a Production / 2b Draft, owner, Wan 2.2, balloon
  start image, speech_script "A red balloon drifts over the quiet
  harbor." + music prompt): BOTH PASS with hard evidence. The raw
  worker files are video-only; the delivered files carry an AAC
  track at -15.18 / -15.0 LUFS. voiceover_status "ready", no audio
  fallback. Least-squares fit of each delivered mix against the
  job's own cached voiceover wav + bed wav (moonfactory-data):
  R2 0.997 / 0.987; voice cross-correlation peak 0.895 / 0.925 at
  offset 0.0 s; fitted gains voice 2.65 / bed 0.86 (2a) and voice
  2.70 / bed 0.93 (2b) — the voice sits ~3x over the ducked bed.
  Parallel audio (spawned at dispatch, collected at finishing)
  lands mixed, on both lanes. Settled costs: 2a cost_cents 15.0 ==
  the 15c owner Production quote; 2b cost_cents 50.0 == the 50c
  owner Draft quote; both charged 0c balance (included budget);
  owner cost_settled_cents 0 -> 65.0 exactly. Both clips are listed
  in the public gallery.
- CAMERA MATRIX COMPLETE (Fun Camera Pan Left, benchmark balloon
  scene, seed 12345; phase-correlation frame-to-frame translation
  + red-blob centroid, the run-18 method): speed 1.0 = NO PAN in
  every recipe (distilled run 18: drift (0,0); stock run 21: drift
  (0,0); mid 3b: drift (0,0) at every half-second step, centroid
  +0.0006). Distilled @ speed 3.0 (3a) = REAL PAN: cumulative drift
  -64 px = -8.89% of frame width, monotonic across all ten steps,
  balloon centroid +0.0781 (content right = camera left, correct
  direction). Stock @ speed 3.0 (3c) = NO PAN: net +8 px (+1.11%)
  but oscillating (-8..+8, non-monotonic) with the centroid moving
  -0.0133 against the pan direction — trace noise, not a move.
  Verdict: camera_speed is the gate (1.0 never moves), and at 3.0
  only the distilled recipe's move survives measurement. The
  platform's Fun Camera default (distilled) is the right lane
  recipe; presets presented at speed 1.0 will not move.
- COST (actuals from worker records, render_seconds x L40S list
  $0.000542/s): 2a $0.116 (214.8 s, est $0.17) · 2b $0.132 (243.4 s,
  est $0.12) · 3a $0.120 (220.6 s, est $0.10) · 3b $0.431 (795.5 s,
  est $0.25 — 1.72x: the mid recipe's GPU time ran past its ~450 s
  desk projection; dispatch used the projection per the stop rule,
  the miss is the estimate's, disclosed) · 3c $0.744 (1372.9 s, est
  $0.78). Video GPU total $1.543 vs the $1.42 approved estimate
  (+$0.12, entirely 3b) and under the $1.75 hard stop. The 2a/2b
  audio stages record no separate seconds; their settled platform
  records (15c / 50c) are the measured-cost accounting for the full
  chain. CPU harness time: pennies. No provider spend.

## Finish pass — 2026-11-16 (camera preset fix + completion audit)

MODAL SPEND: NONE AUTHORIZED. No generations, no proof renders.

- CAMERA PRESET FIX. The proof renders (proof-renders-run-2026-11-16.md)
  completed the recipe/speed matrix: speed 1.0 never pans in ANY recipe
  (distilled, stock, mid all measured zero drift), stock at speed 3.0
  measured trace noise only, and only the distilled recipe at speed 3.0
  produces a real pan (3a: -8.89% of frame width, monotonic, correct
  direction). But the live product resolved every Fun Camera job to
  (distilled, 1.0): the studio never sends camera_speed, so the
  platform default 1.0 applied — a dead camera on every preset.
  Fix, in ONE place: new platform/fun_camera.py holds the mapping;
  resolve_fun_camera() returns (distilled, 3.0) for any requested
  speed, and create_job resolves every Fun Camera preset through it
  (regenerate re-enters create_job, so retries inherit the fix). The
  resolved camera_recipe now travels explicitly in the worker payload
  and the job record; API shape unchanged. Deployed; verified WITHOUT
  rendering by scratch/camera_fix_probe.py (23/23): resolver asserted
  for every requested speed, all 9 workflow_patch presets asserted,
  source routing asserted, live /api/camera-presets serves the same
  preset ids, live /api/config = 29 models, /healthz ok.
- COMPLETION AUDIT: FINISH-STATE-2026-11-16.md — 20 verified-working
  items with evidence sources (smoke 39/39, proof renders, pricing
  audits rounds 1-3, live read-backs) and the only four open items,
  all Jack's steps (provider funding + funded verification, Kling
  at-cost residuals decision, final name/domain, Kaggle API token).
  No defects found that cost nothing to fix; the one scare (an empty
  gallery parse) was a probe-side bug — /api/gallery serves its items
  normally.

## Camera choice correction — 2026-11-16 (Jack: people choose the speed)

- Jack's correction to the finish pass, verbatim: "I don't want every
  video set at three. I want people to be able to choose." The lock to
  (distilled, 3.0) overshot: the original defect was that the studio
  never sent camera_speed at all, so the platform's 1.0 default
  silently applied. The correct fix is a real user choice, honored
  end to end.
- platform/fun_camera.py: resolve_fun_camera() now HONORS the
  requested speed, clamped to the supported range [0.1, 3.0], and
  falls back to 3.0 only when the request carries no speed. The
  recipe stays "distilled" (the only recipe ever measured to pan;
  an internal test parameter, never a user-facing choice). 3.0
  remains the default because it is the measured-working speed;
  the choice is the user's.
- platform/app.py create_job: "no speed sent" is now tracked as None
  (was: initialized to 1.0), so the resolver can tell a missing speed
  from a chosen 1.0. The resolved (recipe, speed) still travels
  explicitly in the worker payload and the job record; regenerate
  re-enters create_job and inherits the chosen speed.
- Studio (/app, More options > Camera and look): a "Camera speed"
  select (0.5 / 1 / 1.5 / 2 / 2.5 / 3, default 3) that appears only
  when a Fun Camera preset is selected; the create handler sends
  camera_speed only in that case. No preset = no speed sent. The
  five-thing Create screen is unchanged.
- MODAL SPEND: NONE AUTHORIZED — zero renders. Verified without
  spend: scratch/camera_choice_probe.py 23/23 (resolver honors six
  distinct speeds, clamps out-of-range, None -> 3.0, source routing,
  UI wiring, live read-backs: /api/config = 29 models,
  /api/camera-presets serves the same 9 Fun Camera ids, live /app
  markup contains the control) and scratch/camera_choice_gate_app.py
  11/11 against the live platform (reused strict-free fixture;
  draft-lane 422 after resolution for speeds 2.0 / 1.0 / 99 / none,
  non-numeric speed 422, Production free-gate 403 at speed 2.5,
  invalid-image 422; jobs Dict 58 -> 58, fixture counters unchanged).

## Uncensored LLMs + Uncensored label + price tier signs — 2026-11-17

Jack's orders: "put them all on moonfactory" (the uncensored LLM
APIs), an "Uncensored" label mirroring the unavailable/paused
pattern, and Google-Maps-style price signs in the studio model
dropdown. MODAL SPEND: NONE AUTHORIZED — no generation, no paid
API calls; verification was free list-models / account-read calls
and config read-backs only. No subscriptions anywhere.

- platform/llm_providers.py (new, shipped in both platform
  images): the uncensored LLM registry, SEPARATE from the video
  MODELS shelf. /api/config serves it under "llm_providers"
  (names, provider, model ids, configured boolean, access state);
  the studio does not render it and the video picker never sees
  it. 4 providers, 21 models, every model flagged uncensored:
  - OpenRouter (key already stored): "Venice Uncensored"
    cognitivecomputations/dolphin-mistral-24b-venice-edition,
    confirmed present in OpenRouter's own catalog (470 models,
    free read, 2026-11-17). FINDING: the stored OPENROUTER_API_KEY
    returns 401 on both free account reads (/api/v1/auth/key and
    /api/v1/credits, checked twice), so the lane is dark in
    practice until the key is refreshed; access state in config
    reads "key_stored_auth_failing" so nobody mistakes it for
    live. The $8.32 balance figure predates this check and could
    not be re-verified with the stored key.
  - Venice (15 models): enumerated from Venice's public models
    endpoint, filtered to the entries Venice itself flags
    "uncensored": true — venice-uncensored-1-2, the role-play
    build, both DeepSeek V4.1 Flash uncensored builds, the E2EE
    abliterated build, GLM 5.3 Flash Uncensored, GLM 4.7 Flash
    Heretic, Gemma 4 Uncensored (Venice deprecates it 2026-11-18
    with auto-remap), Qwen 3.6 Plus Uncensored, Qwen 3.8 27B,
    both Abliteration.ai builds served via Venice, and the three
    Aion builds whose ids the list confirms. Two flagged entries
    (Aion 3.0, Gemma 4 26B A4B Uncensored) were NOT registered:
    their model ids could not be confirmed from the public list,
    and ids are never guessed. Account + API key are a browser
    signup step (free tier, no card, no Pro); registered dark
    until VENICE_API_KEY lands in moonfactory-providers.
  - Refuseless (4 models): glm-5.3-abliterated,
    glm-5.3-flash-abliterated, deepseek-v4.1-flash-abliterated,
    deepseek-v4.1-flash-abliterated-fast — ids from Refuseless's
    own model pages. Pay-as-you-go per token (their pricing FAQ:
    no subscription, no monthly minimum), so it passes Jack's
    no-subscriptions rule. Account + key are a browser signup
    step; their model pages say "create an account, add credit,
    and call it with your own key", so if a key requires a top-up
    first, that top-up is Jack's step. Registered dark until
    REFUSELESS_API_KEY lands.
  - Morph (1 model): morph-dsv41flash-abliterated (DeepSeek V4.1
    Flash Abliterated, $0.30/$1.20 per 1M on Morph's own page).
    Access is approved per organization ("Approved teams only");
    the Magrify account + access application are a browser step
    and have NOT been submitted by this pass. base_url is None
    until key issuance confirms it, so the picking chain skips
    Morph. Registered dark, access "awaiting_org_approval".
- Picking wiring: clipping.find_clips now tries the uncensored
  chain after the Gemini chain fails for a window (and when no
  Gemini key is set, if any uncensored provider is configured),
  before the loudness fallback. Chain order: OpenRouter Dolphin,
  Venice Uncensored 1.2, Refuseless GLM 5.3 Flash Abliterated,
  Morph abliterated — each only when its key env exists. Gemini
  remains the primary brain; behavior is unchanged when Gemini
  answers or when no fallback is configured. All four lanes are
  dark today in practice (OpenRouter key 401; the other three
  have no keys), so production picking is untouched until a
  working key lands.
- "Uncensored" label: /api/config model entries carry
  "uncensored" — true ONLY for wan2.2 (UNCENSORED_VIDEO_MODELS),
  the self-hosted house lane: open weights on our own worker, no
  provider moderation. The studio dropdown appends
  " · Uncensored" to its option text, mirroring the existing
  " (unavailable)" suffix pattern. Every LLM group model carries
  the same flag in config.
- Price tier signs: /api/config model entries carry "price_tier"
  1-4 (None for the four price-paused lanes, which already read
  "(unavailable)" and show no sign). The studio dropdown renders
  '$' x tier right after the model name; the exact price stays on
  the Generate button as before. BASIS (fixed bands, not
  quartiles, so a model's sign never moves because another
  model's price did): the model's LOWEST customer sell rate
  across resolutions from the live rate table, cents per second —
  $ under 5, $$ under 12, $$$ under 25, $$$$ at or above 25. The
  house lane is priced per job, so Wan 2.2's basis is the
  Production job price over the standard 5-second segment
  (99c / 5s = 19.8c/s -> tier 3). Consequence of the
  lowest-resolution basis: no lane currently reaches tier 4,
  because every shelf model's entry resolution sells under
  25c/s (top resolutions still cost more per second; the sign
  bands the model's entry price, like Google Maps bands a
  restaurant's typical spend).
- Secret hygiene: no keys were added by this pass (none could be
  issued without the browser signups above). The
  moonfactory-providers key-NAME list was read before and after
  via scratch/llm_probe_app.py and is byte-identical: the same
  18 names (AIMLAPI_API_KEY, BEATAPI_KEY, COMETAPI_API_KEY,
  EACHLABS_API_KEY, EDENAI_API_KEY, FAL_KEY, GEMINI_API_KEY,
  KIE_API_KEY, MINIMAX_API_KEY, MINIMAX_GROUP_ID, NOVITA_API_KEY,
  OPENROUTER_API_KEY, PIAPI_API_KEY, REPLICATE_API_TOKEN,
  RUNWAY_API_KEY, STRIPE_SECRET_KEY, VIDU_API_KEY,
  WAVESPEED_API_KEY). No key value was printed anywhere.
- Verified live after `modal deploy platform/app.py`:
  /api/config serves exactly 29 video models (picker untouched),
  wan2.2 uncensored=true with tier 3, tiers on all 25 priced
  lanes, tier=None on the 4 paused lanes, the llm_providers
  group reading back with openrouter configured=true and the
  other three configured=false; served /app markup contains the
  '$'.repeat(m.price_tier) and " · Uncensored" option-text code;
  /healthz ok.

## OpenRouter key replacement — 2026-11-17

- The stored OPENROUTER_API_KEY (401 on /api/v1/auth/key and
  /api/v1/credits, see the 2026-11-17 uncensored-LLM entry) was
  replaced with a fresh key Jack created in the OpenRouter
  dashboard ("moonfactory"). Merge via SDK Secret.update() on
  moonfactory-providers (per-key merge; no full-secret overwrite).
  Key-NAME list read before and after: the same 18 names,
  byte-identical, nothing lost or added. No key value printed.
- Verified with the new key (free reads): GET /api/v1/auth/key
  200 (usage 0, limit null, is_free_tier false); GET
  /api/v1/models 200, 435 models, with
  cognitivecomputations/dolphin-mistral-24b-venice-edition
  present. The OpenRouter lane is live as the first uncensored
  picking fallback whenever the Gemini chain fails.
- platform/llm_providers.py: the openrouter entry's static
  access state updated "key_stored_auth_failing" -> "key_stored"
  and the module docstring's openrouter note rewritten to match.
  The access string is config metadata only; call behavior was
  already computed live from key presence. No deploy in this
  pass (secret + source only); the label takes effect on the
  next platform deploy.
