"""Proof attempt 3 for the FLUX.2 Klein 4B Kaggle fallback
(2026-11-20).

Attempts 1 and 2 verified the routing (create answered
queued at charged 0 on provider kaggle-comfy, a kernel
session claimed the job, the neuron ledger restored intact,
the owner balance untouched) but both claiming sessions died
on Kaggle before the render landed: attempt 1's job was
failed by the dead-kernel rule (through the shelf path, the
bug since fixed), attempt 2's job still sits running under a
dead session. This attempt creates one more fallback job
(the create also wakes a kernel), then watches BOTH jobs in
the platform Dicts (no API mirroring, so the watch itself
never trips the dead-kernel rule): a live session's first
heartbeat requeues attempt 2's job, and whichever job lands
done first proves the lane. The file is fetched through the
live API and probed as a PNG at the sold size; the new
create must move the owner's free counter by exactly one
and the balance not at all.

Run:  modal run scratch/klein_proof3_app.py --base https://<platform-url>
"""
import hashlib
import hmac
import json
import struct
import time

import modal

app = modal.App("moonfactory-klein-proof3")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)

PROMPT = "a red barn beside a calm lake at dawn, soft light"
PRIOR_JOB = "c53f94096c8d4379"


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


def _owner():
    for key in list(users.keys()):
        rec = users.get(key)
        if rec and rec.get("is_owner"):
            return rec
    return None


def _session_token(user_id: str, secret: str) -> str:
    import base64

    payload = {"uid": user_id, "exp": int(time.time()) + 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=3600,
)
def run_proof(base: str) -> dict:
    import os

    import requests

    base = base.rstrip("/")
    out = {"steps": {}}
    owner = _owner()
    assert owner, "no owner account found"
    before = {
        "balance_cents": int(owner.get("balance_cents", 0)),
        "free_jobs_total": int(owner.get("free_jobs_total") or 0),
    }
    out["owner_before"] = before

    s = requests.Session()
    s.cookies.set("mf_session", _session_token(
        owner["id"], os.environ["SESSION_SECRET"]))

    def bot_fields():
        c = s.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"],
                                         c["difficulty_bits"]),
        }

    cf_key = "cfneurons:" + time.strftime("%Y-%m-%d", time.gmtime())
    cf_prior = meta.get(cf_key)
    meta[cf_key] = 9500.0
    created_at = time.time()
    try:
        r = s.post(f"{base}/api/images",
                   json={"prompt": PROMPT, "image_model": "flux2_klein4b",
                         "image_size": "1024x1024", **bot_fields()},
                   timeout=120)
    finally:
        if cf_prior is None:
            try:
                del meta[cf_key]
            except Exception:
                meta[cf_key] = 0.0
        else:
            meta[cf_key] = cf_prior
    out["cf_ledger_restored_to"] = meta.get(cf_key)
    out["steps"]["create_status"] = r.status_code
    assert r.status_code == 200, r.text[:300]
    created = r.json()
    out["create"] = created
    new_job = created["job_id"]
    assert created.get("status") == "queued", created
    assert created.get("charged_cents") == 0, created

    watch_ids = [PRIOR_JOB, new_job]
    done_job = None
    deadline = time.time() + 3200
    states = {}
    while time.time() < deadline:
        states = {}
        for jid in watch_ids:
            rec = jobs.get(jid) or {}
            states[jid] = rec.get("status")
            if rec.get("status") == "done":
                done_job = jid
        out["last_states"] = states
        out["last_note"] = meta.get("comfy:note")
        if done_job:
            break
        if all(states.get(j) in ("error", "failed") for j in watch_ids):
            break
        time.sleep(30)
    out["watch_seconds"] = round(time.time() - created_at, 1)
    out["done_job"] = done_job
    for jid in watch_ids:
        rec = jobs.get(jid) or {}
        out[f"record_{jid}"] = {k: rec.get(k) for k in (
            "provider", "route", "status", "image_model", "image_size",
            "charged_cents", "cost_cents", "free_tier", "gpu_seconds",
            "bytes", "error", "internal_error", "created_at")}
        if rec.get("created_at") and jid == done_job:
            out["done_job_create_to_done_seconds"] = round(
                time.time() - float(rec["created_at"]), 1)

    owner = _owner()
    after = {
        "balance_cents": int(owner.get("balance_cents", 0)),
        "free_jobs_total": int(owner.get("free_jobs_total") or 0),
    }
    out["owner_after"] = after
    out["balance_delta"] = after["balance_cents"] - before["balance_cents"]
    out["free_counter_delta"] = (
        after["free_jobs_total"] - before["free_jobs_total"])

    if done_job:
        r = s.get(f"{base}/api/jobs/{done_job}/file", timeout=120)
        out["steps"]["file"] = r.status_code
        payload = r.content
        out["file_bytes"] = len(payload)
        out["file_is_png"] = payload[:8] == b"\x89PNG\r\n\x1a\n"
        if out["file_is_png"] and len(payload) > 24:
            out["file_dimensions"] = list(
                struct.unpack(">II", payload[16:24]))
    out["ok"] = bool(done_job) and out.get("file_is_png") is True \
        and out.get("file_dimensions") == [1024, 1024] \
        and out["balance_delta"] == 0 and out["free_counter_delta"] == 1
    print(json.dumps(out, indent=1, default=str))
    return out


@app.local_entrypoint()
def main(base: str):
    result = run_proof.remote(base)
    print(json.dumps(result, indent=2))
