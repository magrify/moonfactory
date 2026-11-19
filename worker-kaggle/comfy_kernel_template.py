# Moonfactory ComfyUI worker: a Kaggle kernel (script type).
#
# Renders the platform's self hosted generation lanes (the
# house Wan 2.2 video lanes Draft, Production, Effects and
# Uncut, and the house image lane, Z-Image Turbo) on Kaggle's
# free GPU quota instead of the Modal worker, per Jack's
# direction of 2026-11-19: the expensive self hosted lanes and
# the uncensored lanes run where the compute is free.
#
# The shape mirrors the HyperFrames worker beside it: the
# platform queues jobs, wakes this kernel with a version push
# when no heartbeat is fresh, and the kernel claims jobs one at
# a time through the platform's worker endpoints, renders, and
# uploads the finished file back. The worker key rides in the
# same tiny private dataset the HyperFrames worker uses
# (magrify/moonfactory-hf-key); it is read into memory only.
#
# Models live only in the Modal volume (Jack's single copy
# decision), so a session downloads the model families it
# needs from Hugging Face at job time and verifies every file
# against the manifest baked into this script (byte size for
# every file, sha256 for the assets the Modal staging pinned).
# Kaggle's session disk cannot hold every family at once, so
# families are staged on demand and the least recently used
# family is evicted when the disk needs the room; the shared
# family (text encoder, VAE, clip vision) is never evicted.
#
# The workflow builders below are the Modal worker's builders
# (modal-worker/app.py) carried over function for function, so
# a job renders the identical graph whichever host runs it.
# One deliberate difference: the Uncut lane builds its settled
# distilled_notrigger graph here (the LightX2V distilled
# template with the community NSFW LoRA pair stacked behind it
# at nodes 19 and 20, 4 steps at cfg 1.0, and the caller's
# prompt carried exactly as sent, no trigger token anywhere),
# the routing Jack settled on 2026-11-18 and the Modal worker
# resolves by its own default.
#
# The loop heartbeats from a background thread (renders run
# far longer than the platform's heartbeat staleness window),
# claims queued jobs one at a time, and shuts itself down
# after ten idle minutes plus a hard session cap, so the
# weekly free quota is spent on renders, not on waiting.

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

import requests

PLATFORM = "https://magrify--moonfactory-api.us-east.modal.run"
INPUT = Path("/kaggle/input")
WORKING = Path("/kaggle/working")
COMFY_DIR = WORKING / "ComfyUI"
MODELS_DIR = WORKING / "comfy-models"
STAGE_DIR = WORKING / "comfy-stage"

# Baked in by worker-kaggle/build_comfy_kernel.py from the
# repo's workflow files, the Modal worker's own literals, and
# comfy_model_manifest.json. Never edit the generated file.
WORKFLOW_TEMPLATES = json.loads(r'''__TEMPLATES_JSON__''')
MODEL_MANIFEST = json.loads(r'''__MANIFEST_JSON__''')
EFFECTS = {k: tuple(v) for k, v in
           json.loads(r'''__EFFECTS_JSON__''').items()}
LANE_DIMS = json.loads(r'''__LANE_DIMS_JSON__''')
EFFECTS_DIMS = json.loads(r'''__EFFECTS_DIMS_JSON__''')
IMAGE_DIMS = json.loads(r'''__IMAGE_DIMS_JSON__''')
FUN_CAMERA_POSES = tuple(json.loads(r'''__POSES_JSON__'''))
NEGATIVE_PROMPT = json.loads(r'''__NEGATIVE_JSON__''')

LANE_WORKFLOWS = {
    "draft": "draft_i2v_5b.json",
    "production": "prod_i2v_a14b_distilled.json",
    # The Uncut lane on this host builds from the distilled
    # template too: the settled distilled_notrigger graph is
    # that template with the NSFW pair stacked in, built by
    # build_lane_workflow below.
    "nsfw": "prod_i2v_a14b_distilled.json",
}
NSFW_LORA_HIGH = "NSFW-22-H-e8.safetensors"
NSFW_LORA_LOW = "NSFW-22-L-e8.safetensors"
NSFW_LANE = "nsfw"
ASPECT_RATIOS = ("9:16", "16:9", "1:1")

