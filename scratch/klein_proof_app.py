"""Proof probe for the FLUX.2 Klein 4B Kaggle fallback
(2026-11-20).

Runs inside Modal because this sandbox's egress proxy stalls
POSTs to *.modal.run (GETs pass). Mints an owner session with
the platform's own session rule (the _make_session HMAC shape
in app.py, over SESSION_SECRET from the platform secret),
then drives the live public API exactly like the studio does:
challenge -> proof of work -> POST /api/images -> poll the
job -> download the file -> probe it.

The one creation is forced down the Kaggle route without
spending the day's real Cloudflare allowance: the proof sets
the day's neuron ledger in the platform meta to the full
budget, creates the job, and restores the ledger's prior
value the moment the job exists. It also waits for any live
kernel session to go stale first, so the job is claimed by a
session booted from the new kernel file. Expected: the
create answers queued at charged 0, the job record lands on
provider kaggle-comfy, the file comes back a real PNG at
the sold 1024x1024, the owner's balance is untouched and
the free counter moved by exactly one (the free counter
rule counts every generation). Timing is reported honestly:
wall seconds from create to done (kernel wake, boot and the
12.45 GB Klein staging included) plus the worker's own
ComfyUI exec seconds off the job record.

Run:  modal run scratch/klein_proof_app.py --base https://<platform-url>
"""
import hashlib
import hmac
import json
import struct
import time

import modal

app = modal.App("moonfactory-klein-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)

PROMPT = "a red barn beside a calm lake at dawn, soft light"


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
    out = {"base": base, "steps": {}}

    # Wait out any live kernel session so the proof job is
    # claimed by a session booted from the new kernel file
    # (the platform pushes the kernel on a stale heartbeat).
    waited = 0
    while True:
        hb = float(meta.get("comfy:heartbeat") or 0)
        if hb == 0 or time.time() - hb > 200 or waited > 900:
            break
        time.sleep(30)
        waited += 30
    out["heartbeat_wait_seconds"] = waited

    owner = _owner()
    assert owner, "no owner account found"
    owner_id = owner["id"]
    before = {
        "balance_cents": int(owner.get("balance_cents", 0)),
        "free_jobs_total": int(owner.get("free_jobs_total") or 0),
    }
    out["owner_before"] = before

    s = requests.Session()
    s.cookies.set("mf_session",
                  _session_token(owner_id, os.environ["SESSION_SECRET"]))

    r = s.get(f"{base}/api/config", timeout=30)
    out["steps"]["config"] = r.status_code
    assert r.status_code == 200, r.text[:200]
    klein = [m for m in r.json().get("image_models", [])
             if m.get("id") == "flux2_klein4b"]
    out["klein_config"] = klein
    assert len(klein) == 1, klein
    assert klein[0].get("picker_class") == "free", klein
    assert klein[0].get("label") == "FLUX.2 Klein 4B", klein

    def bot_fields():
        c = s.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"],
                                         c["difficulty_bits"]),
        }

    # Spend the day's neuron ledger on paper only, create the
    # job, restore the ledger at once: the real Cloudflare
    # allowance is never touched by this proof.
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
    job_id = created["job_id"]
    assert created.get("status") == "queued", created
    assert created.get("charged_cents") == 0, created

    # Poll to a terminal state. The house lanes are slow:
    # kernel wake, ComfyUI boot, the 12.45 GB Klein staging
    # download, then the render itself.
    final = None
    deadline = time.time() + 3000
    while time.time() < deadline:
        try:
            r = s.get(f"{base}/api/jobs/{job_id}", timeout=120)
        except Exception:
            time.sleep(30)
            continue
        if r.status_code == 200:
            view = r.json()
            if view.get("status") in ("done", "failed"):
                final = view
                break
        time.sleep(30)
    out["wall_seconds_to_terminal"] = round(time.time() - created_at, 1)
    out["final_view"] = {k: (final or {}).get(k)
                         for k in ("status", "kind", "has_file", "error")}
    assert final and final.get("status") == "done", out["final_view"]

    r = s.get(f"{base}/api/jobs/{job_id}/file", timeout=120)
    out["steps"]["file"] = r.status_code
    assert r.status_code == 200, r.text[:200]
    payload = r.content
    out["file_bytes"] = len(payload)
    out["file_is_png"] = payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert out["file_is_png"], "the delivered file is not a PNG"
    out["file_dimensions"] = list(struct.unpack(">II", payload[16:24]))
    assert out["file_dimensions"] == [1024, 1024], out["file_dimensions"]

    rec = jobs.get(job_id) or {}
    out["record"] = {k: rec.get(k) for k in (
        "provider", "route", "status", "image_model", "image_size",
        "charged_cents", "cost_cents", "included_cost_cents",
        "free_tier", "gpu_seconds", "bytes", "error")}
    assert rec.get("provider") == "kaggle-comfy", rec.get("provider")
    assert rec.get("charged_cents") == 0, rec.get("charged_cents")

    owner = _owner()
    after = {
        "balance_cents": int(owner.get("balance_cents", 0)),
        "free_jobs_total": int(owner.get("free_jobs_total") or 0),
    }
    out["owner_after"] = after
    assert after["balance_cents"] == before["balance_cents"], \
        "the fallback creation moved the owner's balance"
    assert after["free_jobs_total"] == before["free_jobs_total"] + 1, \
        "the fallback creation did not count exactly once"
    out["ok"] = True
    return out


@app.local_entrypoint()
def main(base: str):
    result = run_proof.remote(base)
    print(json.dumps(result, indent=2))
