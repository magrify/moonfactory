"""Local checks for the FLUX.2 Klein 4B Kaggle fallback
(2026-11-20).

Jack's order: FLUX.2 Klein 4B joins the image picker's Free
Models group as ONE entry whose routing is Cloudflare's free
tier first and the Kaggle house worker second, the same
model on both routes at $0, the split invisible in the
picker. These checks pin the shape without any network,
Modal, or Kaggle call:

- exactly one picker entry carries the Klein name, still
  stamped with the cloudflare provider (the first route)
  and the free flag;
- create_image diverts a Klein job to the Kaggle queue when
  the neuron budget refuses, both at the budget check and
  when the submit's own reservation loses the race, while
  Schnell (no second route) keeps its clean 503;
- an edit job never diverts (the house image flow is
  text-to-image only; the edit fails clean instead of
  silently dropping its source image);
- the diverted record takes the house image job's shape
  (kaggle-comfy, queued, zeroed charge and restore fields),
  counts through the same count_generation call as every
  shelf creation, and the kernel claim hands the kernel the
  image_model and image_size it picks the graph by;
- the mirror's kaggle-comfy branch runs before its shelf
  image branch, so a diverted job that the weekly Kaggle
  quota never reaches fails clean at the comfy bounds and
  never spills to a paid route;
- the kernel's Klein graph is ComfyUI's official distilled
  template shape (FP8 weights, Qwen3 4B encoder as type
  flux2, Flux2 scheduler at 4 steps, CFG guider at 1.0,
  zeroed-positive negative), and every file it names is
  manifest-covered under the klein family.

Run:  modal-venv python scratch/check_klein_kaggle_fallback.py
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


# --- one picker entry, cloudflare first -------------------------
labels = [lane_id for lane_id, lane in pricing.IMAGE_LANES.items()
          if lane.get("label") == "FLUX.2 Klein 4B"]
check(labels == ["flux2_klein4b"],
      "exactly one image lane is named FLUX.2 Klein 4B")
KLEIN = pricing.IMAGE_LANES["flux2_klein4b"]
check(KLEIN.get("provider") == "cloudflare",
      "the Klein lane's first route stays the cloudflare provider")
check(KLEIN.get("free") is True
      and app.image_picker_class("flux2_klein4b") == "free",
      "the Klein entry groups as free in the image picker")
check(all(v == 0.0 for v in KLEIN["raw_cents"].values()),
      "the Klein lane is $0 at every sold size on both routes")

# --- the create-flow split ---------------------------------------
create_src = func_src("create_image_core")
check("klein_on_kaggle = False" in create_src
      and "klein_on_kaggle = True" in create_src,
      "create_image carries the Klein route split flag")
check(create_src.index('lane_id == "flux2_klein4b"')
      < create_src.index("jobs[job_id] = job"),
      "the split is decided before the job is created")
check("_queue_klein_on_kaggle(job, served_free)" in create_src,
      "a diverted Klein job queues on the Kaggle worker")
check('"free image allowance is used up"' in create_src
      and "in str(exc)" in create_src,
      "a lost reservation race diverts instead of failing")
check("if source_image:" in create_src
      and "klein_on_kaggle = False" in create_src,
      "an edit job never takes the Kaggle route")
check(create_src.count("raise HTTPException") >= 1
      and "the free image allowance is used up for today" in create_src,
      "Schnell still fails clean on the spent allowance")
check("count_generation(user, free_job=served_free)" in create_src,
      "the diverted creation counts like every shelf creation")
check('"status": "queued" if klein_on_kaggle' in create_src,
      "the create answer reports the queued state honestly")

# --- the diverted record's shape ---------------------------------
queue_src = func_src("_queue_klein_on_kaggle")
for needle in ('job["provider"] = "kaggle-comfy"',
               'job["route"] = "kaggle"',
               'job["status"] = "queued"',
               'job["charged_cents"] = 0',
               'job["cost_cents"] = 0.0',
               'job["included_cost_cents"] = 0',
               'job["free_tier"] = bool(served_free)',
               "_comfy_maybe_trigger()"):
    check(needle in queue_src,
          f"the Kaggle queueing stamps {needle}")

# --- the kernel claim ---------------------------------------------
next_src = func_src("comfy_job_next")
check('"image_model": job.get("image_model")' in next_src
      and '"image_size": job.get("image_size")' in next_src,
      "the claim hands the kernel the image model and size")

# --- both routes exhausted: the comfy bounds own the job ---------
mirror_src = func_src("mirror_status")
check(mirror_src.index('job.get("provider") == "kaggle-comfy"')
      < mirror_src.index('job.get("image_model")'),
      "the mirror works a diverted job as a comfy job, so the "
      "dead-kernel bounds fail it clean when Kaggle never comes")
stale_src = func_src("_fail_stale_job")
check('job.get("provider") != "kaggle-comfy"' in stale_src
      and stale_src.index('job.get("provider") != "kaggle-comfy"')
      < stale_src.index("_restore_house_image(job)"),
      "a stale comfy image job fails through the house restore "
      "path, never the shelf refund path")

# --- the kernel graph ----------------------------------------------
spec = importlib.util.spec_from_file_location(
    "comfy_kernel", KERNEL_PATH)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)

check('job.get("image_model") == "flux2_klein4b"' in KERNEL
      and 'JOB_FAMILIES["klein"]' in KERNEL,
      "the kernel routes Klein image jobs to the klein family")
g = kernel.build_klein_image_workflow(
    "a lighthouse at sunset", 777, 1024, 768, prefix="pfx")
by_class = {}
for node in g.values():
    by_class.setdefault(node["class_type"], []).append(node)
check(by_class["UNETLoader"][0]["inputs"]["unet_name"]
      == "flux-2-klein-4b-fp8.safetensors",
      "the Klein graph loads the FP8 distilled weights")
check(by_class["CLIPLoader"][0]["inputs"]["clip_name"]
      == "qwen_3_4b.safetensors"
      and by_class["CLIPLoader"][0]["inputs"]["type"] == "flux2",
      "the Klein graph loads the Qwen3 4B encoder as type flux2")
check(by_class["VAELoader"][0]["inputs"]["vae_name"]
      == "flux2-vae.safetensors",
      "the Klein graph loads the Flux2 VAE")
check(by_class["Flux2Scheduler"][0]["inputs"]["steps"] == 4
      and by_class["Flux2Scheduler"][0]["inputs"]["width"] == 1024
      and by_class["Flux2Scheduler"][0]["inputs"]["height"] == 768,
      "the Klein graph runs the distilled 4 steps at the sold size")
check(by_class["CFGGuider"][0]["inputs"]["cfg"] == 1.0,
      "the Klein graph guides at CFG 1.0, the distilled recipe")
check(by_class["KSamplerSelect"][0]["inputs"]["sampler_name"]
      == "euler", "the Klein graph samples with euler")
check(by_class["CLIPTextEncode"][0]["inputs"]["text"]
      == "a lighthouse at sunset",
      "the Klein graph takes the caller's prompt")
check(by_class["RandomNoise"][0]["inputs"]["noise_seed"] == 777,
      "the Klein graph takes the caller's seed")
zero_ref = by_class["ConditioningZeroOut"][0]["inputs"]["conditioning"]
check(isinstance(zero_ref, list)
      and g[zero_ref[0]]["class_type"] == "CLIPTextEncode",
      "the Klein negative is the zeroed positive, as templated")
named = {v for node in g.values() for v in node.get("inputs", {}).values()
         if isinstance(v, str) and v.endswith(".safetensors")}
by_dest = {e["dest"].split("/")[-1]: e for e in kernel.MODEL_MANIFEST}
for name in sorted(named):
    entry = by_dest.get(name)
    check(entry is not None and entry["family"] == "klein"
          and entry["size"] > 0 and len(entry["sha256"]) == 64,
          f"the klein family covers {name} with size and sha256")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_klein_kaggle_fallback")