# Which model families a job needs on disk before it renders.
JOB_FAMILIES = {
    "draft": ("shared", "draft"),
    "production": ("shared", "a14b"),
    "camera": ("shared", "a14b", "camera"),
    "nsfw": ("shared", "a14b"),
    "effects": ("shared", "effects"),
    "image": ("image",),
}
# Keep at least this much disk free after staging a family.
DISK_RESERVE_BYTES = 6 * 1024 ** 3

IDLE_LIMIT_SECONDS = 600
SESSION_CAP_SECONDS = 5 * 3600
POLL_SECONDS = 20
HEARTBEAT_SECONDS = 60
RENDER_POLL_TIMEOUT = 5400

STARTED = time.time()


def log(msg):
    print(f"[comfy-worker] {msg}", flush=True)


def find_dataset_file(slug: str, filename: str) -> Path:
    for hit in INPUT.rglob(filename):
        if hit.parent.name == slug:
            return hit
    raise RuntimeError(f"dataset {slug} is not attached (no {filename})")


def read_key() -> str:
    try:
        from kaggle_secrets import UserSecretsClient

        value = UserSecretsClient().get_secret("MOONFACTORY_TEMPLATE_KEY")
        if value:
            return value.strip()
    except Exception:
        pass
    key_file = find_dataset_file("moonfactory-hf-key", "worker_key.txt")
    return key_file.read_text().strip()


KEY = None
HEADERS = {}


def heartbeat():
    try:
        requests.post(f"{PLATFORM}/api/worker/comfy/heartbeat",
                      headers=HEADERS, timeout=20)
    except Exception:
        pass


def heartbeat_loop():
    while True:
        heartbeat()
        time.sleep(HEARTBEAT_SECONDS)


# ---------------------------------------------------------------------------
# Model staging: download the families a job needs from Hugging
# Face, verify against the manifest, evict cold families when
# the disk needs the room.
# ---------------------------------------------------------------------------

_family_used = {}


def _disk_free() -> int:
    return shutil.disk_usage(WORKING).free


def _family_bytes(family: str) -> int:
    return sum(m["size"] for m in MODEL_MANIFEST
               if m["family"] == family)


def _family_present(family: str) -> bool:
    for m in MODEL_MANIFEST:
        if m["family"] != family:
            continue
        dest = MODELS_DIR / m["dest"]
        if not dest.exists() or dest.stat().st_size != m["size"]:
            return False
    return True


def _evict(family: str):
    for m in MODEL_MANIFEST:
        if m["family"] == family:
            try:
                (MODELS_DIR / m["dest"]).unlink()
            except Exception:
                pass
    _family_used.pop(family, None)
    log(f"evicted model family {family}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_families(families) -> int:
    """Stage any missing family from Hugging Face, verified
    against the manifest. Returns how many files were
    downloaded, so the caller can restart ComfyUI when the
    model set changed under it (its file lists are cached)."""
    from huggingface_hub import hf_hub_download

    downloaded = 0
    wanted = [f for f in families if not _family_present(f)]
    for family in wanted:
        need = _family_bytes(family)
        while _disk_free() < need + DISK_RESERVE_BYTES:
            candidates = [
                f for f in _family_used
                if f not in families and f != "shared"
            ]
            if not candidates:
                raise RuntimeError(
                    f"not enough disk to stage {family}: needs "
                    f"{need / 1e9:.1f} GB, free {_disk_free() / 1e9:.1f} GB")
            candidates.sort(key=lambda f: _family_used[f])
            _evict(candidates[0])
        for m in MODEL_MANIFEST:
            if m["family"] != family:
                continue
            dest = MODELS_DIR / m["dest"]
            if dest.exists() and dest.stat().st_size == m["size"]:
                continue
            log(f"downloading {m['repo']} :: {m['file']}")
            staged = hf_hub_download(
                repo_id=m["repo"], filename=m["file"],
                local_dir=str(STAGE_DIR))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(staged, dest)
            if dest.stat().st_size != m["size"]:
                raise RuntimeError(
                    f"size mismatch for {m['dest']}: got "
                    f"{dest.stat().st_size}, manifest {m['size']}")
            if m.get("sha256") and _sha256(dest) != m["sha256"]:
                raise RuntimeError(f"sha256 mismatch for {m['dest']}")
            log(f"staged {m['dest']} ({m['size']} bytes)")
            downloaded += 1
        shutil.rmtree(STAGE_DIR, ignore_errors=True)
    for family in families:
        _family_used[family] = time.time()
    return downloaded


# ---------------------------------------------------------------------------
# Workflow builders: the Modal worker's builders, carried over
# function for function (see the header). Templates come from
# the baked WORKFLOW_TEMPLATES instead of /workflows files.
# ---------------------------------------------------------------------------

def build_image_workflow(prompt: str, seed: int, aspect: str = "9:16",
                         prefix: str = ""):
    workflow = json.loads(json.dumps(WORKFLOW_TEMPLATES["z_image_t2i.json"]))
    width, height = IMAGE_DIMS[aspect]
    for node in workflow.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})
        if ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = prompt
        elif ct == "KSampler":
            inputs["seed"] = seed
        elif ct == "EmptySD3LatentImage":
            inputs["width"] = width
            inputs["height"] = height
        elif ct == "SaveImage":
            inputs["filename_prefix"] = prefix or "sinclip/api_image"
    return workflow


