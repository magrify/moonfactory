"""Local checks for the 2026-11-18 house Uncut lane.

Jack's order: wire the community Wan 2.2 NSFW LoRA
(lopi999/Wan2.2-I2V_General-NSFW-LoRA) into the house worker as
its own lane. The H file drives the high-noise expert, the L
file the low-noise expert, the worker prepends the trigger
token for this lane only, and the platform prices the lane
exactly like the house Production lane.

No Modal, no network, no generation: reads the worker and
platform sources, extracts literals by AST, and replays the
workflow patch on the real stock template JSON locally (the
same node surgery _build_lane_workflow performs), so the
sampler settings are pinned to the authored stock values.

Run:  modal-venv python scratch/check_nsfw_lane.py
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKER = (ROOT / "modal-worker" / "app.py").read_text()
PLATFORM = (ROOT / "platform" / "app.py").read_text()
PRICING = (ROOT / "platform" / "pricing.py").read_text()
WTREE = ast.parse(WORKER)
PTREE = ast.parse(PLATFORM)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def literal(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at module level")


def dict_literal(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return {
                ast.literal_eval(k): ast.literal_eval(v)
                for k, v in zip(node.value.keys, node.value.values)
            }
    raise AssertionError(f"{name} not found at module level")


# --- The asset: repo, files, sizes, hashes ---------------------
NSFW_LORA_FILES = literal(WTREE, "NSFW_LORA_FILES")
check(literal(WTREE, "NSFW_LORA_REPO")
      == "lopi999/Wan2.2-I2V_General-NSFW-LoRA",
      "NSFW LoRA repo id drifted")
EXPECTED_FILES = [
    ("NSFW-22-H-e8.safetensors", "loras/NSFW-22-H-e8.safetensors",
     613516752,
     "34e2144d3cd65360f97d09ccbe03e1c39a096df6c9234af5fe3899d1b63cda39"),
    ("NSFW-22-L-e8.safetensors", "loras/NSFW-22-L-e8.safetensors",
     613516752,
     "d6b783742f4d5fd63a0223ae1d5bf64fc995a6b408480ac2a00528ae0d4146db"),
]
check(NSFW_LORA_FILES == EXPECTED_FILES,
      "NSFW_LORA_FILES does not match the verified repo listing")
check(literal(WTREE, "NSFW_LORA_HIGH") == "NSFW-22-H-e8.safetensors",
      "high-noise LoRA name drifted")
check(literal(WTREE, "NSFW_LORA_LOW") == "NSFW-22-L-e8.safetensors",
      "low-noise LoRA name drifted")
check(literal(WTREE, "NSFW_TRIGGER") == "nsfwsks",
      "trigger token drifted")
check(literal(WTREE, "NSFW_LANE") == "nsfw", "worker lane id drifted")
check("def download_nsfw_lora" in WORKER
      and '"download-nsfw"' in WORKER,
      "staging function or entrypoint mode missing")
check("NSFW LoRA byte verification failed" in WORKER,
      "staging no longer refuses a file that fails verification")

# --- The worker lane: route, template, dims --------------------
LANE_WORKFLOWS = dict_literal(WTREE, "LANE_WORKFLOWS")
check(LANE_WORKFLOWS.get("nsfw") == "prod_i2v_a14b.json",
      "nsfw lane does not run the stock A14B template")
check(LANE_WORKFLOWS.get("production") == "prod_i2v_a14b_distilled.json",
      "production lane template changed")
LANE_DIMS = dict_literal(WTREE, "LANE_DIMS")
check(LANE_DIMS.get("nsfw") == LANE_DIMS.get("production"),
      "nsfw lane dims do not match the production house dims")

# --- The workflow patch, replayed on the real template ---------
# Mirror the surgery _build_lane_workflow performs for this lane
# and pin its source shape, then verify the graph that results.
check('prompt = f"{NSFW_TRIGGER}, {prompt}" if prompt else NSFW_TRIGGER'
      in WORKER, "trigger prepend changed shape")
check(WORKER.count("NSFW_TRIGGER") >= 3
      and 'if lane == NSFW_LANE:' in WORKER,
      "trigger handling is not scoped to the nsfw lane")
check('detail="the nsfw lane needs a start image; it is an image-to-video lane"'
      in WORKER, "worker endpoint no longer refuses a text only nsfw job")

wf = json.loads((ROOT / "workflows" / "prod_i2v_a14b.json").read_text())
check("17" not in wf and "18" not in wf,
      "stock template node ids 17/18 are no longer free")
wf["17"] = {
    "class_type": "LoraLoaderModelOnly",
    "inputs": {"lora_name": "NSFW-22-H-e8.safetensors",
               "model": ["1", 0], "strength_model": 1.0},
}
wf["18"] = {
    "class_type": "LoraLoaderModelOnly",
    "inputs": {"lora_name": "NSFW-22-L-e8.safetensors",
               "model": ["2", 0], "strength_model": 1.0},
}
for node in wf.values():
    if node.get("class_type") == "ModelSamplingSD3":
        src = node.get("inputs", {}).get("model")
        if src == ["1", 0]:
            node["inputs"]["model"] = ["17", 0]
        elif src == ["2", 0]:
            node["inputs"]["model"] = ["18", 0]
sampling = [n for n in wf.values()
            if n.get("class_type") == "ModelSamplingSD3"]
check(sorted(n["inputs"]["model"] for n in sampling)
      == [["17", 0], ["18", 0]],
      "ModelSamplingSD3 nodes are not both rewired through the LoRA pair")
check(wf["17"]["inputs"]["lora_name"] == "NSFW-22-H-e8.safetensors"
      and wf["17"]["inputs"]["model"] == ["1", 0],
      "high-noise expert does not carry the H file")
check(wf["18"]["inputs"]["lora_name"] == "NSFW-22-L-e8.safetensors"
      and wf["18"]["inputs"]["model"] == ["2", 0],
      "low-noise expert does not carry the L file")
check(wf["17"]["inputs"]["strength_model"] == 1.0
      and wf["18"]["inputs"]["strength_model"] == 1.0,
      "LoRA strength is not 1.0 on both experts")
# The standard sampler path: the stock template's authored
# sampler values, untouched by the patch.
samplers = [n for n in wf.values()
            if n.get("class_type") == "KSamplerAdvanced"]
check(len(samplers) == 2
      and all(s["inputs"]["steps"] == 20 for s in samplers)
      and all(s["inputs"]["cfg"] == 3.5 for s in samplers),
      "stock sampler path changed (expected 20 steps at cfg 3.5)")

# --- The platform shelf entry ----------------------------------
MODELS = literal(PTREE, "MODELS")
by_id = {m["id"]: m for m in MODELS}
check("wan22_uncut" in by_id, "wan22_uncut missing from MODELS")
check(by_id["wan22_uncut"].get("label") == "Wan 2.2 Uncut",
      "wan22_uncut label drifted")
check(by_id["wan22_uncut"].get("paid_only") is True,
      "wan22_uncut is not paid only")
check("wan22_uncut" not in literal(PTREE, "PER_SECOND_MODELS"),
      "wan22_uncut must not be a per-second lane")
UNC = literal(PTREE, "UNCENSORED_VIDEO_MODELS")
check("wan22_uncut" in UNC, "wan22_uncut not in the uncensored set")

# Display name, computed the way /api/config computes it.
display = (by_id["wan22_uncut"]["label"]
           + (" · Uncensored" if "wan22_uncut" in UNC else ""))
check(display == "Wan 2.2 Uncut · Uncensored",
      "display name is not Wan 2.2 Uncut · Uncensored")
for other_id, other in by_id.items():
    if other_id == "wan22_uncut":
        continue
    other_display = (other["label"]
                     + (" · Uncensored" if other_id in UNC else ""))
    check(other_display != display,
          f"display name collides with {other_id}")

# House routing map and availability treatment.
check(dict_literal(PTREE, "HOUSE_MODEL_PRICING_LANE")
      == {"wan2.2": "production", "wan22_uncut": "nsfw",
          "ltx25_unc": "ltx_unc", "hunyuan15": "hunyuan15"},
      "house model pricing lane map drifted (hunyuan15 added "
      "2026-11-26 on its own pricing lane)")
check("True if m[\"id\"] in HOUSE_MODEL_PRICING_LANE" in PLATFORM,
      "config availability no longer treats house models as available")
check('pricing.JOB_PRICE_CENTS[\n                HOUSE_MODEL_PRICING_LANE[model_id]] / 5.0'
      in PLATFORM or "HOUSE_MODEL_PRICING_LANE[model_id]] / 5.0" in PLATFORM,
      "menu rate basis no longer reads the house pricing lane")

# create_job routing: the model forces the worker lane, with the
# clean refusals for the shapes this lane cannot take.
check('if model == "wan22_uncut":' in PLATFORM
      and 'lane = "nsfw"' in PLATFORM,
      "create_job does not route wan22_uncut to the nsfw worker lane")
check('detail="This lane needs a start image. Upload the photo the video starts from"'
      in PLATFORM, "start image refusal wording changed")
check('detail="Effects run on Wan 2.2. Pick that model for an effect video"'
      in PLATFORM, "effect combination refusal missing")
check('detail="Fun Camera presets run on Wan 2.2. Pick that model for a camera move"'
      in PLATFORM, "fun camera combination refusal missing")
check('"model": model,' in PLATFORM,
      "house job record no longer stores the real model")
check("lip_sync_applies(model, speech_script)" in PLATFORM,
      "lip sync planning no longer reads the job model")

# Studio treatment: the house form, the hidden lane choice, and
# the house price line reading this lane's price.
check("const HOUSE_MODEL_IDS = ['wan2.2', 'wan22_uncut', 'ltx25_unc', 'hunyuan15'];"
      in PLATFORM,
      "studio house model list drifted (hunyuan15 added 2026-11-26)")
check("const api = !isHouseModel(m);" in PLATFORM,
      "updateModelUI no longer treats the uncut model as a house model")
check("jp.nsfw" in PLATFORM,
      "house price line does not read the nsfw job price")
check("const jobBody = isHouseModel(model)" in PLATFORM,
      "create payload no longer sends the house body for the uncut model")

# --- Pricing parity with the house lane -------------------------
sys.path.insert(0, str(ROOT / "platform"))
import pricing  # noqa: E402

check(pricing.MEASURED_COST_CENTS["nsfw"]
      == pricing.MEASURED_COST_CENTS["production"],
      "nsfw measured cost does not mirror production")
check(pricing.JOB_PRICE_CENTS["nsfw"]
      == pricing.JOB_PRICE_CENTS["production"] == 99,
      "nsfw job price does not mirror production")
check(pricing.job_price_cents("nsfw")
      == pricing.job_price_cents("production"),
      "customer price differs from the house lane")
check(pricing.job_price_cents("nsfw", owner=True)
      == pricing.job_price_cents("production", owner=True) == 15,
      "owner price is not raw cost, same as the house lane")
check(pricing.lane_paused("nsfw") == pricing.lane_paused("production"),
      "nsfw lane pause state differs from the house lane")
check('"nsfw": 15,' in PRICING, "pricing entry missing from the table")

if failures:
    print("NSFW LANE CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL NSFW LANE CHECKS PASSED")
