"""Local checks for the 2026-11-19 Kaggle move.

Jack's order: the expensive self hosted lanes ($$$ and $$$$
band) and the uncensored house models run on Kaggle's free
GPU quota instead of the Modal worker. This suite pins the
move's shape without any network or Modal call:

- every house video lane and the house image lane route to
  the Kaggle ComfyUI worker and never dispatch to the Modal
  worker from create_job / create_image;
- the platform carries the comfy worker endpoints (claim,
  heartbeat, images, file, fail) behind the worker key, the
  comfy trigger beside the template trigger, and the mirror's
  dead-kernel bound for comfy jobs;
- the generated kernel parses, embeds every workflow
  template and the full model manifest, and its workflow
  builders produce byte-identical graphs to the Modal
  worker's builders (parity by replay), including the Uncut
  lane's settled distilled_notrigger surgery with NO trigger
  token anywhere in the built graph;
- the manifest covers every model file the builders name,
  each with a pinned size and sha256.

Run:  modal-venv python scratch/check_kaggle_comfy.py
"""
import ast
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLATFORM = (ROOT / "platform" / "app.py").read_text()
WORKER = (ROOT / "modal-worker" / "app.py").read_text()
KERNEL_PATH = ROOT / "worker-kaggle" / "comfy_worker_kernel.py"
KERNEL = KERNEL_PATH.read_text()
MANIFEST = json.loads(
    (ROOT / "worker-kaggle" / "comfy_model_manifest.json").read_text())

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
    raise AssertionError(f"{name} not found")


PTREE = ast.parse(PLATFORM)

# --- routing -------------------------------------------------------------
lanes = literal(PTREE, "KAGGLE_COMFY_LANES")
check(lanes == ("draft", "production", "effects", "nsfw",
                "hunyuan15"),
      "KAGGLE_COMFY_LANES is exactly the house video lanes "
      "(the four Wan lanes plus HunyuanVideo 1.5, 2026-11-26)")
check("on_kaggle = lane in KAGGLE_COMFY_LANES" in PLATFORM
      and "if not on_kaggle:" in PLATFORM,
      "create_job dispatches to the Modal worker only off the Kaggle route")
check(PLATFORM.count('f"{WORKER_BASE}/jobs"') == 1,
      "exactly one Modal video dispatch remains, behind the route gate")
check('f"{WORKER_BASE}/image-jobs"' not in PLATFORM,
      "create_image no longer dispatches to the Modal worker at all")
check('"provider": "kaggle-comfy", "route": "kaggle"' in PLATFORM,
      "image jobs are stamped with the Kaggle route")
check('**({"provider": "kaggle-comfy", "route": "kaggle"}' in PLATFORM,
      "video jobs are stamped with the Kaggle route")
check(PLATFORM.count("_comfy_maybe_trigger()") >= 3,
      "creation and the mirror both wake the comfy kernel")
check("COMFY_KERNEL_SLUG = \"magrify/moonfactory-comfy-worker\""
      in PLATFORM, "the comfy kernel slug is pinned")
check('"enableGpu": gpu' in PLATFORM and "gpu=True" in PLATFORM,
      "the comfy kernel pushes with a GPU attached")
check('"/platform/comfy_worker_kernel.py"' in PLATFORM,
      "the platform image bundles the generated comfy kernel")

# --- worker endpoints ------------------------------------------------------
for route in ('"/api/worker/comfy/heartbeat"',
              '"/api/worker/comfy/jobs/next"',
              '"/api/worker/comfy/jobs/{job_id}/start-image"',
              '"/api/worker/comfy/jobs/{job_id}/end-image"',
              '"/api/worker/comfy/jobs/{job_id}/file"',
              '"/api/worker/comfy/jobs/{job_id}/fail"'):
    check(route in PLATFORM, f"worker endpoint {route} exists")
check("def _comfy_worker_ok" in PLATFORM
      and "return _hf_worker_ok(x_api_key)" in PLATFORM,
      "the comfy endpoints check the shared worker key")
check('f"{job_id}.raw.mp4"' in PLATFORM,
      "video uploads land where the finishing chain looks")
check('f"{job_id}.png"' in PLATFORM,
      "image uploads land where the image delivery looks")
check('users.get(job["user_id"])' not in PLATFORM,
      "no restore path looks a user up by bare id (the Dict keys "
      "are id: and email:; a bare lookup silently skips restores)")
check("_restore_house_image(job)" in PLATFORM
      and "def _fail_comfy_job" in PLATFORM,
      "comfy failures restore through the house restore paths")