def build_lane_workflow(lane: str, prompt: str, negative: str, seed: int,
                        aspect: str = "9:16", text_only: bool = False,
                        prefix: str = ""):
    workflow = json.loads(json.dumps(WORKFLOW_TEMPLATES[LANE_WORKFLOWS[lane]]))
    prefix = prefix or f"sinclip/api_{lane}"
    width, height = LANE_DIMS[lane][aspect]
    for node in workflow.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})
        if ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = prompt
            elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                inputs["text"] = negative
        elif ct == "KSampler":
            inputs["seed"] = seed
        elif ct == "KSamplerAdvanced":
            inputs["noise_seed"] = seed
        elif ct == "WanImageToVideo":
            inputs["width"] = width
            inputs["height"] = height
            if text_only:
                inputs.pop("start_image", None)
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix
    if lane == NSFW_LANE:
        # The settled distilled_notrigger graph (Jack,
        # 2026-11-18): the distilled template carries its
        # LightX2V pair as authored (nodes 17 and 18, high
        # noise and low noise); the NSFW pair stacks behind
        # that pair at the next free node ids (19 and 20 on
        # this template), H behind the high noise expert's
        # LightX2V node and L behind the low noise one,
        # strength 1.0, and each ModelSamplingSD3 is rewired
        # through its expert's NSFW node: UNET, LightX2V,
        # NSFW, sampling. The prompt is the caller's words
        # exactly as sent: no trigger token is prepended on
        # this host, ever (the token typeset itself onto the
        # picture on the distilled recipe; that is why the
        # settled routing drops it).
        high_lora = low_lora = None
        for nid, n in workflow.items():
            if n.get("class_type") != "LoraLoaderModelOnly":
                continue
            name = str(n.get("inputs", {}).get("lora_name") or "")
            if "lightx2v" in name and "high_noise" in name:
                high_lora = nid
            elif "lightx2v" in name and "low_noise" in name:
                low_lora = nid
        if high_lora is None or low_lora is None:
            raise RuntimeError(
                "distilled template does not carry its LightX2V pair")
        used = {int(n) for n in workflow if str(n).isdigit()}
        high_nsfw = str(max(used) + 1)
        low_nsfw = str(max(used) + 2)
        workflow[high_nsfw] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": NSFW_LORA_HIGH,
                "model": [high_lora, 0], "strength_model": 1.0,
            },
        }
        workflow[low_nsfw] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": NSFW_LORA_LOW,
                "model": [low_lora, 0], "strength_model": 1.0,
            },
        }
        for node in workflow.values():
            if node.get("class_type") == "ModelSamplingSD3":
                src = node.get("inputs", {}).get("model") or []
                if src and str(src[0]) == str(high_lora):
                    node["inputs"]["model"] = [high_nsfw, 0]
                elif src and str(src[0]) == str(low_lora):
                    node["inputs"]["model"] = [low_nsfw, 0]
    if text_only:
        for nid in [k for k, n in workflow.items()
                    if n.get("class_type") == "LoadImage"]:
            del workflow[nid]
    return workflow


