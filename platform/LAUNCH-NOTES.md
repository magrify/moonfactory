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
  free plan is Draft only, 5 clips per account per month, 100 free
  jobs per month across the whole platform, watermarked. Creator is
  $20/mo with the included production seconds the formula computes
  (about 40 s at the measured costs from the test phase).
- Bot check on signup and job creation: Cloudflare Turnstile,
  provisioned for the platform hostname and live. A proof-of-work
  challenge stays accepted as a fallback for clients without a
  browser.

## Switched off until Jack supplies these

### 1. Stripe (test mode first)

The checkout and webhook code is finished and refuses live keys. To
switch payments on, Jack needs to:

1. Create or open the Stripe account Moonfactory will bill under
   (his call which business entity).
2. In the Stripe dashboard, test mode: Developers, API keys. Copy the
   test secret key (starts sk_test_) and the test publishable key.
3. Add a webhook endpoint in test mode pointing at
   <platform URL>/api/stripe/webhook for the events
   checkout.session.completed, invoice.paid, invoice.payment_failed,
   customer.subscription.deleted, and copy its signing secret
   (starts whsec_).
4. Hand the three values over through the Secure Vault, not chat.
   Muse then puts STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET into
   the Modal Secret named moonfactory-secrets and redeploys. No code
   change is needed.
5. To go live later: repeat with the live keys, and remove the
   sk_test_ guard in platform/app.py (stripe_client) as a deliberate,
   separate step.

### 2. Transactional email

Signups work today, but verification emails land in the operator
outbox (readable only with the admin key) because no mail provider is
configured. For real public signup, Jack picks a provider
(PrivateEmail SMTP for a magrify.com address, Resend, Postmark, SES)
and supplies SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
through the Secure Vault. They go into the same Modal Secret; the
mailer switches from outbox to SMTP on the next deploy, no code change.

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
- Every delivered video carries the house music bed generated during
  the test phase. Per-prompt music generation is a later step; the
  audio app accepts a fixed caption today.
- Production renders take about 25 minutes and Draft about 6 to 13.
  The studio says so up front; there is no queue position display yet.
