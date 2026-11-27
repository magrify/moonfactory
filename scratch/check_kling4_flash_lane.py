"""Local checks for the 2026-11-27 Kling 4.0 Flash lane.

Pins the lane's shape so a later edit cannot quietly break
it:

- Kling 4.0 Flash is a real picker entry under its own
  name, paid only, a per-second lane routed to Kie at its
  one tier, with the three Kie flash slugs in the submit
  (text and reference ride the flash reference endpoint,
  image rides the flash keyframe endpoint) and the Kling
  family's sound selection on the shared Kie input;
- the seeded rate is Kie's verified flat $0.085/s at 720p
  (the reference endpoint's with-video rate bills the
  input duration too, so the no-video rate is the basis),
  the Higgsfield policy figure is the published $0.1475/s,
  and pricing rides the standard machinery: the lane sells
  at the 1.25x minimum under Higgsfield with can_undercut
  true, and the owner pays the raw Kie cost;
- durations are the model's own 3 to 20 whole seconds,
  the reference caps are the endpoint's 10 images / 5
  videos / no audio, and the audio record carries the
  Kling family's native audio.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_kling4_flash_lane.py
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
check("kling4_flash" in model_ids, "kling4_flash missing from MODELS")
entry = next(m for m in app.MODELS if m["id"] == "kling4_flash")
check(entry.get("label") == "Kling 4.0 Flash",
      "kling4_flash label is not its own name Kling 4.0 Flash")
check(entry.get("paid_only") is True,
      "kling4_flash is not paid_only (an API-cost lane is never free)")
check("kling4_flash" in app.PER_SECOND_MODELS,
      "kling4_flash missing from PER_SECOND_MODELS")
check(app.MODEL_PROVIDER.get("kling4_flash") == "kie",
      "kling4_flash does not route to kie")
check(app.provider_for("kling4_flash", "720p") == "kie",
      "provider_for does not resolve kling4_flash 720p to kie")
check(app.PROVIDER_KEY_ENV.get("kie") == "KIE_API_KEY",
      "kie key env missing")

# -- pricing: standard machinery, Higgsfield figure recorded -----------

check(pricing.PROVIDER_RATES_CENTS_PER_SECOND.get("kling4_flash")
      == {"720p": 8.5},
      "kling4_flash seeded rate drifted from Kie's $0.085/s at 720p")
check(pricing.HIGGSFIELD_POLICY_CENTS_PER_SECOND.get("kling4_flash")
      == {"720p": 14.75},
      "kling4_flash Higgsfield figure drifted from $0.1475/s")
check(pricing.MODEL_RESOLUTIONS.get("kling4_flash") == ("720p",),
      "kling4_flash resolutions drifted (720p is the only tier)")
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
lane = pricing.frontier_lane("kling4_flash", "720p", rates)
check(lane["pricing_basis"] == "standard",
      "kling4_flash does not resolve on the standard basis")
check(lane["cost_cents_per_second"] == 8.51,
      "kling4_flash cost is not the Kie rate plus the cut post figure")
check(lane["sell_cents_per_second"] == 10.75,
      "kling4_flash customer sell is not the 1.25x minimum "
      "(8.6 shelf cost x 1.25)")
check(lane["can_undercut"] is True,
      "kling4_flash no longer undercuts Higgsfield's $0.1475/s")
check(lane["higgsfield_effective_cents_per_second"] == 14.75,
      "kling4_flash Higgsfield comparison figure drifted")
check(lane["sell_cents_per_second"]
      >= lane["cost_cents_per_second"] * 1.25,
      "kling4_flash sell fell below the owner's 25 percent "
      "profit target over true cost")
owner_lane = pricing.frontier_lane("kling4_flash", "720p", rates,
                                   owner=True)
check(owner_lane["sell_cents_per_second"] == 8.51,
      "kling4_flash owner rate is not the raw Kie cost")
est = pricing.per_second_estimate("kling4_flash", "720p", 5, 0.0,
                                  rates)
check(round(est["raw_cost_cents"], 2) == 42.55,
      "kling4_flash 5s raw cost drifted")
check(est["balance_debit_cents"]
      == pricing.customer_debit_cents(10.75 * 5),
      "kling4_flash 5s debit is not the sell rate rounded up")
owner_est = pricing.per_second_estimate(
    "kling4_flash", "720p", 5, 0.0, rates, owner=True)
check(owner_est["balance_debit_cents"]
      == pricing.customer_debit_cents(est["raw_cost_cents"]),
      "kling4_flash owner debit is not the raw cost rounded up")

# -- lengths, caps, audio ------------------------------------------------

check(app.MODEL_DURATION_BOUNDS.get("kling4_flash") == (3, 20),
      "kling4_flash duration bounds drifted from (3, 20)")
check(app.native_lengths("kling4_flash") == list(range(3, 21)),
      "kling4_flash native lengths are not every whole second 3 to 20")
check(app.offered_resolutions("kling4_flash", rates) == ["720p"],
      "kling4_flash offered resolutions are not just 720p")
check(app.ref_caps_for("kling4_flash") == (10, 5, 0),
      "kling4_flash reference caps drifted from (10, 5, 0)")
audio = app.LANE_AUDIO.get("kling4_flash") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "kling4_flash audio record lost its native speech/audio")

# -- Kie submit ------------------------------------------------------------

submit = func_src("submit_kie_job")
check(bool(submit), "submit_kie_job not found")
for token in ('("kling4_flash", "text"): '
              '"kling-4.0/flash-reference-to-video"',
              '("kling4_flash", "reference"): '
              '"kling-4.0/flash-reference-to-video"',
              '("kling4_flash", "image"): '
              '"kling-4.0/flash-keyframe-to-video"'):
    check(token in submit,
          f"submit_kie_job missing {token}")
check('"kling4_flash"' in submit and 'inp["sound"] = True' in submit,
      "submit_kie_job lost the Kling family sound selection for "
      "kling4_flash")
model_opts = SRC.split("const MODEL_OPTS = {")[1].split("};")[0]
check("kling4_flash:" in model_opts
      and "resolutions: ['720p']" in model_opts.split("kling4_flash:")[1]
      .split("},")[0],
      "studio MODEL_OPTS lost the kling4_flash entry")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_kling4_flash_lane")