check("worker_session" in PLATFORM
      and "requeued_at" in PLATFORM
      and 'other.get("worker_session") != session' in PLATFORM,
      "a heartbeat from a different session requeues orphaned jobs")
check("X-Comfy-Session" in KERNEL and "SESSION_ID" in KERNEL,
      "the kernel identifies its session on heartbeat and claim")
check("COMFY_DEAD_HEARTBEAT_SECONDS" in PLATFORM
      and "COMFY_QUEUED_LIMIT_SECONDS" in PLATFORM
      and "COMFY_RUNNING_LIMIT_SECONDS" in PLATFORM,
      "the mirror carries the comfy dead-kernel bounds")
check("no Modal fallback" in PLATFORM or "NO\n# Modal fallback" in PLATFORM
      or "There is NO" in PLATFORM,
      "the record in code states there is no Modal fallback")

# --- the generated kernel --------------------------------------------------
check(KERNEL.startswith("# GENERATED by worker-kaggle/build_comfy_kernel.py"),
      "the kernel on disk is the generated file")
spec = importlib.util.spec_from_file_location("comfy_kernel", KERNEL_PATH)
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)

check(sorted(kernel.WORKFLOW_TEMPLATES) == sorted(
    p.name for p in (ROOT / "workflows").glob("*.json")),
    "the kernel embeds every workflow template")
check(len(kernel.MODEL_MANIFEST) == len(MANIFEST),
      "the kernel embeds the full manifest")

# Builder parity: exec the Modal worker's builders against
# the same templates and compare graphs node for node. The
# worker reads its templates from /workflows; the shim path
# resolves those reads into this repo's workflows directory.
class ShimPath(type(Path())):
    def read_text(self, *a, **k):
        s = str(self)
        if s.startswith("/workflows/"):
            return (ROOT / "workflows" / s.split("/")[-1]).read_text()
        return super().read_text(*a, **k)


worker_ns = {"Path": ShimPath, "json": json}
worker_tree = ast.parse(WORKER)
BUILDERS = {"_build_image_workflow", "_build_lane_workflow",
            "_build_firstlast_workflow", "_build_fun_camera_workflow",
            "_build_effects_workflow"}
CONSTS = {"EFFECTS", "LANE_DIMS", "EFFECTS_DIMS", "IMAGE_DIMS",
          "FUN_CAMERA_POSES", "NEGATIVE_PROMPT", "LANE_WORKFLOWS",
          "NSFW_LANE", "NSFW_TRIGGER", "NSFW_LORA_HIGH",
          "NSFW_LORA_LOW", "FUN_CAMERA_RECIPES"}
for node in worker_tree.body:
    if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in CONSTS
            for t in node.targets):
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "worker", "exec"), worker_ns)
    if isinstance(node, ast.FunctionDef) and node.name in BUILDERS:
        exec(compile(ast.Module(body=[node], type_ignores=[]),
                     "worker", "exec"), worker_ns)

for lane in ("draft", "production"):
    for text_only in (False, True):
        got = kernel.build_lane_workflow(
            lane, "a balloon over hills", "neg words", 777, "9:16",
            text_only=text_only, prefix="pfx")
        want = worker_ns["_build_lane_workflow"](
            lane, "a balloon over hills", "neg words", 777, "9:16",
            text_only=text_only, prefix="pfx")
        check(got == want, f"lane graph parity: {lane} text_only={text_only}")
got = kernel.build_firstlast_workflow(
    "a balloon", "neg", 5, "16:9", prefix="pfx")
want = worker_ns["_build_firstlast_workflow"](
    "a balloon", "neg", 5, "16:9", prefix="pfx")
check(got == want, "firstlast graph parity")
for recipe in ("distilled", "stock", "mid"):
    got = kernel.build_fun_camera_workflow(
        "a balloon", "neg", 5, "Orbit", camera_speed=1.25,
        aspect="9:16", prefix="pfx", camera_recipe=recipe)
    want = worker_ns["_build_fun_camera_workflow"](
        "a balloon", "neg", 5, "Orbit", camera_speed=1.25,
        aspect="9:16", prefix="pfx", camera_recipe=recipe)
    check(got == want, f"fun camera graph parity ({recipe})")
for effect in ("inflate", "cakeify", "bullet-time"):
    got = kernel.build_effects_workflow(
        "a balloon", "neg", 5, effect, aspect="16:9", prefix="pfx")
    want = worker_ns["_build_effects_workflow"](
        "a balloon", "neg", 5, effect, aspect="16:9", prefix="pfx")
    check(got == want, f"effects graph parity ({effect})")
