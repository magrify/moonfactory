"""
Sinclip generation worker on Modal (draft lane proof).

Pattern follows Modal's official ComfyUI example (modal-examples
06_gpu_and_ml/comfyui/comfyapp.py): a Modal Image with ComfyUI installed
via comfy-cli, and a GPU class that launches a headless ComfyUI server in
@modal.enter() and drives its HTTP API from a @modal.method().

Adaptations for Sinclip:
- Model weights live on a Modal Volume ("sinclip-models") mounted as
  ComfyUI's models directory, instead of being baked into the image.
- The workflow is our draft template (workflows/draft_i2v_5b.json, Wan 2.2
  TI2V-5B), patched and submitted through ComfyUI's /prompt API using the
  same logic as worker/handler.py.
- A separate CPU function downloads the model set into the Volume.

Usage:
  modal run app.py --mode download   # one time: pull models into the Volume
  modal run app.py --mode render     # run the draft workflow, save the mp4
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import modal

app = modal.App("sinclip-comfyui")

VOLUME_NAME = "sinclip-models"
# Modal refuses to mount a Volume over a non-empty path, and the ComfyUI
# install populates its models dir, so the Volume mounts at its own path
# and the class symlinks the model categories into ComfyUI at startup.
MODELS_MOUNT = "/sinclip-models"
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

# ---------------------------------------------------------------------------
# Model download (CPU function, Hugging Face -> Volume)
# ---------------------------------------------------------------------------

download_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("huggingface_hub==2.5.0")
)

MODEL_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
# source path in repo -> path inside the ComfyUI models dir
MODEL_FILES = {
    "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors":
        "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
    "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors":
        "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "split_files/vae/wan2.2_vae.safetensors":
        "vae/wan2.2_vae.safetensors",
}


@app.function(image=download_image, volumes={"/models": volume}, timeout=3600)
def download_models():
    import shutil

    from huggingface_hub import hf_hub_download

    results = {}
    for src, dst in MODEL_FILES.items():
        final = Path("/models") / dst
        if final.exists() and final.stat().st_size > 0:
            results[dst] = final.stat().st_size
            print(f"already present: {dst} ({final.stat().st_size} bytes)")
            continue
        print(f"downloading {src} ...", flush=True)
        staged = hf_hub_download(
            repo_id=MODEL_REPO, filename=src, local_dir="/models/_stage"
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, final)
        results[dst] = final.stat().st_size
        print(f"stored {dst}: {final.stat().st_size} bytes", flush=True)
    shutil.rmtree("/models/_stage", ignore_errors=True)
    volume.commit()
    return results


# ---------------------------------------------------------------------------
# ComfyUI worker (GPU class)
# ---------------------------------------------------------------------------

comfy_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg")
    .pip_install("comfy-cli==1.29.0")
    .run_commands("comfy --skip-prompt install --nvidia --commit 7c78b5641ab2d6cb7f7766a183a8a43a59de0a8b")
    .run_commands(
        "git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite"
        " && git -C /root/comfy/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite"
        " checkout 775ae691ecbab6a6d09a2c186f95d130cf8a10d3"
    )
    # The requirements.txt installs below read files inside the
    # clones pinned to commits above, so their contents are
    # transitively pinned by those checkouts.
    .run_commands(
        "pip install -r "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt"
    )
    .run_commands(
        "git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation"
        " && git -C /root/comfy/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation"
        " checkout 26545cc2dd95bc3d27f056016300673bdeee78f5"
    )
    .run_commands(
        "pip install -r "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation/requirements.txt "
        "|| true"
    )
    .run_commands(
        "git clone https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler"
        " && git -C /root/comfy/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler"
        " checkout f7362118d67db1045f4374428a0f5c814c7d0b7a"
    )
    .run_commands(
        "pip install -r "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler/requirements.txt "
        "|| true"
    )
    # The LTX-2.5 lane's loader and joint A/V decode nodes
    # (LTXV25ModelsLoader, LTXV25AVDecode) come from the model
    # author's own pack, the one the model card names. Only its
    # core requirements are installed (gguf, sentencepiece,
    # protobuf): the pack's optional TTS requirements pull a
    # transformers major version the worker's stack does not
    # carry, and no LTX node reads them.
    .run_commands(
        "git clone https://github.com/ChrisColeTech/ComfyUI-GGUF-Loader.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-GGUF-Loader"
        " && git -C /root/comfy/ComfyUI/custom_nodes/ComfyUI-GGUF-Loader"
        " checkout 109a281b1a49570cb708f8543efea6383baba40a"
    )
    .run_commands(
        "pip install gguf==0.19.0 sentencepiece==0.2.3 protobuf==7.36.2 || true"
    )
)

COMFY_BASE = "http://127.0.0.1:8188"
COMFY_ROOT = Path("/root/comfy/ComfyUI")


@app.cls(
    image=comfy_image,
    gpu="L40S",
    volumes={MODELS_MOUNT: volume},
    scaledown_window=300,
    timeout=3600,
)
class ComfyUI:
    @modal.enter()
    def launch_comfy(self):
        # Point ComfyUI's model categories at the Volume via symlinks.
        import shutil

        models_dir = COMFY_ROOT / "models"
        for category in ("diffusion_models", "text_encoders", "vae", "clip_vision", "SEEDVR2", "loras", "latent_upscale_models"):
            target = Path(MODELS_MOUNT) / category
            link = models_dir / category
            if link.is_symlink() or link.exists():
                if link.is_dir() and not link.is_symlink():
                    shutil.rmtree(link)
                else:
                    link.unlink()
            os.symlink(target, link)
            print(f"linked {link} -> {target}", flush=True)
        subprocess.run("comfy launch --background", shell=True, check=True)
        self._wait_for_comfy(timeout=300)

    def _wait_for_comfy(self, timeout=300):
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = requests.get(f"{COMFY_BASE}/system_stats", timeout=5)
                if r.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(2)
        raise RuntimeError("ComfyUI API did not come up in time")

    def _find_output_file(self, workflow):
        """Return the Path of an existing video for this workflow's VHS
        filename_prefix in ComfyUI's output dir, or None."""
        prefix = None
        for node in workflow.values():
            if node.get("class_type") == "VHS_VideoCombine":
                prefix = node.get("inputs", {}).get("filename_prefix")
                break
        if not prefix:
            return None
        out_dir = COMFY_ROOT / "output" / Path(prefix).parent
        stem = Path(prefix).name
        if out_dir.is_dir():
            matches = sorted(out_dir.glob(f"{stem}_*.mp4"))
            if matches:
                return matches[-1]
        return None

    def _persist_output(self, path: Path) -> bytes:
        """Copy a finished video into the Volume (so it survives the
        container and can be fetched by a separate function), then return
        its bytes."""
        import shutil

        dest_dir = Path(MODELS_MOUNT) / "outputs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        shutil.copyfile(path, dest)
        volume.commit()
        print(f"persisted {dest} ({dest.stat().st_size} bytes)", flush=True)
        return path.read_bytes()

    @modal.method()
    def probe_nodes(self, class_types: list):
        """Return ComfyUI's /object_info input spec for the given node
        class types ({} for any type that is not registered)."""
        import requests

        self._wait_for_comfy(timeout=60)
        out = {}
        for ct in class_types:
            r = requests.get(f"{COMFY_BASE}/object_info/{ct}", timeout=15)
            out[ct] = r.json().get(ct, {}) if r.status_code == 200 else {}
        return out

    @modal.method()
    def run_workflow(self, workflow: dict, image_bytes=None, image_name: str = "",
                     images: dict = None):
        """Submit one patched API-format workflow to the local ComfyUI and
        return the produced video as bytes. Logic mirrors worker/handler.py.
        If a previous run already produced this workflow's output in this
        container, return it without re-rendering.
        Inputs: either a single (image_bytes, image_name) pair or an
        `images` dict of {filename: bytes} for multi-image workflows."""
        import requests

        existing = self._find_output_file(workflow)
        if existing is not None:
            print(f"reusing existing output {existing}", flush=True)
            return self._persist_output(existing)

        input_dir = COMFY_ROOT / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        if images:
            for name, data in images.items():
                (input_dir / name).write_bytes(data)
        elif image_bytes is not None:
            (input_dir / image_name).write_bytes(image_bytes)

        self._wait_for_comfy(timeout=60)

        payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
        r = requests.post(f"{COMFY_BASE}/prompt", json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(
                f"ComfyUI /prompt returned {r.status_code}: {r.text[:2000]}"
            )
        data = r.json()
        if data.get("node_errors"):
            raise RuntimeError(f"ComfyUI rejected workflow: {data['node_errors']}")
        prompt_id = data["prompt_id"]
        print(f"submitted prompt {prompt_id}", flush=True)

        deadline = time.time() + 3300
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
            for key in ("gifs", "videos", "video", "images"):
                for item in node_output.get(key, []):
                    filename = item.get("filename", "")
                    if filename.endswith((".mp4", ".webm")):
                        path = (
                            COMFY_ROOT
                            / item.get("type", "output")
                            / item.get("subfolder", "")
                            / filename
                        )
                        print(f"reading output {path}", flush=True)
                        return self._persist_output(path)
        raise RuntimeError(f"No video file in ComfyUI outputs: {entry.get('outputs')}")

    def _find_image_output(self, workflow):
        """Return the Path of an existing PNG for this workflow's
        SaveImage filename_prefix in ComfyUI's output dir, or None."""
        prefix = None
        for node in workflow.values():
            if node.get("class_type") == "SaveImage":
                prefix = node.get("inputs", {}).get("filename_prefix")
                break
        if not prefix:
            return None
        out_dir = COMFY_ROOT / "output" / Path(prefix).parent
        stem = Path(prefix).name
        if out_dir.is_dir():
            matches = sorted(out_dir.glob(f"{stem}_*.png"))
            if matches:
                return matches[-1]
        return None

    @modal.method()
    def run_image_workflow(self, workflow: dict):
        """Submit one patched API-format image workflow to the local
        ComfyUI and return the produced PNG as bytes. Mirrors
        run_workflow, but the terminal node is SaveImage and the
        output is a still image. Returns (bytes, gpu_seconds) where
        gpu_seconds is the wall time of the ComfyUI execution poll,
        the figure the platform prices the lane from."""
        import requests

        existing = self._find_image_output(workflow)
        if existing is not None:
            print(f"reusing existing output {existing}", flush=True)
            return self._persist_output(existing), 0.0

        self._wait_for_comfy(timeout=60)

        payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
        r = requests.post(f"{COMFY_BASE}/prompt", json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(
                f"ComfyUI /prompt returned {r.status_code}: {r.text[:2000]}"
            )
        data = r.json()
        if data.get("node_errors"):
            raise RuntimeError(f"ComfyUI rejected workflow: {data['node_errors']}")
        prompt_id = data["prompt_id"]
        print(f"submitted image prompt {prompt_id}", flush=True)

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
        gpu_seconds = time.time() - started

        for node_output in entry.get("outputs", {}).values():
            for item in node_output.get("images", []):
                filename = item.get("filename", "")
                if filename.endswith(".png"):
                    path = (
                        COMFY_ROOT
                        / item.get("type", "output")
                        / item.get("subfolder", "")
                        / filename
                    )
                    print(f"reading output {path}", flush=True)
                    return self._persist_output(path), gpu_seconds
        raise RuntimeError(f"No image file in ComfyUI outputs: {entry.get('outputs')}")


MODEL_REPO_21 = "Comfy-Org/Wan_2.1_ComfyUI_repackaged"
# (repo, source path in repo, path inside the ComfyUI models dir)
PROD_MODEL_FILES = [
    (MODEL_REPO,
     "split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
     "diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"),
    (MODEL_REPO,
     "split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
     "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"),
    (MODEL_REPO,
     "split_files/vae/wan_2.1_vae.safetensors",
     "vae/wan_2.1_vae.safetensors"),
    (MODEL_REPO_21,
     "split_files/clip_vision/clip_vision_h.safetensors",
     "clip_vision/clip_vision_h.safetensors"),
]
# Byte sizes from the HF listings (verified 2026-11-16 via the tree API).
PROD_EXPECTED_SIZES = {
    "diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors": 14294742832,
    "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors": 14294742832,
    "vae/wan_2.1_vae.safetensors": 253815318,
    "clip_vision/clip_vision_h.safetensors": 1264219396,
}


@app.function(image=download_image, volumes={"/models": volume}, timeout=3600)
def download_prod_models():
    import shutil

    from huggingface_hub import hf_hub_download

    results = {}
    for repo, src, dst in PROD_MODEL_FILES:
        final = Path("/models") / dst
        if final.exists() and final.stat().st_size > 0:
            results[dst] = final.stat().st_size
            print(f"already present: {dst} ({final.stat().st_size} bytes)")
            continue
        print(f"downloading {repo}:{src} ...", flush=True)
        staged = hf_hub_download(
            repo_id=repo, filename=src, local_dir="/models/_stage_prod"
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, final)
        results[dst] = final.stat().st_size
        print(f"stored {dst}: {final.stat().st_size} bytes", flush=True)
    shutil.rmtree("/models/_stage_prod", ignore_errors=True)
    volume.commit()
    return results


LORA_FILES = [
    ("split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
     "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
     1226977424),
    ("split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
     "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
     1226977424),
]


@app.function(image=download_image, volumes={"/models": volume}, timeout=1800)
def download_loras():
    import shutil

    from huggingface_hub import hf_hub_download

    results = {}
    for src, dst, expected in LORA_FILES:
        final = Path("/models") / dst
        if final.exists() and final.stat().st_size > 0:
            results[dst] = final.stat().st_size
            print(f"already present: {dst} ({final.stat().st_size} bytes)")
            continue
        print(f"downloading {MODEL_REPO}:{src} ...", flush=True)
        staged = hf_hub_download(
            repo_id=MODEL_REPO, filename=src, local_dir="/models/_stage_lora"
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, final)
        results[dst] = final.stat().st_size
        print(f"stored {dst}: {final.stat().st_size} bytes "
              f"(expected {expected}, match={final.stat().st_size == expected})",
              flush=True)
    shutil.rmtree("/models/_stage_lora", ignore_errors=True)
    volume.commit()
    return results


SEEDVR2_REPO = "numz/SeedVR2_comfyUI"
SEEDVR2_FILES = {
    "seedvr2_ema_3b_fp16.safetensors": 6783018808,
    "ema_vae_fp16.safetensors": 501324814,
}


@app.function(image=download_image, volumes={"/models": volume}, timeout=1800)
def download_seedvr2():
    import shutil

    from huggingface_hub import hf_hub_download

    results = {}
    for filename, expected in SEEDVR2_FILES.items():
        final = Path("/models/SEEDVR2") / filename
        if final.exists() and final.stat().st_size > 0:
            results[filename] = final.stat().st_size
            print(f"already present: {filename} ({final.stat().st_size} bytes)")
            continue
        print(f"downloading {SEEDVR2_REPO}:{filename} ...", flush=True)
        staged = hf_hub_download(
            repo_id=SEEDVR2_REPO, filename=filename, local_dir="/models/_stage_svr2"
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, final)
        results[filename] = final.stat().st_size
        print(f"stored {filename}: {final.stat().st_size} bytes "
              f"(expected {expected}, match={final.stat().st_size == expected})",
              flush=True)
    shutil.rmtree("/models/_stage_svr2", ignore_errors=True)
    volume.commit()
    return results


EFFECTS_DISTILL_FILE = (
    "loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
)
EFFECTS_DISTILL_SIZE = 738005744
EFFECTS_DISTILL_SHA256 = (
    "85c4a61c30e0497aa44b91d93a893b624708461a56fe5485183b28fa07e2dfb3"
)


@app.function(image=download_image, volumes={"/models": volume}, timeout=1800)
def download_effects_distill():
    """Stage the LightX2V 2.1 step-distill LoRA for the effects lane
    (Kijai/WanVideo_comfy, the effects app's optional fast preset).
    Byte-verified against the repo listing: exact size and sha256."""
    import hashlib
    import shutil

    from huggingface_hub import hf_hub_download

    final = Path("/models") / EFFECTS_DISTILL_FILE
    if final.exists() and final.stat().st_size == EFFECTS_DISTILL_SIZE:
        digest = hashlib.sha256(final.read_bytes()).hexdigest()
        if digest == EFFECTS_DISTILL_SHA256:
            print(f"already present and verified: {EFFECTS_DISTILL_FILE}")
            return {EFFECTS_DISTILL_FILE: final.stat().st_size}
    print("downloading Kijai/WanVideo_comfy distill LoRA ...", flush=True)
    staged = hf_hub_download(
        repo_id="Kijai/WanVideo_comfy",
        filename="Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors",
        local_dir="/models/_stage_fx",
    )
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(staged, final)
    size = final.stat().st_size
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    print(f"stored {EFFECTS_DISTILL_FILE}: {size} bytes "
          f"(expected {EFFECTS_DISTILL_SIZE}, match={size == EFFECTS_DISTILL_SIZE}); "
          f"sha256 match={digest == EFFECTS_DISTILL_SHA256}", flush=True)
    if size != EFFECTS_DISTILL_SIZE or digest != EFFECTS_DISTILL_SHA256:
        raise RuntimeError("distill LoRA byte verification failed")
    shutil.rmtree("/models/_stage_fx", ignore_errors=True)
    volume.commit()
    return {EFFECTS_DISTILL_FILE: size}


NSFW_LORA_REPO = "lopi999/Wan2.2-I2V_General-NSFW-LoRA"
# (file in repo, path inside the ComfyUI models dir, bytes, sha256).
# Sizes and hashes from the repo listing (HF resolve headers,
# verified 2026-11-18). The pair is the community general NSFW
# LoRA for Wan2.2-I2V-A14B (a mirror of a Civitai model): the H
# file drives the high-noise expert, the L file the low-noise
# expert, and the pair is driven by the trigger token below.
NSFW_LORA_FILES = [
    ("NSFW-22-H-e8.safetensors", "loras/NSFW-22-H-e8.safetensors",
     613516752,
     "34e2144d3cd65360f97d09ccbe03e1c39a096df6c9234af5fe3899d1b63cda39"),
    ("NSFW-22-L-e8.safetensors", "loras/NSFW-22-L-e8.safetensors",
     613516752,
     "d6b783742f4d5fd63a0223ae1d5bf64fc995a6b408480ac2a00528ae0d4146db"),
]
NSFW_LORA_HIGH = "NSFW-22-H-e8.safetensors"
NSFW_LORA_LOW = "NSFW-22-L-e8.safetensors"
NSFW_TRIGGER = "nsfwsks"
NSFW_LANE = "nsfw"

# ---------------------------------------------------------------------------
# LTX-2.5 Uncensored lane (2026-11-20, Jack's order)
#
# ChrisColeTech/LTX-2.5-uncensored-v1.1-FP8: the full LTX-2.5
# (Lightricks, 21B) with the Eros10 NSFW LoRA and the DMD
# distilled LoRA baked into the FP8-scaled transformer, the
# uncensored Gemma text encoder baked in alongside, and native
# synchronized audio with speech generated in the same pass.
# The model card's license field reads "unknown"; Jack knows,
# and the lane ships dark and unpromoted (paid only, never in
# the public gallery — the platform side enforces both).
#
# This is the one house lane that executes on Modal (Jack's
# call, 2026-11-20, reversing a Kaggle test plan): the FP8 set
# is 37.7 GB and does not fit the free Kaggle GPU. The files
# live in the models Volume under an ltx25/ subfolder of each
# category, the layout the loader node's own example workflows
# use, staged by the local batch upload recorded in
# ~/workspace/sinclip/ltx25-uncensored-modal-2026-11-20.md and
# size-verified there. The latent upscalers are staged with
# the set (the card's two-stage flows use them) but v1 renders
# the single-stage distilled graph below and never calls them.
#
# The graph is built in code (no template file): the loader
# and the joint A/V decode come from the author's own
# ComfyUI-GGUF-Loader pack (installed in the worker image);
# everything between them is core ComfyUI LTXV nodes, wired
# exactly as the repo's own giga_ltx25_distilled and
# ltx25_i2v_simple example workflows wire them. The recipe is
# the card's: 8 steps, cfg 1.0, euler/simple, 24 fps, 121
# frames (one 5.04 s clip, the house segment shape). Sizes use
# the model's native grid (dimensions are multiples of 32):
# 1280x704 for 16:9, the nearest grid shape to the card's
# recommended 1280x720.
LTX_LANE = "ltx_unc"
LTX_UNET_NAME = "ltx25/ltx25_uncensored_v1.1-fp8_scaled.safetensors"
LTX_CLIP_NAME = "ltx25/gemma4_12b_ltx25_uncensored-int8.safetensors"
LTX_VIDEO_VAE_NAME = "ltx25/ltx25_uncensored_video_vae.safetensors"
LTX_AUDIO_VAE_NAME = "ltx25/ltx25_uncensored_audio_vae.safetensors"
LTX_FRAMES = 121
LTX_FPS = 24.0
LTX_STEPS = 8
LTX_CFG = 1.0
# The default negative from the repo's own LTX 2.5 example
# workflows (the "LTX 2.5 Clip Encode (prompt)" subgraph in
# ltx25_i2v_simple.json), verbatim, used when the caller sends
# none (the endpoint's Wan-shaped house negative does not
# describe this model).
LTX_NEGATIVE = (
    "blurry, out of focus, overexposed, underexposed, low contrast, "
    "washed out colors, excessive noise, grainy texture, poor lighting, "
    "flickering, motion blur, distorted proportions, unnatural skin "
    "tones, deformed facial features, asymmetrical face, missing facial "
    "features, extra limbs, disfigured hands, wrong hand count, "
    "artifacts around text, unreadable text on shirt or hat, incorrect "
    "lettering on cap (“PNTR”), incorrect t-shirt slogan (“JUST DO "
    "IT”), missing microphone, misplaced microphone, inconsistent "
    "perspective, camera shake, incorrect depth of field, background "
    "too sharp, background clutter, distracting reflections, harsh "
    "shadows, inconsistent lighting direction, color banding, "
    "cartoonish rendering, 3D CGI look, unrealistic materials, uncanny "
    "valley effect, incorrect ethnicity, wrong gender, exaggerated "
    "expressions, smiling, laughing, exaggerated sadness, wrong gaze "
    "direction, eyes looking at camera, mismatched lip sync, silent or "
    "muted audio, distorted voice, robotic voice, echo, background "
    "noise, off-sync audio, missing sniff sounds, incorrect dialogue, "
    "added dialogue, repetitive speech, jittery movement, awkward "
    "pauses, incorrect timing, unnatural transitions, inconsistent "
    "framing, tilted camera, missing door or shelves, missing shallow "
    "depth of field, flat lighting, inconsistent tone, cinematic "
    "oversaturation, stylized filters, or AI artifacts."
)


@app.function(image=download_image, volumes={"/models": volume}, timeout=1800)
def download_nsfw_lora():
    """Stage the community Wan 2.2 NSFW LoRA pair for the house
    Uncut lane. Byte-verified and sha256-verified against the
    repo listing, the same discipline as the effects distill
    staging: a file that does not match is refused, never
    silently kept."""
    import hashlib
    import shutil

    from huggingface_hub import hf_hub_download

    def _digest(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    results = {}
    for src, dst, expected_size, expected_sha in NSFW_LORA_FILES:
        final = Path("/models") / dst
        if final.exists() and final.stat().st_size == expected_size \
                and _digest(final) == expected_sha:
            print(f"already present and verified: {dst}")
            results[dst] = final.stat().st_size
            continue
        print(f"downloading {NSFW_LORA_REPO}:{src} ...", flush=True)
        staged = hf_hub_download(
            repo_id=NSFW_LORA_REPO, filename=src,
            local_dir="/models/_stage_nsfw",
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, final)
        size = final.stat().st_size
        digest = _digest(final)
        print(f"stored {dst}: {size} bytes "
              f"(expected {expected_size}, match={size == expected_size}); "
              f"sha256 match={digest == expected_sha}", flush=True)
        if size != expected_size or digest != expected_sha:
            raise RuntimeError(f"NSFW LoRA byte verification failed: {dst}")
        results[dst] = size
    shutil.rmtree("/models/_stage_nsfw", ignore_errors=True)
    volume.commit()
    return results


@app.function(image=download_image, volumes={"/models": volume}, timeout=600)
def fetch_output(filename: str):
    """Fallback retrieval path: read a persisted output from the Volume."""
    path = Path("/models/outputs") / filename
    if not path.exists():
        available = sorted(p.name for p in (Path("/models") / "outputs").glob("*")) \
            if (Path("/models") / "outputs").is_dir() else []
        raise FileNotFoundError(f"{filename} not in volume; available: {available}")
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Web endpoint (job dispatch for the Sinclip app)
#
# POST /jobs            {lane, prompt, negative_prompt?, seed?,
#                        start_image_png_b64?} -> {job_id, status}
# GET  /jobs/{job_id}   -> {job_id, lane, status, error?, created_at}
# GET  /jobs/{job_id}/file -> the finished mp4 (video/mp4) once done
# Auth: header X-API-Key must match the Modal Secret "sinclip-api-key"
# (env SINCLIP_API_KEY). The key itself lives only in that Secret and in
# modal-worker/api-key.txt (mode 600) on this machine; never printed.
# ---------------------------------------------------------------------------

jobs = modal.Dict.from_name("sinclip-jobs", create_if_missing=True)

LANE_WORKFLOWS = {
    "draft": "draft_i2v_5b.json",
    # Production runs the LightX2V 4-step distilled recipe since
    # 2026-11-16 (run 7): benchmarked at 216 s wall / ~$0.12 per
    # segment on L40S vs 23m39s / ~$0.77 for the 20-step original,
    # with quality holding on the frame comparison. The 20-step
    # template (prod_i2v_a14b.json) is kept for reference and for
    # the local render-prod / chain modes.
    "production": "prod_i2v_a14b_distilled.json",
    # The Uncut lane (2026-11-18): the stock A14B template, with
    # the community NSFW LoRA pair patched in by
    # _build_lane_workflow. The stock sampler path is deliberate:
    # the LoRA was trained on the full base model, so the lane
    # does not stack it on the LightX2V distilled recipe.
    "nsfw": "prod_i2v_a14b.json",
}
LANE_DEFAULT_IMAGES = {
    "draft": "/assets/test_start.png",
    "production": "/assets/test_start_720.png",
}

api_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("fastapi[standard]==0.165.0")
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "draft_i2v_5b.json"),
        "/workflows/draft_i2v_5b.json",
    )
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "prod_i2v_a14b.json"),
        "/workflows/prod_i2v_a14b.json",
    )
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "prod_i2v_a14b_distilled.json"),
        "/workflows/prod_i2v_a14b_distilled.json",
    )
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "firstlast_chain.json"),
        "/workflows/firstlast_chain.json",
    )
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "z_image_t2i.json"),
        "/workflows/z_image_t2i.json",
    )
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "fun_camera_i2v.json"),
        "/workflows/fun_camera_i2v.json",
    )
    .add_local_file(
        str(Path(__file__).parent.parent / "workflows" / "effects_i2v.json"),
        "/workflows/effects_i2v.json",
    )
    .add_local_file(
        str(Path(__file__).parent / "test_start.png"),
        "/assets/test_start.png",
    )
    .add_local_file(
        str(Path(__file__).parent / "test_start_720.png"),
        "/assets/test_start_720.png",
    )
)