def build_firstlast_workflow(prompt: str, negative: str, seed: int,
                             aspect: str = "9:16", prefix: str = ""):
    workflow = json.loads(
        json.dumps(WORKFLOW_TEMPLATES["firstlast_chain.json"]))
    width, height = LANE_DIMS["production"][aspect]
    workflow["19"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "model": ["1", 0], "strength_model": 1.0,
        },
    }
    workflow["20"] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "model": ["2", 0], "strength_model": 1.0,
        },
    }
    for node in workflow.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})
        if ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = prompt
            elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                inputs["text"] = negative
        elif ct == "ModelSamplingSD3":
            inputs["model"] = [("19" if inputs.get("model") == ["1", 0]
                                else "20"), 0]
        elif ct == "WanFirstLastFrameToVideo":
            inputs["width"] = width
            inputs["height"] = height
        elif ct == "KSamplerAdvanced":
            inputs["noise_seed"] = seed
            inputs["cfg"] = 1.0
            inputs["steps"] = 4
            if inputs.get("start_at_step") == 0:
                inputs["end_at_step"] = 2
            else:
                inputs["start_at_step"] = 2
                inputs["end_at_step"] = 4
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix or "sinclip/api_firstlast"
    return workflow


def build_fun_camera_workflow(prompt: str, negative: str, seed: int,
                              camera_pose: str, camera_speed: float = 1.0,
                              aspect: str = "9:16", prefix: str = "",
                              camera_recipe: str = "distilled"):
    if camera_recipe not in ("distilled", "stock", "mid"):
        raise ValueError(f"unknown camera_recipe: {camera_recipe}")
    workflow = json.loads(
        json.dumps(WORKFLOW_TEMPLATES["fun_camera_i2v.json"]))
    width, height = LANE_DIMS["production"][aspect]
    for node in workflow.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})
        if ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = prompt
            elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                inputs["text"] = negative
        elif ct == "WanCameraEmbedding":
            inputs["camera_pose"] = camera_pose
            inputs["speed"] = camera_speed
            inputs["width"] = width
            inputs["height"] = height
        elif ct == "LoraLoaderModelOnly":
            if camera_recipe == "distilled":
                inputs["strength_model"] = 1.0
            elif camera_recipe == "mid":
                inputs["strength_model"] = 0.5
        elif ct == "KSamplerAdvanced":
            inputs["noise_seed"] = seed
            if camera_recipe == "distilled":
                inputs["cfg"] = 1.0
                inputs["steps"] = 4
                if inputs.get("start_at_step") == 0:
                    inputs["end_at_step"] = 2
                else:
                    inputs["start_at_step"] = 2
                    inputs["end_at_step"] = 4
            elif camera_recipe == "mid":
                inputs["cfg"] = 2.0
                inputs["steps"] = 10
                if inputs.get("start_at_step") == 0:
                    inputs["end_at_step"] = 5
                else:
                    inputs["start_at_step"] = 5
                    inputs["end_at_step"] = 10
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix or "sinclip/api_funcamera"
    return workflow


def build_effects_workflow(subject: str, negative: str, seed: int,
                           effect: str, aspect: str = "9:16",
                           prefix: str = ""):
    lora_name, template = EFFECTS[effect]
    workflow = json.loads(
        json.dumps(WORKFLOW_TEMPLATES["effects_i2v.json"]))
    width, height = EFFECTS_DIMS[aspect]
    prompt = template.replace("{subject}", subject)
    for node in workflow.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})
        if ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = prompt
            elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                inputs["text"] = negative
        elif ct == "LoraLoaderModelOnly":
            if inputs.get("lora_name") == "PLACEHOLDER_EFFECT_LORA":
                inputs["lora_name"] = lora_name
            else:
                inputs["strength_model"] = 1.0
        elif ct == "KSampler":
            inputs["seed"] = seed
            inputs["steps"] = 6
            inputs["cfg"] = 1.0
            inputs["sampler_name"] = "euler"
        elif ct == "WanImageToVideo":
            inputs["width"] = width
            inputs["height"] = height
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix or "sinclip/api_effects"
    return workflow


# ---------------------------------------------------------------------------
# ComfyUI: cloned into the session, launched headless on the
# local port, driven through its HTTP API exactly as the Modal
# worker's class drives it. Low VRAM mode is on: Kaggle's free
# GPUs carry 16 GB, a class below the Modal worker's L40S, and
# the A14B fp8 experts only fit with offloading.
# ---------------------------------------------------------------------------

COMFY_BASE = "http://127.0.0.1:8188"
_comfy_proc = None


