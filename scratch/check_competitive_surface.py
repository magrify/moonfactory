"""Local checks for the 2026-11-26 competitive pass.

The teardown (~/workspace/sinclip/competitive-teardown-2026-11-26.md)
found two surface losses that cost nothing to close:

- the Create surface never stated the value facts that win
  the valuable axis (5 free clips, no watermark, balance
  never expires, finished videos stay in the library), while
  FrameHush's homepage leads with its price claim and
  Higgsfield's pricing page leads with its plans;
- the competitive win over Higgsfield was invisible at the
  moment of decision: the quote line gave our exact price
  but never the comparison, even though /api/pricing
  already publishes a Higgsfield effective figure per lane.

Pins:

- the studio HTML carries the value strip under the
  composer note, worded without any claim the pricing
  table cannot back (no "every price" wording: the Kling
  lanes above Higgsfield are a known, flagged exception);
- the quote path appends the Higgsfield comparison only
  from the published per lane figures, only when the
  Higgsfield figure for the picked model and resolution
  is above our sell figure, and never for flat per clip
  lanes, whose per second figures are a worst case
  ceiling rather than a per clip comparison.

No Modal calls, no network, no paid generation.

Run:  modal-venv python scratch/check_competitive_surface.py
"""

import pathlib
import sys

APP = pathlib.Path(__file__).resolve().parent.parent / "platform" / "app.py"
SRC = APP.read_text(encoding="utf-8")

failures = []


def pin(cond, label):
    if cond:
        print("ok   -", label)
    else:
        failures.append(label)
        print("FAIL -", label)


STRIP = ('<p class="muted" id="value-strip">Five free clips to start, '
         'and no watermark. Your balance never expires, and your '
         'finished videos stay in your library.</p>')
pin(STRIP in SRC, "value strip sits in the studio HTML, exact wording")
pin('id="value-strip"' in SRC and "every price" not in STRIP.lower(),
    "value strip makes no every-price claim the Kling lanes falsify")

pin("Higgsfield would charge about $" in SRC,
    "quote path carries the Higgsfield comparison line")
pin("lane.higgsfield_effective_cents_per_second" in SRC
    and "lane.sell_cents_per_second" in SRC,
    "comparison reads the published per lane figures, nothing invented")
pin("hfRate > sellRate" in SRC,
    "comparison shows only where the Higgsfield figure is above ours")
pin("fr.cost_basis === 'flat_per_clip'" in SRC,
    "flat per clip lanes are excluded from the per second comparison")

if failures:
    print("\n%d pin(s) failed" % len(failures))
    sys.exit(1)
print("\nall competitive surface pins pass")
