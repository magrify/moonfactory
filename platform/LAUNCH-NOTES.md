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
  computes (about 40 s at the measured costs from the test phase).
- Bot check on signup and job creation: Cloudflare Turnstile,
  provisioned for the platform hostname and live. A proof-of-work
  challenge stays accepted as a fallback for clients without a
  browser.

## Switched off, with the exact remaining step for each

### 1. Stripe (live is the requirement)

Jack's ruling (2026-11-16): live Stripe, for real, not a test-mode
placeholder. The code now accepts live keys as the active
configuration (the old test-only guard is removed), /api/config
reports the configured mode, and checkout can pin configured Stripe
price IDs (STRIPE_PRICE_CREATOR, STRIPE_PRICE_TOPUP_500/1000/2000)
or create prices inline from pricing.py.

State found on 2026-11-16: the Secure Vault holds NO stripe.com
login (the full saved-login list was checked), and the Stripe
connector is not connected. A secret key (sk_live_) exists only
inside the Stripe dashboard; no API or connector can export one.
The remaining account-side step, in one pass:

1. Sign in to the Stripe dashboard (or connect the account through
   the Stripe connector link the main agent can send: an OAuth
   consent, not a key hand-over).
2. Create the products/prices: Creator $20/mo, top-ups $5/$10/$20.
3. Add the live webhook endpoint for
   https://magrify--moonfactory-api.us-east.modal.run/api/stripe/webhook
   with events checkout.session.completed, invoice.paid,
   invoice.payment_failed, customer.subscription.deleted.
4. Reveal the live secret key and the webhook signing secret.
5. Those two values go straight into the Modal Secret
   moonfactory-secrets (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET)
   plus the price IDs, and the platform redeploys. No code change.

If the account is not yet activated for live payments, activation
in the dashboard (business details, bank account) is the step that
has to happen first; everything above is ready to land the moment
it is done.

### 2. Transactional email (Resend, decided 2026-11-16)

Jack's pick: Resend. The platform code already sends through the
Resend API as its primary route whenever RESEND_API_KEY is in the
deployment secret; the sender is config (RESEND_FROM), default
Moonfactory <noreply@magrify.com>. Fallbacks stay in place: 
PrivateEmail SMTP (public settings are code defaults: host
mail.privateemail.com, port 587 STARTTLS, user support@magrify.com)
whenever a mailbox password is configured, then the operator
outbox, so signup never breaks.

Remaining setup, all account-side (needs a browser session):
1. Create the Resend account as a Magrify business signup under
   support@magrify.com (free tier), completing any verification
   email in the PrivateEmail inbox.
2. Add magrify.com as the sending domain in Resend. Merge its DNS
   records into the magrify.com Cloudflare zone WITHOUT breaking
   PrivateEmail: extend the existing SPF record with Resend's
   include (never replace it), add Resend's DKIM record(s) as
   given, DMARC as given. Afterward, confirm support@magrify.com
   still receives mail with a test message.
3. Create a Resend API key and put it in the Modal Secret
   moonfactory-secrets as RESEND_API_KEY (value never written to
   any file or log).
4. The next deploy sends verification mail from
   noreply@magrify.com; verify with a fresh signup to a readable
   inbox and follow the link.

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