ASPECT_RATIOS = ("9:16", "16:9", "1:1")
# Native render dimensions per lane and aspect. The worker renders
# at exactly these dimensions; nothing downstream crops or pads, so
# the delivered file's aspect is the aspect that was ordered.
LANE_DIMS = {
    "draft": {"9:16": (480, 832), "16:9": (800, 448), "1:1": (640, 640)},
    "production": {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (960, 960)},
    # The Uncut lane renders at the production house dimensions.
    "nsfw": {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (960, 960)},
    # LTX-2.5 renders on its native grid (dimensions are
    # multiples of 32): 1280x704 is the nearest grid shape to
    # the model card's recommended 1280x720.
    "ltx_unc": {"9:16": (704, 1280), "16:9": (1280, 704), "1:1": (960, 960)},
    # HunyuanVideo 1.5 (2026-11-26) renders at its 480p grid
    # on the Kaggle worker only (the FP8 distilled builds the
    # lane serves); the Modal worker never takes its jobs, but
    # the kernel's baked LANE_DIMS is extracted from this
    # table, so the lane's shapes live here like the rest.
    "hunyuan15": {"9:16": (480, 848), "16:9": (848, 480), "1:1": (640, 640)},
}

# Start-image generation (Pass 3): one still image per job at the
# production house shape for the chosen aspect, so the frame feeds
# either house lane at its native aspect with no crop or pad. The
# workflow is workflows/z_image_t2i.json, built from the Freefield
# z-image app definition (8 steps, res_multistep, cfg 1).
IMAGE_DIMS = {
    "9:16": (720, 1280), "16:9": (1280, 720), "1:1": (960, 960),
}


