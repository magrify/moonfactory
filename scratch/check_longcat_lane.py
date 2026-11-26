"""Local checks for the LongCat-Image lane (2026-11-26).

Jack's order: wire Meituan's LongCat-Image (Apache 2.0, from
the FreeAiVideo gap shortlist) as a paid image lane on the
Kaggle house worker, its own individually named entry,
notably strong at rendering text inside images. These
checks pin the shape without any network, Modal, or Kaggle
call:

- exactly one picker entry carries the LongCat-Image name,
  a clean lane (never uncensored, never free, not an edit
  lane), grouping as paid in the image picker at the $ tier;
- the price shape matches the Krea house lane (the same raw
  basis, raw x 1.25 for a customer), and the owner pays the
  Kaggle route's true raw cost, $0;
- create_image routes the lane to the Kaggle queue at
  submit instead of any provider submit, and reports the
  queued state honestly;
- the queued record takes the house image job's shape with
  the paid charge kept as charged_cents (so the house
  restore path refunds it in full) and no gallery stamp
  (a clean lane is judged like a regular generation);
- the kernel's LongCat graph is ComfyUI's official template
  graph flattened (BF16 weights, Qwen2.5 VL encoder as type
  longcat_image, Flux AE VAE, CFG Norm, Flux Guidance at 4,
  20 steps at cfg 4, euler on simple, the template's baked
  negative), and every file it names is manifest-covered
  under the longcat family.

Run:  modal-venv python scratch/check_longcat_lane.py
"""
import ast
import importlib.util
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
KERNEL_PATH = ROOT / "worker-kaggle" / "comfy_worker_kernel.py"
KERNEL = KERNEL_PATH.read_text()


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# --- one picker entry, clean and paid ------------------------------
labels = [lane_id for lane_id, lane in pricing.IMAGE_LANES.items()
          if lane.get("label", "").split(" · ")[0] == "LongCat-Image"]
check(labels == ["longcat"],
      "exactly one image lane is named LongCat-Image")
LC = pricing.IMAGE_LANES["longcat"]
check(LC.get("provider") == "kaggle-comfy",
      "the LongCat lane's route is the house Kaggle worker")
check(not LC.get("uncensored") and not LC.get("free")
      and not LC.get("edit"),
      "the LongCat lane is a clean paid text to image lane")
check(app.image_picker_class("longcat") == "paid",
      "the LongCat entry groups as paid in the image picker")
check(app.image_lane_name("longcat") == "LongCat-Image",
      "the picker serves the plain name LongCat-Image")
check(set(LC["raw_cents"]) == {"1024x1024", "832x1216", "1216x832"}
      and all(v == 1.9 for v in LC["raw_cents"].values()),
      "the LongCat lane sells three sizes at one flat raw")

# --- the price shape ------------------------------------------------
check(LC["raw_cents"]["1024x1024"]
      == pricing.IMAGE_LANES["krea2_verite"]["raw_cents"]["1024x1024"],
      "the LongCat raw basis matches the Krea house lane")
raw, debit = pricing.image_lane_price("longcat", "1024x1024")
check(raw == 1.9 and abs(debit - raw * pricing.FRONTIER_MIN_MARKUP) < 1e-9,
      "a customer pays raw x the standard frontier markup per image")
raw_o, debit_o = pricing.image_lane_price(
    "longcat", "1024x1024", owner=True)
check(raw_o == 0.0 and debit_o == 0.0,
      "the owner pays the Kaggle route's true raw cost, $0")
check(pricing.image_lane_charge_cents("longcat", "1024x1024") == 3,
      "the customer charge is the shared rounded debit (3c)")
check(app.image_lane_tier("longcat") == 1,
      "the LongCat lane bands at the $ tier like its label says")

# --- the create-flow route -------------------------------------------
create_src = func_src("create_image")
check('lane_id == "longcat"' in create_src
      and "_queue_longcat_on_kaggle(job)" in create_src,
      "create_image queues a LongCat job on the Kaggle worker")