def setup_comfy() -> dict:
    info = {"gpu": None, "torch": None, "disk_free_gb": None}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30)
        info["gpu"] = (out.stdout or "").strip() or None
    except Exception:
        pass
    log(f"gpu: {info['gpu']}")
    info["disk_free_gb"] = round(_disk_free() / 1e9, 1)
    log(f"disk free at start: {info['disk_free_gb']} GB")
    if not (COMFY_DIR / "main.py").exists():
        log("cloning ComfyUI")
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/comfyanonymous/ComfyUI.git",
             str(COMFY_DIR)], check=True, timeout=900)
        req = (COMFY_DIR / "requirements.txt").read_text().splitlines()
        try:
            import torch

            info["torch"] = torch.__version__
            if not torch.cuda.is_available():
                raise RuntimeError("torch is present but CUDA is not")
            # The session image's torch drives ComfyUI fine and
            # reinstalling it would eat the session; drop the
            # torch lines from the requirements instead.
            req = [ln for ln in req
                   if not ln.strip().lower().startswith(
                       ("torch", "torchvision", "torchaudio"))]
            log(f"using the session image's torch {info['torch']}")
        except ImportError:
            log("no torch in the session image, installing from "
                "ComfyUI's requirements")
        req_path = WORKING / "comfy-requirements.txt"
        req_path.write_text("\n".join(req))
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q",
             "-r", str(req_path)], check=True, timeout=1800)
        vhs = COMFY_DIR / "custom_nodes" / "ComfyUI-VideoHelperSuite"
        if not vhs.exists():
            subprocess.run(
                ["git", "clone", "--depth", "1",
                 "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git",
                 str(vhs)], check=True, timeout=600)
            vhs_req = vhs / "requirements.txt"
            if vhs_req.exists():
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q",
                     "-r", str(vhs_req)], check=True, timeout=900)
    if not shutil.which("ffmpeg"):
        # The combine nodes shell out to ffmpeg; where the
        # session image lacks it, drop in the static binary
        # imageio-ffmpeg ships and put it on PATH for every
        # child process.
        log("no ffmpeg on PATH, installing imageio-ffmpeg")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q",
             "imageio-ffmpeg"], check=True, timeout=600)
        import imageio_ffmpeg

        bin_dir = WORKING / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / "ffmpeg"
        shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), target)
        target.chmod(0o755)
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
        log(f"ffmpeg ready at {target}")
    # Point ComfyUI's model categories at the staged families,
    # the Modal worker's symlink pattern.
    models_dir = COMFY_DIR / "models"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for category in ("diffusion_models", "text_encoders", "vae",
                     "clip_vision", "loras"):
        target = MODELS_DIR / category
        target.mkdir(parents=True, exist_ok=True)
        link = models_dir / category
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        os.symlink(target, link)
    start_comfy()
    return info


