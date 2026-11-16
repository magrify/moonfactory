# Sinclip Modal worker: status

Task: stand up the generation worker on Modal and produce the first real
vertical draft clip (Wan 2.2 TI2V-5B, 480x832, 81 frames, 16 fps).
Spend ceiling: stay inside the $30 Modal credit; abort and report near $3.

## RUN 7 (2026-11-16): RENDER-COST OPTIMIZATION — DISTILLED PRODUCTION LANE ADOPTED

- [x] Production lane switched to the LightX2V 4-step distilled
      recipe and it is LIVE (worker + platform redeployed, public
      pricing endpoint verified). New workflow:
      workflows/prod_i2v_a14b_distilled.json = the A14B pair with
      wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise / _low_noise
      LoRAs (strength 1.0, one per UNET; both files byte-verified in
      the Volume under loras/, 1,226,977,424 B each), 4 steps split
      2+2, cfg 1.0, euler/simple, shift 5.0. LANE_WORKFLOWS in app.py
      now maps "production" to the distilled file; the 20-step
      template stays for reference / local modes.
- [x] Benchmark (identical inputs to the baseline render: same
      start image, seed 12345, same prompts): wall 216.4 s on L40S
      = ~$0.12 per segment, vs the baseline 23m39s / ~$0.77. Output
      outputs/prod-distilled-test.mp4: 720x1280, 81 frames, 5.06 s,
      spec-identical to outputs/first-prod-test.mp4.
- [x] Quality verdict: HOLDS on the benchmark scene. Side-by-side
      frames at 0.3 / 2.5 / 4.8 s (local comparison): composition,
      balloon geometry, colors and edges are clean in both; no
      warping or structural defects in the distilled frames.
      Within-clip motion MAE 1.17 / 1.79 / 1.04 (distilled) vs
      1.06 / 1.36 / 0.99 (baseline); Laplacian sharpness higher in
      the distilled frames at all three timestamps (73.9 / 70.7 /
      62.2 vs 41.9 / 32.2 / 27.5). Differences are trajectory
      differences (cloud/hill arrangement, balloon slightly higher
      late in the clip), plus two tiny bright specks on the sun's
      rim in the 4.8 s distilled frame. HONEST CAVEAT: the test
      scene is flat illustration with gentle motion, the content
      distillation handles best; parity on photoreal, fine-detail,
      fast-motion content is NOT proven by this benchmark.
- [x] Pricing updated (platform/pricing.py, 3x formula untouched):
      production measured cost 80c -> 15c worst case (verified
      $0.117 measure + cold-start headroom). Floor $0.78, per-job
      production price $0.99, Creator included production seconds
      40 -> 214 (about 3.6 minutes) at the same $20 plan.
- [ ] NOT tested, deliberately: the distilled config on L4
      ($0.000222/s). Best case saves only ~$0.03/segment over the
      L40S figure now that L40S measures $0.12, against the known
      24 GB offload risk (the A10 attempt at 20 steps never
      finished) and run-7 budget uncertainty (see BUILD-LOG run 7
      for the duplicate-delivery accounting). TeaCache stacking
      also untested: at 4 steps there is little left to cache.
- Spend (run 7): LoRA download ~$0.01; verified benchmark render
      ~$0.12; worst-case total ~$0.37 if the duplicate-reported and
      killed duplicate runs billed in full. Cap was $0.65.

## RUN 2 (2026-11-16, started 02:23 UTC): AUDIO PASS + PRODUCTION LANE

- [x] Step 1 DONE (02:28 UTC): ACE-Step route produced the bed (no
      fallback needed). outputs/first-draft-test-audio.mp4: video copied
      untouched (h264 960x1664, 16 fps, 81 frames); audio AAC LC 48 kHz
      stereo 194 kb/s, duration 5.06 s matching the video. Final measured
      loudness of the muxed file: -13.96 LUFS integrated, -1.40 dBTP
      (two-pass loudnorm, I=-14 target; TP target set to -1.5 on the
      second attempt after the first mux measured -0.83 dBTP post-encode).
      Raw bed kept at outputs/music_bed_first.wav (10.00 s, 48 kHz PCM)
      and in the Volume at audio/music_bed_first.wav.
