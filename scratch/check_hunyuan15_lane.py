"""Local checks for the HunyuanVideo 1.5 house lane (2026-11-26).

Jack's order: wire Tencent's HunyuanVideo 1.5 (from the
FreeAiVideo gap shortlist) as its own individually named
house video lane on the Kaggle ComfyUI worker, following the
house lanes' shape: paid only, its own pricing lane on the
Production serving basis, text to video and image to video,
segments chaining on the worker. A clean lane: no
uncensored flag, so the regular gallery, classifier and
input rules apply.

No Modal, no network, no generation: reads the platform,
pricing and worker sources, extracts literals by AST, and
executes the generated kernel's Hunyuan builders so the
graphs that would be submitted are pinned node for node:
ComfyUI's official Hunyuan Video 1.5 template shape (the
sampler stack, shift 7, the dual encoder) on the 480p FP8
distilled builds the lane serves, the CFG distilled text
build at 20 steps with baked guidance, the step distilled
image build at 8 steps.

Run:  modal-venv python scratch/check_hunyuan15_lane.py
"""
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import pricing  # noqa: E402

PLATFORM = (ROOT / "platform" / "app.py").read_text()
PTREE = ast.parse(PLATFORM)
KERNEL_PATH = ROOT / "worker-kaggle" / "comfy_worker_kernel.py"
KERNEL = KERNEL_PATH.read_text()

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


# --- The shelf entry -------------------------------------------
MODELS = literal(PTREE, "MODELS")
by_id = {m["id"]: m for m in MODELS}
check("hunyuan15" in by_id, "hunyuan15 missing from MODELS")
check(by_id["hunyuan15"].get("label") == "HunyuanVideo 1.5",
      "hunyuan15 label is not its own plain name")
check(by_id["hunyuan15"].get("paid_only") is True,
      "hunyuan15 is not paid only")
check("hunyuan15" not in literal(PTREE, "PER_SECOND_MODELS"),
      "hunyuan15 must not be a per-second lane")
check("hunyuan15" not in literal(PTREE, "UNCENSORED_VIDEO_MODELS"),
      "hunyuan15 is a clean lane and must not carry the "
      "uncensored flag")
check(dict_literal(PTREE, "HOUSE_MODEL_PRICING_LANE").get("hunyuan15")
      == "hunyuan15", "house pricing lane map does not route hunyuan15")
check("hunyuan15" in literal(PTREE, "KAGGLE_COMFY_LANES"),
      "hunyuan15 must ride the Kaggle ComfyUI lanes")
audio = dict_literal(PTREE, "LANE_AUDIO")["hunyuan15"]
check(audio["native_speech"] is False
      and audio["native_audio"] is False,
      "lane audio must record the house model as silent")
check('if model == "hunyuan15":' in PLATFORM
      and 'lane = "hunyuan15"' in PLATFORM,
      "create_job does not route hunyuan15 to its lane")
check("const HOUSE_MODEL_IDS = ['wan2.2', 'wan22_uncut', 'ltx25_unc', 'hunyuan15'];"
      in PLATFORM, "studio house model list does not carry hunyuan15")
check("jp.hunyuan15" in PLATFORM,
      "house price line does not read the hunyuan15 job price")

# --- Pricing: the Production basis on its own entry -------------
check(pricing.MEASURED_COST_CENTS["hunyuan15"] == 15,
      "hunyuan15 measured entry drifted from the Production basis (15c)")
check(pricing.MEASURED_COST_CENTS["hunyuan15"]
      == pricing.MEASURED_COST_CENTS["production"],
      "hunyuan15 basis is not the Production lane's basis")
check(pricing.floor_price_cents("hunyuan15") == 78,
      "hunyuan15 floor is not gross_up(15 x 3) = 78")
check(pricing.JOB_PRICE_CENTS["hunyuan15"] == 99,
      "hunyuan15 job price is not the first clean price at/above floor (99)")
check(pricing.job_price_cents("hunyuan15", owner=True) == 15,
      "owner price is not the raw measured entry")
check(pricing.job_price_cents("hunyuan15") == 99,
      "customer price is not the job price")
check(pricing.lane_paused("hunyuan15") is False,
      "hunyuan15 lane is paused by its own floor")

# --- The kernel graphs ------------------------------------------
spec = importlib.util.spec_from_file_location("comfy_kernel",
                                              KERNEL_PATH)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)

check(kernel.LANE_DIMS["hunyuan15"]
      == {"9:16": [480, 848], "16:9": [848, 480], "1:1": [640, 640]},
      "hunyuan15 dims are not the 480p grid shapes")
check(kernel.JOB_FAMILIES["hunyuan15"] == ("hunyuan15",),
      "the kernel stages no family for a hunyuan15 job")