def start_comfy():
    global _comfy_proc
    _comfy_proc = subprocess.Popen(
        [sys.executable, "main.py", "--listen", "127.0.0.1",
         "--port", "8188", "--lowvram", "--disable-auto-launch"],
        cwd=str(COMFY_DIR),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    deadline = time.time() + 600
    while time.time() < deadline:
        if _comfy_proc.poll() is not None:
            raise RuntimeError("ComfyUI exited during startup")
        try:
            r = requests.get(f"{COMFY_BASE}/system_stats", timeout=5)
            if r.status_code == 200:
                log("ComfyUI is up")
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise RuntimeError("ComfyUI API did not come up in time")


def restart_comfy():
    """Restart ComfyUI after new models were staged, so its
    cached file lists see them."""
    global _comfy_proc
    log("restarting ComfyUI for newly staged models")
    try:
        _comfy_proc.terminate()
        _comfy_proc.wait(timeout=30)
    except Exception:
        try:
            _comfy_proc.kill()
        except Exception:
            pass
    start_comfy()


def check_nodes(workflow: dict):
    """Fail the job clean, naming the node, when this ComfyUI
    build does not register a class the workflow needs."""
    wanted = {n.get("class_type") for n in workflow.values()}
    for ct in sorted(wanted):
        r = requests.get(f"{COMFY_BASE}/object_info/{ct}", timeout=15)
        got = r.json().get(ct) if r.status_code == 200 else None
        if not got:
            raise RuntimeError(f"ComfyUI node not registered: {ct}")


def run_workflow(workflow: dict, image_bytes=None, image_name: str = "",
                 images: dict = None) -> bytes:
    """Submit one patched workflow and return the produced
    video as bytes. The Modal worker's run_workflow, verbatim
    in shape."""
    import uuid

    input_dir = COMFY_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    if images:
        for name, data in images.items():
            (input_dir / name).write_bytes(data)
    elif image_bytes is not None:
        (input_dir / image_name).write_bytes(image_bytes)

    payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
    r = requests.post(f"{COMFY_BASE}/prompt", json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(
            f"ComfyUI /prompt returned {r.status_code}: {r.text[:2000]}")
    data = r.json()
    if data.get("node_errors"):
        raise RuntimeError(f"ComfyUI rejected workflow: {data['node_errors']}")
    prompt_id = data["prompt_id"]
    log(f"submitted prompt {prompt_id}")

    deadline = time.time() + RENDER_POLL_TIMEOUT
    entry = None
    while time.time() < deadline:
        r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=15)
        r.raise_for_status()
        history = r.json()
        if prompt_id in history:
            candidate = history[prompt_id]
            status = candidate.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI execution error: {status}")
            if candidate.get("outputs"):
                entry = candidate
                break
        time.sleep(3)
    if entry is None:
        raise TimeoutError(f"prompt {prompt_id} did not finish in time")

    for node_output in entry.get("outputs", {}).values():
        for key in ("gifs", "videos", "images"):
            for item in node_output.get(key, []):
                filename = item.get("filename", "")
                if filename.endswith((".mp4", ".webm")):
                    path = (COMFY_DIR / item.get("type", "output")
                            / item.get("subfolder", "") / filename)
                    log(f"reading output {path}")
                    return path.read_bytes()
    raise RuntimeError(f"No video file in ComfyUI outputs: {entry.get('outputs')}")


def run_image_workflow(workflow: dict):
    """The image twin of run_workflow: returns (png bytes,
    execution seconds)."""
    import uuid

    payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
    r = requests.post(f"{COMFY_BASE}/prompt", json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(
            f"ComfyUI /prompt returned {r.status_code}: {r.text[:2000]}")
    data = r.json()
    if data.get("node_errors"):
        raise RuntimeError(f"ComfyUI rejected workflow: {data['node_errors']}")
    prompt_id = data["prompt_id"]
    log(f"submitted image prompt {prompt_id}")

    started = time.time()
    deadline = started + 1800
    entry = None
    while time.time() < deadline:
        r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=15)
        r.raise_for_status()
        history = r.json()
        if prompt_id in history:
            candidate = history[prompt_id]
            status = candidate.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI execution error: {status}")
            if candidate.get("outputs"):
                entry = candidate
                break
        time.sleep(2)
    if entry is None:
        raise TimeoutError(f"image prompt {prompt_id} did not finish in time")
    exec_seconds = time.time() - started

    for node_output in entry.get("outputs", {}).values():
        for item in node_output.get("images", []):
            filename = item.get("filename", "")
            if filename.endswith(".png"):
                path = (COMFY_DIR / item.get("type", "output")
                        / item.get("subfolder", "") / filename)
                log(f"reading output {path}")
                return path.read_bytes(), exec_seconds
    raise RuntimeError(f"No image file in ComfyUI outputs: {entry.get('outputs')}")


# ---------------------------------------------------------------------------
# Job execution: the Modal worker's run_job dispatcher, with
# the segment chain and the stitch carried over. A segment
# that fails after at least one completed delivers the
# completed segments as a partial job, exactly as on Modal.
# ---------------------------------------------------------------------------

def download_job_image(job_id: str, which: str):
    resp = requests.get(
        f"{PLATFORM}/api/worker/comfy/jobs/{job_id}/{which}",
        headers=HEADERS, timeout=300)
    resp.raise_for_status()
    return resp.content


def run_video_job(job: dict) -> dict:
    import tempfile

    job_id = job["id"]
    lane = job["lane"]
    prompt = job.get("prompt") or ""
    negative = job.get("negative_prompt") or NEGATIVE_PROMPT
    seed = int(job.get("seed") or 12345)
    aspect = job.get("aspect_ratio") or "9:16"
    segments = int(job.get("segments") or 1)
    effect = job.get("effect") or ""
    camera_pose = job.get("camera_pose") or ""
    families = JOB_FAMILIES["camera" if camera_pose else
                             ("effects" if effect else lane)]
    if ensure_families(families):
        restart_comfy()

    start_bytes = None
    end_bytes = None
    if job.get("has_start_image"):
        start_bytes = download_job_image(job_id, "start-image")
    if job.get("has_end_image"):
        end_bytes = download_job_image(job_id, "end-image")
    if lane == "production" and not start_bytes and not camera_pose:
        raise RuntimeError("the production lane needs a start image")
    if lane == NSFW_LANE and not start_bytes:
        raise RuntimeError("the nsfw lane needs a start image")
    if effect and not start_bytes:
        raise RuntimeError("effects need a start image")

    render_started = time.time()
    workdir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))
    seg_paths = []
    failure = None
    for i in range(segments):
        is_last = i == segments - 1
        use_firstlast = bool(end_bytes) and is_last and lane == "production"
        prefix = f"sinclip/api_{lane}_{job_id}_seg{i + 1}"
        seg_seed = seed + i
        try:
            if effect:
                workflow = build_effects_workflow(
                    prompt, negative, seg_seed, effect, aspect, prefix)
                image_name = f"job_{job_id}_seg{i + 1}.png"
                for node in workflow.values():
                    if node.get("class_type") == "LoadImage":
                        node["inputs"]["image"] = image_name
                check_nodes(workflow)
                video = run_workflow(workflow, image_bytes=start_bytes,
                                     image_name=image_name)
            elif camera_pose:
                workflow = build_fun_camera_workflow(
                    prompt, negative, seg_seed, camera_pose,
                    float(job.get("camera_speed") or 1.0), aspect,
                    prefix, job.get("camera_recipe") or "distilled")
                image_name = f"job_{job_id}_seg{i + 1}.png"
                for node in workflow.values():
                    if node.get("class_type") == "LoadImage":
                        node["inputs"]["image"] = image_name
                check_nodes(workflow)
                video = run_workflow(workflow, image_bytes=start_bytes,
                                     image_name=image_name)
            elif use_firstlast:
                workflow = build_firstlast_workflow(
                    prompt, negative, seg_seed, aspect, prefix)
                start_name = f"job_{job_id}_seg{i + 1}_start.png"
                end_name = f"job_{job_id}_seg{i + 1}_end.png"
                for node in workflow.values():
                    if node.get("class_type") == "LoadImage":
                        if "PLACEHOLDER_START_IMAGE" in str(
                                node["inputs"].get("image", "")):
                            node["inputs"]["image"] = start_name
                        elif "PLACEHOLDER_END_IMAGE" in str(
                                node["inputs"].get("image", "")):
                            node["inputs"]["image"] = end_name
                check_nodes(workflow)
                video = run_workflow(
                    workflow,
                    images={start_name: start_bytes, end_name: end_bytes})
            else:
                text_only = start_bytes is None
                workflow = build_lane_workflow(
                    lane, prompt, negative, seg_seed, aspect,
                    text_only=text_only, prefix=prefix)
                check_nodes(workflow)
                if text_only:
                    video = run_workflow(workflow)
                else:
                    image_name = f"job_{job_id}_seg{i + 1}.png"
                    for node in workflow.values():
                        if node.get("class_type") == "LoadImage":
                            node["inputs"]["image"] = image_name
                    video = run_workflow(workflow, image_bytes=start_bytes,
                                         image_name=image_name)
        except Exception as exc:  # noqa: BLE001 - partial delivery below
            failure = exc
            break
        seg_path = workdir / f"seg{i + 1}.mp4"
        seg_path.write_bytes(video)
        seg_paths.append(seg_path)
        log(f"job {job_id}: segment {i + 1} of {segments} done")
        if not is_last:
            last_png = workdir / f"seg{i + 1}_last.png"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(seg_path),
                 "-frames:v", "1", str(last_png)],
                capture_output=True, text=True, timeout=120)
            if proc.returncode != 0 or not last_png.exists():
                failure = RuntimeError(
                    f"last-frame extract failed: {proc.stderr[-200:]}")
                break
            start_bytes = last_png.read_bytes()
    if not seg_paths:
        raise failure or RuntimeError("no segment rendered")
    if len(seg_paths) == 1:
        final = seg_paths[0].read_bytes()
    else:
        parts = [seg_paths[0]]
        for idx, seg_path in enumerate(seg_paths[1:], start=2):
            trimmed = workdir / f"seg{idx}_trim.mp4"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(seg_path),
                 "-vf", "select='gte(n\\,1)'", "-vsync", "vfr",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-an", str(trimmed)],
                capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"stitch trim failed: {proc.stderr[-200:]}")
            parts.append(trimmed)
        listfile = workdir / "concat.txt"
        listfile.write_text("".join(f"file '{p}'\n" for p in parts))
        out_path = workdir / "stitched.mp4"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listfile), "-c", "copy", str(out_path)],
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not out_path.exists():
            proc = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(listfile), "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "18", "-an",
                 str(out_path)],
                capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(
                    f"stitch failed: {proc.stderr[-200:]}")
        final = out_path.read_bytes()
    return {
        "data": final,
        "render_seconds": round(time.time() - render_started, 1),
        "segments_done": len(seg_paths),
        "partial": failure is not None,
        "partial_error": (f"{type(failure).__name__}: {failure}"[:300]
                          if failure is not None else ""),
    }


