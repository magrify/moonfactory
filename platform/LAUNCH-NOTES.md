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

The ONE outstanding value was STRIPE_SECRET_KEY. A secret key
cannot be created or exported through the API or the connector;
it exists only inside the Stripe dashboard, and that sign-in is
gated by a passkey Muse's browser cannot complete. RESOLVED
2026-11-16: Jack created a restricted key in the dashboard
(Checkout Sessions write, template: Recurring subscriptions and
billing) and pasted it in chat; it is stored as STRIPE_SECRET_KEY
in the Modal secret `moonfactory-providers` (alongside FAL_KEY),
and payments verified LIVE (payments_mode "live", real checkout
session creation confirmed).

Webhook crediting verified end to end 2026-11-16 (run 11): with
the key present, an unsigned POST to /api/stripe/webhook is
refused 400 "bad signature" (was 503 before the key). Properly
signed synthetic events through the public URL credited a test
account exactly: a $10 top-up (price STRIPE_PRICE_TOPUP_1000)
moved balance 0 -> 1000 cents and net_paid_cents 0 -> 941.0 (the
fee-net amount); a Creator invoice.paid set plan=creator with
the included budget at 637.33 cost-cents and net_paid_cents
1912.0, and dedupe held at both layers (same event resent ->
duplicate, no double count; new event id with the same invoice
id -> still no double count). That run also found and fixed a
real bug: stripe-python's construct_event returns typed objects
whose .get() raises AttributeError, so every signed event had
been crashing the handler with a 500 before crediting (and the
event id was already marked seen, so Stripe retries would have
been dropped as duplicates). The handler now parses the
signature-verified payload as plain JSON after construct_event.

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

## Update, 2026-11-16 (later): Wan 3.0, Wan 3.0 Prime, and the cost ceiling

The model picker is live. The studio and the landing page offer
three models by name only: Wan 2.2, Wan 3.0, and Wan 3.0 Prime. No
surface says where any model runs. The default for every account
and every job is Wan 2.2, the cheapest (BUSINESS-RULES Rule 3,
Amendment 2); switching models is a Creator-plan feature, enforced
on the server: a free account that asks for Wan 3.0 or Prime gets a
clean 403 before anything is quoted, held, or dispatched.

Wan 3.0 and Wan 3.0 Prime run per second: 480p, 720p, or 1080p, 3
to 30 seconds in one pass, with the sound the model makes kept in
the delivered file and normalized to the house loudness standard.
Every job follows quote, hold, dispatch, settle: the exact price
is quoted first (POST /api/jobs/quote, and the studio shows it
before you create), the job's spend cap (estimate times 1.1) is
held before dispatch, and the hold settles at the actual billed
seconds, never above the cap. Any failure on these two models
surfaces as one sentence, "this model is temporarily unavailable";
the hold is released in full and nothing is charged.

Included time is one budget now, not per-model allowances: 637
cents of measured generation cost per Creator month at current
prices. A Wan 2.2 segment debits 15 cents of it (the same 214
included production seconds as before); a Wan 3.0 second at 720p
debits 10.1 cents; a Prime second at 720p debits 14.1 cents.
Balance payment runs at three times measured cost, as always.

Amendment 2's hard ceiling is enforced before dispatch on every
paid path: an account's settled generation cost across all models
combined can never exceed what it has paid in, net of card fees
(the Stripe webhook keeps the paid-in total; subscriptions count
on invoice.paid, top-ups on checkout completion). A job whose
worst-case cost would cross the ceiling is refused with a 402
before anything runs. Free accounts stay bounded by the
5-lifetime-clips rule and the platform-wide free ceiling instead.

The per-second lanes guard themselves: the published price table
behind them is re-read every six hours, and if it ever goes more
than 24 hours without a verified refresh, the lanes pause rather
than quote from stale prices.

