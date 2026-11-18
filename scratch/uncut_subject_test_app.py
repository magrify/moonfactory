"""Subject test for the Wan 2.2 Uncut lane (2026-11-18).

MODAL SPEND: AUTHORIZED BY JACK'S IN-CHAT CHOICE, Nov 18,
2026 ("Subject test first, then decide") — exactly ONE
worker job on the nsfw lane's distilled_notrigger variant
with a subject scene, expected at the distilled rate of
roughly 12 cents, hard cap 80 cents total. Nothing else is
authorized: the submit action is idempotent through the
state Dict and refuses to create a second job, ever. A
failure is reported, never retried. No comparison render.

The question the balloon proofs could not answer: does
the LoRA's uncensored effect survive on the distilled
no-trigger recipe with real subject matter? The job goes
straight to the worker API with a stock start frame (a
fictional adult woman in her thirties in swimwear at a
beach, Pexels photo 32761957 by Vika Glitter, free
license, prepared at 720x1280 as subject_start_720.png),
a plain customer style prompt asking for the undressed
outcome, seed 12345, 1 segment, 9:16.

Usage:
  modal run uncut_subject_test_app.py --action submit
  modal run uncut_subject_test_app.py --action status
  modal run uncut_subject_test_app.py --action collect
"""

import base64
import json
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-uncut-subject-test")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests", "numpy", "pillow")
    .add_local_file(
        str(Path(__file__).parent / "subject_start_720.png"),
        "/assets/subject_start_720.png",
    )
)

wjobs = modal.Dict.from_name("sinclip-jobs", create_if_missing=True)
state = modal.Dict.from_name(
    "moonfactory-uncut-subject-state", create_if_missing=True)
volume = modal.Volume.from_name("sinclip-models", create_if_missing=False)

WORKER_BASE = "https://magrify--sinclip-comfyui-api.us-east.modal.run"
L40S_USD_PER_SEC = 0.000542  # Modal list rate, same basis as the Nov 16 proofs

SCENE = ("a beautiful woman in her thirties standing on a sunny beach "
         "slowly unties her bikini and takes it off, standing fully "
         "nude and relaxed in the warm sun, gentle waves rolling in "
         "behind her, bright daylight")
SEED = 12345

WORKER_FIELDS = (
    "lane", "status", "error", "bytes", "render_seconds",
    "segments_done", "segments_total", "aspect_ratio", "prompt",
    "nsfw_variant", "partial", "partial_error",
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("sinclip-api-key")],
    timeout=600,
)
def submit() -> dict:
    """Create the ONE authorized test job. Idempotent: once a
    worker job id exists in state, this returns it and never
    posts again."""
    import os

    import requests

    if state.get("worker_job_id"):
        return {"already_submitted": True,
                "worker_job_id": state["worker_job_id"]}
    img = base64.b64encode(
        Path("/assets/subject_start_720.png").read_bytes()).decode()
    r = requests.post(
        f"{WORKER_BASE}/jobs", timeout=60,
        headers={"X-API-Key": os.environ["SINCLIP_API_KEY"]},
        json={
            "lane": "nsfw",
            "nsfw_variant": "distilled_notrigger",
            "prompt": SCENE,
            "seed": SEED,
            "start_image_png_b64": img,
            "segments": 1,
            "aspect_ratio": "9:16",
        })
    assert r.status_code == 202, (r.status_code, r.text[:400])
    job = r.json()
    state["worker_job_id"] = job["job_id"]
    state["submitted_at"] = time.time()
    return {"worker_job_id": job["job_id"], "response": job}


@app.function(image=image, timeout=600)
def status() -> dict:
    """Read-only status from the worker jobs Dict."""
    wid = state.get("worker_job_id")
    if not wid:
        return {"submitted": False}
    rec = wjobs.get(wid) or {}
    out = {
        "worker_job_id": wid,
        "record": {k: rec.get(k) for k in WORKER_FIELDS},
        "elapsed_since_submit": round(
            time.time() - state["submitted_at"], 1),
    }
    if rec.get("render_seconds"):
        out["real_gpu_cost_cents"] = round(
            rec["render_seconds"] * L40S_USD_PER_SEC * 100, 2)
    return out


def _ffprobe(path):
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=120)
    return json.loads(probe.stdout)


@app.function(
    image=image,
    volumes={"/models": volume},
    timeout=1200,
)
def collect() -> dict:
    """Once the worker job is done: copy the raw worker file
    into proof_renders/ as uncut_subject.mp4, probe it, grab
    frames at 0.5s and 4.5s beside the earlier proofs, and
    measure the within-clip motion."""
    import subprocess

    import numpy as np
    from PIL import Image

    wid = state.get("worker_job_id")
    if not wid:
        return {"submitted": False}
    if state.get("collected"):
        return {"already_collected": True, "result": state["collected"]}
    rec = wjobs.get(wid) or {}
    if rec.get("status") != "done":
        return {"ready": False, "status": rec.get("status")}
    outdir = Path("/models/proof_renders")
    src = Path("/models/api_jobs") / f"{wid}.mp4"
    dest = outdir / "uncut_subject.mp4"
    dest.write_bytes(src.read_bytes())
    info = _ffprobe(dest)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    result = {
        "volume_file": "proof_renders/uncut_subject.mp4",
        "bytes": dest.stat().st_size,
        "duration": float(info["format"]["duration"]),
        "width": vstream["width"],
        "height": vstream["height"],
        "codec": vstream["codec_name"],
        "streams": [s["codec_type"] for s in info["streams"]],
        "record": {k: rec.get(k) for k in WORKER_FIELDS},
    }
    if rec.get("render_seconds"):
        result["real_gpu_cost_cents"] = round(
            rec["render_seconds"] * L40S_USD_PER_SEC * 100, 2)
    frames = {}
    for tag, t in (("f0", 0.5), ("f1", 4.5)):
        fp = outdir / f"uncut_subject_{tag}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(dest),
             "-frames:v", "1", str(fp)],
            capture_output=True, timeout=120, check=True)
        frames[tag] = np.asarray(
            Image.open(fp).convert("L")).astype(np.float64)
        result[f"frame_{tag}"] = f"proof_renders/uncut_subject_{tag}.png"
    result["frame_mean_abs_diff"] = round(
        float(np.abs(frames["f0"] - frames["f1"]).mean()), 3)
    state["collected"] = result
    volume.commit()
    return result


@app.local_entrypoint()
def main(action: str):
    if action == "submit":
        print(json.dumps(submit.remote(), indent=1, default=str))
    elif action == "status":
        print(json.dumps(status.remote(), indent=1, default=str))
    elif action == "collect":
        print(json.dumps(collect.remote(), indent=1, default=str))
    else:
        raise SystemExit(f"unknown action {action}")
