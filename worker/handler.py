"""
Sinclip RunPod Serverless worker handler (SKELETON, untested).

Job flow:
  1. Receive a job from RunPod Serverless.
  2. Download the job's input image(s) into ComfyUI's input folder.
  3. Load the requested workflow template, patch in the job's values.
  4. Submit to the local ComfyUI API, poll until done.
  5. Return where the output landed.

Assumption: ComfyUI is already running inside this worker container and
listening on 127.0.0.1:8188 (the standard layout for ComfyUI serverless
images). This handler never exposes ComfyUI outside the container.

Job input shape (job["input"]):
  {
    "workflow_id": "prod_i2v_a14b",          # file stem in /workflows
    "prompt": "she turns toward camera...",   # positive / motion prompt
    "negative_prompt": "...",                 # optional, house default used
    "start_image_url": "https://...",         # or "start_image_key" (R2 key)
    "end_image_url": "https://...",           # optional, chain workflow only
    "params": {                               # all optional
      "width": 1280, "height": 720,
      "length": 81, "seed": 12345
    },
    "job_id": "app-side id for output naming"
  }

Returns:
  {"status": "ok", "output": {"filename": ..., "subfolder": ..., "type": "output"}}
  or a dict with "error" set. Uploading the file to R2 and returning a
  durable URL is a TODO below; for the first smoke test the raw ComfyUI
  output reference is enough to prove the pipe end to end.

Nothing in this file has been executed. See ../BUILD-LOG.md.
"""

import copy
import json
import os
import time
import urllib.request
import uuid

import requests

# runpod is imported lazily in main() so this file can be syntax checked
# and unit poked on a machine without the runpod SDK installed.

COMFY_BASE = os.environ.get("COMFY_BASE", "http://127.0.0.1:8188")
COMFY_INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", "/comfyui/input")
WORKFLOW_DIR = os.environ.get("WORKFLOW_DIR", "/workflows")
POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL", "2"))
JOB_TIMEOUT_SECONDS = float(os.environ.get("JOB_TIMEOUT", "1500"))

DEFAULT_NEGATIVE = (
    "blurry, distorted face, extra fingers, morphing, flicker, text, watermark"
)


# --------------------------------------------------------------------------
# ComfyUI API helpers
# --------------------------------------------------------------------------

def wait_for_comfy(timeout=300):
    """Block until the local ComfyUI API answers. Workers cold start, and
    submitting before ComfyUI is up is the classic first-run failure."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{COMFY_BASE}/system_stats", timeout=5)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError("ComfyUI API did not come up in time")


def submit_workflow(workflow):
    """POST /prompt, return the prompt_id."""
    payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
    r = requests.post(f"{COMFY_BASE}/prompt", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("node_errors"):
        raise RuntimeError(f"ComfyUI rejected workflow: {data['node_errors']}")
    return data["prompt_id"]


def poll_until_done(prompt_id, timeout=JOB_TIMEOUT_SECONDS):
    """Poll /history until the prompt finishes. Return its history entry."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=15)
        r.raise_for_status()
        history = r.json()
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI execution error: {status}")
            if entry.get("outputs"):
                return entry
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Job {prompt_id} exceeded {timeout}s")


def find_video_output(history_entry):
    """Walk the outputs for the VHS_VideoCombine result and return the
    first video file reference.

    TODO(verify): VHS output key shape is unverified offline. It is
    expected under outputs[<node>]["gifs"] as a list of
    {filename, subfolder, type}. Confirm on the first live run and
    tighten this walker.
    """
    for node_output in history_entry.get("outputs", {}).values():
        for key in ("gifs", "videos", "images"):
            for item in node_output.get(key, []):
                if item.get("filename", "").endswith((".mp4", ".webm")):
                    return {
                        "filename": item["filename"],
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    }
    raise RuntimeError("No video file found in ComfyUI outputs")


# --------------------------------------------------------------------------
# Workflow patching
# --------------------------------------------------------------------------

def load_workflow(workflow_id):
    path = os.path.join(WORKFLOW_DIR, f"{workflow_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def patch_workflow(workflow, job_input, local_images):
    """Replace template placeholders with this job's values.

    Patching is by class_type, not node id, so node renumbering in the
    templates does not silently break the worker.
    """
    wf = copy.deepcopy(workflow)
    params = job_input.get("params", {})
    seed = params.get("seed", 0)

    for node in wf.values():
        ct = node.get("class_type")
        inputs = node.get("inputs", {})

        if ct == "LoadImage":
            # Templates carry at most two LoadImage nodes: start, then end.
            current = inputs.get("image", "")
            if "END" in current and local_images.get("end"):
                inputs["image"] = local_images["end"]
            elif local_images.get("start"):
                inputs["image"] = local_images["start"]

        elif ct == "CLIPTextEncode":
            if inputs.get("text") == "PLACEHOLDER_POSITIVE_PROMPT":
                inputs["text"] = job_input["prompt"]
            elif inputs.get("text") == "PLACEHOLDER_NEGATIVE_PROMPT":
                inputs["text"] = job_input.get("negative_prompt") or DEFAULT_NEGATIVE

        elif ct in ("WanImageToVideo", "WanFirstLastFrameToVideo"):
            for field in ("width", "height", "length"):
                if field in params:
                    inputs[field] = params[field]

        elif ct == "KSampler":
            inputs["seed"] = seed
        elif ct == "KSamplerAdvanced":
            inputs["noise_seed"] = seed

        elif ct == "VHS_VideoCombine":
            job_id = job_input.get("job_id", "job")
            inputs["filename_prefix"] = f"sinclip/{job_id}"

    return wf


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------

def fetch_image(job_input, kind):
    """Download one input image into ComfyUI's input folder.

    kind is "start" or "end". Accepts a direct URL. R2 key support is a
    TODO: resolve the key to a presigned URL in the app layer for now.
    Returns the local filename ComfyUI's LoadImage should reference.
    """
    url = job_input.get(f"{kind}_image_url")
    if not url:
        return None
    ext = os.path.splitext(urllib.request.urlparse(url).path)[1] or ".png"
    filename = f"{job_input.get('job_id', 'job')}_{kind}{ext}"
    dest = os.path.join(COMFY_INPUT_DIR, filename)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
    return filename


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

def handler(job):
    job_input = job["input"]

    wait_for_comfy()

    local_images = {
        "start": fetch_image(job_input, "start"),
        "end": fetch_image(job_input, "end"),
    }
    if not local_images["start"]:
        return {"error": "start image is required"}

    workflow = load_workflow(job_input["workflow_id"])
    workflow = patch_workflow(workflow, job_input, local_images)

    prompt_id = submit_workflow(workflow)
    history_entry = poll_until_done(prompt_id)
    output = find_video_output(history_entry)

    # TODO(post): run the post chain here or as a follow up job:
    #   1. frame interpolation to 24-32 fps
    #   2. upscale pass
    #   3. color / exposure match (multi segment jobs)
    #   4. audio: music generation + TTS, mixed with ffmpeg
    #   5. stitch segments (chain jobs) with ffmpeg
    # TODO(storage): upload the finished file to R2 and return its key
    # plus a durable URL instead of the raw ComfyUI reference below.

    return {"status": "ok", "prompt_id": prompt_id, "output": output}


def main():
    import runpod

    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