def _build_image_workflow(prompt: str, seed: int, aspect: str = "9:16",
                          prefix: str = ""):
    workflow = json.loads(
        (Path("/workflows") / "z_image_t2i.json").read_text()
    )
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


def _build_ltx_workflow(prompt: str, negative: str, seed: int,
                        aspect: str = "9:16", text_only: bool = False,
                        prefix: str = ""):
    """The LTX-2.5 graph, built in code (see the LTX block at
    the lane constants for the why). One 121-frame clip at
    24 fps with joint audio: the loader feeds both text
    encodes and both latents, the sampler runs the card's
    distilled recipe (8 steps, cfg 1.0), and the author's
    decode node muxes picture and sound into one VIDEO the
    core SaveVideo writes. With a start image, the empty video
    latent passes through LTXVImgToVideoInplace at strength
    1.0, the i2v subgraph's own wiring; without one the graph
    is the distilled text-to-video example as authored."""
    width, height = LANE_DIMS[LTX_LANE][aspect]
    workflow = {
        "1": {
            "class_type": "LTXV25ModelsLoader",
            "inputs": {
                "unet_name": LTX_UNET_NAME,
                "clip_name": LTX_CLIP_NAME,
                "video_vae_name": LTX_VIDEO_VAE_NAME,
                "audio_vae_name": LTX_AUDIO_VAE_NAME,
            },
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": prompt},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": negative},
        },
        "4": {
            "class_type": "LTXVConditioning",
            "inputs": {
                "positive": ["2", 0],
                "negative": ["3", 0],
                "frame_rate": LTX_FPS,
            },
        },
        "5": {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {
                "width": width,
                "height": height,
                "length": LTX_FRAMES,
                "batch_size": 1,
            },
        },
        "7": {
            "class_type": "LTXVEmptyLatentAudio",
            "inputs": {
                "frames_number": LTX_FRAMES,
                "frame_rate": int(LTX_FPS),
                "audio_vae": ["1", 3],
                "batch_size": 1,
            },
        },
        "8": {
            "class_type": "LTXVConcatAVLatent",
            "inputs": {
                "video_latent": ["5", 0],
                "audio_latent": ["7", 0],
            },
        },
        "10": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["4", 1],
                "latent_image": ["8", 0],
                "seed": seed,
                "steps": LTX_STEPS,
                "cfg": LTX_CFG,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "11": {
            "class_type": "LTXV25AVDecode",
            "inputs": {
                "latent": ["10", 0],
                "vae": ["1", 2],
                "audio_vae": ["1", 3],
                "fps": LTX_FPS,
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 64,
                "temporal_overlap": 16,
            },
        },
        "12": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["11", 0],
                "filename_prefix": prefix or f"sinclip/api_{LTX_LANE}",
                "format": "auto",
                "codec": "auto",
            },
        },
    }
    if not text_only:
        workflow["9"] = {
            "class_type": "LoadImage",
            "inputs": {"image": "PLACEHOLDER_START_IMAGE"},
        }
        workflow["6"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["1", 2],
                "image": ["9", 0],
                "latent": ["5", 0],
                "strength": 1.0,
            },
        }
        workflow["8"]["inputs"]["video_latent"] = ["6", 0]
    return workflow


