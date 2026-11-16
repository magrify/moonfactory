# Studio look checklist (PARITY-PROGRAM Pass 8)

Built 2026-11-16, run 20. Fixed comparison: Moonfactory vs
higgsfield.ai, on the program's criteria: clicks from landing to a
running job, every price visible before dispatch, no dead or
decorative controls, model shelf visible at a glance.

Method, stated plainly: no true screenshots were captured in this
environment. This checklist is built from rendered page structure
and page reads: higgsfield.ai's public homepage as read on
2026-11-16, and Moonfactory's served pages and API responses read
back through the public URL after the Pass 8 deploy. Where a
Higgsfield surface sits behind sign-in (its studio composer), the
row says so and judges from its public pages and the parity map
(HIGGSFIELD-PARITY.md) instead of pretending to a screenshot.

## The four fixed views

### 1. Landing first screen

| | Moonfactory | Higgsfield |
|---|---|---|
| First screen | Name, one-line offer, a prompt field with a Start creating button (the typed prompt carries into the studio), Gallery and studio links | A rotating promo and feature carousel (Black Friday, Talent Contest, Genjutsu, model launches) over a community feed |
| Model shelf on the page | A catalog grid rendered from /api/config + /api/pricing: every model with its price, lengths, sizes, and who can use it | Model stories in the carousel; the full shelf lives inside the signed-in studio |
| Price visible | Yes: per-segment and per-second prices on the model cards, plan prices in the Pricing section, all from /api/pricing | Credit prices; the dollar value of a credit changes by plan |

Verdict: **par**. Moonfactory's first screen is plainer, but the
shelf and the prices are on it, and the prompt field starts the
same job the studio would. Higgsfield's first screen is richer
media with no prices and no way to start a job from it.

### 2. Composer with a model picked

| | Moonfactory | Higgsfield |
|---|---|---|
| Shape | One composer card: prompt first and largest, then the model catalog, then grouped setting controls (start image, camera and look, sound, length and shape), then Create | One composer in the signed-in studio, model catalog, setting pills (per the open-higgsfield pattern notes) |
| Price before dispatch | Per-second models: an exact quote line under the size picker. House lanes and effects: a price line under the Create button, computed from /api/pricing. Start images: the exact price is stated before the frame is made | A credit cost on the generate button; the dollar meaning depends on the plan's credit rate |
| Dead controls | None found: every control on the card maps to a field the server accepts; paid-only controls render locked with the reason on free accounts | Not auditable from public pages |

Verdict: **par** on shape and price visibility (Moonfactory's
exact-dollar quote is the stronger of the two on the program's
own criteria). Behind on composer depth: no reference slots, no
saved elements, no multi-shot rig; those are features, listed
open below, not presentation.

### 3. Gallery

| | Moonfactory | Higgsfield |
|---|---|---|
| Layout | A card grid: handle, model, camera presets, effect, look, length, size, aspect, segments, and the job's cost on every card | A feed of project cards with titles and handles; opening a project shows its prompts, assets, and settings |
| History as input | Not yet: a finished clip cannot be attached as the next job's start from the gallery or the job list (open item, feature) | Yes: projects open into their settings and assets |

Verdict: **par** as a gallery (every Moonfactory card now says
what made the clip and what it cost). **Behind** on gallery
depth: no project page, no attach-as-input. Both are features,
not presentation, and are listed open below.

### 4. A finished job card

| | Moonfactory | Higgsfield |
|---|---|---|
| Shows | Model and lane, length and size, status, the prompt, the exact cost (balance charge, included-budget debit, or free clip), camera presets in pick order, effect, film look picks, custom-music flag, segment progress and partial-delivery note, queue position while running | Job history inside the studio; the public surface for a finished job is the project view with prompts and settings |

Verdict: **ahead** on the program's stated criteria for a job
card: the exact dollar cost and the full recipe are on the card
itself, with no credit conversion and no second screen.

## Criteria scoreboard

| Criterion | Verdict | Note |
|---|---|---|
| Clicks from landing to a running job | par | Signed-in returning user: landing, Open the studio (or type a prompt on the landing and press Start creating), Create video. Higgsfield: landing, sign in / open studio, generate. First-time users on both sides add signup and, at Moonfactory, email verification |
| Every price visible before dispatch | ahead | Exact dollars everywhere: model cards, the house-lane price line, the per-second quote, the start-image price statement, the clip charge line. Higgsfield shows credits whose dollar value varies by plan |
| No dead or decorative controls | par | Every Moonfactory control is wired to a server-accepted field or states its gate; Higgsfield's signed-in studio is not auditable from public pages |
| Model shelf visible at a glance | par | Both shelves are one grid of named models; Moonfactory's carries prices and sizes on the cards, Higgsfield's carries more models (35+ vs 7, a shelf-breadth gap scored in PARITY-PROGRAM row 3, not a look gap) |

## What this pass changed to close the presentation gaps

- Landing: the hand-written model table is gone. The shelf is a
  catalog grid rendered from /api/config + /api/pricing (name,
  price per segment or per second at each size, lengths, sizes,
  who can use it, temporarily-unavailable state). A prompt field
  on the first screen carries the typed prompt into the studio.
- Studio Create: one composer. The prompt leads, large; the
  model picker is the same catalog as cards (click to pick, the
  plan gate disables the same cards it always disabled); every
  setting sits in a labeled group under the prompt (start image
  source, camera presets, film look, effects, music prompt,
  segments, aspect, end image), all rendered from served data in
  the picker's existing way (camera and effects were already
  data-rendered; the model catalog was the hand-written holdout).
- Prices: a price line under the Create button for the house
  lanes and effects, from /api/pricing, next to the per-second
  quote line that already existed.
- Job cards and gallery cards: model, camera presets, effect,
  film look, custom-music flag, and the exact cost on every card
  (the job data already carried the charge; the cards now show
  it). Gallery cards also gained length, size, aspect, and
  segment count.
- Clip tab: same tab bar, card, headings, and button treatment
  as Create (shared header with the gallery link, a "Your clips"
  results heading to match "Your videos"), so the studio reads
  as one product.
- One honest limit: the length options on the per-second cards
  come from the same single table that fills the form's length
  select (it mirrors what the server accepts per model), because
  no endpoint serves per-model lengths. Names, prices, sizes,
  availability, and access on the cards are all served data.

## Open items (features, not presentation; not faked)

- Attach a finished clip as the next job's start image from the
  gallery or job list (gallery-as-history's input half). Needs a
  server path that resolves a finished job's file into a start
  image; Pass 5 territory.
- A project page per gallery item (full prompt, settings, and
  assets on one page, the way Higgsfield projects open). Needs a
  new page and route, not a restyle.
- Reference and element slots in the composer (saved characters,
  products, locations). Pass 5.
- Cinema Studio style setting pills with popovers (lens, focal,
  aperture as pill controls). The film look already exists as
  data-rendered selects with the full vocabulary; the pill
  treatment is presentation, but the popover interaction is a
  rebuild of a working control, left for the look approval round
  rather than swapped blind.
- Model shelf breadth (7 names vs 35+). PARITY-PROGRAM row 3;
  a wiring program (Pass 2 and later), not a look gap.
