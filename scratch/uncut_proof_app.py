"""Approved proof render for the Wan 2.2 Uncut lane (2026-11-18).

MODAL SPEND: APPROVED BY JACK IN CHAT, Nov 18, 2026: "Approve
the proof render" — exactly ONE generation job on the platform
model wan22_uncut (worker/pricing lane nsfw), quoted at $0.80 or
less, run on the owner account (support@magrify.com, markup
exempt). Nothing else is authorized: the submit action is
idempotent through the state Dict and refuses to create a second
job, ever. A failure is reported, never retried.

The job reuses the repo's existing test start image
(modal-worker/test_start_720.png, the balloon frame from the
Nov 16 proofs) and a neutral scene prompt. No speech script and
no music prompt: the word check and voiceover fields must stay
inert on this silent lane.

Usage:
  modal run uncut_proof_app.py --action submit
  modal run uncut_proof_app.py --action status
  modal run uncut_proof_app.py --action collect
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-uncut-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests", "numpy", "pillow")
    .add_local_file(
        str(Path(__file__).parent.parent / "modal-worker" / "test_start_720.png"),
        "/assets/test_start_720.png",
    )
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
pjobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
wjobs = modal.Dict.from_name("sinclip-jobs", create_if_missing=True)
state = modal.Dict.from_name("moonfactory-uncut-proof-state", create_if_missing=True)
volume = modal.Volume.from_name("sinclip-models", create_if_missing=False)

OWNER_EMAIL = "support@magrify.com"
L40S_USD_PER_SEC = 0.000542  # Modal list rate, same basis as the Nov 16 proofs

SCENE = ("a red hot air balloon rising over green hills, "
         "white clouds drifting, bright daylight")
SEED = 12345

PLATFORM_FIELDS = (
    "status", "model", "lane", "error", "bytes", "charged_cents",
    "cost_cents", "included_cost_cents", "voiceover_status",
    "lipsync_status", "word_check_status", "word_check_listen_at",
    "worker_job_id", "segments_total", "segments_done", "partial",
    "duration_seconds",
)
WORKER_FIELDS = (
    "lane", "status", "error", "bytes", "render_seconds",
    "duration_seconds", "delivered_seconds", "segments_done",
    "segments_total", "aspect_ratio", "prompt",
)


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


def _owner_session():
    import requests
    rec = users.get(f"email:{OWNER_EMAIL}")
    assert rec, "owner record missing"
    s = requests.Session()
    s.cookies.set("mf_session",
                  make_session(rec["id"], os.environ["SESSION_SECRET"]))
    return s, rec


def _bot_fields(sess, base):
    c = sess.get(f"{base}/api/challenge", timeout=30).json()
    return {
        "challenge_id": c["challenge_id"],
        "challenge_nonce": solve_pow(c["challenge"], c["difficulty_bits"]),
    }


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def submit(base: str) -> dict:
    """Create the ONE approved job. Idempotent: once a platform
    job id exists in state, this returns it and never posts again."""
    import requests

    base = base.rstrip("/")
    if state.get("platform_job_id"):
        return {"already_submitted": True,
                "platform_job_id": state["platform_job_id"]}
    sess, owner = _owner_session()
    state["owner_before"] = {k: owner.get(k) for k in (
        "plan", "net_paid_cents", "balance_cents",
        "cost_settled_cents", "is_owner")}
    img = base64.b64encode(
        Path("/assets/test_start_720.png").read_bytes()).decode()
    r = sess.post(f"{base}/api/jobs", timeout=60, json={
        "model": "wan22_uncut",
        "prompt": SCENE,
        "seed": SEED,
        "start_image_png_b64": img,
        "segments": 1,
        "aspect_ratio": "9:16",
        **_bot_fields(sess, base),
    })
    assert r.status_code == 202, (r.status_code, r.text[:400])
    job = r.json()
    state["platform_job_id"] = job["id"]
    state["submitted_at"] = time.time()
    return {"platform_job_id": job["id"], "public_job": job}


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def status(base: str) -> dict:
    """Read-only status: the jobs list call drives the platform's
    status mirror; the records come straight from the Dicts."""
    import requests

    base = base.rstrip("/")
    pid = state.get("platform_job_id")
    if not pid:
        return {"submitted": False}
    sess, owner = _owner_session()
    pub = None
    r = sess.get(f"{base}/api/jobs", timeout=60)
    if r.status_code == 200:
        pub = next((x for x in r.json().get("jobs") or []
                    if x.get("id") == pid), None)
    rec = pjobs.get(pid) or {}
    out = {
        "platform_job_id": pid,
        "public": ({k: pub.get(k) for k in (
            "status", "error", "voiceover_status", "lipsync_status",
            "word_check_status", "word_check_listen_at",
            "charged_cents", "has_file")} if pub else None),
        "record": {k: rec.get(k) for k in PLATFORM_FIELDS},
        "owner_now": {k: owner.get(k) for k in (
            "balance_cents", "cost_settled_cents")},
        "owner_before": state.get("owner_before"),
        "elapsed_since_submit": round(time.time() - state["submitted_at"], 1),
    }
    wj = rec.get("worker_job_id")
    if wj:
        wrec = wjobs.get(wj) or {}
        out["worker_job_id"] = wj
        out["worker_record"] = {k: wrec.get(k) for k in WORKER_FIELDS}
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
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    volumes={"/models": volume},
    timeout=1200,
)
def collect(base: str) -> dict:
    """Once the platform job is done: fetch the delivered file
    (the fetch drives finishing), stash it on the volume under
    proof_renders/, probe it, grab two frames, and measure the
    frame-to-frame motion between them."""
    import subprocess

    import numpy as np
    import requests
    from PIL import Image

    base = base.rstrip("/")
    pid = state.get("platform_job_id")
    if not pid:
        return {"submitted": False}
    if state.get("collected"):
        return {"already_collected": True, "result": state["collected"]}
    sess, _owner = _owner_session()
    rec = pjobs.get(pid) or {}
    if rec.get("status") != "done":
        return {"ready": False, "status": rec.get("status")}
    fr = sess.get(f"{base}/api/jobs/{pid}/file", timeout=600)
    assert fr.status_code == 200, fr.status_code
    outdir = Path("/models/proof_renders")
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / "uncut.mp4"
    dest.write_bytes(fr.content)
    info = _ffprobe(dest)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    result = {
        "volume_file": "proof_renders/uncut.mp4",
        "bytes": dest.stat().st_size,
        "duration": float(info["format"]["duration"]),
        "width": vstream["width"],
        "height": vstream["height"],
        "codec": vstream["codec_name"],
        "streams": [s["codec_type"] for s in info["streams"]],
    }
    frames = {}
    for tag, t in (("f0", 0.5), ("f1", 4.5)):
        fp = outdir / f"uncut_{tag}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(dest),
             "-frames:v", "1", str(fp)],
            capture_output=True, timeout=120, check=True)
        frames[tag] = np.asarray(
            Image.open(fp).convert("L")).astype(np.float64)
        result[f"frame_{tag}"] = f"proof_renders/uncut_{tag}.png"
    result["frame_mean_abs_diff"] = round(
        float(np.abs(frames["f0"] - frames["f1"]).mean()), 3)
    result["record"] = {k: rec.get(k) for k in PLATFORM_FIELDS}
    wj = rec.get("worker_job_id")
    if wj:
        wrec = wjobs.get(wj) or {}
        result["worker_record"] = {k: wrec.get(k) for k in WORKER_FIELDS}
        if wrec.get("render_seconds"):
            result["real_gpu_cost_cents"] = round(
                wrec["render_seconds"] * L40S_USD_PER_SEC * 100, 2)
    state["collected"] = result
    volume.commit()
    return result


@app.local_entrypoint()
def main(action: str, base: str = "https://magrify--moonfactory-api.us-east.modal.run"):
    if action == "submit":
        print(json.dumps(submit.remote(base), indent=1, default=str))
    elif action == "status":
        print(json.dumps(status.remote(base), indent=1, default=str))
    elif action == "collect":
        print(json.dumps(collect.remote(base), indent=1, default=str))
    else:
        raise SystemExit(f"unknown action {action}")