def _build_lane_workflow(lane: str, prompt: str, negative: str, seed: int,
                         aspect: str = "9:16", text_only: bool = False,
                         prefix: str = ""):
    if lane == NSFW_LANE:
        # The community NSFW LoRA is driven by its trigger token,
        # prepended here, server side, for this lane only. The
        # caller's prompt text is never altered on any other lane,
        # and the job record keeps the caller's own words.
        prompt = f"{NSFW_TRIGGER}, {prompt}" if prompt else NSFW_TRIGGER
    workflow = json.loads(
        (Path("/workflows") / LANE_WORKFLOWS[lane]).read_text()
    )
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
                # The 5B lane is a text-and-image-to-video model:
                # with no start image wired in, the same node runs
                # its text-to-video path.
                inputs.pop("start_image", None)
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix
    if lane == NSFW_LANE:
        # Patch the LoRA pair into the stock graph exactly where
        # the distilled template carries its LightX2V pair: one
        # LoraLoaderModelOnly per UNET at strength 1.0 (the
        # workflow's existing LoRA convention), with each
        # ModelSamplingSD3 rewired through its expert's LoRA. The
        # sampler stays exactly as authored (20 steps, cfg 3.5,
        # 10/10 split): the standard sampler path for the base
        # model this LoRA was trained on.
        workflow["17"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": NSFW_LORA_HIGH,
                "model": ["1", 0], "strength_model": 1.0,
            },
        }
        workflow["18"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": NSFW_LORA_LOW,
                "model": ["2", 0], "strength_model": 1.0,
            },
        }
        for node in workflow.values():
            if node.get("class_type") == "ModelSamplingSD3":
                src = node.get("inputs", {}).get("model")
                if src == ["1", 0]:
                    node["inputs"]["model"] = ["17", 0]
                elif src == ["2", 0]:
                    node["inputs"]["model"] = ["18", 0]
    if text_only:
        for nid in [k for k, n in workflow.items()
                    if n.get("class_type") == "LoadImage"]:
            del workflow[nid]
    return workflow


def _build_firstlast_workflow(prompt: str, negative: str, seed: int,
                              aspect: str = "9:16", prefix: str = ""):
    """First frame / last frame segment on the production model set,
    with the lane's distilled recipe applied (the same LightX2V LoRA
    pair and 4-step split the production lane renders with), so an
    end-frame segment costs what a production segment costs. The
    end image conditions the render through
    WanFirstLastFrameToVideo; it is never stored and ignored."""
    workflow = json.loads(
        (Path("/workflows") / "firstlast_chain.json").read_text()
    )
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
            # Node 12 wraps the high-noise UNET (1), node 13 the low (2).
            inputs["model"] = ["19", 0] if inputs.get("model") == ["1", 0] else ["20", 0]
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


# Fun Camera conditioning (PARITY-PROGRAM Pass 4): the Wan 2.2 Fun
# Camera UNET pair takes the camera move as an embedding
# (WanCameraEmbedding.camera_pose), not prompt text. The nine poses
# are the values the preset library's fun-* entries patch in.
FUN_CAMERA_POSES = (
    "Static", "Pan Up", "Pan Down", "Pan Left", "Pan Right",
    "Zoom In", "Zoom Out", "Anti Clockwise (ACW)", "ClockWise (CW)",
)


FUN_CAMERA_RECIPES = ("distilled", "stock", "mid")


def _build_fun_camera_workflow(prompt: str, negative: str, seed: int,
                               camera_pose: str, camera_speed: float = 1.0,
                               aspect: str = "9:16", prefix: str = "",
                               camera_recipe: str = "distilled"):
    """One Fun Camera segment: workflows/fun_camera_i2v.json with the
    chosen pose and speed patched into WanCameraEmbedding.

    Recipes (finish run, 2026-11-16 — the run-18 measurement showed
    the distilled recipe washing the camera conditioning out, so
    the recipe is now a job parameter while the lane's recipe is
    re-decided on evidence):
      distilled: the LightX2V patch — LoRA pair at strength 1.0,
        4-step split (2 + 2) at cfg 1.0. Cheap (~$0.10) but the
        run-18 proof render showed NO measurable camera move.
      stock: the workflow exactly as authored — LoRA pair left at
        strength 0.0, 20 steps at cfg 3.5, split 0-10 / 10-end.
        The reference recipe the Fun Camera UNETs were built for
        (~$0.77 a segment at the long recipe's measured rate).
      mid: LoRA pair at 0.5, 10 steps (5 + 5) at cfg 2.0 — the
        compromise candidate: some guidance headroom and some
        distill speed."""
    if camera_recipe not in FUN_CAMERA_RECIPES:
        raise ValueError(f"unknown camera_recipe: {camera_recipe}")
    workflow = json.loads(
        (Path("/workflows") / "fun_camera_i2v.json").read_text()
    )
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
            # stock: leave the workflow's authored 0.0 in place
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
            # stock: leave steps/cfg/split exactly as authored
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix or "sinclip/api_funcamera"
    return workflow


# ---------------------------------------------------------------------------
# Effects (PARITY-PROGRAM Pass 9, pulled forward): one-tap effect
# LoRAs on the Wan 2.1 I2V 480p 14B base, ported from the Freefield
# effects app (MIT; vendor/freefield/apps/effects). The LoRAs are
# Wan 2.1-only, so this lane has its own base model; it cannot ride
# the Wan 2.2 lanes. Each entry: the LoRA file staged in the Volume
# and the prompt template from the LoRA's model card (trigger words
# included), with {subject} filled from the job's prompt.
#
# Recipe: the app's own fast preset, the distilled precedent from
# the production and Fun Camera lanes. The stock recipe is 20 steps
# at cfg 6 (the app's sampler default); the fast preset raises the
# LightX2V 2.1 step-distill LoRA from strength 0.0 to 1.0 and runs
# 6 steps of euler at cfg 1.0. The app's own manifest warns the
# effect can come out weaker on the fast preset; that is measured
# and reported, not hidden.
# ---------------------------------------------------------------------------

