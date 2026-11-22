"""Local checks for the Krea 2 Verite image lane (2026-11-21).

Jack's order: wire the Verite realism LoRA (f23gg/Krea-2-NSFW-V5)
into Moonfactory as a paid image lane on the Kaggle house
worker, the graph the standalone Kaggle test proved the same
day. These checks pin the shape without any network, Modal,
or Kaggle call:

- exactly one picker entry carries the Krea 2 Verite name,
  stamped uncensored, never free, grouping as uncensored in
  the image picker at the $ tier;
- the price shape matches the comparable uncensored lanes
  (the Z-Image Pro raw as the basis, raw x 1.25 for a
  customer), and the owner pays the Kaggle route's true raw
  cost, $0;
- create_image routes the lane to the Kaggle queue at
  submit instead of any provider submit, and reports the
  queued state honestly;
- the queued record takes the house image job's shape with
  the paid charge kept as charged_cents (so the house
  restore path refunds it in full), the trigger words
  stamped in front of the user's words, the user's words
  kept as user_prompt, and the public gallery stamp off;
- the kernel's Krea graph is the proven test graph (int8
  convrot Raw build, Verite LoRA at 0.8, Qwen3VL encoder as
  type krea2, Qwen image VAE, 30 steps at cfg 4.5, euler on
  simple, the sold size in the latent), and every file it
  names is manifest-covered under the krea2 family.

Run:  modal-venv python scratch/check_krea2_verite.py
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


# --- one picker entry, uncensored and paid -----------------------
labels = [lane_id for lane_id, lane in pricing.IMAGE_LANES.items()
          if lane.get("label", "").split(" · ")[0] == "Krea 2 Verite"]
check(labels == ["krea2_verite"],
      "exactly one image lane is named Krea 2 Verite")
KREA = pricing.IMAGE_LANES["krea2_verite"]
check(KREA.get("provider") == "kaggle-comfy",
      "the Krea lane's route is the house Kaggle worker")
check(KREA.get("uncensored") is True and not KREA.get("free")
      and not KREA.get("edit"),
      "the Krea lane is uncensored, paid only, and not an edit lane")
check(app.image_picker_class("krea2_verite") == "uncensored",
      "the Krea entry groups as uncensored in the image picker")
check(app.image_lane_name("krea2_verite") == "Krea 2 Verite",
      "the picker serves the plain name Krea 2 Verite")
check(set(KREA["raw_cents"]) == {"832x1216", "1024x1024", "1216x832"}
      and all(v == 1.9 for v in KREA["raw_cents"].values()),
      "the Krea lane sells the tested frame and two more at one flat raw")

# --- the price shape ----------------------------------------------
pro_raw = pricing.IMAGE_LANES["zimage_pro_unc"]["raw_cents"]["1024x1024"]
check(KREA["raw_cents"]["832x1216"] == pro_raw,
      "the Krea raw basis matches the Z-Image Pro uncensored lane")
raw, debit = pricing.image_lane_price("krea2_verite", "832x1216")
check(raw == 1.9 and abs(debit - raw * pricing.FRONTIER_MIN_MARKUP) < 1e-9,
      "a customer pays raw x the standard frontier markup per image")
raw_o, debit_o = pricing.image_lane_price(
    "krea2_verite", "832x1216", owner=True)
check(raw_o == 0.0 and debit_o == 0.0,
      "the owner pays the Kaggle route's true raw cost, $0")
check(app.image_lane_tier("krea2_verite") == 1,
      "the Krea lane bands at the $ tier like its label says")

# --- the create-flow route -----------------------------------------
create_src = func_src("create_image")
check('lane_id == "krea2_verite"' in create_src
      and "_queue_krea2_on_kaggle(job)" in create_src,
      "create_image queues a Krea job on the Kaggle worker")
check(create_src.index("_queue_krea2_on_kaggle(job)")
      < create_src.index("count_generation(user, free_job=served_free)"),
      "the Krea queueing happens before the shared generation count")
check('or lane_id == "krea2_verite"' in create_src,
      "the create answer reports the queued state honestly")

# --- the queued record's shape --------------------------------------
queue_src = func_src("_queue_krea2_on_kaggle")
for needle in ('job["provider"] = "kaggle-comfy"',
               'job["route"] = "kaggle"',
               'job["status"] = "queued"',
               'job["charged_cents"] = int(job.get("image_charge_cents")',
               'job["cost_cents"] = 0.0',
               'job["included_cost_cents"] = 0',
               'job["free_tier"] = False',
               'job["gallery_public"] = False',
               'job["user_prompt"] = job.get("prompt") or ""',
               '"verite realism, nsfw, "',
               "_comfy_maybe_trigger()"):
    check(needle in queue_src,
          f"the Kaggle queueing stamps {needle}")

# --- the kernel graph -------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "comfy_kernel", KERNEL_PATH)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)

check('job.get("image_model") == "krea2_verite"' in KERNEL
      and 'JOB_FAMILIES["krea2"]' in KERNEL,
      "the kernel routes Krea image jobs to the krea2 family")
g = kernel.build_krea2_image_workflow(
    "verite realism, nsfw, a portrait", 424242, 832, 1216,
    prefix="pfx")
by_class = {}
for node in g.values():
    by_class.setdefault(node["class_type"], []).append(node)
check(by_class["UNETLoader"][0]["inputs"]["unet_name"]
      == "krea2_raw_int8_convrot.safetensors",
      "the Krea graph loads the int8 convrot Raw build")
check(by_class["LoraLoaderModelOnly"][0]["inputs"]["lora_name"]
      == "Verite_realism.safetensors"
      and by_class["LoraLoaderModelOnly"][0]["inputs"]["strength_model"]
      == 0.8,
      "the Krea graph stacks the Verite LoRA at 0.8")
ids_by_class = {}
for nid, node in g.items():
    ids_by_class.setdefault(node["class_type"], []).append(nid)
check(by_class["LoraLoaderModelOnly"][0]["inputs"]["model"]
      == [ids_by_class["UNETLoader"][0], 0]
      and by_class["KSampler"][0]["inputs"]["model"]
      == [ids_by_class["LoraLoaderModelOnly"][0], 0],
      "the Krea graph wires the UNET through the LoRA into the sampler")
check(by_class["CLIPLoader"][0]["inputs"]["clip_name"]
      == "qwen3vl_4b_fp8_scaled.safetensors"
      and by_class["CLIPLoader"][0]["inputs"]["type"] == "krea2",
      "the Krea graph loads the Qwen3VL encoder as type krea2")
check(by_class["VAELoader"][0]["inputs"]["vae_name"]
      == "qwen_image_vae.safetensors",
      "the Krea graph loads the Qwen image VAE")
ksampler = by_class["KSampler"][0]["inputs"]
check(ksampler["steps"] == 30 and ksampler["cfg"] == 4.5
      and ksampler["sampler_name"] == "euler"
      and ksampler["scheduler"] == "simple"
      and ksampler["seed"] == 424242,
      "the Krea graph samples 30 steps at cfg 4.5, euler on simple")
check(by_class["EmptyLatentImage"][0]["inputs"]["width"] == 832
      and by_class["EmptyLatentImage"][0]["inputs"]["height"] == 1216,
      "the Krea graph takes the sold size into the latent")
texts = {n["inputs"].get("text") for n in g.values()
         if n.get("class_type") == "CLIPTextEncode"}
check("verite realism, nsfw, a portrait" in texts,
      "the Krea graph takes the caller's prompt with its triggers")
check(kernel.KREA2_NEGATIVE_PROMPT in texts
      and "clothing" not in kernel.KREA2_NEGATIVE_PROMPT,
      "the Krea negative is the baked quality negative, no clothing ban")
named = {v for node in g.values() for v in node.get("inputs", {}).values()
         if isinstance(v, str) and v.endswith(".safetensors")}
by_dest = {e["dest"].split("/")[-1]: e for e in kernel.MODEL_MANIFEST}
for name in sorted(named):
    entry = by_dest.get(name)
    check(entry is not None and entry["family"] == "krea2"
          and entry["size"] > 0 and len(entry["sha256"]) == 64,
          f"the krea2 family covers {name} with size and sha256")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_krea2_verite")
