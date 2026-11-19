"""Live proof for the 2026-11-19 Kaggle move: one end to end
job per moved lane (Draft, Production, Uncut, Effects, Fun
Camera, house image), submitted as the owner through the real
platform path (POST /api/jobs and POST /api/images) and read
back from the platform's own records.

MODAL SPEND: NONE AUTHORIZED. Every generation job in this
run executes in a free Kaggle session through the platform's
Kaggle ComfyUI route; nothing here dispatches a Modal
generation job. The harness's own functions only read
records, forge the owner's session with the platform's
SESSION_SECRET (the secret never leaves the Modal function),
and post the creations, the same pattern as
scratch/bundle_proof_app.py.

Every submit is idempotent through the state Dict: a job id,
once recorded, is never re-posted.

Usage:
  modal run scratch/kaggle_move_proof_app.py --action inspect
  modal run scratch/kaggle_move_proof_app.py --action submit --which draft
  modal run scratch/kaggle_move_proof_app.py --action submit --which production
  modal run scratch/kaggle_move_proof_app.py --action submit --which nsfw
  modal run scratch/kaggle_move_proof_app.py --action submit --which effects
  modal run scratch/kaggle_move_proof_app.py --action submit --which camera
  modal run scratch/kaggle_move_proof_app.py --action submit --which image
  modal run scratch/kaggle_move_proof_app.py --action status
  modal run scratch/kaggle_move_proof_app.py --action collect --job-id <id>
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-kaggle-move-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests")
    .add_local_file(
        str(Path(__file__).parent.parent
            / "modal-worker" / "test_start_720.png"),
        "/assets/test_start_720.png",
    )
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
pjobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
state = modal.Dict.from_name("moonfactory-kaggle-move-proof-state",
                             create_if_missing=True)
data_volume = modal.Volume.from_name("moonfactory-data",
                                     create_if_missing=False)

BASE = "https://magrify--moonfactory-api.us-east.modal.run"
OWNER_EMAIL = "support@magrify.com"
BALLOON = ("a red hot air balloon rising over green hills, "
           "white clouds drifting, bright daylight")

USER_FIELDS = (
    "plan", "is_owner", "verified", "balance_cents",
    "cost_settled_cents", "free_jobs_total", "net_paid_cents",
)
JOB_FIELDS = (
    "status", "provider", "route", "lane", "model", "kind",
    "charged_cents", "cost_cents", "included_cost_cents",
    "included_seconds", "free_tier", "render_seconds",
    "worker_gpu", "gpu_seconds", "segments_done",
    "segments_total", "partial", "partial_reason", "bytes",
    "duration_seconds", "error", "internal_error",
    "gallery_public",
)


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(
            f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


def _session_for(user_rec):
    import requests
    s = requests.Session()
    s.cookies.set("mf_session", make_session(
        user_rec["id"], os.environ["SESSION_SECRET"]))
    return s


def _bot_fields(sess):
    c = sess.get(f"{BASE}/api/challenge", timeout=30).json()
    return {
        "challenge_id": c["challenge_id"],
        "challenge_nonce": solve_pow(c["challenge"],
                                     c["difficulty_bits"]),
    }


def _owner():
    rec = users.get(f"email:{OWNER_EMAIL}")
    assert rec, "owner record missing"
    return rec


def _user_snapshot(rec):
    return {k: rec.get(k) for k in USER_FIELDS}


def _snapshot_baseline():
    if not state.get("baseline"):
        state["baseline"] = {
            "owner": _user_snapshot(_owner()),
            "taken_at": time.time(),
        }


def _record_job(slot: str, job_id: str):
    tracked = state.get("jobs") or {}
    tracked[slot] = job_id
    state["jobs"] = tracked


@app.function(image=image, timeout=300)
def inspect() -> dict:
    _snapshot_baseline()
    return {
        "baseline": state.get("baseline"),
        "owner_now": _user_snapshot(_owner()),
        "comfy_heartbeat_age_s": (
            round(time.time() - float(meta.get("comfy:heartbeat") or 0), 1)
            if meta.get("comfy:heartbeat") else None),
        "comfy_last_trigger_age_s": (
            round(time.time() - float(meta.get("comfy:last_trigger") or 0), 1)
            if meta.get("comfy:last_trigger") else None),
        "comfy_last_trigger_error": meta.get("comfy:last_trigger_error"),
        "tracked_jobs": state.get("jobs") or {},
    }


def _start_image_b64() -> str:
    return base64.b64encode(
        Path("/assets/test_start_720.png").read_bytes()).decode()


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-secrets")],
              timeout=300)
def submit(which: str) -> dict:
    _snapshot_baseline()
    tracked = state.get("jobs") or {}
    if tracked.get(which):
        return {"already_submitted": True, "job_id": tracked[which]}
    sess = _session_for(_owner())
    bot = _bot_fields(sess)
    if which == "image":
        r = sess.post(f"{BASE}/api/images", timeout=60, json={
            "prompt": BALLOON,
            "aspect_ratio": "9:16",
            "seed": 12345,
            **bot,
        })
        assert r.status_code == 202, (r.status_code, r.text[:400])
        job = r.json()
        _record_job(which, job["id"])
        return {"job_id": job["id"], "public_job": job}
    payloads = {
        "draft": {
            "model": "wan2.2", "lane": "draft",
            "prompt": BALLOON, "seed": 12345,
            "segments": 1, "aspect_ratio": "9:16",
        },
        "production": {
            "model": "wan2.2", "lane": "production",
            "prompt": BALLOON, "seed": 12345,
            "segments": 1, "aspect_ratio": "9:16",
            "start_image_png_b64": _start_image_b64(),
        },
        "nsfw": {
            "model": "wan22_uncut", "lane": "nsfw",
            "prompt": BALLOON, "seed": 12345,
            "segments": 1, "aspect_ratio": "9:16",
            "start_image_png_b64": _start_image_b64(),
        },
        "effects": {
            "model": "wan2.2", "lane": "effects",
            "effect": "inflate",
            "prompt": "a red hot air balloon", "seed": 12345,
            "segments": 1, "aspect_ratio": "9:16",
            "start_image_png_b64": _start_image_b64(),
        },
        "camera": {
            "model": "wan2.2", "lane": "production",
            "prompt": BALLOON, "seed": 12345,
            "segments": 1, "aspect_ratio": "9:16",
            "camera_presets": ["fun-zoom-in"],
            "start_image_png_b64": _start_image_b64(),
        },
    }
    assert which in payloads, f"unknown proof: {which}"
    r = sess.post(f"{BASE}/api/jobs", timeout=60,
                  json={**payloads[which], **bot})
    assert r.status_code == 202, (r.status_code, r.text[:400])
    job = r.json()
    _record_job(which, job["id"])
    return {"job_id": job["id"], "public_job": job}


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-secrets")],
              timeout=600)
def status() -> dict:
    tracked = state.get("jobs") or {}
    out = {"jobs": {}, "baseline": state.get("baseline")}
    owner = _owner()
    out["owner_now"] = _user_snapshot(owner)
    base_owner = (state.get("baseline") or {}).get("owner") or {}
    out["owner_settled_delta_cents"] = round(
        float(owner.get("cost_settled_cents") or 0.0)
        - float(base_owner.get("cost_settled_cents") or 0.0), 2)
    out["comfy_heartbeat_age_s"] = (
        round(time.time() - float(meta.get("comfy:heartbeat") or 0), 1)
        if meta.get("comfy:heartbeat") else None)
    sess = None
    for slot, job_id in tracked.items():
        rec = pjobs.get(job_id) or {}
        pub = None
        if rec:
            if sess is None:
                sess = _session_for(owner)
            try:
                r = sess.get(f"{BASE}/api/jobs", timeout=60)
                if r.status_code == 200:
                    pub = next(
                        (x for x in r.json().get("jobs") or []
                         if x.get("id") == job_id), None)
            except Exception as exc:
                pub = {"error": str(exc)}
        out["jobs"][slot] = {
            "job_id": job_id,
            "record": {k: rec.get(k) for k in JOB_FIELDS},
            "public_status": (pub or {}).get("status"),
        }
    return out


def _ffprobe(path):
    import subprocess
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=120)
    return json.loads(probe.stdout)


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-secrets")],
              volumes={"/data": data_volume},
              timeout=1200)
def collect(job_id: str) -> dict:
    """Fetch the delivered file through the owner's file route
    (the fetch drives the finishing chain for video jobs),
    stash the exact served bytes on the data volume under
    kaggle-move-proof/, and probe them."""
    rec = pjobs.get(job_id) or {}
    if not rec:
        return {"error": "job not found"}
    sess = _session_for(_owner())
    fr = sess.get(f"{BASE}/api/jobs/{job_id}/file", timeout=900)
    assert fr.status_code == 200, fr.status_code
    outdir = Path("/data/kaggle-move-proof")
    outdir.mkdir(parents=True, exist_ok=True)
    ext = "png" if rec.get("kind") == "image" else "mp4"
    dest = outdir / f"{job_id}.{ext}"
    dest.write_bytes(fr.content)
    data_volume.commit()
    info = _ffprobe(dest)
    vstream = next((s for s in info["streams"]
                    if s["codec_type"] == "video"), {})
    return {
        "job_id": job_id,
        "bytes": dest.stat().st_size,
        "duration": (float(info["format"]["duration"])
                     if info.get("format", {}).get("duration") else None),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "codec": vstream.get("codec_name"),
        "record": {k: (pjobs.get(job_id) or {}).get(k)
                   for k in JOB_FIELDS},
    }


@app.local_entrypoint()
def main(action: str = "inspect", which: str = "",
         job_id: str = ""):
    if action == "inspect":
        print(json.dumps(inspect.remote(), indent=1, default=str))
    elif action == "submit":
        print(json.dumps(submit.remote(which), indent=1, default=str))
    elif action == "status":
        print(json.dumps(status.remote(), indent=1, default=str))
    elif action == "collect":
        print(json.dumps(collect.remote(job_id), indent=1, default=str))
    else:
        raise SystemExit(f"unknown action {action}")
