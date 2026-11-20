"""Local checks for the 2026-11-20 LTX-2.5 Uncensored lane.

Jack's order: stage the ChrisColeTech LTX-2.5 uncensored FP8
build in the models Volume and wire it as a house lane that
executes on Modal (his explicit call: this model does not go
on Kaggle), following the house Uncut lane's shape — paid
only, never in the public gallery, owner at raw cost — with
its own pricing lane and the model's native synchronized
audio (speech included) carried as a first-class capability.

No Modal, no network, no generation: reads the worker and
platform sources, extracts literals by AST, and executes the
worker's _build_ltx_workflow in isolation (its only names are
module-level literals) so the graph that would be submitted
is pinned node for node: the author's loader and A/V decode,
core LTXV nodes between them, the card's distilled recipe.

Run:  modal-venv python scratch/check_ltx25_lane.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKER = (ROOT / "modal-worker" / "app.py").read_text()
PLATFORM = (ROOT / "platform" / "app.py").read_text()
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


# --- The lane identity and the staged file set -----------------
check(literal(WTREE, "LTX_LANE") == "ltx_unc", "worker lane id drifted")
check(literal(WTREE, "LTX_UNET_NAME")
      == "ltx25/ltx25_uncensored_v1.1-fp8_scaled.safetensors",
      "transformer path drifted from the staged set")
check(literal(WTREE, "LTX_CLIP_NAME")
      == "ltx25/gemma4_12b_ltx25_uncensored-int8.safetensors",
      "text encoder path drifted from the staged set")
check(literal(WTREE, "LTX_VIDEO_VAE_NAME")
      == "ltx25/ltx25_uncensored_video_vae.safetensors",
      "video VAE path drifted from the staged set")
check(literal(WTREE, "LTX_AUDIO_VAE_NAME")
      == "ltx25/ltx25_uncensored_audio_vae.safetensors",
      "audio VAE path drifted from the staged set")
check(literal(WTREE, "LTX_FRAMES") == 121
      and literal(WTREE, "LTX_FPS") == 24.0
      and literal(WTREE, "LTX_STEPS") == 8
      and literal(WTREE, "LTX_CFG") == 1.0,
      "the card recipe constants drifted (121 frames, 24 fps, 8 steps, cfg 1)")
neg = literal(WTREE, "LTX_NEGATIVE")
check(neg.startswith("blurry, out of focus, overexposed")
      and neg.endswith("or AI artifacts."),
      "LTX default negative no longer matches the repo example's")

# --- The worker image and the volume mount ----------------------
check("https://github.com/ChrisColeTech/ComfyUI-GGUF-Loader.git" in WORKER,
      "worker image no longer installs the author's node pack")
check("pip install gguf sentencepiece protobuf" in WORKER,
      "worker image no longer installs the pack's core requirements")
check('"latent_upscale_models"' in WORKER,
      "ComfyUI launch no longer links the latent_upscale_models category")
check('for key in ("gifs", "videos", "video", "images")' in WORKER,
      "run_workflow no longer reads SaveVideo's output key")

# --- The graph, built by the real builder ------------------------
ns = {}
for name in ("LTX_LANE", "LTX_UNET_NAME", "LTX_CLIP_NAME",
             "LTX_VIDEO_VAE_NAME", "LTX_AUDIO_VAE_NAME", "LTX_FRAMES",
             "LTX_FPS", "LTX_STEPS", "LTX_CFG"):
    ns[name] = literal(WTREE, name)
ns["LANE_DIMS"] = dict_literal(WTREE, "LANE_DIMS")
check(ns["LANE_DIMS"].get("ltx_unc")
      == {"9:16": (704, 1280), "16:9": (1280, 704), "1:1": (960, 960)},
      "ltx dims are not the model's native grid shapes")
for node in WTREE.body:
    if isinstance(node, ast.FunctionDef) and node.name == "_build_ltx_workflow":
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "<ltx-builder>", "exec"), ns)
        break
else:
    raise AssertionError("_build_ltx_workflow not found")
build = ns["_build_ltx_workflow"]

t2v = build("a lighthouse at dawn", "neg", 42, "16:9",
            text_only=True, prefix="sinclip/test")
classes = {n["class_type"] for n in t2v.values()}
check("LoadImage" not in classes and "LTXVImgToVideoInplace" not in classes,
      "text-only graph carries image nodes")
loader = [n for n in t2v.values()
          if n["class_type"] == "LTXV25ModelsLoader"][0]
check(loader["inputs"] == {
    "unet_name": "ltx25/ltx25_uncensored_v1.1-fp8_scaled.safetensors",
    "clip_name": "ltx25/gemma4_12b_ltx25_uncensored-int8.safetensors",
    "video_vae_name": "ltx25/ltx25_uncensored_video_vae.safetensors",
    "audio_vae_name": "ltx25/ltx25_uncensored_audio_vae.safetensors",
}, "loader inputs do not name the staged set")
sampler = [n for n in t2v.values() if n["class_type"] == "KSampler"][0]
check(sampler["inputs"]["steps"] == 8
      and sampler["inputs"]["cfg"] == 1.0
      and sampler["inputs"]["sampler_name"] == "euler"
      and sampler["inputs"]["scheduler"] == "simple"
      and sampler["inputs"]["seed"] == 42,
      "sampler does not run the card's distilled recipe")
latent = [n for n in t2v.values()
          if n["class_type"] == "EmptyLTXVLatentVideo"][0]
check(latent["inputs"]["width"] == 1280
      and latent["inputs"]["height"] == 704
      and latent["inputs"]["length"] == 121,
      "16:9 latent is not 1280x704x121")
audio_latent = [n for n in t2v.values()
                if n["class_type"] == "LTXVEmptyLatentAudio"][0]
check(audio_latent["inputs"]["frames_number"] == 121,
      "audio latent does not span the clip")
decode = [n for n in t2v.values()
          if n["class_type"] == "LTXV25AVDecode"][0]
check(decode["inputs"]["tile_size"] == 512
      and decode["inputs"]["overlap"] == 64
      and decode["inputs"]["temporal_size"] == 64
      and decode["inputs"]["temporal_overlap"] == 16,
      "decode tile values drifted from the official workflow's")
save = [n for n in t2v.values() if n["class_type"] == "SaveVideo"][0]
check(save["inputs"]["filename_prefix"] == "sinclip/test",
      "SaveVideo does not carry the job prefix")

i2v = build("a lighthouse at dawn", "neg", 42, "9:16",
            text_only=False, prefix="sinclip/test")
classes = {n["class_type"] for n in i2v.values()}
check("LoadImage" in classes and "LTXVImgToVideoInplace" in classes,
      "image graph is missing its image path")
inplace = [n for n in i2v.values()
           if n["class_type"] == "LTXVImgToVideoInplace"][0]
check(inplace["inputs"]["strength"] == 1.0,
      "image conditioning strength drifted from the example's 1.0")
concat = [n for n in i2v.values()
          if n["class_type"] == "LTXVConcatAVLatent"][0]
inplace_id = [k for k, n in i2v.items()
              if n["class_type"] == "LTXVImgToVideoInplace"][0]
check(concat["inputs"]["video_latent"] == [inplace_id, 0],
      "image graph's video latent does not flow through the inplace node")

# --- The endpoint's refusals --------------------------------------
check('detail="the ltx lane renders one clip per job"' in WORKER,
      "worker endpoint no longer refuses a multi-segment ltx job")
check('detail="the ltx lane does not take an end image"' in WORKER,
      "worker endpoint no longer refuses an end image on the ltx lane")
check("lane != LTX_LANE" in WORKER,
      "worker endpoint lane validation no longer admits the ltx lane")

# --- The platform shelf entry --------------------------------------
MODELS = literal(PTREE, "MODELS")
by_id = {m["id"]: m for m in MODELS}
check("ltx25_unc" in by_id, "ltx25_unc missing from MODELS")
check(by_id["ltx25_unc"].get("label") == "LTX 2.5 Uncensored",
      "ltx25_unc label drifted")
check(by_id["ltx25_unc"].get("paid_only") is True,
      "ltx25_unc is not paid only")
check("ltx25_unc" not in literal(PTREE, "PER_SECOND_MODELS"),
      "ltx25_unc must not be a per-second lane")
UNC = literal(PTREE, "UNCENSORED_VIDEO_MODELS")
check("ltx25_unc" in UNC, "ltx25_unc not in the uncensored set")
check(dict_literal(PTREE, "HOUSE_MODEL_PRICING_LANE").get("ltx25_unc")
      == "ltx_unc", "house pricing lane map does not route ltx25_unc")
check("ltx_unc" not in literal(PTREE, "KAGGLE_COMFY_LANES"),
      "ltx_unc must never join the Kaggle lanes: it executes on Modal")
audio = dict_literal(PTREE, "LANE_AUDIO")["ltx25_unc"]
check(audio["native_speech"] is True and audio["native_audio"] is True,
      "lane audio no longer records native speech and audio")
check('if model == "ltx25_unc":' in PLATFORM
      and 'lane = "ltx_unc"' in PLATFORM,
      "create_job does not route ltx25_unc to the ltx_unc lane")
check('detail="LTX 2.5 Uncensored renders one clip per job"' in PLATFORM,
      "platform segment refusal missing")
check("const HOUSE_MODEL_IDS = ['wan2.2', 'wan22_uncut', 'ltx25_unc'];"
      in PLATFORM, "studio house model list does not carry ltx25_unc")
check("jp.ltx_unc" in PLATFORM,
      "house price line does not read the ltx_unc job price")

# --- Pricing: the Uncut structure on its own entry ------------------
sys.path.insert(0, str(ROOT / "platform"))
import pricing  # noqa: E402

check(pricing.MEASURED_COST_CENTS["ltx_unc"] == 26,
      "ltx_unc measured entry drifted from the priced estimate (26c)")
check(pricing.floor_price_cents("ltx_unc") == 112,
      "ltx_unc floor is not gross_up(26 x 3) = 112")
check(pricing.JOB_PRICE_CENTS["ltx_unc"] == 149,
      "ltx_unc job price is not the first clean price at/above floor (149)")
check(pricing.job_price_cents("ltx_unc", owner=True) == 26,
      "owner price is not the raw measured entry")
check(pricing.job_price_cents("ltx_unc") == 149,
      "customer price is not the job price")
check(pricing.lane_paused("ltx_unc") is False,
      "ltx_unc lane is paused by its own floor")

if failures:
    print("LTX-2.5 LANE CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL LTX-2.5 LANE CHECKS PASSED")