EFFECTS = {
    "angry": ("angry_face_5_epochs.safetensors",
              "The video shows {subject} with a neutral expression. The expression changes to 4ngr23 angry face, and they start yelling, then throw their arms up while making the 4ngr23 angry face."),
    "crying": ("crying_20_epochs.safetensors",
               "The video starts with {subject}, appearing with a sad expression. Then a tear rolls down their cheek, as they are cr471ng crying."),
    "laughing": ("laughing_15_epochs.safetensors",
                 "The video shows {subject}, who starts by looking into the camera and smiling, and then starts l4a6ing laughing, still l4a6ing laughing at the end."),
    "puppies": ("puppy_50_epochs.safetensors",
                "The video begins with a close-up portrait of {subject}. The background changes and then the pu11y puppy effect begins. They are now surrounded by many puppies and pet the puppies."),
    "steam": ("wan21_penqi_000003500.safetensors",
              "p3n91\uff0c\u9f3b\u5b54\u548c\u8033\u6735\u5192\u51fa\u767d\u70df\u3002The video shows {subject}, who gets angry as white steam shoots out of their nostrils and ears."),
    "money-rain": ("money_000003000.safetensors",
                   "\u98d8\u843d\u7684\u7f8e\u949e\u3002The video shows {subject}, smiling as US dollar bills fall from above all around them."),
    "cakeify": ("cakeify_16_epochs.safetensors",
                "The video opens on {subject}. A knife, held by a hand, is coming into frame and hovering over it. The knife then begins cutting into it to c4k3 cakeify it. As the knife slices it open, the inside is revealed to be cake with chocolate layers. The knife cuts through and the contents are revealed."),
    "crush": ("crush_20_epochs.safetensors",
              "The video begins with {subject}. A hydraulic press positioned above slowly descends towards it. Upon contact, the hydraulic press c5us4 crushes it, deforming and flattening it, causing it to collapse inward until it is no longer recognizable."),
    "deflate": ("deflate_20_epochs.safetensors",
                "The video opens with {subject}. As the video progresses, it begins to d3d1at3 deflate it, gradually shrinking and losing shape, eventually flattening completely into a lifeless, deflated mass on the ground."),
    "electrify": ("electrify_50_epochs.safetensors",
                  "The video shows {subject}. The e13c7r1c electricity effect streaks through the scene: bright electric arcs crackle around it, each strike lighting it up for a brief moment."),
    "explode": ("explode_30_epochs.safetensors",
                "The video opens with {subject}. Then it 3xp105ion huge explosion that engulfs the entire screen with a bright light. The light is so bright that it obscures everything around."),
    "inflate": ("inflate_20_epochs.safetensors",
                "The video shows {subject}, then infl4t3 inflates it, expanding into a giant, round, inflated figure."),
    "rotate": ("rotate_20_epochs.safetensors",
               "The video shows {subject}. It performs a r0t4tion 360 degrees rotation."),
    "squish": ("squish_18.safetensors",
               "In the video, {subject} is presented, held in a person's hands. The person then presses on it, causing a sq41sh squish effect. The person keeps pressing down on it, further showing the sq41sh squish effect."),
    "orbit-360": ("360_Orbit.safetensors",
                  "The video shows {subject}. The camera performs an 0rb4it 360 degree orbit, circling around and showing the surroundings, before returning to the front view. Throughout the 0rb4it 360 degree orbit, the main subject stays in place."),
    "arc-shot": ("Arc_shot.safetensors",
                 "The video shows {subject}. The 34Ar2c arc the camera moves in a smooth curve around the main subject, shifting the perspective around it."),
    "bullet-time": ("Wan21_I2V_rotation_000003750.safetensors",
                    "rw360xz\uff0c\u4eba\u7269\u5b9a\u5728\u7a7a\u4e2d\uff0c\u76f8\u673a\u89c6\u89d2\u56f4\u7ed5\u4eba\u7269\u5411\u4e00\u4e2a\u65b9\u5411\u65cb\u8f6c\u3002The video shows {subject}, frozen in mid-air while the camera circles around them in one direction."),
    "car-chase": ("car_chase.safetensors",
                  "The video depicts a dramatic c4r ch4s3 car chase. {subject} speeds along the road, followed closely by a pursuing car, while a helicopter shines a spotlight from above. The camera tracks the pursuit, panning as the car drifts into a turn, emphasizing the ongoing c4r ch4s3 car chase."),
    "crane-down": ("crane_down.safetensors",
                   "The video shows {subject}. The cr4n3 crane down camera motion begins high above the scene, slowly lowering the view. The camera continues to move downwards, getting closer."),
    "crane-over-head": ("crane_overhead.safetensors",
                        "The video shows {subject}. The camera begins a cr4n3 crane over the head movement, rising smoothly upwards. As the cr4n3 crane over the head movement progresses, the camera passes overhead. The cr4n3 crane over the head movement concludes with a high-angle shot looking down."),
    "crane-up": ("crane_up.safetensors",
                 "The video shows {subject}. The cr4n3 crane up effect is applied, smoothly lifting the camera's viewpoint higher, revealing more of the surrounding landscape."),
    "crash-zoom-in": ("crash_zoom_in.safetensors",
                      "The video shows {subject}. The camera performs a cr34sh crash zoom in effect, rapidly zooming closer to the face."),
    "dolly-zoom": ("wan_21_i2v_dolly_zoom.safetensors",
                   "dolly zoom. The video shows {subject}. The camera moves toward them while zooming out: they stay the same size and the background behind them stretches away."),
    "hero-run": ("Hero_run.safetensors",
                 "The video shows {subject}, poised in a starting position and facing the camera. Then they explode from the starting position, beginning the 4ct3ion Action Run directly towards the camera, pumping their arms."),
}

# Native render dimensions for the effects lane, from the effects
# app's own format options (480p class in every aspect).
EFFECTS_DIMS = {"9:16": (480, 832), "16:9": (832, 480), "1:1": (624, 624)}


def _build_effects_workflow(subject: str, negative: str, seed: int,
                            effect: str, aspect: str = "9:16",
                            prefix: str = ""):
    """One effects segment: workflows/effects_i2v.json (built from
    the Freefield effects app definition) with the chosen effect's
    LoRA and prompt template patched in, running the app's fast
    preset (LightX2V step-distill LoRA at strength 1.0, 6 steps,
    euler, cfg 1.0). The terminal node is the house VHS combine the
    other lanes use, so output handling is identical."""
    lora_name, template = EFFECTS[effect]
    workflow = json.loads(
        (Path("/workflows") / "effects_i2v.json").read_text()
    )
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
                # The step-distill LoRA: the fast preset raises it
                # from the template's no-op 0.0 to 1.0.
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


