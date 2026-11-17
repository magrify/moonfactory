# Moonfactory Proof Renders — 2026-11-16
MODAL SPEND: APPROVED BY JACK IN CHAT, Nov 16, 2026, verbatim: "Approve all 5 proof renders (~$1.42 total)".
Scope: EXACTLY the five jobs below at their estimates. Stop rules: any job's projected
actual > 1.5x its estimate, or projected running total > $1.75 -> STOP before dispatch
and report. No other Modal spend.

## Jobs
- [x] 2a (~$0.17): owner Production, Wan 2.2, balloon start image, speech_script
      "A red balloon drifts over the quiet harbor." + music prompt. Verify audio mix
      (LUFS + speech presence), gallery listing, settled cost vs 15c owner quote.
      DONE: platform job 7262f6f29bdcafcd / worker 5a88643ff8924cf6. Render
      214.8 s = $0.116 video GPU. Settled cost_cents 15.0 == 15c owner quote;
      charged 0 (included budget). In gallery: YES. Audio: AAC track added
      (raw worker file is video-only); LUFS -15.18; voiceover_status "ready";
      mix fit vs the job's own voice+bed wavs: R2 0.997, voice NCC 0.895 at
      offset 0.0 s, gains voice 2.65 / bed 0.86 (voice ~3.1x bed).
- [x] 2b (~$0.12): owner Draft, same audio payload. Same verification.
      DONE: platform job d2c44a8c5ad33c2f / worker 80730b1926704c96. Render
      243.4 s = $0.132 video GPU. Settled cost_cents 50.0 == 50c owner Draft
      quote; charged 0 (included budget). In gallery: YES. Audio: LUFS -15.0;
      voiceover_status "ready"; mix fit R2 0.987, voice NCC 0.925 at 0.0 s,
      gains voice 2.70 / bed 0.93 (voice ~2.9x bed).
- [x] 3a (~$0.10): Fun Camera Pan Left, distilled, speed 3.0, benchmark scene/seed 12345.
      DONE: worker job 94b650a4bf1346f5. Render 220.6 s = $0.120. MEASURED
      REAL PAN: cumulative phase drift -64 px = -8.89% of frame width,
      monotonic across all 10 half-second steps; balloon centroid cx
      +0.0781 (content moves right = camera pans left, correct direction).
      Runs 18/21 measured ZERO at speed 1.0; speed 3.0 distilled pans.
- [x] 3b (~$0.25): Fun Camera Pan Left, mid (LoRA 0.5, 10 steps, cfg 2.0), speed 1.0, same scene/seed.
      DONE: worker job c05ba0a920194570. Render 795.5 s = $0.431 video GPU —
      OVER the $0.25 estimate (1.72x; the mid recipe's GPU time ran well past
      the ~450 s desk projection). Dispatch used the projection per the stop
      rule; the miss is in the estimate, disclosed here and in the report.
      MEASURED: NO PAN — phase drift (0,0) at every step, centroid +0.0006.
- [x] 3c (~$0.78): Fun Camera Pan Left, stock, speed 3.0, same scene/seed.
      DONE: worker job 1fc6192027424894. Render 1372.9 s = $0.744 video GPU
      (under estimate). Dispatched after the guard check: actuals 2a+2b+3a
      video GPU $0.368 + 3b est $0.25 + 3c est $0.78 = projected $1.398
      <= $1.75. MEASURED: NO PAN — net cumulative drift +8 px (+1.11% of
      width) but oscillating (-8..+8 across steps, non-monotonic) and the
      balloon centroid moved -0.0133 (against the pan direction; the
      balloon rises/shrinks instead). Trace-level noise, not a pan.

## Final accounting (actuals from settled/worker records)
Video GPU (worker render_seconds x L40S list $0.000542/s):
  2a $0.116 (214.8 s) · 2b $0.132 (243.4 s) · 3a $0.120 (220.6 s) ·
  3b $0.431 (795.5 s) · 3c $0.744 (1372.9 s) · TOTAL $1.543
vs $1.42 approved estimate (+$0.12, entirely 3b's estimate miss) and under
the $1.75 hard stop. The 2a/2b audio stages (Kokoro + ACE-Step on A10G,
spawned in parallel at dispatch) do not record separate seconds; the
platform's settled cost records for those jobs are 15c and 50c, its
measured-cost accounting for the full chain. CPU harness time: pennies.
Camera matrix (Pan Left, benchmark scene/seed): speed 1.0 = NO PAN in all
three recipes (distilled run 18, stock run 21, mid 3b); distilled @ 3.0 =
REAL PAN (3a); stock @ 3.0 = no pan (3c). Speed is necessary; recipe
decides whether the move survives at speed.
Outputs: ~/workspace/sinclip/outputs/proof-renders-2026-11-16/ (5 files,
sizes verified against the measured records).
RUN COMPLETE.
Camera jobs go direct to the worker endpoint (platform API does not forward
camera_recipe). Movement measured: red-blob centroid series + phase-correlation
frame-to-frame translation (px and % of frame width), per-recipe verdict.

## Log
- Run started. Probe: scratch/proof_renders_app.py (CPU-only harness; GPU spend
  happens only inside the five approved jobs themselves).
- Submitted (phase 1): 2a platform job 7262f6f29bdcafcd; 2b platform job
  d2c44a8c5ad33c2f; 3a worker job 94b650a4bf1346f5 (distilled, speed 3.0);
  3b worker job c05ba0a920194570 (mid, speed 1.0). 3c held until phase-1
  actuals confirm the $1.75 guard.
- CONSTRAINT (Jack, via parent, after phase 1 dispatched): "make them as
  short and as cheaply as possible." Jobs already dispatched (2a, 2b, 3a,
  3b) finish as-is per the constraint's own terms. Applies to 3c, the only
  undispatched job: reviewed for reductions — the Fun Camera lane renders
  exactly one segment at the workflow's fixed length (no duration parameter
  exists on the worker or platform for camera jobs), and the stock recipe's
  steps/cfg ARE the variable under test, so reducing them would void the
  proof. 3c therefore runs in its already-minimal form: single segment,
  no extras, benchmark scene/seed. No other jobs exist to shorten.
