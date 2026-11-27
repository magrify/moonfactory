"""Local checks for the 2026-11-26 Seedance 2.5 reroute to
FrameHush.

Pins the lane's new shape so a later edit cannot quietly
break it:

- Seedance 2.5 stays a real picker entry under its own name,
  paid only, a per-second lane now routed to FrameHush at
  every resolution, with the FRAMEHUSH_API_KEY env mapping
  and both poll provider lists carrying the new supplier;
  the old per-resolution CometAPI override is gone;
- the seeded rates are FrameHush's verified account rates
  ($0.035 / $0.047 / $0.08 per generated second, plus the
  4K tier at $0.09 wired 2026-11-27 once Higgsfield's own
  published 4K credit count, 140 credits per 5 s clip, was
  on record to price it against), and the displaced
  CometAPI and BeatAPI rates are recorded in
  DISPLACED_RATES;
- pricing rides the standard machinery: the customer sell
  rates stay on the 0.97x Higgsfield targets (the cheaper
  route widens the margin, it does not move the shelf
  price), the 1.25x minimums sit below them, and the owner
  pays the raw FrameHush cost;
- the rate refresh no longer reads BeatAPI's Seedance page
  (the lane's rate is seeded-only, like Wan 3.0 Prime's);
- the submit and mirror functions exist, post the
  documented /v1/videos body for model seedance-2-5 with
  Bearer auth, branch on FrameHush's documented status
  words, resolve the result URL from the task's artifacts
  list (the live-verified envelope: the completed poll
  answer carries no URL and no price; finished files live
  server-side for only 24 hours), and settle from the
  completed answer's charged_price when one is reported.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_framehush_seedance_lane.py
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
check("seedance" in model_ids, "seedance missing from MODELS")
entry = next(m for m in app.MODELS if m["id"] == "seedance")
check(entry.get("label") == "Seedance 2.5",
      "seedance label is not its own name Seedance 2.5")
check(entry.get("paid_only") is True,
      "seedance is not paid_only (an API-cost lane is never free)")
check("seedance" in app.PER_SECOND_MODELS,
      "seedance missing from PER_SECOND_MODELS")
check(app.MODEL_PROVIDER.get("seedance") == "framehush",
      "seedance does not route to framehush")
for res in ("480p", "720p", "1080p", "4K"):
    check(app.provider_for("seedance", res) == "framehush",
          f"provider_for does not resolve seedance {res} to framehush")
check("seedance" not in app.MODEL_PROVIDER_OVERRIDES,
      "seedance kept a per-resolution override after the reroute")
check(app.PROVIDER_KEY_ENV.get("framehush") == "FRAMEHUSH_API_KEY",
      "framehush key env missing")
check("framehush" in app.API_POLL_PROVIDERS,
      "framehush missing from API_POLL_PROVIDERS")
check("framehush" in app.URL_RESULT_PROVIDERS,
      "framehush missing from URL_RESULT_PROVIDERS")
check("https://www.framehush.com" in SRC,
      "FRAMEHUSH_BASE drifted from https://www.framehush.com")

# -- pricing: standard machinery, displaced routes recorded -------------

check(pricing.PROVIDER_RATES_CENTS_PER_SECOND.get("seedance")
      == {"480p": 3.5, "720p": 4.7, "1080p": 8.0, "4K": 9.0},
      "seedance seeded rates drifted from FrameHush's "
      "$0.035/$0.047/$0.08/$0.09")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("seedance_cometapi")
      == {"480p": 8.24, "720p": 18.48},
      "displaced CometAPI seedance rates not recorded")
check(pricing.DISPLACED_RATES_CENTS_PER_SECOND.get("seedance_beatapi")
      == {"1080p": 48.1},
      "displaced BeatAPI seedance 1080p rate not recorded")
check(pricing.MODEL_RESOLUTIONS.get("seedance")
      == ("480p", "720p", "1080p", "4K"),
      "seedance resolutions drifted (the 4K tier wired "
      "2026-11-27 must stay listed)")
check(app.offered_resolutions(
    "seedance", pricing.PROVIDER_RATES_CENTS_PER_SECOND)
    == ["480p", "720p", "1080p", "4K"],
    "the picker does not offer seedance at 4K")
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
expected = {
    # res: (provider $/s, owner c/s, customer sell c/s, higgsfield c/s)
    "480p": (3.5, 3.51, 14.308, 14.75),
    "720p": (4.7, 4.71, 33.368, 34.4),
    "1080p": (8.0, 8.01, 76.307, 78.666),
    "4K": (9.0, 9.01, 133.537, 137.666),
}
for res, (provider, owner_sell, sell, higgs) in expected.items():
    lane = pricing.frontier_lane("seedance", res, rates)
    check(lane["pricing_basis"] == "standard",
          f"seedance {res} does not resolve on the standard basis")
    check(lane["cost_cents_per_second"] == owner_sell,
          f"seedance {res} cost is not FrameHush rate + post")
    check(lane["sell_cents_per_second"] == sell,
          f"seedance {res} customer sell moved off the Higgsfield target")
    check(lane["higgsfield_effective_cents_per_second"] == higgs,
          f"seedance {res} Higgsfield comparison figure drifted")
    check(lane["minimum_cents_per_second"]
          == round(provider * 1.25 + 0.125, 3)
          or lane["minimum_cents_per_second"] < sell,
          f"seedance {res} 1.25x minimum is not below the sell rate")
    owner_lane = pricing.frontier_lane("seedance", res, rates,
                                       owner=True)
    check(owner_lane["sell_cents_per_second"] == owner_sell,
          f"seedance {res} owner rate is not the raw FrameHush cost")
    est = pricing.per_second_estimate("seedance", res, 5, 0.0, rates)
    check(round(est["raw_cost_cents"], 2) == round(owner_sell * 5, 2),
          f"seedance {res} 5s raw cost drifted")
    check(est["balance_debit_cents"] > est["raw_cost_cents"],
          f"seedance {res} customer debit carries no markup")
    owner_est = pricing.per_second_estimate(
        "seedance", res, 5, 0.0, rates, owner=True)
    check(owner_est["balance_debit_cents"]
          == pricing.customer_debit_cents(est["raw_cost_cents"]),
          f"seedance {res} owner debit is not the raw cost rounded up")

# -- lengths, caps, audio ------------------------------------------------

check(app.MODEL_DURATION_BOUNDS.get("seedance") == (4, 30),
      "seedance duration bounds drifted from (4, 30)")
check(app.native_lengths("seedance") == list(range(4, 31)),
      "seedance native lengths are not every whole second 4 to 30")
check(app.ref_caps_for("seedance") == (30, 10, 10),
      "seedance reference caps drifted from FrameHush's 30/10/10")
audio = app.LANE_AUDIO.get("seedance") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "seedance audio record lost its native speech/audio")

# -- rate refresh: BeatAPI's Seedance page is no longer read --------------

refresh = func_src("refresh_provider_rates")
check(bool(refresh), "refresh_provider_rates not found")
check("beatapi.io/seedance" not in refresh,
      "rate refresh still reads BeatAPI's Seedance page")
check("required = set()" in refresh,
      "rate refresh required set not emptied by the audit moves")

# -- submit and mirror ----------------------------------------------------

submit = func_src("submit_framehush_job")
check(bool(submit), "submit_framehush_job not found")
check('"seedance": "seedance-2-5"' in SRC,
      "FrameHush model id for the seedance lane drifted")
for token in ("FRAMEHUSH_VIDEO_MODELS", "/v1/videos", '"first_frame"',
              '"last_frame"', '"image_refs"', '"video_refs"',
              '"audio_refs"', "_framehush_media_url",
              "_api_headers(\"framehush\")", "_submit_failed",
              "provider_task_id", "PAYLOAD-UNVERIFIED"):
    check(token in submit, f"submit_framehush_job missing {token}")
mirror = func_src("mirror_framehush_status")
check(bool(mirror), "mirror_framehush_status not found")
for token in ('"completed"', '"in_progress"', '"queued"',
              '"failed"', '"charged_price"', "cost_cents=cost",
              "_framehush_result_url", "_poll_done", "_poll_fail",
              '_note_unparsed(job, "framehush"'):
    check(token in mirror, f"mirror_framehush_status missing {token}")
resolver = func_src("_framehush_result_url")
check(bool(resolver), "_framehush_result_url not found")
check("artifacts" in resolver and "content_url" in resolver
      and "signed-url" not in resolver,
      "result resolver lost the artifacts-list content_url read "
      "(or regained the invented signed-url endpoint)")
check('provider == "framehush"' in func_src("submit_api_job"),
      "submit_api_job does not dispatch framehush")
check("mirror_framehush_status" in func_src("mirror_api_status"),
      "mirror_api_status does not dispatch framehush")
settle = func_src("settle_fal_job")
check('job.get("provider_cost_cents")' in settle,
      "settlement no longer honors a provider-reported charge")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_framehush_seedance_lane")
