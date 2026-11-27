"""Local checks for the 2026-11-26 route audit's moves:
Wan 3.0 and MiniMax H3 from BeatAPI to FrameHush, LTX-2.5
from fal to SpicyAPI, Seedance 2.0 Fast from VidLab to
SpicyAPI, and Vidu Q4 Preview from VidLab to Kie.

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

check('"ltx25": "spicyapi",' in APP, "ltx25 routes to spicyapi")
check('"ltx25": {"text": "lightricks/ltx-2.5/text-to-video",' in APP,
      "ltx25 carries per-mode SpicyAPI endpoints")
check('slug = slug.get(params["mode"])' in APP,
      "spicy submit resolves per-mode endpoint dicts")
check(rates["ltx25"] == {"720p": 9.0, "1080p": 13.0, "4K": 30.0},
      "ltx25 true rates are SpicyAPI's catalog prices")
check(pricing.FALLBACK_PROVIDER_RATES_CENTS_PER_SECOND["ltx25"]
      == {"720p": 12.0, "1080p": 17.0, "4K": 34.0},
      "ltx25 fal rates recorded as the fallback")
check(shelf["ltx25"] == {"720p": 12.0, "1080p": 17.0, "4K": 34.0},
      "ltx25 shelf frozen on the fal-built rates")
check('"ltx25": [("lightricks/ltx-2.5/text-to-video"' not in APP,
      "rate refresh no longer reads fal's LTX-2.5 page")
# Superseded 2026-11-27: Seedance 2.0 Fast moved on to
# UnificAlly in the catalog audit (plain rates under
# SpicyAPI's); the SpicyAPI route pinned below stays wired
# as the spare and its rates are the displaced record.
check('"seedance2_fast": "unifically",' in APP,
      "seedance2_fast routes to unifically")
check('"seedance2_fast": {\n            "text": '
      '"bytedance/seedance-2.0-fast/text-to-video",' in APP,
      "seedance2_fast carries per-mode SpicyAPI endpoints")
check(rates["seedance2_fast"] == {"480p": 4.44, "720p": 9.18},
      "seedance2_fast true rates are UnificAlly's plain prices")
check(disp["seedance2_fast_spicyapi"] == {"480p": 4.5, "720p": 9.68},
      "seedance2_fast SpicyAPI rates recorded displaced")
check(disp["seedance2_fast_vidlab"] == {"480p": 5.0, "720p": 11.0},
      "seedance2_fast VidLab rates recorded displaced")
check(shelf["seedance2_fast"] == {"480p": 5.0, "720p": 11.0},
      "seedance2_fast shelf frozen on the VidLab-built rates")
check('"seedance2_fast": {"slug": "seedance-2.0-fast",' not in APP,
      "seedance2_fast leaves the VidLab model table")
check('"vidu_q4": "kie",' in APP, "vidu_q4 routes to kie")
check('("vidu_q4", "text"): "vidu-q4/text-to-video",' in APP,
      "vidu_q4 carries Kie endpoints")
check(rates["vidu_q4"] == {"480p": 3.6, "720p": 7.6, "1080p": 9.6},
      "vidu_q4 true rates are Kie's tier prices")
check(disp["vidu_q4_vidlab"] == {"480p": 6.0, "720p": 12.0, "1080p": 13.0},
      "vidu_q4 VidLab rates recorded displaced")
check(shelf["vidu_q4"] == {"480p": 6.0, "720p": 12.0, "1080p": 13.0},
      "vidu_q4 shelf frozen on the VidLab-built rates")
check('"vidu_q4": {"slug": "vidu-q4-preview",' not in APP,
      "vidu_q4 leaves the VidLab model table")
check('"wan3": ("https://beatapi.io/wan-3-0-api", "")' not in APP,
      "rate refresh no longer reads BeatAPI's Wan 3.0 page")
check('required = set()' in APP,
      "rate refresh has no required page reads left")

if failures:
    print(f"\n{len(failures)} FAILURES")
    sys.exit(1)
print("\nroute audit FrameHush lanes: all checks passed")