check('lane == "hunyuan15"' in KERNEL
      and "build_hunyuan_workflow" in KERNEL,
      "the kernel video runner has no hunyuan15 branch")

t2v = kernel.build_hunyuan_workflow(
    "a balloon over hills", "", 777, "9:16", text_only=True,
    prefix="pfx")
by_class = {}
for node in t2v.values():
    by_class.setdefault(node["class_type"], []).append(node)
check("LoadImage" not in by_class
      and "HunyuanVideo15ImageToVideo" not in by_class,
      "text-only graph carries image nodes")
check(by_class["UNETLoader"][0]["inputs"]["unet_name"]
      == "hunyuanvideo1.5_480p_t2v_cfg_distilled_fp8_scaled.safetensors",
      "the text graph does not load the CFG distilled FP8 build")
dual = by_class["DualCLIPLoader"][0]["inputs"]
check(dual["clip_name1"] == "qwen_2.5_vl_7b_fp8_scaled.safetensors"
      and dual["clip_name2"] == "byt5_small_glyphxl_fp16.safetensors"
      and dual["type"] == "hunyuan_video_15",
      "the dual encoder is not the template's Qwen VL plus ByT5 pair")
check(by_class["VAELoader"][0]["inputs"]["vae_name"]
      == "hunyuanvideo15_vae_fp16.safetensors",
      "the text graph does not load the Hunyuan 1.5 VAE")
latent = by_class["EmptyHunyuanVideo15Latent"][0]["inputs"]
check(latent["width"] == 480 and latent["height"] == 848
      and latent["length"] == 121,
      "9:16 latent is not 480x848x121")
check(by_class["BasicScheduler"][0]["inputs"]["steps"] == 20
      and by_class["CFGGuider"][0]["inputs"]["cfg"] == 1
      and by_class["ModelSamplingSD3"][0]["inputs"]["shift"] == 7
      and by_class["RandomNoise"][0]["inputs"]["noise_seed"] == 777,
      "the text graph does not run the distilled recipe "
      "(20 steps, cfg 1, shift 7, the job seed)")
check(by_class["CreateVideo"][0]["inputs"]["fps"] == 24
      and by_class["SaveVideo"][0]["inputs"]["filename_prefix"] == "pfx",
      "the text graph does not finish at 24 fps under the job prefix")
texts = [n["inputs"].get("text", "") for n in t2v.values()
         if n.get("class_type") == "CLIPTextEncode"]
check("a balloon over hills" in texts,
      "the caller's words do not ride the text graph")

i2v = kernel.build_hunyuan_workflow(
    "a balloon over hills", "", 777, "16:9", text_only=False,
    prefix="pfx")
by_class = {}
for node in i2v.values():
    by_class.setdefault(node["class_type"], []).append(node)
check(by_class["UNETLoader"][0]["inputs"]["unet_name"]
      == "hunyuanvideo1.5_480p_i2v_step_distilled_fp8_scaled.safetensors",
      "the image graph does not load the step distilled FP8 build")
check(by_class["BasicScheduler"][0]["inputs"]["steps"] == 8
      and by_class["CFGGuider"][0]["inputs"]["cfg"] == 1,
      "the image graph does not run the step distilled recipe (8 steps)")
i2v_node = by_class["HunyuanVideo15ImageToVideo"][0]["inputs"]
check(i2v_node["width"] == 848 and i2v_node["height"] == 480
      and i2v_node["length"] == 121,
      "16:9 image conditioning is not 848x480x121")
check(by_class["CLIPVisionLoader"][0]["inputs"]["clip_name"]
      == "sigclip_vision_patch14_384.safetensors",
      "the image graph does not load the SigCLIP vision encoder")
check(by_class["LoadImage"][0]["inputs"]["image"]
      == "PLACEHOLDER_START_IMAGE",
      "the image graph does not carry the start image placeholder")

# --- Manifest coverage ------------------------------------------
named = set()
for g in (t2v, i2v):
    for n in g.values():
        for v in n.get("inputs", {}).values():
            if isinstance(v, str) and v.endswith(".safetensors"):
                named.add(v)
by_dest = {e["dest"].split("/")[-1]: e
           for e in kernel.MODEL_MANIFEST
           if e["family"] == "hunyuan15"}
for name in sorted(named):
    entry = by_dest.get(name)
    check(entry is not None and entry["size"] > 0
          and len(entry["sha256"]) == 64,
          f"the hunyuan15 family does not cover {name} "
          "with size and sha256")

if failures:
    print("HUNYUANVIDEO 1.5 LANE CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL HUNYUANVIDEO 1.5 LANE CHECKS PASSED")
