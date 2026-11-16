# Sinclip Business Rules: Margin by Construction

Set by Jack, 2026-11-15: save money as a business, and structure pricing so every job makes money. The honest version of "guaranteed profit" that a business can actually keep: no system can guarantee the business profits overall, but the platform can guarantee that **no individual job ever runs at a loss**, by construction, in code, before any GPU second is spent. These rules are requirements for the build, not aspirations.

## Rule 1: Money lands before compute runs

- Paid work is prepaid. Subscriptions bill at the start of the cycle; top-ups are charged before the job enters the queue. There is no "generate now, invoice later" path anywhere in the system.
- A job is dispatched only after the API layer confirms paid status or a prepaid balance that covers the job's worst-case cost. Stripe webhooks are the source of truth; a failed or disputed payment freezes dispatch immediately.

## Rule 2: Every lane has a hard price floor, computed from measured cost

- Price floor per lane = (worst-case measured cost per finished second, including retries, post chain, storage, and payment processing fees) x margin multiplier. The multiplier starts at 3x and is a config value, never a guess typed into a page.
- Costs are measured, not estimated: every job logs its actual GPU seconds, API charges, and post-processing time. Lane prices are re-derived from the trailing 30 days of real job costs. Until the first test session produces real numbers, the working estimates are: self-hosted lanes about $0.40 to $0.75 per finished minute; Wan 3.0 API $0.10/s at 720p; Wan 3.0 Prime about $0.14/s at 720p.
- If a provider raises prices so a lane's floor rises above its selling price, the lane auto-pauses for new jobs instead of quietly losing money. Selling a job below its floor is impossible in code, not just discouraged.

## Rule 3: Per-second API lanes are never bundled

- Wan 3.0 and Wan 3.0 Prime are sold per use or as prepaid top-ups only. They never appear as "included minutes" in any plan, because their cost is set by Alibaba's meter, not ours.
- Before an API-lane job runs, the user sees the exact estimated charge, and the job's hard spend cap is set to that estimate plus a small tolerance. If the provider call would exceed the cap, the job stops and the unused balance stays in the user's account.

## Rule 4: Included minutes live only on infrastructure we control

- Plan inclusions (for example the Creator plan's 15 finished minutes) exist only on the self-hosted lanes, where marginal cost is our own GPU time and falls as hardware gets cheaper.
- Included minutes do not roll over indefinitely; they expire at cycle end (or roll one month, capped). Unused inclusion is not a liability that compounds.
- Retries are capped per job (a set number of regenerations included; beyond that, the job costs additional balance). One unhappy user cannot turn one job into ten renders on our dime.

## Rule 5: The free tier is a marketing budget with a ceiling, not an open tap

- Free clips run only on the cheapest lane (Draft, self-hosted 5B), at 480p, watermarked, hard-capped per account per month, lowest queue priority.
- Free jobs run in spare capacity: when paid work is queued, free jobs wait. Free-tier spend has a monthly platform-wide ceiling in config; when the ceiling is hit, free jobs pause until the next month. The free tier can never crowd out or outspend paying work.
- Anti-abuse is part of the cost model: one account per person enforced by email verification and rate limits, because a farmed free tier is a real GPU bill.

## Rule 6: Idle costs zero, always

- Workers scale to zero. No GPU runs, and nothing bills, while nobody is generating. This rule killed always-on designs twice already (RunPod pods, then any standing server) and it stays.
- Storage and delivery use pay-as-you-go tiers with lifecycle rules: finished videos move to cheap storage after a set window, and free-tier outputs expire on a stated schedule.

## Rule 7: Every job carries its own profit record

- Each completed job stores: price charged, actual cost by component (GPU seconds, API fees, storage, processing fees), and margin. A weekly report rolls these up per lane.
- Any lane whose measured margin goes negative two weeks running is flagged for repricing or pause. The business finds out from the report, not from the bank balance.

## Rule 8: Fees and failure are priced in, not absorbed silently

- Stripe's processing fee (2.9% + 30c) is inside the price floor formula in Rule 2, so small transactions are priced knowing the fee eats a fixed 30c; this is why top-ups have a sensible minimum purchase.
- Failed generations caused by our pipeline are re-run on us within the retry cap; failures caused by a provider outage are re-queued, not refunded in cash, balance is restored in credits. Refund policy: credits, not cash, except where the law requires otherwise.

## What this means for the launch numbers

- Creator $20/mo with 15 included Production minutes: at the estimated $0.40 to $0.75 per finished minute, worst-case included cost is about $11.25 against $20 revenue, before Stripe fees (about $0.88 on $20), leaving margin even if a subscriber uses every included minute. If measured costs come in above the estimate, Rule 2 forces the included minutes or the price to move before launch, not after losses.
- Prime lane: a 30-second 720p job costs about $4.20 raw; at the 3x floor it sells for about $12.60 or consumes equivalent top-up balance. The user sees that number before running it (Rule 3).