- [x] Step 2 DONE with a GPU change: attempt 1 on A10G TIMED OUT (the
      3300 s poll deadline hit at ~03:25 UTC; the app stopped 03:23 UTC
      and the output was unrecoverable). A14B fp8 on A10G (Ampere, no
      native fp8 acceleration, likely memory offloading) could not
      finish 20 steps x 81 frames at 720x1280 in 55 min. Attempt 2 on
      L40S (48 GB, native fp8) COMPLETED: container 03:27:51-03:51 UTC
      (~24 min total, ~23 min execution). outputs/first-prod-test.mp4
      (821,606 bytes): ffprobe h264, EXACTLY 720x1280 (no 2x quirk on
      the A14B + wan_2.1_vae path), 16 fps, 81 frames, 5.06 s. Motion:
      full-frame MAE 17.05 / 16.81 between frames at 0.3 / 2.5 / 4.8 s.
      Mid-clip frame viewed: clean flat-illustration balloon scene,
      faithful to the start image's style, on-prompt. The ComfyUI class
      now runs on L40S permanently (gpu="L40S" in app.py).

## RUN 2 PHASE 3 (2026-11-16): CHAIN TEST + POST CHAIN + WEB ENDPOINT

STANDING RULE (Jack, 2026-11-16): anything that can be processed on
      Muse's own machine must NOT use Modal. Modal is GPU-generation
      only. Stitching, muxing, loudness, frame extraction/QA, and file
      handling are all LOCAL steps. (The chain render itself is GPU
      work and stays on Modal; its stitch + seam check are local.)

- [x] 3.1 Chain test DONE (run 3, 2026-11-16, ~05:00 UTC): segment 2
      rendered on L40S via render-chain2 (firstlast_chain.json,
      start = chain_seg1_last.png, end = chain_seg2_end.png, seed
      12346). Modal run 04:35:25 -> 04:59:04 UTC (23m39s wall, ~$0.77
      at the L40S list rate). outputs/chain-seg2-raw.mp4: h264,
      720x1280, 16 fps, 81 frames, 5.06 s — spec-identical to
      segment 1. Stitch + seam check done LOCALLY (standing rule):
      outputs/first-chain-test.mp4 = seg1 (81) + seg2 frames 1..80
      = 161 frames, 720x1280, 16 fps, 10.06 s, 1,702,498 B.
      SEAM VERDICT: no visible jump — frames 80 and 81 viewed side
      by side show identical balloon position/scale, sun, clouds,
      hills; no cut or teleporting. A subtle grain/texture settle is
      measurable at the join: inter-frame MAE 80->81 = 3.14 vs
      0.56-1.01 for neighbouring in-segment steps, driven by segment
      2's regenerated boundary frame (MAE 3.33 vs segment 1's true
      last frame). Push-in verified: balloon bbox width 223 -> 239
      -> 255 px and height 259 -> 277 -> 299 px across frames
      80/120/160 (final ~1.15x, matching the authored end keyframe).
      Post chain on the stitched clip NOT run: this run's spend
      (~$0.77 of the ~$1.00 authorized, abort line $1.25) leaves no
      safe margin for a multi-attempt GPU post pass, and the post
      chain is already proven on a production segment
      (outputs/first-postchain-test.mp4). Cumulative project spend
      ~$3.50 of the $5 build/test cap (list-rate estimates).
- [x] 3.2 Post chain DONE (04:26 UTC): production segment loaded via
      VHS_LoadVideo -> RIFE VFI (rife49, 2x: 81 -> 161 frames) ->
      SeedVR2 3B fp16 (resolution=1080 shortest edge) -> VHS combine at
      32 fps, all in ComfyUI on L40S; then local ffmpeg fps=30.
      SeedVR2 stood up cleanly — no Lanczos fallback needed.
      outputs/first-postchain-test.mp4: h264, EXACTLY 1080x1920, 30 fps,
      151 frames, 5.03 s. Raw 32 fps version kept as
      outputs/postchain-raw.mp4 (1080x1920, 32 fps, 161 frames).
      Upscaled frame viewed: clean, sharp, colors preserved.
      Consecutive-frame MAE at 30 fps: 0.6 (smooth interpolated motion).
      Two validation failures preceded the success, both fixed from
      ComfyUI's own error bodies: (1) VHS_LoadVideo in this ComfyUI
      version takes custom_width/custom_height, not force_size, and
      RIFE VFI requires ckpt_name; (2) SeedVR2VideoUpscaler requires
      uniform_batch_size and color_correction in API format (all its
      schema inputs are now sent explicitly). run_workflow now includes
      the /prompt error body in its exception, and a probe-nodes mode
      dumps /object_info specs — use it before guessing node inputs.
