# Moonfactory launch notes

What is live, what is switched off, and exactly what Jack has to supply
to finish the launch. Nothing in this file is a secret value.

## Live now

- The platform at its Modal URL (see BUILD-LOG.md run 4 for the exact
  address): landing page, studio, accounts with email verification,
  Draft and Production job dispatch to the worker, status polling, and
  file delivery with the house music bed muxed on and a watermark on
  free-tier files.
- Quotas and pricing are enforced in code from platform/pricing.py:
  free plan is Draft only, 5 clips per account TOTAL (lifetime
  counter on the account, no reset, no monthly allowance), plus a
  platform-wide total budget of 100 free jobs in config, watermarked.
  Creator is $20/mo with the included production seconds the formula
  computes (214 s at current measured costs; the production lane
  runs a 4-step distilled recipe since 2026-11-16, about $0.12 per
  segment measured, 15c worst case in pricing.py).
- Bot check on signup and job creation: Cloudflare Turnstile,
  provisioned for the platform hostname and live. A proof-of-work
  challenge stays accepted as a fallback for clients without a
  browser.
- Public gallery (Jack's decision, 2026-11-16): every finished clip
  lists at /gallery under the creator's handle only (a handle the
  user picks, or a neutral derived creator-XXXXXX; never an email
  or real name, no identifying job metadata). Creator-plan users
  get an opt-out checkbox in the studio; free clips always list.
  Every item has a Report button (stored for the operator), owners
  can delete their own clips at any time (which unlists them), and
  the operator can remove or restore any item with the admin key
  (GET /api/admin/reports, POST /api/admin/gallery/{id}/remove).
- Paid-only features, enforced server-side (Jack, 2026-11-16):
  custom audio (a per-job music prompt, up to 300 characters; the
  ACE-Step stage generates that job's bed at delivery time and the
  platform muxes it; free accounts keep the house bed and get a
  403 if they submit a music prompt) and model switching (choosing
  the Production lane; free accounts are locked to Draft with a
  403). The studio shows both controls locked with a plan note on
  free accounts.

## Switched off, with the exact remaining step for each

### 1. Stripe (live is the requirement)

Jack's ruling (2026-11-16): live Stripe, for real, not a test-mode
placeholder. The code now accepts live keys as the active
configuration (the old test-only guard is removed), /api/config
reports the configured mode, and checkout can pin configured Stripe
price IDs (STRIPE_PRICE_CREATOR, STRIPE_PRICE_TOPUP_500/1000/2000)
or create prices inline from pricing.py.

State as of 2026-11-16 (run 8): the account side is done except
one value. The Stripe connector was connected (OAuth consent), and
through it: the Magrify account (acct_1Q16NdAQThnByJFL) is fully
activated for live charges; the products and prices were created
live (Creator $20/mo; Credit Top-Up $5 / $10 / $20 one-time); and
the live webhook endpoint for
https://magrify--moonfactory-api.us-east.modal.run/api/stripe/webhook
was created with events checkout.session.completed, invoice.paid,
invoice.payment_failed, customer.subscription.deleted.

The webhook signing secret and the four price IDs are stored in
the second Modal secret, moonfactory-integrations (alongside the
Resend entries), and the platform was redeployed on them. Verified
live: a fresh signup still returns email_mode "resend" (the
integrations secret is reaching the app), and an unsigned POST to
the webhook is refused (503) because the handler builds its Stripe
client first and that needs the one missing value below; the
400 bad-signature path opens once it lands.

The ONE outstanding value: STRIPE_SECRET_KEY (sk_live_). A secret
key cannot be created or exported through the API or the
connector; it exists only inside the Stripe dashboard, and that
sign-in is gated by a passkey Muse's browser cannot complete.
When Jack supplies it (dashboard reveal, entered through the
Secure Vault), it goes into moonfactory-integrations the same way,
the platform redeploys, /api/config flips payments_mode to
"live", and checkout opens. No code change. Until then checkout
stays off, deliberately, rather than half-configured.

### 2. Transactional email (Resend) — DONE 2026-11-16

Jack's pick: Resend. The account exists under the Magrify GitHub
(account email support@magrify.com) and magrify.com is VERIFIED as
a sending domain in Resend. The platform sends through the Resend
API as its primary route; the sender is config (RESEND_FROM),
Moonfactory <noreply@magrify.com>. Fallbacks stay in place:
PrivateEmail SMTP (public settings are code defaults: host
mail.privateemail.com, port 587 STARTTLS, user support@magrify.com)
whenever a mailbox password is configured, then the operator
outbox, so signup never breaks.

Secret arrangement: the original Modal secret moonfactory-secrets
was NOT touched (its SESSION_SECRET and TURNSTILE_SECRET_KEY exist
nowhere else). The Resend API key (key name "moonfactory", full
access) lives in a SECOND Modal secret, moonfactory-integrations,
alongside RESEND_FROM; the platform function mounts both secrets.
The key value is in no file, log, or commit.

Delivery proof (2026-11-16): a fresh signup through the public URL
with a Gmail plus-address returned email_mode "resend", and the
verification email arrived in that Gmail inbox from Moonfactory
<noreply@magrify.com>, subject "Verify your Moonfactory account",
within seconds of the signup. The one remaining click (open the
link, log in, run a job) goes through the browser with the
connector's credential reference, because the Gmail connector
redacts verification links in its read path by design.

### 3. Cloudflare Turnstile (done)

A Turnstile widget was created for the platform hostname through the
Cloudflare API and its keys are in the deployment secret; the studio
renders the widget and the API verifies it. Nothing is needed from
Jack here. If the platform moves to a custom domain, the widget's
domain list needs the new hostname added in the Cloudflare dashboard.

### 4. Name and domain

Moonfactory is the product name everywhere in the copy. Still open:
the final domain (the platform runs on its modal.run address until
then) and the trademark screen from the original decision list.

## Deliberate launch limits, stated plainly

- One video per job is a single ~5 second segment. Chained
  multi-segment videos are proven in the worker (see BUILD-LOG run 3)
  but are not wired into the public job form yet; that is the next
  product step, not a hidden gap.
- Every delivered video carries a music bed: the house bed by
  default, or a bed generated from the job's own music prompt for
  Creator-plan jobs (live since 2026-11-16; the audio app takes any
  caption now and caches per caption+seed). If the audio stage ever
  fails at delivery time, the file falls back to the house bed and
  the job record is flagged (audio_fallback) rather than breaking
  delivery.
- Production renders take about 25 minutes and Draft about 6 to 13.
  The studio says so up front; there is no queue position display yet.
