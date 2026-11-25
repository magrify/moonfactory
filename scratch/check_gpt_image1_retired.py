"""Local checks for the 2026-11-25 GPT Image 1 removal.

Jack's order, 2026-11-25: a lane whose upstream is dead is
not left in place for him to decide about; take it out.
OpenAI shut GPT Image 1 down on 2026-10-23; the lane
(via VidLab) could only ever fail, yet it still sat in the
catalog for customers to pick.

Pins:

- "gpt_image1" is gone from pricing.IMAGE_LANES, so it
  cannot appear in /api/config's image_models, the studio
  image picker, or the API image listing (all three build
  from IMAGE_LANES);
- it is also gone from IMAGE_SIZE_LABELS and
  IMAGE_ASPECTS (the aspects expansion indexes IMAGE_LANES
  by IMAGE_ASPECTS keys, so a leftover row would break
  the module import itself);
- pricing.public_pricing carries no gpt_image1 entry;
- an old job record carrying image_model "gpt_image1"
  still resolves harmlessly through the .get read paths
  the library and gallery use (not uncensored, no image
  input classification, no exception);
- the app source no longer routes a VidLab submit by the
  retired id.

No Modal calls, no network, no paid generation.

Run:  modal-venv python scratch/check_gpt_image1_retired.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "platform"))
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if cond:
        print(f"  ok: {msg}")
    else:
        failures.append(msg)
        print(f"  FAIL: {msg}")


check("gpt_image1" not in pricing.IMAGE_LANES,
      "gpt_image1 is not in IMAGE_LANES (the live catalog)")
check("gpt_image1" not in pricing.IMAGE_SIZE_LABELS,
      "gpt_image1 is not in IMAGE_SIZE_LABELS")
check("gpt_image1" not in pricing.IMAGE_ASPECTS,
      "gpt_image1 is not in IMAGE_ASPECTS")
pub = pricing.public_pricing()
check("gpt_image1" not in str(pub),
      "public_pricing carries no gpt_image1 entry")

# An old job record must still read clean through the
# .get-based classifiers app.py uses for library/gallery
# rendering.
lane = pricing.IMAGE_LANES.get("gpt_image1" or "") or {}
check(lane.get("uncensored") is None and lane.get("edit") is None,
      "old gpt_image1 job record resolves to an empty lane, no error")

app_src = (Path(__file__).resolve().parent.parent
           / "platform" / "app.py").read_text()
check('"gpt_image1"' not in app_src,
      "app.py carries no gpt_image1 routing reference")

if failures:
    print(f"\n{len(failures)} FAILURE(S)")
    sys.exit(1)
print("\nall gpt_image1 retirement checks passed")
