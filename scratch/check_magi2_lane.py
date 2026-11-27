"""Local checks for the MAGI-2 lane (wired 2026-11-25 on
Higgsfield's own API; rerouted to FrameHush 2026-11-26).

Pins the lane's shape so a later edit cannot quietly break
it:

- MAGI-2 is a real picker entry under its own name, paid
  only, a per-second lane routed to FrameHush (the shared
  FrameHush client, model id magi-2-1080P), with the
  FRAMEHUSH_API_KEY env mapping and both poll provider lists
  carrying the supplier;
- the seeded rate is FrameHush's account-verified $0.06/s at
  the model's only resolution (1080p), and pricing is the
  STANDARD markup (Jack's ruling, 2026-11-25): the lane is
  NOT an at-cost exception and carries no
  HIGGSFIELD_POLICY_CENTS_PER_SECOND entry; the owner pays
  the raw FrameHush cost plus the cut post figure;
- the CUSTOMER SHELF PRICE DOES NOT MOVE in the reroute
  (Jack, 2026-11-26: "cut OUR cost, not our users' prices"):
  the shelf basis keeps the pre-reroute $0.08/s rate
  (SHELF_PROVIDER_RATES_CENTS_PER_SECOND), so the customer
  sell rate, the 1.25x minimum and the balance debits equal
  the figures the lane carried on the Higgsfield route, and
  the saving lands as margin;
- the displaced Higgsfield direct route is recorded in
  DISPLACED_RATES (magi2_higgsfield, $0.08/s) and its submit
  and mirror stay wired as the spare, key env mapping and
  endpoint spec intact;
- the lane's lengths are exactly the model's own 4 to 30
  seconds: no chained lengths past 30;
- the audio record says the model speaks natively (its
  dialogue, effects and music generate in the same pass);
- the FrameHush submit and mirror serve the lane through
  the shared client (per-lane model id), and the Higgsfield
  spare's submit and mirror still exist with their
  documented shapes.

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
check(app.MODEL_PROVIDER.get("magi2") == "framehush",
      "magi2 does not route to framehush")
check(app.provider_for("magi2", "1080p") == "framehush",
      "provider_for does not resolve magi2 to framehush")
check(app.PROVIDER_KEY_ENV.get("framehush") == "FRAMEHUSH_API_KEY",
      "framehush key env missing")
check("framehush" in app.API_POLL_PROVIDERS,
      "framehush missing from API_POLL_PROVIDERS")
check("framehush" in app.URL_RESULT_PROVIDERS,
      "framehush missing from URL_RESULT_PROVIDERS")
check('"magi2": "magi-2-1080P"' in SRC,
      "FrameHush model id for the magi2 lane drifted")
check('"seedance": "seedance-2-5"' in SRC,
      "FrameHush model id for the seedance lane drifted")

# -- the displaced Higgsfield route stays wired as the spare -----------

check(app.PROVIDER_KEY_ENV.get("higgsfield") == "HIGGSFIELD_API_KEY",
      "higgsfield key env missing (the spare lost its key mapping)")
check("higgsfield" in app.API_POLL_PROVIDERS,
      "higgsfield missing from API_POLL_PROVIDERS (spare unwired)")
check("higgsfield" in app.URL_RESULT_PROVIDERS,
      "higgsfield missing from URL_RESULT_PROVIDERS (spare unwired)")
check(app.HIGGSFIELD_BASE == "https://api.higgsfield.ai",
      "HIGGSFIELD_BASE drifted")
spec = app.HIGGSFIELD_VIDEO_MODELS.get("magi2") or {}
check(spec.get("text") == "sand/magi-2/text-to-video",
      "magi2 spare text endpoint drifted")
check(spec.get("image") == "sand/magi-2/image-to-video",
      "magi2 spare image endpoint drifted")
check(spec.get("reference") == "sand/magi-2/reference-to-video",
      "magi2 spare reference endpoint drifted")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("magi2_higgsfield")
      == {"1080p": 8.0},
      "displaced Higgsfield magi2 rate not recorded at $0.08/s")

# -- pricing: FrameHush cost, frozen customer shelf ---------------------

check(pricing.PROVIDER_RATES_CENTS_PER_SECOND.get("magi2")
      == {"1080p": 6.0}, "magi2 seeded rate drifted from $0.06/s")
check(pricing.MODEL_RESOLUTIONS.get("magi2") == ("1080p",),
      "magi2 resolutions drifted from 1080p only")
check("magi2" not in pricing.HIGGSFIELD_POLICY_CENTS_PER_SECOND,
      "magi2 gained a policy-table entry (it is not at-cost)")
check(pricing.SHELF_PROVIDER_RATES_CENTS_PER_SECOND.get("magi2")
      == {"1080p": 8.0},
      "magi2 shelf rate is not frozen at the pre-reroute $0.08/s")
check(pricing.per_second_shelf_cost_cents("magi2", "1080p") == 8.1,
      "magi2 shelf cost is not frozen shelf rate + shelf post")
check(pricing.per_second_lane_cost_cents("magi2", "1080p") == 6.01,
      "magi2 true cost is not FrameHush rate + cut post")
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
lane = pricing.frontier_lane("magi2", "1080p", rates)
check(lane["pricing_basis"] == "standard",
      "magi2 does not resolve on the standard pricing basis")
check(lane["cost_cents_per_second"] == 6.01,
      "magi2 reported cost is not the FrameHush rate + post")
check(lane["sell_cents_per_second"] == 10.125,
      "magi2 customer rate moved off the frozen (8.0 + 0.1) x 1.25")
check(lane["minimum_cents_per_second"] == 10.125,
      "magi2 customer minimum moved off the frozen shelf figure")
owner_lane = pricing.frontier_lane("magi2", "1080p", rates,
                                   owner=True)
check(owner_lane["sell_cents_per_second"] == 6.01,
      "magi2 owner rate is not the raw FrameHush cost + cut post")
est = pricing.per_second_estimate("magi2", "1080p", 5, 0.0, rates)
check(round(est["raw_cost_cents"], 2) == 30.05,
      "magi2 5s raw cost is not (6.0 + 0.01 post) x 5")
check(est["balance_debit_cents"] == 51,
      "magi2 5s customer debit moved off the frozen shelf figure")
check(est["balance_debit_cents"] > est["raw_cost_cents"],
      "magi2 customer debit carries no markup")
owner_est = pricing.per_second_estimate(
    "magi2", "1080p", 5, 0.0, rates, owner=True)
check(owner_est["balance_debit_cents"] == 31,
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
check(app.ref_caps_for("magi2") == (10, 5, 5),
      "magi2 reference caps drifted from 10 images / 5 video / 5 audio")
check(app.MODEL_REF_VIDEO_SECONDS_CAP.get("magi2") == 30.0,
      "magi2 reference video cap is not its documented 30s")
audio = app.LANE_AUDIO.get("magi2") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "magi2 audio record lost its native speech/audio")

# -- submit and mirror: the shared FrameHush client -----------------------

submit = func_src("submit_framehush_job")
check(bool(submit), "submit_framehush_job not found")
for token in ("FRAMEHUSH_VIDEO_MODELS", 'job["model"]',
              "/v1/videos", "_api_headers(\"framehush\")",
              "provider_task_id", "PAYLOAD-UNVERIFIED"):
    check(token in submit, f"submit_framehush_job missing {token}")
mirror = func_src("mirror_framehush_status")
check(bool(mirror), "mirror_framehush_status not found")
for token in ('"completed"', '"in_progress"', '"queued"',
              '"failed"', '"charged_price"', "_poll_done",
              "_poll_fail"):
    check(token in mirror, f"mirror_framehush_status missing {token}")
check('provider == "framehush"' in func_src("submit_api_job"),
      "submit_api_job does not dispatch framehush")
check("mirror_framehush_status" in func_src("mirror_api_status"),
      "mirror_api_status does not dispatch framehush")

# -- the Higgsfield spare's submit and mirror stay wired ------------------

headers = func_src("_api_headers")
check('"higgsfield"' in headers and 'f"Key {key}"' in headers,
      "_api_headers has no Higgsfield Key scheme branch")
spare_submit = func_src("submit_higgsfield_job")
check(bool(spare_submit), "submit_higgsfield_job (spare) not found")
for token in ("HIGGSFIELD_VIDEO_MODELS", '"generate_audio": True',
              '"1080p"', '"status_url"', "provider_status_url",
              "_beatapi_media_url", "_submit_failed",
              "PAYLOAD-UNVERIFIED"):
    check(token in spare_submit,
          f"submit_higgsfield_job (spare) missing {token}")
spare_mirror = func_src("mirror_higgsfield_status")
check(bool(spare_mirror), "mirror_higgsfield_status (spare) not found")
for token in ('"completed"', '"in_progress"', '"queued"',
              '"failed"', '"nsfw"', '"canceled"',
              '_note_unparsed(job, "higgsfield"', 'get("video")',
              "_poll_done", "_poll_fail"):
    check(token in spare_mirror,
          f"mirror_higgsfield_status (spare) missing {token}")
check('provider == "higgsfield"' in func_src("submit_api_job"),
      "submit_api_job lost the higgsfield spare dispatch")
check("mirror_higgsfield_status" in func_src("mirror_api_status"),
      "mirror_api_status lost the higgsfield spare dispatch")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_magi2_lane")