check(create_src.index("_queue_longcat_on_kaggle(job)")
      < create_src.index("count_generation(user, free_job=served_free)"),
      "the LongCat queueing happens before the shared generation count")
check('or lane_id == "longcat"' in create_src,
      "the create answer reports the queued state honestly")

# --- the queued record's shape ----------------------------------------
queue_src = func_src("_queue_longcat_on_kaggle")
for needle in ('job["provider"] = "kaggle-comfy"',
               'job["route"] = "kaggle"',
               'job["status"] = "queued"',
               'job["charged_cents"] = int(job.get("image_charge_cents")',
               'job["cost_cents"] = 0.0',
               'job["included_cost_cents"] = 0',
               'job["free_tier"] = False',
               "_comfy_maybe_trigger()"):
    check(needle in queue_src,
          f"the Kaggle queueing stamps {needle}")
check("gallery_public" not in queue_src,
      "a clean lane takes no gallery stamp at queue time")

# --- the kernel graph ---------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "comfy_kernel", KERNEL_PATH)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)

check('job.get("image_model") == "longcat"' in KERNEL
      and 'JOB_FAMILIES["longcat"]' in KERNEL,
      "the kernel routes LongCat image jobs to the longcat family")
g = kernel.build_longcat_image_workflow(
    "a shop sign that says OPEN LATE", 424242, 832, 1216,
    prefix="pfx")
by_class = {}
for node in g.values():
    by_class.setdefault(node["class_type"], []).append(node)
check(by_class["UNETLoader"][0]["inputs"]["unet_name"]
      == "longcat_image_bf16.safetensors",
      "the LongCat graph loads the BF16 weights")
check(by_class["CLIPLoader"][0]["inputs"]["clip_name"]
      == "qwen_2.5_vl_7b_fp8_scaled.safetensors"
      and by_class["CLIPLoader"][0]["inputs"]["type"]
      == "longcat_image",
      "the LongCat graph loads the Qwen2.5 VL encoder as "
      "type longcat_image")
check(by_class["VAELoader"][0]["inputs"]["vae_name"]
      == "ae.safetensors",
      "the LongCat graph loads the Flux AE VAE")
check(by_class["CFGNorm"][0]["inputs"]["strength"] == 1,
      "the LongCat graph runs CFG Norm at 1")
guidance = sorted(n["inputs"]["guidance"]
                  for n in by_class["FluxGuidance"])
check(guidance == [4, 4],
      "Flux Guidance sits at 4 on both conditionings")
ksampler = by_class["KSampler"][0]["inputs"]
check(ksampler["steps"] == 20 and ksampler["cfg"] == 4
      and ksampler["sampler_name"] == "euler"
      and ksampler["scheduler"] == "simple"
      and ksampler["seed"] == 424242,
      "the LongCat graph samples 20 steps at cfg 4, euler on simple")
check(by_class["EmptySD3LatentImage"][0]["inputs"]["width"] == 832
      and by_class["EmptySD3LatentImage"][0]["inputs"]["height"]
      == 1216,
      "the LongCat graph takes the sold size into the latent")
texts = {n["inputs"].get("text") for n in g.values()
         if n.get("class_type") == "CLIPTextEncode"}
check("a shop sign that says OPEN LATE" in texts,
      "the LongCat graph takes the caller's prompt")
check(kernel.LONGCAT_NEGATIVE_PROMPT in texts,
      "the LongCat negative is the template's baked negative")
named = {v for node in g.values() for v in node.get("inputs", {}).values()
         if isinstance(v, str) and v.endswith(".safetensors")}
by_dest = {e["dest"].split("/")[-1]: e for e in kernel.MODEL_MANIFEST}
for name in sorted(named):
    entry = by_dest.get(name)
    check(entry is not None and entry["family"] == "longcat"
          and entry["size"] > 0 and len(entry["sha256"]) == 64,
          f"the longcat family covers {name} with size and sha256")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_longcat_lane")
