"""Live proof for the voice cloning lane (2026-11-21):
exactly ONE owner-account creation through the real
platform path (POST /api/shorts with voice "clone", the
synthetic Kokoro af_heart reference from the VoiceStudio
test, and one script line, the CosyVoice test's target
sentence), cloned by the Kaggle voice worker, read back
from the platform's own records and the data Volume.

MODAL SPEND: NONE AUTHORIZED. The cloning executes in a
free Kaggle session through the platform's voice worker
route; nothing here dispatches a Modal generation job. The
harness's own functions only read records, forge the
owner's session with the platform's SESSION_SECRET (the
secret never leaves the Modal function), and post the one
creation, the same pattern as scratch/krea2_proof_app.py.

The submit is idempotent through the state Dict: a job id,
once recorded, is never re-posted.

Usage:
  modal run scratch/voice_clone_proof_app.py --action inspect
  modal run scratch/voice_clone_proof_app.py --action submit
  modal run scratch/voice_clone_proof_app.py --action status
  modal run scratch/voice_clone_proof_app.py --action collect
"""

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-voice-clone-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
pjobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
state = modal.Dict.from_name("moonfactory-voice-clone-proof-state",
                             create_if_missing=True)
data_volume = modal.Volume.from_name("moonfactory-data",
                                     create_if_missing=False)

BASE = "https://magrify--moonfactory-api.us-east.modal.run"
OWNER_EMAIL = "support@magrify.com"
# The CosyVoice test's exact pair (2026-11-21 record): the
# synthetic af_heart reference and its transcript, and the
# one script line the cloned voice speaks in the proof.
REF_TEXT = ("Thank you for calling. Our office is open every day, "
            "and we are happy to help you plan your next visit "
            "with us.")
TARGET_LINE = ("The morning train to Fort Worth carried 128 "
               "passengers across 3 counties in under 2 hours.")
# The shorts route requires at least two script lines, so
# the proof script is the target line plus one short
# closer; the collected proof audio is the first line's
# clone child, the measured sentence.
PROOF_SCRIPT = (TARGET_LINE + "\n"
                "We look forward to seeing you soon.")
REF_PATH = (Path(__file__).parent.parent.parent / "sinclip"
            / "voicestudio-test-2026-11-21" / "reference.wav")

USER_FIELDS = (
    "plan", "is_owner", "verified", "balance_cents",
    "cost_settled_cents", "free_jobs_total", "net_paid_cents",
)
JOB_FIELDS = (
    "status", "stage", "provider", "kind", "speech_voice",
    "voiceover_status", "charged_cents", "clone_charge_cents",
    "clone_charged", "clone_charge_refunded", "clone_children",
    "clone_reference_ext", "duration_seconds", "error",
    "internal_error",
)
CHILD_FIELDS = (
    "status", "kind", "provider", "clone_parent", "clone_text",
    "bytes", "error", "internal_error", "created_at", "claimed_at",
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


def _children(job_id):
    out = []
    for key in list(pjobs.keys()):
        rec = pjobs.get(key) or {}
        if rec.get("clone_parent") == job_id:
            out.append({**{k: rec.get(k) for k in CHILD_FIELDS},
                        "id": rec.get("id")})
    return out


@app.function(image=image, timeout=300)
def inspect() -> dict:
    _snapshot_baseline()
    return {
        "baseline": state.get("baseline"),
        "owner_now": _user_snapshot(_owner()),
        "voice_heartbeat_age_s": (
            round(time.time() - float(meta.get("voice:heartbeat") or 0), 1)
            if meta.get("voice:heartbeat") else None),
        "voice_last_trigger_age_s": (
            round(time.time() - float(meta.get("voice:last_trigger") or 0), 1)
            if meta.get("voice:last_trigger") else None),
        "voice_last_trigger_error": meta.get("voice:last_trigger_error"),
        "tracked_job": state.get("job_id"),
    }


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-secrets")],
              timeout=300)
def submit(reference_b64: str) -> dict:
    _snapshot_baseline()
    prior_id = state.get("job_id")
    if prior_id:
        prior = pjobs.get(prior_id) or {}
        if prior.get("status") != "error":
            return {"already_submitted": True, "job_id": prior_id}
    sess = _session_for(_owner())
    bot = _bot_fields(sess)
    r = sess.post(f"{BASE}/api/shorts", timeout=60, json={
        "voice": "clone",
        "script": PROOF_SCRIPT,
        "visuals": "stock",
        "music": "house",
        "caption_style": "classic",
        "clone_reference_audio_b64": reference_b64,
        "clone_reference_text": REF_TEXT,
        **bot,
    })
    assert r.status_code in (200, 202), (r.status_code, r.text[:400])
    body = r.json()
    state["job_id"] = body["id"]
    state["submitted_at"] = time.time()
    return {"job_id": body["id"], "response": body}


@app.function(image=image, timeout=300)
def status() -> dict:
    job_id = state.get("job_id")
    if not job_id:
        return {"job": None}
    job = pjobs.get(job_id) or {}
    return {
        "job": {k: job.get(k) for k in JOB_FIELDS},
        "children": _children(job_id),
        "submitted_at": state.get("submitted_at"),
        "age_s": (round(time.time() - state["submitted_at"], 1)
                  if state.get("submitted_at") else None),
    }


@app.function(image=image, volumes={"/data": data_volume}, timeout=300)
def collect() -> dict:
    job_id = state.get("job_id")
    job = pjobs.get(job_id) or {}
    out = {"job": {k: job.get(k) for k in JOB_FIELDS},
           "children": _children(job_id)}
    children = out["children"]
    if children:
        child_id = children[0]["id"]
        path = Path("/data/files/voice_clone") / f"{child_id}.wav"
        if path.exists():
            data = path.read_bytes()
            out["clone_file"] = {
                "path": str(path),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "b64": base64.b64encode(data).decode(),
            }
    short_path = Path("/data/files") / f"{job_id}.mp4"
    if short_path.exists():
        out["short_file"] = {"path": str(short_path),
                             "bytes": short_path.stat().st_size}
    return out


@app.local_entrypoint()
def main(action: str):
    if action == "inspect":
        print(json.dumps(inspect.remote(), indent=2, default=str))
    elif action == "submit":
        ref_b64 = base64.b64encode(REF_PATH.read_bytes()).decode()
        print(json.dumps(submit.remote(ref_b64), indent=2,
                         default=str))
    elif action == "status":
        print(json.dumps(status.remote(), indent=2, default=str))
    elif action == "collect":
        got = collect.remote()
        f = got.pop("clone_file", None)
        if f:
            b64 = f.pop("b64")
            target = (Path(__file__).parent.parent.parent
                      / "sinclip" / "voice-cloning-wiring-2026-11-21"
                      / "proof_clone.wav")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(b64))
            got["clone_file"] = {**f, "saved_to": str(target)}
        print(json.dumps(got, indent=2, default=str))
    else:
        raise SystemExit(f"unknown action {action}")