@app.function(image=api_image, timeout=3600)
def run_job(job_id: str, lane: str, prompt: str, negative: str, seed: int,
            start_image_b64: str = "", end_image_b64: str = "",
            aspect: str = "9:16", segments: int = 1,
            camera_pose: str = "", camera_speed: float = 1.0,
            effect: str = "", camera_recipe: str = "distilled"):
    """Dispatcher: build the lane workflow, run it on the ComfyUI class,
    persist the result into the Volume, and record the outcome in the
    jobs Dict. Spawned by the web endpoint; never called synchronously.

    Chaining (segments > 1): segment 1 renders from the job's start
    image (or text only on the draft lane); each later segment starts
    from the previous segment's last frame, extracted here, and the
    final segment steers to the job's end image when one was given
    (production lane, firstlast workflow). Segments are stitched with
    the shared boundary frame dropped from every later segment (the
    seam shape proven in BUILD-LOG run 3). If a segment fails after
    at least one completed, the completed segments are stitched and
    delivered as a partial job (status done, partial=true) instead of
    discarding real rendered work; segment 1 failing is a plain error.
    """
    import base64
    import subprocess
    import tempfile

    jobs[job_id] = {**(jobs.get(job_id) or {}), "status": "running",
                    "segments_total": segments, "segments_done": 0}
    render_started = time.time()
    workdir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))
    try:
        start_bytes = base64.b64decode(start_image_b64) if start_image_b64 else None
        end_bytes = base64.b64decode(end_image_b64) if end_image_b64 else None
        seg_paths = []
        failure = None
        for i in range(segments):
            is_last = i == segments - 1
            use_firstlast = bool(end_bytes) and is_last and lane == "production"
            prefix = f"sinclip/api_{lane}_{job_id}_seg{i + 1}"
            seg_seed = seed + i
            try:
                if effect:
                    workflow = _build_effects_workflow(
                        prompt, negative, seg_seed, effect, aspect, prefix)
                    jobs[job_id] = {
                        **(jobs.get(job_id) or {}),
                        "effect_prompt": EFFECTS[effect][1].replace(
                            "{subject}", prompt),
                    }
                    image_name = f"job_{job_id}_seg{i + 1}.png"
                    for node in workflow.values():
                        if node.get("class_type") == "LoadImage":
                            node["inputs"]["image"] = image_name
                    video = ComfyUI().run_workflow.remote(
                        workflow, image_bytes=start_bytes,
                        image_name=image_name)
                elif camera_pose:
                    workflow = _build_fun_camera_workflow(
                        prompt, negative, seg_seed, camera_pose,
                        camera_speed, aspect, prefix, camera_recipe)
                    image_name = f"job_{job_id}_seg{i + 1}.png"
                    for node in workflow.values():
                        if node.get("class_type") == "LoadImage":
                            node["inputs"]["image"] = image_name
                    video = ComfyUI().run_workflow.remote(
                        workflow, image_bytes=start_bytes,
                        image_name=image_name)
                elif use_firstlast:
                    workflow = _build_firstlast_workflow(
                        prompt, negative, seg_seed, aspect, prefix)
                    start_name = f"job_{job_id}_seg{i + 1}_start.png"
                    end_name = f"job_{job_id}_seg{i + 1}_end.png"
                    for node in workflow.values():
                        if node.get("class_type") == "LoadImage":
                            if "PLACEHOLDER_START_IMAGE" in str(node["inputs"].get("image", "")):
                                node["inputs"]["image"] = start_name
                            elif "PLACEHOLDER_END_IMAGE" in str(node["inputs"].get("image", "")):
                                node["inputs"]["image"] = end_name
                    video = ComfyUI().run_workflow.remote(
                        workflow,
                        images={start_name: start_bytes, end_name: end_bytes},
                    )
                elif lane == LTX_LANE:
                    # LTX-2.5: one clip per job (the endpoint
                    # refuses segments above 1), text-to-video or
                    # image-to-video by whether a start image
                    # came with the job. A caller negative wins;
                    # the house default is Wan-shaped, so this
                    # lane falls back to the model's own example
                    # negative instead.
                    text_only = start_bytes is None
                    workflow = _build_ltx_workflow(
                        prompt,
                        negative if negative != NEGATIVE_PROMPT
                        else LTX_NEGATIVE,
                        seg_seed, aspect,
                        text_only=text_only, prefix=prefix)
                    if text_only:
                        video = ComfyUI().run_workflow.remote(workflow)
                    else:
                        image_name = f"job_{job_id}_seg{i + 1}.png"
                        for node in workflow.values():
                            if node.get("class_type") == "LoadImage":
                                node["inputs"]["image"] = image_name
                        video = ComfyUI().run_workflow.remote(
                            workflow, image_bytes=start_bytes,
                            image_name=image_name)
                else:
                    text_only = start_bytes is None
                    workflow = _build_lane_workflow(
                        lane, prompt, negative, seg_seed, aspect,
                        text_only=text_only, prefix=prefix)
                    if text_only:
                        video = ComfyUI().run_workflow.remote(workflow)
                    else:
                        image_name = f"job_{job_id}_seg{i + 1}.png"
                        for node in workflow.values():
                            if node.get("class_type") == "LoadImage":
                                node["inputs"]["image"] = image_name
                        video = ComfyUI().run_workflow.remote(
                            workflow, image_bytes=start_bytes,
                            image_name=image_name)
            except Exception as exc:  # noqa: BLE001 - partial delivery below
                failure = exc
                break
            seg_path = workdir / f"seg{i + 1}.mp4"
            seg_path.write_bytes(video)
            seg_paths.append(seg_path)
            jobs[job_id] = {**(jobs.get(job_id) or {}),
                            "segments_done": i + 1}
            if not is_last:
                # The next segment continues from this one's last frame.
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
            # Stitch: drop the first (shared boundary) frame of every
            # segment after the first, then concat identical encodes.
            parts = [seg_paths[0]]
            for idx, seg_path in enumerate(seg_paths[1:], start=2):
                trimmed = workdir / f"seg{idx}_trim.mp4"
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(seg_path),
                     "-vf", "select='gte(n\,1)'", "-vsync", "vfr",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                     "-an", str(trimmed)],
                    capture_output=True, text=True, timeout=600)
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"stitch trim failed: {proc.stderr[-200:]}")
                parts.append(trimmed)
            listfile = workdir / "concat.txt"
            listfile.write_text(
                "".join(f"file '{p}'\n" for p in parts))
            out_path = workdir / "stitched.mp4"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(listfile), "-c", "copy", str(out_path)],
                capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not out_path.exists():
                # Re-encode fallback if stream copy refuses the parts.
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
        store_job_file.remote(job_id, final)
        record = {
            **(jobs.get(job_id) or {}),
            "status": "done",
            "file": f"api_jobs/{job_id}.mp4",
            "bytes": len(final),
            "segments_done": len(seg_paths),
            "render_seconds": round(time.time() - render_started, 1),
        }
        if failure is not None:
            record["partial"] = True
            record["partial_error"] = f"{type(failure).__name__}: {failure}"[:300]
        jobs[job_id] = record
        return {"job_id": job_id, "status": "done", "bytes": len(final),
                "segments_done": len(seg_paths),
                "partial": bool(failure is not None)}
    except Exception as exc:  # noqa: BLE001 - record any failure for the API
        jobs[job_id] = {
            **(jobs.get(job_id) or {}),
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
        raise


@app.function(image=download_image, volumes={"/models": volume}, timeout=600)
def store_job_file(job_id: str, data: bytes):
    dest_dir = Path("/models/api_jobs")
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"{job_id}.mp4").write_bytes(data)
    volume.commit()
    return len(data)


@app.function(image=download_image, volumes={"/models": volume}, timeout=600)
def read_job_file(job_id: str):
    path = Path("/models/api_jobs") / f"{job_id}.mp4"
    if not path.exists():
        raise FileNotFoundError(f"no file for job {job_id}")
    return path.read_bytes()


@app.function(image=api_image, timeout=3600)
def run_image_job(job_id: str, prompt: str, seed: int, aspect: str = "9:16"):
    """Dispatcher for one start-image job: build the image workflow,
    run it on the ComfyUI class, persist the PNG into the Volume,
    and record the outcome in the jobs Dict. Spawned by the web
    endpoint; never called synchronously. The record carries the
    measured GPU seconds (the remote call wall, cold start included,
    the same convention the video lane benchmarks use) so the
    platform prices the lane from real renders."""
    jobs[job_id] = {**(jobs.get(job_id) or {}), "status": "running"}
    started = time.time()
    try:
        workflow = _build_image_workflow(
            prompt, seed, aspect, prefix=f"sinclip/api_image_{job_id}")
        data, exec_seconds = ComfyUI().run_image_workflow.remote(workflow)
        wall = time.time() - started
        store_image_file.remote(job_id, data)
        jobs[job_id] = {
            **(jobs.get(job_id) or {}),
            "status": "done",
            "file": f"api_jobs/{job_id}.png",
            "bytes": len(data),
            "gpu_seconds": round(wall, 1),
            "exec_seconds": round(float(exec_seconds or 0), 1),
        }
        return {"job_id": job_id, "status": "done", "bytes": len(data),
                "gpu_seconds": round(wall, 1)}
    except Exception as exc:  # noqa: BLE001 - record any failure for the API
        jobs[job_id] = {
            **(jobs.get(job_id) or {}),
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "gpu_seconds": round(time.time() - started, 1),
        }
        raise


@app.function(image=download_image, volumes={"/models": volume}, timeout=600)
def store_image_file(job_id: str, data: bytes):
    dest_dir = Path("/models/api_jobs")
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"{job_id}.png").write_bytes(data)
    volume.commit()
    return len(data)


@app.function(image=download_image, volumes={"/models": volume}, timeout=600)
def read_image_file(job_id: str):
    path = Path("/models/api_jobs") / f"{job_id}.png"
    if not path.exists():
        raise FileNotFoundError(f"no image file for job {job_id}")
    return path.read_bytes()


