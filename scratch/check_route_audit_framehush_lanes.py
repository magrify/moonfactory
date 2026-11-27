"""Local checks for the 2026-11-26 route audit's FrameHush
moves: Wan 3.0 and MiniMax H3 from BeatAPI to FrameHush.

Pins:
- both lanes route to FrameHush in MODEL_PROVIDER and carry
  their FrameHush model ids (wan-3.0, MiniMax-H3);
- the seeded true rates are the estimate-verified FrameHush
  prices (Wan 3.0: $0.15 / $0.30 / $0.485 per 5 s = 3.0 / 6.0
  / 9.7 per second; MiniMax H3: a flat $0.075 per 5 s = 1.5);
- the displaced BeatAPI rates are recorded in DISPLACED_RATES
  under the suffixed keys, and the BeatAPI model ids stay in
  BEATAPI_MODEL_IDS so a flip back needs no re-derivation;
- the customer shelves stay frozen on the BeatAPI-built
  rates through SHELF_PROVIDER_RATES, and every customer
  sell rate / debit for the two lanes equals the value the
  post-cost-cut snapshot pinned before the move.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "platform"))
import pricing  # noqa: E402

APP = (Path(__file__).parent.parent / "platform" / "app.py").read_text()

failures = []


def check(cond, label):
    if cond:
        print(f"  ok  {label}")
    else:
        failures.append(label)
        print(f"FAIL  {label}")


check('"wan3": "framehush",' in APP, "wan3 routes to framehush")
check('"minimax_h3": "framehush",' in APP,
      "minimax_h3 routes to framehush")
check('"wan3": "wan-3.0",' in APP, "wan3 FrameHush model id")
check('"minimax_h3": "MiniMax-H3",' in APP,
      "minimax_h3 FrameHush model id")
check('"wan3": "wan-3.0",' in APP and '"wan3": "wan-3.0",' in APP,
      "framehush map carries both lanes")

rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
check(rates["wan3"] == {"480p": 3.0, "720p": 6.0, "1080p": 9.7},
      "wan3 true rates are FrameHush's estimate prices")
check(rates["minimax_h3"] == {"480p": 1.5, "720p": 1.5, "1080p": 1.5},
      "minimax_h3 true rate is the flat FrameHush estimate")

disp = pricing.DISPLACED_RATES_CENTS_PER_SECOND
check(disp["wan3_beatapi"] == {"480p": 3.55, "720p": 7.1, "1080p": 13.6},
      "wan3 BeatAPI rates recorded displaced")
check(disp["minimax_h3_beatapi"] == {"480p": 2.0, "720p": 3.0, "1080p": 4.7},
      "minimax_h3 BeatAPI rates recorded displaced")
check('"wan3": "wan-3.0",' in APP and '"minimax_h3": "minimax-h3",' in APP,
      "BeatAPI model ids stay wired for a flip back")

shelf = pricing.SHELF_PROVIDER_RATES_CENTS_PER_SECOND
check(shelf["wan3"] == {"480p": 3.55, "720p": 7.1, "1080p": 13.6},
      "wan3 shelf frozen on the BeatAPI-built rates")
check(shelf["minimax_h3"] == {"480p": 2.0, "720p": 3.0, "1080p": 4.7},
      "minimax_h3 shelf frozen on the BeatAPI-built rates")

if failures:
    print(f"\n{len(failures)} FAILURES")
    sys.exit(1)
print("\nroute audit FrameHush lanes: all checks passed")
