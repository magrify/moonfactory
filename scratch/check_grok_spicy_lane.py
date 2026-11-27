"""Local checks for the 2026-11-27 Grok Imagine spicy lane.

Pins the lane's shape so a later edit cannot quietly break
it:

- Grok Imagine spicy is a real picker entry under its base
  model's own name, paid only, flagged uncensored (so the
  18+ gate, the gallery rules and the Uncensored Models
  group all read it), a per-second lane routed to Kie at
  Kie's published Grok rates for the two tiers its
  text-to-video schema documents (480p / 720p; its 1080p
  is an image-to-video tier and is not sold);
- the Kie submit carries mode=spicy on the shared
  grok-imagine/text-to-video slug, with the schema's
  string duration and the 720p tier sent explicitly;
- the lane is prompt-only by design: Kie's own docs state
  spicy is not supported with external image inputs and
  automatically switches to normal there, so validation
  refuses image inputs (TEXT_ONLY_MODELS), the reference
  caps are zero, the clone adaptation resolves to text,
  and the lane offers no chained lengths (a continuation
  segment would need a start image);
- pricing rides the standard uncensored machinery: no
  Higgsfield figure exists for an uncensored lane, the
  customer sells at the 1.25x minimum over the shelf cost,
  and the owner pays the raw Kie cost.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_grok_spicy_lane.py
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


# -- picker entry, flag and routing --------------------------------------

model_ids = {m["id"] for m in app.MODELS}
check("grok_unc" in model_ids, "grok_unc missing from MODELS")
entry = next(m for m in app.MODELS if m["id"] == "grok_unc")
check(entry.get("label") == "Grok Imagine Video",
      "grok_unc label is not its base model's name "
      "Grok Imagine Video (uncensored lanes carry the plain "
      "model name; the group heading differentiates)")
check(entry.get("paid_only") is True,
      "grok_unc is not paid_only (an API-cost lane is never free)")
check("grok_unc" in app.UNCENSORED_VIDEO_MODELS,
      "grok_unc missing from UNCENSORED_VIDEO_MODELS (the 18+ "
      "gate, gallery rules and picker group all read that flag)")
check(app.model_picker_class("grok_unc") == "uncensored",
      "grok_unc does not group with the Uncensored Models")
check(app.model_display_name(entry) == "Grok Imagine Video · Uncensored",
      "grok_unc display name lost its Uncensored suffix")
check(app.job_is_uncensored({"model": "grok_unc"}) is True,
      "a grok_unc job is not judged uncensored")
check("grok_unc" in app.PER_SECOND_MODELS,
      "grok_unc missing from PER_SECOND_MODELS")
check(app.MODEL_PROVIDER.get("grok_unc") == "kie",
      "grok_unc does not route to kie")
check(app.provider_for("grok_unc", "720p") == "kie",
      "provider_for does not resolve grok_unc 720p to kie")
check(app.PROVIDER_KEY_ENV.get("kie") == "KIE_API_KEY",
      "kie key env missing")

# -- pricing: standard uncensored machinery, no Higgsfield figure --------

check(pricing.PROVIDER_RATES_CENTS_PER_SECOND.get("grok_unc")
      == {"480p": 1.2, "720p": 2.25},
      "grok_unc seeded rates drifted from Kie's published "
      "$0.012/s at 480p and $0.0225/s at 720p")
check("grok_unc" not in pricing.HIGGSFIELD_POLICY_CENTS_PER_SECOND,
      "grok_unc gained a Higgsfield figure (Higgsfield has no "
      "uncensored shelf; the lane sells at the hard minimum)")
check(pricing.MODEL_RESOLUTIONS.get("grok_unc") == ("480p", "720p"),
      "grok_unc resolutions drifted (Kie's text-to-video "
      "schema tops out at 720p; 1080p is not sold)")
rates = pricing.PROVIDER_RATES_CENTS_PER_SECOND
for res, cost, sell in (("480p", 1.21, 1.625), ("720p", 2.26, 2.938)):
    lane = pricing.frontier_lane("grok_unc", res, rates)
    check(lane["pricing_basis"] == "standard",
          f"grok_unc {res} does not resolve on the standard basis")
    check(lane["cost_cents_per_second"] == cost,
          f"grok_unc {res} cost is not the Kie rate plus the "
          "cut post figure")
    check(lane["sell_cents_per_second"] == sell,
          f"grok_unc {res} customer sell is not the 1.25x "
          "minimum over the shelf cost")
    check(lane["can_undercut"] is None
          and lane["higgsfield_effective_cents_per_second"] is None,
          f"grok_unc {res} carries a Higgsfield comparison "
          "(none exists for an uncensored lane)")
    check(lane["sell_cents_per_second"]
          >= lane["cost_cents_per_second"] * 1.25,
          f"grok_unc {res} sell fell below the owner's 25 "
          "percent profit target over true cost")
    owner_lane = pricing.frontier_lane("grok_unc", res, rates,
                                       owner=True)
    check(owner_lane["sell_cents_per_second"] == cost,
          f"grok_unc {res} owner rate is not the raw Kie cost")
est = pricing.per_second_estimate("grok_unc", "720p", 10, 0.0, rates)
check(round(est["raw_cost_cents"], 2) == 22.6,
      "grok_unc 10s 720p raw cost drifted")
check(est["balance_debit_cents"]
      == pricing.customer_debit_cents(2.938 * 10),
      "grok_unc 10s debit is not the sell rate rounded up")
owner_est = pricing.per_second_estimate(
    "grok_unc", "720p", 10, 0.0, rates, owner=True)
check(owner_est["balance_debit_cents"]
      == pricing.customer_debit_cents(est["raw_cost_cents"]),
      "grok_unc owner debit is not the raw cost rounded up")

# -- lengths, caps, audio: prompt-only by design ---------------------------

check(app.MODEL_DURATION_BOUNDS.get("grok_unc") == (6, 15),
      "grok_unc duration bounds drifted from (6, 15)")
check(app.MODEL_DURATION_STEPS.get("grok_unc") == (6, 10, 15),
      "grok_unc duration steps drifted from Kie's published "
      "6 / 10 / 15")
check(app.native_lengths("grok_unc") == [6, 10, 15],
      "grok_unc native lengths are not exactly its steps")
check(app.offered_lengths("grok_unc") == [6, 10, 15],
      "grok_unc offers lengths past its native steps (a "
      "continuation segment needs a start image, which the "
      "lane refuses, so no chained lengths may be offered)")
check("grok_unc" not in app.CHAIN_CONTINUATION_MODELS,
      "grok_unc joined the chain continuation set")
check(app.offered_resolutions("grok_unc", rates) == ["480p", "720p"],
      "grok_unc offered resolutions are not 480p and 720p")
check("grok_unc" in app.TEXT_ONLY_MODELS,
      "grok_unc missing from TEXT_ONLY_MODELS (an image input "
      "would silently drop spicy back to normal on Kie)")
check(app.ref_caps_for("grok_unc") == (0, 0, 0),
      "grok_unc reference caps drifted from (0, 0, 0)")
check(app.editclone_clone_adaptation("grok_unc") == "text",
      "grok_unc clone adaptation is not text (the prompt "
      "alone carries a clone on a prompt-only lane)")
audio = app.LANE_AUDIO.get("grok_unc") or {}
check(audio.get("native_speech") is True
      and audio.get("native_audio") is True,
      "grok_unc audio record lost its native speech/audio "
      "(Kie generates the audio track with the video)")

# -- validation refusal and Kie submit --------------------------------------

params_src = func_src("_api_job_params")
check(bool(params_src), "_api_job_params not found")
check("model in TEXT_ONLY_MODELS" in params_src
      and '"this model takes a prompt only"' in params_src,
      "_api_job_params lost the prompt-only refusal")
submit = func_src("submit_kie_job")
check(bool(submit), "submit_kie_job not found")
check('("grok_unc", "text"): "grok-imagine/text-to-video"' in submit,
      "submit_kie_job missing the grok_unc text slug")
check('inp["mode"] = "spicy"' in submit,
      "submit_kie_job lost the spicy mode selection")
check('inp["duration"] = str(params["duration"])' in submit,
      "submit_kie_job lost the schema's string duration "
      "for grok_unc")
check('("grok_unc", "image")' not in submit
      and '("grok_unc", "reference")' not in submit,
      "submit_kie_job gained an image or reference slug for "
      "grok_unc (spicy does not survive image inputs)")
model_opts = SRC.split("const MODEL_OPTS = {")[1].split("};")[0]
check("grok_unc:" in model_opts
      and "resolutions: ['480p', '720p']"
      in model_opts.split("grok_unc:")[1].split("},")[0],
      "studio MODEL_OPTS lost the grok_unc entry")

if failures:
    print(f"FAIL ({len(failures)})")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS check_grok_spicy_lane")
