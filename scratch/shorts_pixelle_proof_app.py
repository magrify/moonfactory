"""Live proof for the 2026-11-19 shorts gap pass (Pixelle
comparison): one stock path short exercising the new split,
music, and caption choices, and one AI pictures short, both
submitted as the owner through the real platform path
(POST /api/shorts) and read back from the platform's own
records.

MODAL SPEND: NONE AUTHORIZED. Shorts settle zero by design;
the AI pictures ride the house image lane on free Kaggle
quota. The harness's own functions only read records, forge
the owner's session with the platform's SESSION_SECRET (the
secret never leaves the Modal function), and post the
creations, the same pattern as scratch/kaggle_move_proof_app.py.

Every submit is idempotent through the state Dict: a job id,
once recorded, is never re-posted.

Usage:
  modal run scratch/shorts_pixelle_proof_app.py --action inspect
  modal run scratch/shorts_pixelle_proof_app.py --action submit --which stock
  modal run scratch/shorts_pixelle_proof_app.py --action submit --which ai
  modal run scratch/shorts_pixelle_proof_app.py --action status
  modal run scratch/shorts_pixelle_proof_app.py --action collect --job-id <id>
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-shorts-pixelle-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
pjobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
state = modal.Dict.from_name("moonfactory-shorts-pixelle-proof-state",
                             create_if_missing=True)
data_volume = modal.Volume.from_name("moonfactory-data",
                                     create_if_missing=False)

BASE = "https://magrify--moonfactory-api.us-east.modal.run"
OWNER_EMAIL = "support@magrify.com"

USER_FIELDS = (
    "plan", "verified", "balance_cents", "cost_settled_cents",
    "free_jobs_total", "net_paid_cents",
)
JOB_FIELDS = (
    "status", "stage", "provider", "kind", "charged_cents",
    "short_visuals", "short_style", "short_split", "short_music",
    "short_caption_style", "short_visuals_used", "visuals_fallback",
    "short_lines", "short_scenes", "duration_seconds", "bytes",
    "word_check_status", "voiceover_status", "error",
    "internal_error",
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
        "tracked_jobs": state.get("jobs") or {},
    }


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-secrets")],
              timeout=300)
def config() -> dict:
    sess = _session_for(_owner())
    r = sess.get(f"{BASE}/api/shorts/config", timeout=60)
    assert r.status_code == 200, (r.status_code, r.text[:300])
    data = r.json()
    return {k: data.get(k) for k in (
        "topic_available", "stock_provider", "daily_cap",
        "daily_used", "visual_styles", "split_modes",
        "music_styles", "caption_styles")}


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
    if which == "stock":
        # Two sentences on one pasted line, split by sentence:
        # the record's lines prove the split choice rode in.
        payload = {
            "script": ("The tide comes in twice a day. "
                       "It leaves pools of water in the rocks."),
            "split": "sentences",
            "visuals": "stock",
            "music": "house",
            "caption_style": "big",
            "voice": "af_heart",
        }
    elif which == "ai":
        payload = {
            "script": ("A lighthouse stands over a quiet harbor.\n"
                       "Boats drift home as the light comes on."),
            "split": "lines",
            "visuals": "ai",
            "style": "illustration",
            "music": "house",
            "caption_style": "classic",
            "voice": "af_heart",
        }
    else:
        raise SystemExit(f"unknown proof: {which}")
    r = sess.post(f"{BASE}/api/shorts", timeout=120,
                  json={**payload, **bot})
    assert r.status_code == 202, (r.status_code, r.text[:400])
    job = r.json()
    _record_job(which, job["id"])
    return {"job_id": job["id"], "public_job": job}


@app.function(image=image, timeout=300)
def status() -> dict:
    tracked = state.get("jobs") or {}
    out = {"jobs": {}, "baseline": state.get("baseline")}
    owner = _owner()
    out["owner_now"] = _user_snapshot(owner)
    base_owner = (state.get("baseline") or {}).get("owner") or {}
    out["owner_settled_delta_cents"] = round(
        float(owner.get("cost_settled_cents") or 0.0)
        - float(base_owner.get("cost_settled_cents") or 0.0), 2)
    out["owner_balance_delta_cents"] = (
        int(owner.get("balance_cents") or 0)
        - int(base_owner.get("balance_cents") or 0))
    out["comfy_heartbeat_age_s"] = (
        round(time.time() - float(meta.get("comfy:heartbeat") or 0), 1)
        if meta.get("comfy:heartbeat") else None)
    for slot, job_id in tracked.items():
        rec = pjobs.get(job_id) or {}
        children = []
        for key in list(pjobs.keys()):
            other = pjobs.get(key)
            if other and other.get("short_parent") == job_id:
                children.append({
                    "id": other["id"],
                    "status": other.get("status"),
                    "gpu_seconds": other.get("gpu_seconds"),
                    "bytes": other.get("bytes"),
                    "prompt": other.get("prompt"),
                    "aspect_ratio": other.get("aspect_ratio"),
                    "charged_cents": other.get("charged_cents"),
                })
        out["jobs"][slot] = {
            "job_id": job_id,
            "record": {k: rec.get(k) for k in JOB_FIELDS},
            "children": children,
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
              timeout=900)
def collect(job_id: str) -> dict:
    rec = pjobs.get(job_id) or {}
    if not rec:
        return {"error": "job not found"}
    sess = _session_for(_owner())
    fr = sess.get(f"{BASE}/api/jobs/{job_id}/file", timeout=600)
    assert fr.status_code == 200, fr.status_code
    outdir = Path("/data/shorts-pixelle-proof")
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / f"{job_id}.mp4"
    dest.write_bytes(fr.content)
    data_volume.commit()
    info = _ffprobe(dest)
    vstream = next((s for s in info["streams"]
                    if s["codec_type"] == "video"), {})
    astream = next((s for s in info["streams"]
                    if s["codec_type"] == "audio"), None)
    return {
        "job_id": job_id,
        "bytes": dest.stat().st_size,
        "duration": (float(info["format"]["duration"])
                     if info.get("format", {}).get("duration") else None),
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "codec": vstream.get("codec_name"),
        "has_audio": astream is not None,
        "record": {k: rec.get(k) for k in JOB_FIELDS},
    }


@app.function(image=image, timeout=300)
def queue() -> dict:
    rows = []
    for key in list(pjobs.keys()):
        rec = pjobs.get(key)
        if rec and rec.get("provider") == "kaggle-comfy":
            rows.append({
                "id": rec["id"],
                "kind": rec.get("kind"),
                "status": rec.get("status"),
                "created_at": rec.get("created_at"),
                "short_parent": rec.get("short_parent"),
                "gpu_seconds": rec.get("gpu_seconds"),
            })
    rows.sort(key=lambda r: r.get("created_at") or 0)
    return {"comfy_jobs": rows,
            "heartbeat_age_s": (
                round(time.time()
                      - float(meta.get("comfy:heartbeat") or 0), 1)
                if meta.get("comfy:heartbeat") else None)}


@app.local_entrypoint()
def main(action: str = "inspect", which: str = "",
         job_id: str = ""):
    if action == "inspect":
        print(json.dumps(inspect.remote(), indent=1, default=str))
    elif action == "config":
        print(json.dumps(config.remote(), indent=1, default=str))
    elif action == "submit":
        print(json.dumps(submit.remote(which), indent=1, default=str))
    elif action == "status":
        print(json.dumps(status.remote(), indent=1, default=str))
    elif action == "collect":
        print(json.dumps(collect.remote(job_id), indent=1, default=str))
    elif action == "queue":
        print(json.dumps(queue.remote(), indent=1, default=str))
    else:
        raise SystemExit(f"unknown action {action}")
