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
    .pip_install("huggingface_hub")
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
    .pip_install("comfy-cli")
    .run_commands("comfy --skip-prompt install --nvidia")
    .run_commands(
        "git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite"
    )
    .run_commands(
        "pip install -r "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt"
    )
    .run_commands(
        "git clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation"
    )
    .run_commands(
        "pip install -r "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation/requirements.txt "
        "|| true"
    )
    .run_commands(
        "git clone https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler"
    )
    .run_commands(
        "pip install -r "
        "/root/comfy/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler/requirements.txt "
        "|| true"
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
        for category in ("diffusion_models", "text_encoders", "vae", "clip_vision", "SEEDVR2", "loras"):
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
            for key in ("gifs", "videos", "images"):
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
}
LANE_DEFAULT_IMAGES = {
    "draft": "/assets/test_start.png",
    "production": "/assets/test_start_720.png",
}

api_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]")
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
        str(Path(__file__).parent / "test_start.png"),
        "/assets/test_start.png",
    )
    .add_local_file(
        str(Path(__file__).parent / "test_start_720.png"),
        "/assets/test_start_720.png",
    )
)


def _build_lane_workflow(lane: str, prompt: str, negative: str, seed: int):
    workflow = json.loads(
        (Path("/workflows") / LANE_WORKFLOWS[lane]).read_text()
    )
    prefix = f"sinclip/api_{lane}"
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
        elif ct == "VHS_VideoCombine":
            inputs["filename_prefix"] = prefix
    return workflow


@app.function(image=api_image, timeout=3600)
def run_job(job_id: str, lane: str, prompt: str, negative: str, seed: int,
            start_image_b64: str = ""):
    """Dispatcher: build the lane workflow, run it on the ComfyUI class,
    persist the result into the Volume, and record the outcome in the
    jobs Dict. Spawned by the web endpoint; never called synchronously."""
    import base64

    jobs[job_id] = {**(jobs.get(job_id) or {}), "status": "running"}
    try:
        workflow = _build_lane_workflow(lane, prompt, negative, seed)
        if start_image_b64:
            image_bytes = base64.b64decode(start_image_b64)
            image_name = f"job_{job_id}.png"
        else:
            image_bytes = Path(LANE_DEFAULT_IMAGES[lane]).read_bytes()
            image_name = Path(LANE_DEFAULT_IMAGES[lane]).name
        for node in workflow.values():
            if node.get("class_type") == "LoadImage":
                node["inputs"]["image"] = image_name
        video = ComfyUI().run_workflow.remote(
            workflow, image_bytes=image_bytes, image_name=image_name
        )
        # Persist under the job id in the Volume via a tiny volume writer:
        store_job_file.remote(job_id, video)
        jobs[job_id] = {
            **(jobs.get(job_id) or {}),
            "status": "done",
            "file": f"api_jobs/{job_id}.mp4",
            "bytes": len(video),
        }
        return {"job_id": job_id, "status": "done", "bytes": len(video)}
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
        if lane not in LANE_WORKFLOWS:
            raise HTTPException(status_code=422,
                                detail=f"lane must be one of {sorted(LANE_WORKFLOWS)}")
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=422, detail="prompt is required")
        job_id = uuid.uuid4().hex[:16]
        record = {
            "job_id": job_id,
            "lane": lane,
            "prompt": prompt,
            "status": "queued",
            "created_at": time.time(),
        }
        jobs[job_id] = record
        run_job.spawn(
            job_id,
            lane,
            prompt,
            body.get("negative_prompt") or NEGATIVE_PROMPT,
            int(body.get("seed") or SEED),
            body.get("start_image_png_b64") or "",
        )
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    @web.get("/jobs/{job_id}")
    def get_job(job_id: str, x_api_key: str = Header(default="")):
        _auth(x_api_key)
        record = jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown job_id")
        return {k: record.get(k) for k in
                ("job_id", "lane", "prompt", "status", "error", "bytes", "created_at")}

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