- [x] 3.3 Web endpoint DONE: app deployed (`modal deploy app.py`),
      base URL https://magrify--sinclip-comfyui-api.us-east.modal.run,
      documented in modal-worker/ENDPOINT.md. Smoke test (run inside
      Modal via smoke_app.py because this sandbox's proxy stalls POSTs
      to *.modal.run): 401 no key, 422 missing prompt, 422 bad lane,
      404 unknown job, 202 + job_id on a valid POST, job lifecycle
      queued -> running -> error with the designed pre-GPU error, 409
      on file fetch for an unfinished job. Zero GPU spend in the smoke
      test by design.

Phase 3 log:
- app.py extended: ComfyUI-Frame-Interpolation and
  ComfyUI-SeedVR2_VideoUpscaler cloned into the ComfyUI image (pip
  requirements best-effort); SEEDVR2 added to the Volume symlink
  categories; run_workflow generalized to accept an images dict;
  download_seedvr2 function (seedvr2_ema_3b_fp16 = 6,783,018,808 B,
  ema_vae_fp16 = 501,324,814 B, repo numz/SeedVR2_comfyUI); modes
  render-chain2 / render-post / fetch-chain / fetch-post /
  download-seedvr2 added.
- 02:58:09 UTC: SeedVR2 download launched in parallel (CPU;
  download_seedvr2.log). COMPLETE ~03:02 UTC: both files byte-exact
  against the HF listing.
- FINAL LEDGER for run 2 + phase 3 (estimates at list rates): prod
  download ~$0.03; ACE-Step ~$0.07; SeedVR2 download ~$0.02; prod
  attempt 1 (A10G, lost) ~$1.01; prod attempt 2 (L40S) ~$0.77; post
  attempts 1+2 (validation failures) ~$0.14; node probe ~$0.03; post
  attempt 3 ~$0.18; deploy + smoke ~$0.00. Run 2 + phase 3 ~ $2.25;
  cumulative project ~ $2.73 of the $3 line. Remaining ~$0.27 cannot
  cover the chain render (~$0.75 projected) or a live GPU smoke job
  (~$0.25, borderline) without breaching the line, so both were
  deferred by the numbers, not by choice.
- BUDGET LEDGER (estimates at Modal list rates; A10G $1.10/hr, L40S
  $1.95/hr): run 1 draft ~$0.48; run 2 downloads/ACE ~$0.12; prod
  attempt 1 lost on A10G ~55 min ~$1.01; prod attempt 2 on L40S
  ~24 min ~$0.77. Cumulative ~$2.38 of the $3 line. DECISION: the
  chain segment-2 render projects another ~$0.75 (total ~$3.13,
  past the abort line), so it is NOT launched this run; its
  keyframes are prepared locally instead (chain_seg1_last.png =
  frame 80 of first-prod-test.mp4; chain_seg2_end.png = authored
  1.15x push-in keyframe) so it can fire immediately when budget is
  authorized. Post chain (no Wan render; ~$0.20 projected) and the
  endpoint deploy proceed.

Run 2 log:
- 02:23 UTC: app.py extended (clip_vision symlink category added; class
  timeout 1800 -> 3600 s and prompt poll deadline 1500 -> 3300 s for the
  A14B; download_prod_models function + render-prod / fetch-prod /
  download-prod modes added). Prod start image test_start_720.png created
  (720x1280 Lanczos upscale of test_start.png).
- Prod model set (HF listings verified via tree API before download):
  wan2.2_i2v_high_noise_14B_fp8_scaled = 14,294,742,832 B;
  wan2.2_i2v_low_noise_14B_fp8_scaled = 14,294,742,832 B;
  wan_2.1_vae = 253,815,318 B (Wan 2.2 repo);
  clip_vision_h = 1,264,219,396 B (Comfy-Org/Wan_2.1_ComfyUI_repackaged).
  New total ~30.11 GB.
- 02:23:30 UTC: prod download launched (download_prod.log).
- audio_app.py written: separate Modal app "sinclip-audio", ACE-Step 1.5
  installed via uv sync at image build, DiT-only acestep-v15-turbo
  (no LM; caption/metadata supplied directly, thinking and all CoT off,
  shift=3.0 per the turbo note in the inference docs, 8 steps, seed
  12345, 10 s minimum duration, trimmed at mux time). Output persisted
  to the Volume at audio/music_bed_first.wav and returned as bytes.
