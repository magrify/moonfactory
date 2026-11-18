"""Proof probe for the HyperFrames template lane.

Attaches to the live moonfactory Dicts and data Volume. Inserts
one template job for the owner account in exactly the record
shape POST /api/templates writes, then reports the job's state
and ffprobes the finished file on the volume. No platform code
runs here and nothing is charged anywhere.

Run:  modal run scratch/hf_proof_app.py::seed
      modal run scratch/hf_proof_app.py::check --job-id <id>
"""
import subprocess
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-hf-proof")
jobs = modal.Dict.from_name("moonfactory-jobs")
users = modal.Dict.from_name("moonfactory-users")
meta = modal.Dict.from_name("moonfactory-meta")
data_volume = modal.Volume.from_name("moonfactory-data")
image = modal.Image.debian_slim(python_version="3.12").apt_install("ffmpeg")


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
        "template": "title",
        "template_name": "Title card",
        "hf_fields": {
            "headline": "Proof Night",
            "subline": "the free lane works",
            "color": "#6366f1",
        },
        "prompt": "Proof Night",
        "user_prompt": "Proof Night",
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
        "duration_seconds": 6.0,
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "plan_at_job": owner.get("plan"),
        "free_tier": False,
        "music_prompt": None,
    }
    jobs[job["id"]] = job
    return {"job_id": job["id"], "owner_id": owner["id"]}


@app.function(image=image, timeout=120)
def list_template_jobs() -> list:
    out = []
    for key in list(jobs.keys()):
        rec = jobs.get(key)
        if rec and rec.get("kind") == "template":
            out.append({
                "id": rec["id"], "status": rec.get("status"),
                "template": rec.get("template"),
                "created_at": rec.get("created_at"),
            })
    return sorted(out, key=lambda r: r["created_at"] or 0)


@app.local_entrypoint()
def main(action: str = "list", job_id: str = ""):
    if action == "seed":
        print(seed.remote())
    elif action == "check":
        print(check.remote(job_id))
    else:
        print(list_template_jobs.remote())


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
