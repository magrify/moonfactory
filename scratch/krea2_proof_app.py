"""Live proof for the Krea 2 Verite image lane (2026-11-21):
exactly ONE owner-account generation through the real
platform path (POST /api/images with image_model
krea2_verite), rendered by the Kaggle ComfyUI worker, read
back from the platform's own records and the data Volume.

MODAL SPEND: NONE AUTHORIZED. The generation executes in a
free Kaggle session through the platform's Kaggle ComfyUI
route; nothing here dispatches a Modal generation job. The
harness's own functions only read records, forge the
owner's session with the platform's SESSION_SECRET (the
secret never leaves the Modal function), and post the one
creation, the same pattern as scratch/kaggle_move_proof_app.py.

The submit is idempotent through the state Dict: a job id,
once recorded, is never re-posted.

Usage:
  modal run scratch/krea2_proof_app.py --action inspect
  modal run scratch/krea2_proof_app.py --action submit
  modal run scratch/krea2_proof_app.py --action status
  modal run scratch/krea2_proof_app.py --action collect
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-krea2-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
pjobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
state = modal.Dict.from_name("moonfactory-krea2-proof-state",
                             create_if_missing=True)
data_volume = modal.Volume.from_name("moonfactory-data",
                                     create_if_missing=False)

BASE = "https://magrify--moonfactory-api.us-east.modal.run"
OWNER_EMAIL = "support@magrify.com"
# The proven test prompt's user half (2026-11-21 test
# record): the platform stamps the "verite realism, nsfw"
# triggers in front of it at queue time, reproducing the
# test's positive exactly.
PROOF_PROMPT = (
    "photorealistic portrait of a nude adult woman in her "
    "early 30s standing by a bright window, completely "
    "unclothed, natural unretouched skin with visible pores "
    "and light freckles, candid amateur photo look, soft "
    "daylight, artistic nude photography, detailed eyes, "
    "natural body proportions, shot on 35mm film")

USER_FIELDS = (
    "plan", "is_owner", "verified", "balance_cents",
    "cost_settled_cents", "free_jobs_total", "net_paid_cents",
)
JOB_FIELDS = (
    "status", "provider", "route", "lane", "model", "kind",
    "image_model", "image_size", "charged_cents", "cost_cents",
    "included_cost_cents", "free_tier", "prompt", "user_prompt",
    "gpu_seconds", "bytes", "error", "internal_error",
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
        "tracked_job": state.get("job_id"),
    }


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-secrets")],
              timeout=300)
def submit() -> dict:
    _snapshot_baseline()
    prior_id = state.get("job_id")
    if prior_id:
        prior = pjobs.get(prior_id) or {}
        if prior.get("status") != "error":
            return {"already_submitted": True, "job_id": prior_id}
    sess = _session_for(_owner())
    bot = _bot_fields(sess)
    r = sess.post(f"{BASE}/api/images", timeout=60, json={
        "image_model": "krea2_verite",
        "image_size": "832x1216",
        "prompt": PROOF_PROMPT,
        "seed": 424242,
        **bot,
    })
    assert r.status_code in (200, 202), (r.status_code, r.text[:400])
    body = r.json()
    state["job_id"] = body["job_id"]
    state["submitted_at"] = time.time()
    return {"job_id": body["job_id"], "response": body}


@app.function(image=image, timeout=300)
def status() -> dict:
    job_id = state.get("job_id")
    if not job_id:
        return {"job": None}
    job = pjobs.get(job_id) or {}
    return {
        "job": {k: job.get(k) for k in JOB_FIELDS},
        "submitted_at": state.get("submitted_at"),
        "age_s": (round(time.time() - state["submitted_at"], 1)
                  if state.get("submitted_at") else None),
    }


@app.function(image=image, volumes={"/data": data_volume}, timeout=300)
def collect() -> dict:
    job_id = state.get("job_id")
    job = pjobs.get(job_id) or {}
    out = {"job": {k: job.get(k) for k in JOB_FIELDS}}
    path = Path("/data/files") / f"{job_id}.png"
    # The platform's FILES_DIR sits on the same volume; find
    # the file wherever under /data it landed.
    if not path.exists():
        hits = list(Path("/data").rglob(f"{job_id}.png"))
        path = hits[0] if hits else path
    if path.exists():
        data = path.read_bytes()
        width = height = None
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            width, height = struct.unpack(">II", data[16:24])
        out["file"] = {
            "path": str(path),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "png_width": width,
            "png_height": height,
            "b64": base64.b64encode(data).decode(),
        }
    return out


@app.local_entrypoint()
def main(action: str):
    if action == "inspect":
        print(json.dumps(inspect.remote(), indent=2, default=str))
    elif action == "submit":
        print(json.dumps(submit.remote(), indent=2, default=str))
    elif action == "status":
        print(json.dumps(status.remote(), indent=2, default=str))
    elif action == "collect":
        got = collect.remote()
        f = got.pop("file", None)
        if f:
            b64 = f.pop("b64")
            target = (Path(__file__).parent.parent.parent
                      / "sinclip" / "krea2-wiring-2026-11-21"
                      / "proof_krea2_verite.png")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(b64))
            got["file"] = {**f, "saved_to": str(target)}
        print(json.dumps(got, indent=2, default=str))
    else:
        raise SystemExit(f"unknown action {action}")