got = kernel.build_image_workflow("a balloon", 5, "1:1", prefix="pfx")
want = worker_ns["_build_image_workflow"]("a balloon", 5, "1:1", prefix="pfx")
check(got == want, "image graph parity")

# The Uncut lane's settled shape, pinned directly: main's
# worker builder predates the routing (the live worker runs
# the nsfw-distilled branch, whose default variant resolves
# to exactly this graph), so the kernel's graph is pinned
# against the settled record instead of a replay. Distilled
# template, the NSFW pair stacked behind the LightX2V pair
# at 19/20, the sampling nodes rewired through them, and no
# trigger token anywhere in the built graph.
nsfw = kernel.build_lane_workflow(
    "nsfw", "plain words only", "neg", 1, "9:16",
    text_only=False, prefix="pfx")
check(nsfw["17"]["class_type"] == "LoraLoaderModelOnly"
      and "lightx2v" in nsfw["17"]["inputs"]["lora_name"],
      "the LightX2V pair stays at 17/18 on the Uncut lane")
check(nsfw["19"]["inputs"]["model"] == ["17", 0]
      and nsfw["20"]["inputs"]["model"] == ["18", 0],
      "the NSFW pair stacks behind the LightX2V pair")
check(nsfw["19"]["inputs"]["lora_name"] == "NSFW-22-H-e8.safetensors"
      and nsfw["20"]["inputs"]["lora_name"] == "NSFW-22-L-e8.safetensors",
      "the NSFW pair files are the settled pair")
check(nsfw["10"]["inputs"]["model"] == ["19", 0]
      and nsfw["11"]["inputs"]["model"] == ["20", 0],
      "the sampling nodes read the NSFW pair")
texts = [n["inputs"].get("text", "") for n in nsfw.values()
         if n.get("class_type") == "CLIPTextEncode"]
check("plain words only" in texts
      and all("nsfwsks" not in t for t in texts),
      "the caller's words ride untouched, no trigger token anywhere")
samplers = [n["inputs"] for n in nsfw.values()
            if n.get("class_type") == "KSamplerAdvanced"]
check(all(s.get("steps") == 4 and s.get("cfg") == 1.0 for s in samplers),
      "the Uncut lane runs the distilled 4 step recipe")
check(kernel.NEGATIVE_PROMPT == worker_ns["NEGATIVE_PROMPT"],
      "the negative prompt matches the Modal worker's")

# --- manifest coverage -----------------------------------------------------
named = set()
for build in (kernel.build_lane_workflow,):
    for lane in ("draft", "production", "nsfw"):
        g = build(lane, "p", "n", 1, "9:16", text_only=False, prefix="x")
        for n in g.values():
            for v in n.get("inputs", {}).values():
                if isinstance(v, str) and v.endswith(".safetensors"):
                    named.add(v)
g = kernel.build_fun_camera_workflow("p", "n", 1, "Orbit", prefix="x")
g2 = kernel.build_effects_workflow("p", "n", 1, "inflate", prefix="x")
g3 = kernel.build_image_workflow("p", 1, "9:16", prefix="x")
g4 = kernel.build_klein_image_workflow("p", 1, 1024, 1024, prefix="x")
g5 = kernel.build_hunyuan_workflow("p", "n", 1, "9:16",
                                   text_only=True, prefix="x")
g6 = kernel.build_hunyuan_workflow("p", "n", 1, "9:16",
                                   text_only=False, prefix="x")
g7 = kernel.build_longcat_image_workflow("p", 1, 1024, 1024,
                                         prefix="x")
for g in (g, g2, g3, g4, g5, g6, g7):
    for n in g.values():
        for v in n.get("inputs", {}).values():
            if isinstance(v, str) and v.endswith(".safetensors"):
                named.add(v)
by_dest = {e["dest"].split("/")[-1]: e for e in MANIFEST}
for name in sorted(named):
    entry = by_dest.get(name)
    check(entry is not None, f"manifest covers {name}")
    if entry:
        check(entry["size"] > 0 and len(entry["sha256"]) == 64,
              f"manifest pins size and sha256 for {name}")
families = {e["family"] for e in MANIFEST}
check(families == {"shared", "draft", "a14b", "camera", "effects",
                   "image", "klein", "krea2", "hunyuan15", "longcat"},
      "the manifest stages by family")
total = sum(e["size"] for e in MANIFEST)
check(total > 100 * 1024**3,
      "the manifest carries the full model set (over 100 GB staged by family)")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_kaggle_comfy: all checks passed")
