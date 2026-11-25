"""Local checks for the 2026-11-25 MAGI-2 wiring.

Pins the MAGI-2 lane's shape so a later edit cannot quietly
break it:

- MAGI-2 is a real picker entry under its own name, paid
  only, a per-second lane routed to Higgsfield's own API,
  with the HIGGSFIELD_API_KEY env mapping and both poll
  provider lists carrying the new supplier;
- the seeded rate is Higgsfield's published $0.08/s at its
  only resolution (1080p), and pricing is the STANDARD
  markup (Jack's ruling, 2026-11-25): the frontier lane
  resolves on the standard basis at cost x 1.25, the
  lane is NOT an at-cost exception and carries no
  HIGGSFIELD_POLICY_CENTS_PER_SECOND entry, and the owner
  pays raw cost;
- the lane's lengths are exactly the model's own 4 to 30
  seconds: no chained lengths past 30;
- the audio record says the model speaks natively (its
  dialogue, effects and music generate in the same pass);
- the submit and mirror functions exist, post the mode's
  own endpoint with the documented Key auth scheme and the
  documented text-to-video body, store the returned
  status_url, and branch on Higgsfield's documented status
  words, reading the result from video.url.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_magi2_lane.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- picker entry and routing ------------------------------------------

model_ids = {m["id"] for m in app.MODELS}
check("magi2" in model_ids, "magi2 missing from MODELS")
entry = next(m for m in app.MODELS if m["id"] == "magi2")
check(entry.get("label") == "MAGI-2",
      "magi2 label is not its own name MAGI-2")
check(entry.get("paid_only") is True,
      "magi2 is not paid_only (an API-cost lane is never free)")
check("magi2" in app.PER_SECOND_MODELS,
      "magi2 missing from PER_SECOND_MODELS")
check(app.MODEL_PROVIDER.get("magi2") == "higgsfield",
      "magi2 does not route to higgsfield")
check(app.provider_for("magi2", "1080p") == "higgsfield",
      "provider_for does not resolve magi2 to higgsfield")
check(app.PROVIDER_KEY_ENV.get("higgsfield") == "HIGGSFIELD_API_KEY",
      "higgsfield key env missing")
check("higgsfield" in app.API_POLL_PROVIDERS,
      "higgsfield missing from API_POLL_PROVIDERS")
check("higgsfield" in app.URL_RESULT_PROVIDERS,
      "higgsfield missing from URL_RESULT_PROVIDERS")
check(app.HIGGSFIELD_BASE == "https://api.higgsfield.ai",
      "HIGGSFIELD_BASE drifted")
spec = app.HIGGSFIELD_VIDEO_MODELS.get("magi2") or {}
check(spec.get("text") == "sand/magi-2/text-to-video",
      "magi2 text endpoint drifted")
check(spec.get("image") == "sand/magi-2/image-to-video",
      "magi2 image endpoint drifted")
check(spec.get("reference") == "sand/magi-2/reference-to-video",
      "magi2 reference endpoint drifted")

# -- pricing: standard markup, never at cost ----------------------------

check(pricing.PROVIDER_RATES_CENTS_PER_SECOND.get("magi2")
      == {"1080p": 8.0}, "magi2 seeded rate drifted from $0.08/s")
check(pricing.MODEL_RESOLUTIONS.get("magi2") == ("1080p",),
      "magi2 resolutions drifted from 1080p only")
check("magi2" not in pricing.HIGGSFIELD_POLICY_CENTS_PER_SECOND,
      "magi2 gained a policy-table entry (it is not at-cost)")
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
lane = pricing.frontier_lane("magi2", "1080p", rates)
check(lane["pricing_basis"] == "standard",
      "magi2 does not resolve on the standard pricing basis")
check(lane["sell_cents_per_second"] == 10.125,
      "magi2 customer rate is not (cost + post) x 1.25 = $0.10/s")
owner_lane = pricing.frontier_lane("magi2", "1080p", rates,
                                   owner=True)
check(owner_lane["sell_cents_per_second"] == 8.1,
      "magi2 owner rate is not the raw cost (rate + post)")
est = pricing.per_second_estimate("magi2", "1080p", 5, 0.0, rates)
check(est["raw_cost_cents"] == 40.5,
      "magi2 5s raw cost is not (8.0 + 0.1 post) x 5")
check(est["balance_debit_cents"] > est["raw_cost_cents"],
      "magi2 customer debit carries no markup")
owner_est = pricing.per_second_estimate(
    "magi2", "1080p", 5, 0.0, rates, owner=True)
check(owner_est["balance_debit_cents"] == 41,
      "magi2 owner debit is not the raw cost rounded up")

# -- lengths, caps, audio ------------------------------------------------

check(app.MODEL_DURATION_BOUNDS.get("magi2") == (4, 30),
      "magi2 duration bounds drifted from (4, 30)")
check(app.native_lengths("magi2") == list(range(4, 31)),
      "magi2 native lengths are not every whole second 4 to 30")
check(app.offered_lengths("magi2") == list(range(4, 31)),
      "magi2 picker lengths are not exactly 4 to 30")
check("magi2" not in app.CHAIN_CONTINUATION_MODELS,
      "magi2 gained chained lengths past its 30s cap")
try:
    app.duration_plan("magi2", 45)
    check(False, "magi2 duration_plan accepted 45 seconds")
except ValueError:
    pass
check(app.MODEL_REF_VIDEO_SECONDS_CAP.get("magi2") == 30.0,
      "magi2 reference video cap is not its documented 30s")
audio = app.LANE_AUDIO.get("magi2") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "magi2 audio record lost its native speech/audio")

# -- submit and mirror ----------------------------------------------------

headers = func_src("_api_headers")
check('"higgsfield"' in headers and 'f"Key {key}"' in headers,
      "_api_headers has no Higgsfield Key scheme branch")
submit = func_src("submit_higgsfield_job")
check(bool(submit), "submit_higgsfield_job not found")
for token in ("HIGGSFIELD_VIDEO_MODELS", '"generate_audio": True',
              '"1080p"', '"status_url"', "provider_status_url",
              "_beatapi_media_url", "_submit_failed",
              "PAYLOAD-UNVERIFIED"):
    check(token in submit, f"submit_higgsfield_job missing {token}")
mirror = func_src("mirror_higgsfield_status")
check(bool(mirror), "mirror_higgsfield_status not found")
for token in ('"completed"', '"in_progress"', '"queued"',
              '"failed"', '"nsfw"', '"canceled"',
              '_note_unparsed(job, "higgsfield"', 'get("video")',
              "_poll_done", "_poll_fail"):
    check(token in mirror, f"mirror_higgsfield_status missing {token}")
check('provider == "higgsfield"' in func_src("submit_api_job"),
      "submit_api_job does not dispatch higgsfield")
check("mirror_higgsfield_status" in func_src("mirror_api_status"),
      "mirror_api_status does not dispatch higgsfield")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_magi2_lane")