@app.function(image=api_image, secrets=[modal.Secret.from_name("sinclip-api-key")])
@modal.asgi_app()
def api():
    import os

    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse, Response

    web = FastAPI(title="Sinclip generation API")

    def _auth(x_api_key: str):
        if x_api_key != os.environ.get("SINCLIP_API_KEY", ""):
            raise HTTPException(status_code=401, detail="bad or missing API key")

    @web.post("/jobs")
    async def create_job(request: Request, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        body = await request.json()
        lane = body.get("lane", "draft")
        # An effects job carries lane "effects" (or a house lane
        # label) plus its effect id; the effect block below validates
        # the id and rewrites the record's lane to "effects".
        if lane not in LANE_WORKFLOWS and lane != LTX_LANE \
                and not (body.get("effect") and lane == "effects"):
            raise HTTPException(
                status_code=422,
                detail=f"lane must be one of {sorted(LANE_WORKFLOWS) + [LTX_LANE]}")
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=422, detail="prompt is required")
        aspect = body.get("aspect_ratio") or "9:16"
        if aspect not in ASPECT_RATIOS:
            raise HTTPException(
                status_code=422,
                detail=f"aspect_ratio must be one of {list(ASPECT_RATIOS)}")
        try:
            segments = int(body.get("segments") if body.get("segments") is not None else 1)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="segments must be a whole number")
        if segments < 1 or segments > 12:
            raise HTTPException(status_code=422, detail="segments must be between 1 and 12")
        start_image = body.get("start_image_png_b64") or ""
        end_image = body.get("end_image_png_b64") or ""
        if end_image and not start_image:
            raise HTTPException(
                status_code=422, detail="an end image needs a start image")
        if end_image and lane != "production":
            raise HTTPException(
                status_code=422,
                detail="end images run on the production lane only")
        if lane == "production" and not start_image:
            raise HTTPException(
                status_code=422,
                detail="the production lane needs a start image; text-only jobs run on the draft lane")
        if lane == NSFW_LANE and not start_image:
            raise HTTPException(
                status_code=422,
                detail="the nsfw lane needs a start image; it is an image-to-video lane")
        if lane == LTX_LANE:
            # LTX-2.5 renders one clip per job: no chaining
            # (the stitch path drops later segments' audio,
            # and this model's sound is half the point) and no
            # end image. Text-only and start-image jobs both
            # run.
            if segments != 1:
                raise HTTPException(
                    status_code=422,
                    detail="the ltx lane renders one clip per job")
            if end_image:
                raise HTTPException(
                    status_code=422,
                    detail="the ltx lane does not take an end image")
        # Fun Camera conditioning: a camera_pose switches the
        # production render to the Fun Camera workflow. It is a
        # single conditioned move: production lane only, one
        # segment, no end image.
        camera_pose = body.get("camera_pose") or ""
        try:
            camera_speed = float(body.get("camera_speed") or 1.0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="camera_speed must be a number")
        if camera_pose:
            if camera_pose not in FUN_CAMERA_POSES:
                raise HTTPException(
                    status_code=422,
                    detail=f"camera_pose must be one of {list(FUN_CAMERA_POSES)}")
            if lane != "production":
                raise HTTPException(
                    status_code=422,
                    detail="camera conditioning runs on the production lane only")
            if segments != 1:
                raise HTTPException(
                    status_code=422,
                    detail="camera conditioning renders one segment at a time")
            if end_image:
                raise HTTPException(
                    status_code=422,
                    detail="camera conditioning does not take an end image")
            if not 0.1 <= camera_speed <= 3.0:
                raise HTTPException(
                    status_code=422, detail="camera_speed must be between 0.1 and 3.0")
        camera_recipe = body.get("camera_recipe") or "distilled"
        if camera_recipe not in FUN_CAMERA_RECIPES:
            raise HTTPException(
                status_code=422,
                detail=f"camera_recipe must be one of {list(FUN_CAMERA_RECIPES)}")
        if camera_recipe != "distilled" and not camera_pose:
            raise HTTPException(
                status_code=422,
                detail="camera_recipe applies to camera-conditioned jobs only")
        # Effects: an effect id switches the render to the Wan 2.1
        # effects workflow, whatever lane label the caller sent (the
        # record's lane becomes "effects"). One effect per job, a
        # start image is required, one segment, and it does not
        # combine with camera conditioning or an end image.
        effect = body.get("effect") or ""
        if effect:
            if effect not in EFFECTS:
                raise HTTPException(
                    status_code=422,
                    detail=f"effect must be one of {sorted(EFFECTS)}")
            if not start_image:
                raise HTTPException(
                    status_code=422,
                    detail="effects need a start image")
            if segments != 1:
                raise HTTPException(
                    status_code=422,
                    detail="effects render one segment at a time")
            if end_image:
                raise HTTPException(
                    status_code=422,
                    detail="effects do not take an end image")
            if camera_pose:
                raise HTTPException(
                    status_code=422,
                    detail="effects do not combine with camera conditioning")
            lane = "effects"
        job_id = uuid.uuid4().hex[:16]
        record = {
            "job_id": job_id,
            "lane": lane,
            "prompt": prompt,
            "status": "queued",
            "created_at": time.time(),
            "aspect_ratio": aspect,
            "segments_total": segments,
            "segments_done": 0,
            "camera_pose": camera_pose or None,
            "camera_speed": camera_speed if camera_pose else None,
            "camera_recipe": camera_recipe if camera_pose else None,
            "effect": effect or None,
        }
        jobs[job_id] = record
        run_job.spawn(
            job_id,
            lane,
            prompt,
            body.get("negative_prompt") or NEGATIVE_PROMPT,
            int(body.get("seed") or SEED),
            start_image,
            end_image,
            aspect,
            segments,
            camera_pose,
            camera_speed,
            effect,
            camera_recipe,
        )
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    @web.get("/jobs/{job_id}")
    def get_job(job_id: str, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        record = jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown job_id")
        return {k: record.get(k) for k in
                ("job_id", "lane", "prompt", "status", "error", "bytes",
                 "created_at", "aspect_ratio", "segments_total",
                 "segments_done", "partial", "partial_error",
                 "camera_pose", "camera_speed", "camera_recipe",
                 "render_seconds", "duration_seconds",
                 "delivered_seconds",
                 "effect", "effect_prompt")}

    @web.get("/jobs/{job_id}/file")
    def get_job_file(job_id: str, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        record = jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown job_id")
        if record.get("status") != "done":
            raise HTTPException(status_code=409,
                                detail=f"job status is {record.get('status')}")
        data = read_job_file.remote(job_id)
        return Response(content=data, media_type="video/mp4")

    @web.post("/image-jobs")
    async def create_image_job(request: Request, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        body = await request.json()
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=422, detail="prompt is required")
        aspect = body.get("aspect_ratio") or "9:16"
        if aspect not in ASPECT_RATIOS:
            raise HTTPException(
                status_code=422,
                detail=f"aspect_ratio must be one of {list(ASPECT_RATIOS)}")
        job_id = uuid.uuid4().hex[:16]
        record = {
            "job_id": job_id,
            "lane": "image",
            "prompt": prompt,
            "status": "queued",
            "created_at": time.time(),
            "aspect_ratio": aspect,
        }
        jobs[job_id] = record
        run_image_job.spawn(
            job_id, prompt, int(body.get("seed") or SEED), aspect)
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    @web.get("/image-jobs/{job_id}")
    def get_image_job(job_id: str, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        record = jobs.get(job_id)
        if record is None or record.get("lane") != "image":
            raise HTTPException(status_code=404, detail="unknown job_id")
        return {k: record.get(k) for k in
                ("job_id", "lane", "prompt", "status", "error", "bytes",
                 "created_at", "aspect_ratio", "gpu_seconds", "exec_seconds")}

    @web.get("/image-jobs/{job_id}/file")
    def get_image_job_file(job_id: str, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        record = jobs.get(job_id)
        if record is None or record.get("lane") != "image":
            raise HTTPException(status_code=404, detail="unknown job_id")
        if record.get("status") != "done":
            raise HTTPException(status_code=409,
                                detail=f"job status is {record.get('status')}")
        data = read_image_file.remote(job_id)
        return Response(content=data, media_type="image/png")

    return web


# ---------------------------------------------------------------------------
# Local entrypoint
# ---------------------------------------------------------------------------

WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "draft_i2v_5b.json"
TEST_IMAGE_PATH = Path(__file__).parent / "test_start.png"
OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "first-draft-test.mp4"

PROD_WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "prod_i2v_a14b.json"
PROD_IMAGE_PATH = Path(__file__).parent / "test_start_720.png"
PROD_OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "first-prod-test.mp4"

DISTILLED_WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "prod_i2v_a14b_distilled.json"
DISTILLED_OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "prod-distilled-test.mp4"

CHAIN_WORKFLOW_PATH = Path(__file__).parent.parent / "workflows" / "firstlast_chain.json"
CHAIN_START_PATH = Path(__file__).parent / "chain_seg1_last.png"
CHAIN_END_PATH = Path(__file__).parent / "chain_seg2_end.png"
CHAIN_OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "chain-seg2-raw.mp4"
POST_OUTPUT_PATH = Path(__file__).parent.parent / "outputs" / "postchain-raw.mp4"

POSITIVE_PROMPT = (
    "a red hot air balloon slowly rising into the sky over rolling green "
    "hills, white clouds drifting across the sky, gentle forward camera "
    "movement, bright daylight, smooth motion"
)
NEGATIVE_PROMPT = (
    "blurry, distorted face, extra fingers, morphing, flicker, text, watermark"
)
SEED = 12345


@app.local_entrypoint()
def main(mode: str = "render"):
    if mode == "download":
        sizes = download_models.remote()
        for name, size in sizes.items():
            print(f"{name}: {size / 1e9:.2f} GB")
        print(f"total: {sum(sizes.values()) / 1e9:.2f} GB")
        return

    if mode == "fetch":
        video_bytes = fetch_output.remote("first_draft_test_00001.mp4")
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {OUTPUT_PATH} ({len(video_bytes)} bytes) via volume fetch")
        return

    if mode == "download-prod":
        sizes = download_prod_models.remote()
        ok = True
        for name, size in sizes.items():
            expected = PROD_EXPECTED_SIZES.get(name)
            match = "OK" if size == expected else f"MISMATCH (expected {expected})"
            if size != expected:
                ok = False
            print(f"{name}: {size} bytes [{match}]")
        print(f"total prod set: {sum(sizes.values()) / 1e9:.2f} GB; sizes verified: {ok}")
        return

    if mode == "fetch-prod":
        video_bytes = fetch_output.remote("first_prod_test_00001.mp4")
        PROD_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROD_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {PROD_OUTPUT_PATH} ({len(video_bytes)} bytes) via volume fetch")
        return

    if mode == "download-effects-distill":
        sizes = download_effects_distill.remote()
        for name, size in sizes.items():
            print(f"{name}: {size} bytes (expected {EFFECTS_DISTILL_SIZE}, "
                  f"match={size == EFFECTS_DISTILL_SIZE})")
        return

    if mode == "download-seedvr2":
        sizes = download_seedvr2.remote()
        for name, size in sizes.items():
            expected = SEEDVR2_FILES.get(name)
            print(f"{name}: {size} bytes (expected {expected}, match={size == expected})")
        return

    if mode == "fetch-chain":
        video_bytes = fetch_output.remote("chain_seg2_00001.mp4")
        CHAIN_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHAIN_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {CHAIN_OUTPUT_PATH} ({len(video_bytes)} bytes) via volume fetch")
        return

    if mode == "fetch-post":
        video_bytes = fetch_output.remote("post_prod_00001.mp4")
        POST_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        POST_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {POST_OUTPUT_PATH} ({len(video_bytes)} bytes) via volume fetch")
        return

    if mode == "render-chain2":
        workflow = json.loads(CHAIN_WORKFLOW_PATH.read_text())
        for node in workflow.values():
            ct = node.get("class_type")
            inputs = node.get("inputs", {})
            if ct == "LoadImage":
                if inputs.get("image") == "PLACEHOLDER_START_IMAGE.png":
                    inputs["image"] = CHAIN_START_PATH.name
                elif inputs.get("image") == "PLACEHOLDER_END_IMAGE.png":
                    inputs["image"] = CHAIN_END_PATH.name
            elif ct == "CLIPTextEncode":
                if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                    inputs["text"] = POSITIVE_PROMPT
                elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                    inputs["text"] = NEGATIVE_PROMPT
            elif ct == "KSamplerAdvanced":
                inputs["noise_seed"] = SEED + 1
            elif ct == "VHS_VideoCombine":
                inputs["filename_prefix"] = "sinclip/chain_seg2"

        images = {
            CHAIN_START_PATH.name: CHAIN_START_PATH.read_bytes(),
            CHAIN_END_PATH.name: CHAIN_END_PATH.read_bytes(),
        }
        video_bytes = ComfyUI().run_workflow.remote(workflow, images=images)
        CHAIN_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHAIN_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {CHAIN_OUTPUT_PATH} ({len(video_bytes)} bytes)")
        return

    if mode == "render-post":
        workflow = json.loads(PROD_WORKFLOW_PATH.read_text())
        for node in workflow.values():
            ct = node.get("class_type")
            inputs = node.get("inputs", {})
            if ct == "LoadImage":
                inputs["image"] = PROD_IMAGE_PATH.name
            elif ct == "CLIPTextEncode":
                if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                    inputs["text"] = POSITIVE_PROMPT
                elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                    inputs["text"] = NEGATIVE_PROMPT
            elif ct == "KSamplerAdvanced":
                inputs["noise_seed"] = SEED
            elif ct == "VHS_VideoCombine":
                inputs["filename_prefix"] = "sinclip/post_prod"
                inputs["images"] = ["20", 0]
                inputs["frame_rate"] = 32.0
        # Post chain: VAEDecode (15) -> RIFE VFI (17) -> SeedVR2 (20) -> VHS (16)
        workflow["17"] = {
            "class_type": "RIFE VFI",
            "inputs": {
                "frames": ["15", 0],
                "clear_cache_after_n_frames": 10,
                "multiplier": 2,
                "fast_mode": True,
                "ensemble": False,
                "scale_factor": 1.0,
                "dtype": "float16",
            },
        }
        workflow["18"] = {
            "class_type": "SeedVR2LoadDiTModel",
            "inputs": {
                "model": "seedvr2_ema_3b_fp16.safetensors",
                "device": "cuda:0",
                "blocks_to_swap": 0,
                "swap_io_components": False,
                "offload_device": "none",
                "cache_model": False,
                "attention_mode": "sdpa",
            },
        }
        workflow["19"] = {
            "class_type": "SeedVR2LoadVAEModel",
            "inputs": {
                "model": "ema_vae_fp16.safetensors",
                "device": "cuda:0",
                "encode_tiled": False,
                "decode_tiled": False,
                "offload_device": "none",
            },
        }
        workflow["20"] = {
            "class_type": "SeedVR2VideoUpscaler",
            "inputs": {
                "image": ["17", 0],
                "dit": ["18", 0],
                "vae": ["19", 0],
                "seed": 42,
                "resolution": 1080,
                "max_resolution": 0,
                "batch_size": 21,
                "temporal_overlap": 0,
            },
        }
        image_bytes = PROD_IMAGE_PATH.read_bytes()
        video_bytes = ComfyUI().run_workflow.remote(
            workflow, image_bytes, PROD_IMAGE_PATH.name
        )
        POST_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        POST_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {POST_OUTPUT_PATH} ({len(video_bytes)} bytes)")
        return

    if mode == "probe-nodes":
        specs = ComfyUI().probe_nodes.remote(
            ["RIFE VFI", "SeedVR2LoadDiTModel", "SeedVR2LoadVAEModel",
             "SeedVR2VideoUpscaler", "VHS_LoadVideo"]
        )
        for ct, spec in specs.items():
            inputs = spec.get("input", {})
            print(f"== {ct} ==")
            print("required:", json.dumps(inputs.get("required", {}))[:1500])
            print("optional:", json.dumps(inputs.get("optional", {}))[:800])
        return

    if mode == "render-post2":
        # Post chain on the ALREADY RENDERED production segment:
        # VHS_LoadVideo -> RIFE VFI (2x) -> SeedVR2 -> VHS_VideoCombine.
        workflow = {
            "1": {
                "class_type": "VHS_LoadVideo",
                "inputs": {
                    "video": "prod_input.mp4",
                    "force_rate": 0,
                    "custom_width": 0,
                    "custom_height": 0,
                    "frame_load_cap": 0,
                    "skip_first_frames": 0,
                    "select_every_nth": 1,
                },
            },
            "17": {
                "class_type": "RIFE VFI",
                "inputs": {
                    "ckpt_name": "rife49.pth",
                    "frames": ["1", 0],
                    "clear_cache_after_n_frames": 10,
                    "multiplier": 2,
                    "fast_mode": True,
                    "ensemble": False,
                    "scale_factor": 1.0,
                    "dtype": "float16",
                    "torch_compile": False,
                    "batch_size": 1,
                },
            },
            "18": {
                "class_type": "SeedVR2LoadDiTModel",
                "inputs": {
                    "model": "seedvr2_ema_3b_fp16.safetensors",
                    "device": "cuda:0",
                    "blocks_to_swap": 0,
                    "swap_io_components": False,
                    "offload_device": "none",
                    "cache_model": False,
                    "attention_mode": "sdpa",
                },
            },
            "19": {
                "class_type": "SeedVR2LoadVAEModel",
                "inputs": {
                    "model": "ema_vae_fp16.safetensors",
                    "device": "cuda:0",
                    "encode_tiled": False,
                    "decode_tiled": False,
                    "offload_device": "none",
                },
            },
            "20": {
                "class_type": "SeedVR2VideoUpscaler",
                "inputs": {
                    "image": ["17", 0],
                    "dit": ["18", 0],
                    "vae": ["19", 0],
                    "seed": 42,
                    "resolution": 1080,
                    "max_resolution": 0,
                    "batch_size": 21,
                    "uniform_batch_size": False,
                    "temporal_overlap": 0,
                    "prepend_frames": 0,
                    "color_correction": "lab",
                    "input_noise_scale": 0.0,
                    "latent_noise_scale": 0.0,
                    "offload_device": "cpu",
                    "enable_debug": False,
                },
            },
            "16": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["20", 0],
                    "filename_prefix": "sinclip/post_prod",
                    "format": "video/h264-mp4",
                    "frame_rate": 32.0,
                    "loop_count": 0,
                    "pingpong": False,
                    "save_output": True,
                },
            },
        }
        video_bytes_in = PROD_OUTPUT_PATH.read_bytes()
        video_bytes = ComfyUI().run_workflow.remote(
            workflow, images={"prod_input.mp4": video_bytes_in}
        )
        POST_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        POST_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {POST_OUTPUT_PATH} ({len(video_bytes)} bytes)")
        return

    if mode == "download-loras":
        sizes = download_loras.remote()
        for name, size in sizes.items():
            print(f"{name}: {size} bytes")
        print(f"total lora set: {sum(sizes.values()) / 1e9:.2f} GB")
        return

    if mode == "download-nsfw":
        sizes = download_nsfw_lora.remote()
        for name, size in sizes.items():
            print(f"{name}: {size} bytes")
        print(f"total nsfw lora set: {sum(sizes.values()) / 1e9:.2f} GB")
        return

    if mode == "fetch-distilled":
        video_bytes = fetch_output.remote("prod_distilled_test_00001.mp4")
        DISTILLED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DISTILLED_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {DISTILLED_OUTPUT_PATH} ({len(video_bytes)} bytes) via volume fetch")
        return

    if mode == "render-prod-distilled":
        workflow = json.loads(DISTILLED_WORKFLOW_PATH.read_text())
        for node in workflow.values():
            ct = node.get("class_type")
            inputs = node.get("inputs", {})
            if ct == "LoadImage":
                inputs["image"] = PROD_IMAGE_PATH.name
            elif ct == "CLIPTextEncode":
                if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                    inputs["text"] = POSITIVE_PROMPT
                elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                    inputs["text"] = NEGATIVE_PROMPT
            elif ct == "KSamplerAdvanced":
                inputs["noise_seed"] = SEED
            elif ct == "VHS_VideoCombine":
                inputs["filename_prefix"] = "sinclip/prod_distilled_test"

        image_bytes = PROD_IMAGE_PATH.read_bytes()
        t0 = time.time()
        video_bytes = ComfyUI().run_workflow.remote(
            workflow, image_bytes, PROD_IMAGE_PATH.name
        )
        wall = time.time() - t0
        DISTILLED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DISTILLED_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {DISTILLED_OUTPUT_PATH} ({len(video_bytes)} bytes)")
        print(f"WALL_SECONDS={wall:.1f} L40S_COST_USD={wall * 0.000542:.3f}")
        return

    if mode == "render-prod":
        workflow = json.loads(PROD_WORKFLOW_PATH.read_text())
        for node in workflow.values():
            ct = node.get("class_type")
            inputs = node.get("inputs", {})
            if ct == "LoadImage":
                inputs["image"] = PROD_IMAGE_PATH.name
            elif ct == "CLIPTextEncode":
                if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                    inputs["text"] = POSITIVE_PROMPT
                elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                    inputs["text"] = NEGATIVE_PROMPT
            elif ct == "KSamplerAdvanced":
                inputs["noise_seed"] = SEED
            elif ct == "VHS_VideoCombine":
                inputs["filename_prefix"] = "sinclip/first_prod_test"

        image_bytes = PROD_IMAGE_PATH.read_bytes()
        video_bytes = ComfyUI().run_workflow.remote(
            workflow, image_bytes, PROD_IMAGE_PATH.name
        )
        PROD_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROD_OUTPUT_PATH.write_bytes(video_bytes)
        print(f"saved {PROD_OUTPUT_PATH} ({len(video_bytes)} bytes)")
        return

    workflow = json.loads(WORKFLOW_PATH.read_text())
    for node in workflow.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})
        if ct == "LoadImage":
            inputs["image"] = TEST_IMAGE_PATH.name
        elif ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = POSITIVE_PROMPT
            elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                inputs["text"] = NEGATIVE_PROMPT
        elif ct == "KSampler":
            inputs["seed"] = SEED
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = "sinclip/first_draft_test"

    image_bytes = TEST_IMAGE_PATH.read_bytes()
    video_bytes = ComfyUI().run_workflow.remote(
        workflow, image_bytes, TEST_IMAGE_PATH.name
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(video_bytes)
    print(f"saved {OUTPUT_PATH} ({len(video_bytes)} bytes)")
