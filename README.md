# Moonfactory

Moonfactory is a web app that turns a start image and a prompt into a
short vertical video with a music track. It is built for TikTok, Reels,
and X: 9:16 output, sound on every video.

Live app: https://magrify--moonfactory-api.us-east.modal.run

## What is running today

- Accounts with email verification, sign in, and per-user job history.
- Two generation lanes, both self-hosted:
  - Draft: Wan 2.2 TI2V-5B, fast preview renders of about 5 seconds.
  - Production: Wan 2.2 A14B at 720x1280.
- Every finished video gets a music bed (ACE-Step 1.5) mixed on before
  delivery. Free-tier videos also carry a watermark.
- A post chain (RIFE interpolation to 30 fps, SeedVR2 upscale to
  1080x1920) and two-segment chaining are proven in the worker and
  shown in modal-worker/STATUS.md. The public job form renders one
  segment per job today.
- Payments are wired for Stripe test mode: checkout for the Creator
  plan and for prepaid top-ups, with webhooks as the source of truth.
  Checkout stays off until test keys are added to the deployment
  secret. platform/LAUNCH-NOTES.md lists exactly what is needed.

## Pricing rules

Prices are computed, not typed into pages. platform/pricing.py holds
the measured per-job generation costs from the test phase and derives:

- a per-lane price floor of worst-case measured cost times three,
  grossed up for Stripe's 2.9% + 30c fee,
- the Creator plan's included production seconds from the plan's net
  revenue at that floor rate (about 40 seconds at current costs),
- per-job top-up prices at or above the floor.

BUSINESS-RULES.md is the full rule set: prepaid only, free tier capped
per account and across the whole platform, retries capped, and a lane
pauses itself in code if its floor ever rises above its price.

## Layout

- `platform/` - the public app. app.py is a FastAPI service on Modal:
  pages, accounts, quota checks, job dispatch, file delivery with the
  music mux and watermark, Stripe checkout and webhooks. pricing.py is
  the pricing formula. smoke_app.py is the end-to-end test that signs
  up a fresh account and renders a real Draft job through the public
  URL.
- `modal-worker/` - the private generation worker on Modal. app.py
  runs ComfyUI with the Wan 2.2 models and exposes a small job API
  (ENDPOINT.md) that only the platform can call. audio_app.py is the
  ACE-Step music stage.
- `workflows/` - the ComfyUI workflow templates the worker patches per
  job, plus the short-form delivery spec.
- `worker/` - the first RunPod handler skeleton. Kept for reference;
  the Modal worker replaced it before launch.

## Running it

The worker and the platform are separate Modal apps in the same
workspace:

    modal deploy modal-worker/app.py
    modal deploy platform/app.py

The platform needs a Modal Secret named `moonfactory-secrets` with
WORKER_API_KEY (the worker's key), ADMIN_KEY, and SESSION_SECRET.
Stripe and Turnstile keys are optional entries in the same secret;
LAUNCH-NOTES.md describes what each one switches on.

## Status

Test phase. The generation pipeline and the platform are proven end to
end, including a signup-to-download smoke test through the public URL.
Open items before a public launch push: Stripe test keys, a real
email provider for verification mail, and a domain. All three are
listed with exact steps in platform/LAUNCH-NOTES.md.