def run_image_job(job: dict) -> dict:
    if ensure_families(JOB_FAMILIES["image"]):
        restart_comfy()
    started = time.time()
    workflow = build_image_workflow(
        job.get("prompt") or "", int(job.get("seed") or 12345),
        job.get("aspect_ratio") or "9:16",
        prefix=f"sinclip/api_image_{job['id']}")
    check_nodes(workflow)
    data, exec_seconds = run_image_workflow(workflow)
    return {
        "data": data,
        "render_seconds": round(time.time() - started, 1),
        "exec_seconds": round(float(exec_seconds or 0), 1),
        "segments_done": 1,
        "partial": False,
        "partial_error": "",
    }


# ---------------------------------------------------------------------------
# Platform client and the main loop.
# ---------------------------------------------------------------------------

def upload_result(job_id: str, result: dict, gpu_name):
    headers = dict(HEADERS)
    headers["X-Comfy-Render-Seconds"] = str(result["render_seconds"])
    headers["X-Comfy-Segments-Done"] = str(result["segments_done"])
    headers["X-Comfy-Partial"] = "1" if result["partial"] else "0"
    if result.get("partial_error"):
        headers["X-Comfy-Partial-Error"] = urllib.parse.quote(
            result["partial_error"], safe="")[:280]
    if result.get("exec_seconds") is not None:
        headers["X-Comfy-Exec-Seconds"] = str(result["exec_seconds"])
    if gpu_name:
        headers["X-Comfy-Gpu"] = urllib.parse.quote(gpu_name, safe="")[:120]
    resp = requests.post(
        f"{PLATFORM}/api/worker/comfy/jobs/{job_id}/file",
        headers=headers, data=result["data"], timeout=900)
    resp.raise_for_status()