Payments state: /api/config reports payments_mode "live" (the
platform's restricted key is mounted and recognized), and checkout
session creation is verified against the live account.

One operational state to know: the generation account behind the
two per-second models currently holds zero credit, so a Wan 3.0
or Prime submission is refused at dispatch today and the user
sees the clean unavailable message with the hold released (this
exact path is what the verification below exercised). The moment
credit is added to that account, the same submissions succeed
with no code or configuration change.

Verification, 2026-11-16, against the public URL: /api/config
shows the three models, default Wan 2.2, payments live.
/api/pricing shows the per-second lanes unpaused at 10.0 and 14.0
cents per second (720p). Free account: Wan 3.0 create 403, Prime
quote 403. Test Creator account with a token paid-in total:
Wan 2.2 Production refused 402 and Wan 3.0 refused 402, no jobs
created, balance and budget untouched. With a full month's
paid-in total: Wan 3.0, 5s, 720p quoted at exactly 50.5 cents of
included budget (152 cents from balance; cap 55.55); Wan 3.0,
30s, 1080p quoted at 1,809 cents from balance; Prime, 5s, 720p at
212 cents. One real Wan 3.0 submission reached the provider and
came back refused for the zero balance: the user-visible result
was the 503 unavailable sentence, the job record shows the error
with the hold released, and the account's balance, budget, and
settled cost were all unchanged afterwards.

## Update, 2026-11-16 (later still): camera presets in the studio

The camera-preset library (workflows/camera-presets/, ported from
Freefield's Cinema Studio) is wired into the studio form. The
picker is rendered from the library data, never hand-built:
GET /api/camera-presets serves presets-index.json (42 presets,
the look vocabulary, max 3 chained moves), and the form builds
its chips and look selects from that response.

Behavior: a user chains up to three camera moves in pick order
(numbered chips, with a "Camera: A -> B" line under them) and can
add film-look picks (tempo, genre, era, camera body, lens, focal
length, aperture, lighting, palette) as optional selects. Nothing
is defaulted in: with no camera selection, the prompt that runs
is exactly the one the user wrote. At job creation the server
composes the final prompt in the library's grammar (preset recipe
text + user prompt, look sentence folded in), stores BOTH the
original prompt and the applied preset names on the job record,
and the studio job list and the public gallery show the preset
names ("Camera: Dolly in"). Regenerating a job recomposes from
the stored original, so the identical prompt runs again.

Presets work on both Wan 2.2 lanes and the composed prompt passes
through unchanged to the Wan 3.0 lanes. Presets are not a paid
feature: free accounts use them on Draft; model switching stays
the paid gate. The nine Fun Camera presets (true camera
conditioning, waiting on the Fun Camera model download) appear
in the picker disabled with a plain "coming soon" label, and the
server refuses them with a 422 if asked directly; invalid
selections (unknown preset, a fourth move, an unknown look
value) are also clean 422s before anything is dispatched. The
keyframe recipes carried by 13 of the prompt presets are not
wired (the dispatcher does not expose the firstlast lane yet);
those presets still run today on their prompt text.

Verification, 2026-11-16, against the public URL
(scratch/verify_camera_presets.py, in-Modal harness): the picker
endpoint serves 42 presets (33 available, 9 coming soon) and all
nine look slots. Free account: the four invalid selections each
returned 422; a Draft job with Dolly in plus a Classic
Anamorphic lens pick stored the preset id, the look, and the
original prompt, with the composed prompt on the record. Paid
account: a Production job with Dolly in chained into Pan left
stored both names in order and the ", then " composition. One
live Production render (Dolly in, the balloon benchmark scene)
finished done; the worker's own job record holds the composed
prompt byte for byte, the delivered file is 720x1280 h264 with
AAC audio, and the gallery item for the clip shows
"Camera: Dolly in". GPU spend for the whole verification was
the one distilled production render, about $0.12.

## Update, 2026-11-16 (run 13): Amendment 3 pricing and pay-per-video

Two billing paths, both first-class (Jack, 2026-11-16).
Pay per video: any verified account buys top-ups ($5 / $10 / $20)
and pays per job from balance on every lane, no subscription.
Creator: the $20/mo plan with its included budget, plus top-ups.
"Paid" for the feature gates (switching to Wan 3.0 or Wan 3.0
Prime, custom music prompts, gallery opt-out) now means any
paying customer: an active subscription OR an account that has
ever paid in. The included budget itself stays a subscription
benefit; pay-per-video accounts pay every job from balance. The
free path is unchanged: 5 lifetime Draft clips on Wan 2.2,
watermarked, all gates closed for accounts that have never paid.
Paid jobs ship without a watermark whatever the plan name says.

Per-second lane prices changed under Amendment 3. Balance prices
for Wan 3.0 and Wan 3.0 Prime are no longer cost times three;
they are set against Higgsfield's effective per-clip price for
the comparable output (5 seconds, 720p, with audio), with a hard
minimum of cost times 1.25. On both lanes the minimum is what
binds: Higgsfield's effective price for Wan 3.0 ($0.44 a clip)
sits below the raw provider cost of the clip itself, and Prime's
($0.74) sits below cost times 1.25. So Wan 3.0 sells at 12.625
cents per second at 720p (a 5 second clip is 64 cents of balance,
down from $1.52) and Prime at 17.625 cents per second (89 cents,
down from $2.12), and the pricing data records both lanes as
cannot-undercut rather than pretending otherwise; that flag is
in /api/pricing as data, not in any user-facing copy. Included
budget still debits these lanes at raw cost, and the per-account
cost ceiling still refuses jobs before dispatch on every path.

Verification, 2026-11-16, against the public URL
(scratch/verify_pricing_billing.py, in-Modal harness): a
never-paid free account got 403 on Production create, Wan 3.0
quote, Wan 3.0 create, a music-prompt create, and gallery
opt-out. A signed $10 top-up webhook credited a no-subscription
account (balance 1000, net paid 941.0, plan still free), after
which opt-out returned 200, the Wan 3.0 quote came back as a
64-cent balance charge, the Prime quote as 89 cents, a Draft job
with a music prompt was accepted (and refunded in full when its
deliberately invalid start image failed it before sampling),
and a Production job ran from balance at 99 cents. With the
account's settled cost set to leave one cent of ceiling
headroom, both a Wan 3.0 create and a Production create were
refused 402 before dispatch with nothing created or charged.

## Frontier shelf (Pass 2, 2026-11-16)

Four more models in the picker: Seedance 2.5, Veo 3.1, Veo 3.1
Fast, and Kling 3.0, next to Wan 2.2, Wan 3.0, and Wan 3.0 Prime.
Names only, as always; the default is still Wan 2.2 and switching
is still paid-only. Each model validates its own lengths and
resolutions (Veo makes 4, 6, or 8 second clips; Kling up to 15
seconds; Seedance 4 to 30 seconds at up to 1080p).

Seedance 2.5 is the first lane that beats the big credit
platform's price instead of just clearing our minimum: it sells
at 33.368 cents per second at 720p against their effective 34.4,
so a 5 second clip is $1.67 of balance against their ~$1.72, and
the 1.25x minimum under it is satisfied with room. That is
possible because the lane's supplier charges $0.194 per second
where the first supplier quoted $0.462 for the same model. Veo
3.1, Veo 3.1 Fast, and Kling 3.0 have no measured competitor
effective yet, so they sell at the hard minimum (cost times
1.25): 50.125, 18.875, and 15.875 cents per second at 720p.
Included budget still debits every lane at raw provider cost,
and the per-account cost ceiling is untouched.

The 6-hourly rate refresh now covers the new supplier's
published Seedance prices as a required read alongside the Wan
pages; a failed read still leaves the staleness pause armed.

Verification, 2026-11-16, against the public URL
(scratch/verify_frontier_shelf.py, in-Modal harness): config
lists all seven models in order with Seedance unavailable (its
supplier account is the one remaining setup step; the lane
unpauses the moment its key is stored, no deploy needed). A free
account got 403 on create and quote for every new lane. Quotes
on a Creator account: Seedance 5 s 720p at 167 cents of balance
or 98.21 cents of included budget (the refreshed raw cost),
Veo 3.1 8 s at 401 / 320.8, Kling 3.0 5 s at 80 / 63.5.
Per-model validation refused Veo at 5 seconds and 480p,
Seedance at 3 seconds, and Kling at 20 seconds, all 422. The
cost ceiling refused a Wan 3.0 create at 402 before dispatch
with no job created. Real submissions at $0 provider balances:
Wan 3.0, Veo 3.1 Fast, and Kling 3.0 each returned the clean
"temporarily unavailable" with an error job and the hold fully
released; Seedance refused 503 before dispatch (lane paused,
no key yet). Balances, included budget, and settled cost were
untouched after every submission.