- 02:24:04 UTC: ACE-Step generation launched (audio_gen.log).
- ~02:27 UTC: prod download COMPLETE: all four files stored, byte sizes
  match the HF listings exactly (total prod set 30.11 GB; "sizes
  verified: True" in download_prod.log).
- ~02:27 UTC: ACE-Step generation COMPLETE on the first attempt:
  music_bed_first.wav (1,920,078 bytes; ffprobe: 10.00 s, 48000 Hz,
  stereo, pcm_s16le). DiT-only turbo with llm_handler=None worked with
  thinking/CoT fully off. AUDIO ROUTE USED: ACE-Step 1.5 (Modal A10G).
- 02:28:23 UTC: production render launched on A10G (render_prod.log).

## FINAL STATE (2026-11-16 ~02:14 UTC): FIRST CLIP DELIVERED

- Output: ~/workspace/sinclip/outputs/first-draft-test.mp4 (3,748,246 bytes),
  also persisted in the Modal Volume at outputs/first_draft_test_00001.mp4.
- ffprobe: h264, 960x1664 (NOTE: 2x the requested 480x832 — the
  WanImageToVideo/5B path doubled dimensions; open item), 16 fps,
  81 frames, 5.0625 s duration.
- Motion verified: full-frame MAE 2.87 / 2.26 / 3.57 across frames at
  0.3 / 2.5 / 4.8 s; balloon red-centroid rose y 898.5 -> 889.1 with pixel
  count changing; mid-clip frame visually confirmed (photorealistic
  balloon over hills, matches prompt).
- GPU: A10G (24 GB) throughout; no OOM, no step-up needed.
- Cost: ~26 min A10G across attempts 2+3 (~$0.48 at the $1.10/hr list
  rate) + ~2 min CPU download; inside the $30 credit, far under the $3
  abort line. Exact billed figure lives on the Modal dashboard only.

## Log

- [x] Read README.md, workflows/draft_i2v_5b.json, workflows/NOTES.md,
      FIRST-TEST-RUNBOOK.md, worker/handler.py.
- [x] Modal auth verified: `modal token info` returns the sinclip-cli token,
      workspace magrify. Command returned in ~4 s, no freeze.
- [x] Model files verified on Hugging Face (Comfy-Org/Wan_2.2_ComfyUI_Repackaged):
      - split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors  10.00 GB
        (exact filename in the draft workflow CONFIRMED; settles the 5B
        filename item on the NOTES.md UNVERIFIED list)
      - split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors  6.74 GB
      - split_files/vae/wan2.2_vae.safetensors  1.41 GB
      Total ~18.15 GB.
- [x] Test start image created: test_start.png (480x832, PIL drawn scene).
- [x] app.py written (Modal app: download function + ComfyUI class on A10G,
      Volume "sinclip-models" mounted as the ComfyUI models dir, workflow
      driven through the local ComfyUI /prompt API, VHS node cloned into
      the image at build time).
- [x] Models downloaded into Modal Volume "sinclip-models", sizes verified
      byte for byte against the HF listing:
      5B fp16 = 9,999,658,848 B; umt5 fp8 = 6,735,906,897 B;
      wan2.2 VAE = 1,409,400,960 B. Total 18.14 GB.
      (First `modal run` also built both app images, including the ComfyUI
      GPU image; comfy-cli 1.26.0. No freeze hit; output streamed throughout.)
- [ ] Draft render run; output saved to ../outputs/first-draft-test.mp4.
- [ ] ffprobe verification (dimensions, duration, fps, frame change check).
- [ ] BUILD-LOG.md summary appended.

## Notes / failures

- Render attempt 2: the render itself SUCCEEDED remotely (ComfyUI produced
  output/sinclip/first_draft_test_00001.mp4; execution took ~13 min from
  submit to output), but the local `modal run` process then hung for ~4 min
  on result delivery and never saved the file locally. A separate
  `modal app list` at the same moment answered in ~5 s, so the Modal API
  connection was healthy; the hang was specific to that run's result
  return. Killed the local process. Fix going forward: run_workflow now
  (a) returns an already-existing output file without re-rendering if the
  container is still alive, and (b) copies the finished mp4 into the
  Volume and commits it before returning bytes, and a new CPU function
  fetch_output() can pull the file from the Volume as a fallback path.
- Render attempt 1 FAILED at container start: "cannot mount volume on
  non-empty path: /root/comfy/ComfyUI/models". Modal will not mount a
  Volume over the models dir because the ComfyUI install populates it.
  Fix: mount the Volume at /sinclip-models instead, and in @modal.enter()
  replace the diffusion_models / text_encoders / vae subdirs with symlinks
  into the Volume before launching ComfyUI.