def fail_job(job_id: str, message: str):
    try:
        requests.post(f"{PLATFORM}/api/worker/comfy/jobs/{job_id}/fail",
                      headers=HEADERS, json={"error": message[:280]},
                      timeout=30)
    except Exception:
        pass


def main():
    global KEY, HEADERS
    KEY = read_key()
    HEADERS = {"X-API-Key": KEY}
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    heartbeat()
    info = setup_comfy()
    idle = 0
    heartbeat()
    log("poll loop started")
    while True:
        if time.time() - STARTED > SESSION_CAP_SECONDS:
            log("session cap reached, shutting down")
            return
        try:
            resp = requests.get(f"{PLATFORM}/api/worker/comfy/jobs/next",
                                headers=HEADERS, timeout=30)
            resp.raise_for_status()
            job = resp.json().get("job")
        except Exception as exc:
            log(f"claim failed: {exc}")
            job = None
        if not job:
            idle += POLL_SECONDS
            if idle >= IDLE_LIMIT_SECONDS:
                log("idle limit reached, shutting down")
                return
            time.sleep(POLL_SECONDS)
            continue
        idle = 0
        try:
            log(f"job {job['id']}: claimed ({job.get('kind')}, "
                f"lane {job.get('lane')})")
            if job.get("kind") == "image":
                result = run_image_job(job)
            else:
                result = run_video_job(job)
            log(f"job {job['id']}: uploading result")
            upload_result(job["id"], result, info.get("gpu"))
            log(f"job {job['id']}: done in "
                f"{result['render_seconds']}s")
        except Exception:
            message = traceback.format_exc()[-280:]
            log(f"job {job['id']}: failed: {message}")
            fail_job(job["id"], message)


if __name__ == "__main__":
    main()
