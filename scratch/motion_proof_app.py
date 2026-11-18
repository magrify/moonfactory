"""Proof probe for the Moonfactory motion wing (2026-11-18).

Attaches to the live moonfactory Dicts and data Volume. Inserts
one animated story job for the owner account in exactly the
record shape POST /api/templates writes for the story template,
then reports the job's state, ffprobes the finished file on the
volume, and can pull a few frames out of it for a look check.
No platform code runs here and nothing is charged anywhere.
The render itself runs on the Kaggle worker, never on Modal.

Run:  modal run scratch/motion_proof_app.py::main --action seed
      modal run scratch/motion_proof_app.py::main --action check --job-id <id>
      modal run scratch/motion_proof_app.py::main --action frames --job-id <id>
"""
import base64
import subprocess
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-motion-proof")
jobs = modal.Dict.from_name("moonfactory-jobs")
users = modal.Dict.from_name("moonfactory-users")
meta = modal.Dict.from_name("moonfactory-meta")
data_volume = modal.Volume.from_name("moonfactory-data")
image = modal.Image.debian_slim(python_version="3.12").apt_install("ffmpeg")

TITLE = "The Miller of Willow Ford"
LINES = [
    "The miller woke before the sun.",
    "A letter had come in the night.",
    "By noon the town was dancing.",
]
# The platform plan for these words (hf_story_plan): a 4.5s
# title scene, line scenes at a reading pace (140, 152 and 140
# frames), a 3.5s closing scene. 672 frames at 30 fps = 22.4s.
DURATION = 22.4


@app.function(image=image, volumes={"/data": data_volume}, timeout=300)
def seed() -> dict:
    owner = None
    for key in list(users.keys()):
        rec = users.get(key)
        if rec and rec.get("is_owner"):
            owner = rec
            break
    if owner is None:
        return {"error": "no owner account found"}
    import secrets as pysecrets

    job = {
        "id": pysecrets.token_hex(8),
        "user_id": owner["id"],
        "worker_job_id": None,
        "provider": "hyperframes",
        "kind": "template",
        "lane": "template",
        "model": "hyperframes",
        "template": "story",
        "template_name": "Animated story short",
        "hf_fields": {
            "title": TITLE,
            "lines": "\n".join(LINES),
            "color": "#e8833a",
        },
        "prompt": TITLE,
        "user_prompt": TITLE,
        "camera_presets": [],
        "camera_preset_names": [],
        "camera_look": {},
        "status": "queued",
        "stage": "Waiting for the free renderer",
        "error": None,
        "created_at": time.time(),
        "charged_cents": 0,
        "upload_path": None,
        "source_duration": 0.0,
        "duration_seconds": DURATION,
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "plan_at_job": owner.get("plan"),
        "free_tier": False,
        "music_prompt": None,
    }
    jobs[job["id"]] = job
    return {"job_id": job["id"], "owner_id": owner["id"]}


@app.function(image=image, volumes={"/data": data_volume}, timeout=300)
def check(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        return {"error": "job not found"}
    out = {
        "status": job.get("status"),
        "stage": job.get("stage"),
        "error": job.get("error"),
        "internal_error": job.get("internal_error"),
        "bytes": job.get("bytes"),
        "duration_seconds": job.get("duration_seconds"),
        "heartbeat_age": (
            round(time.time() - float(meta.get("hf:heartbeat") or 0), 1)
            if meta.get("hf:heartbeat") else None),
    }
    path = Path(f"/data/files/{job_id}.mp4")
    data_volume.reload()
    if path.exists():
        out["file_bytes"] = path.stat().st_size
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-show_entries",
             "stream=codec_name,width,height",
             "-of", "default=noprint_wrappers=1", str(path)],
            capture_output=True, text=True)
        out["ffprobe"] = (probe.stdout or probe.stderr).strip()
    return out


@app.function(image=image, volumes={"/data": data_volume}, timeout=300)
def frames(job_id: str) -> dict:
    path = Path(f"/data/files/{job_id}.mp4")
    data_volume.reload()
    if not path.exists():
        return {"error": "file not on the volume yet"}
    out = {}
    for name, at in (("title", 2.0), ("line", 7.0), ("golden", 16.5),
                     ("outro", 21.0)):
        jpg = f"/tmp/{job_id}-{name}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i",
             str(path), "-frames:v", "1", "-vf", "scale=540:-1", jpg],
            capture_output=True, text=True)
        p = Path(jpg)
        if p.exists():
            out[name] = base64.b64encode(p.read_bytes()).decode()
    return out


@app.local_entrypoint()
def main(action: str = "check", job_id: str = ""):
    if action == "seed":
        print(seed.remote())
    elif action == "frames":
        got = frames.remote(job_id)
        outdir = Path("/home/hatch/workspace/.motion-build/proof-frames")
        outdir.mkdir(parents=True, exist_ok=True)
        for name, b64 in got.items():
            (outdir / f"{name}.jpg").write_bytes(base64.b64decode(b64))
        print("frames written:", sorted(got.keys()))
    else:
        print(check.remote(job_id))
