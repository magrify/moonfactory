"""Watcher for the Klein fallback proof job 9b4781aa2e04459b:
polls the platform jobs Dict until the job lands done or
failed, then fetches the delivered file through the live API
with a minted owner session and reports the record, the PNG
probe, and the timings."""
import hashlib
import hmac
import json
import struct
import time

import modal

app = modal.App("moonfactory-klein-watch")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)

JOB_ID = "9b4781aa2e04459b"
BASE = "https://magrify--moonfactory-api.us-east.modal.run"


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
def watch() -> dict:
    import os

    import requests

    out = {"job_id": JOB_ID}
    started = time.time()
    rec = {}
    while time.time() - started < 3300:
        rec = jobs.get(JOB_ID) or {}
        if rec.get("status") in ("done", "failed"):
            break
        out["last_note"] = meta.get("comfy:note")
        out["last_status"] = rec.get("status")
        time.sleep(30)
    out["record"] = {k: rec.get(k) for k in (
        "provider", "route", "status", "image_model", "image_size",
        "charged_cents", "cost_cents", "included_cost_cents",
        "free_tier", "gpu_seconds", "bytes", "error", "created_at",
        "finished_at")}
    out["watch_wait_seconds"] = round(time.time() - started, 1)
    if rec.get("created_at"):
        end = rec.get("finished_at") or time.time()
        out["create_to_terminal_seconds"] = round(
            end - float(rec["created_at"]), 1)
    owner = _owner()
    out["owner"] = {
        "balance_cents": owner.get("balance_cents"),
        "free_jobs_total": owner.get("free_jobs_total"),
    }
    if rec.get("status") == "done":
        s = requests.Session()
        s.cookies.set("mf_session", _session_token(
            owner["id"], os.environ["SESSION_SECRET"]))
        r = s.get(f"{BASE}/api/jobs/{JOB_ID}/file", timeout=120)
        out["file_status"] = r.status_code
        payload = r.content
        out["file_bytes"] = len(payload)
        out["file_is_png"] = payload[:8] == b"\x89PNG\r\n\x1a\n"
        if out["file_is_png"] and len(payload) > 24:
            out["file_dimensions"] = list(
                struct.unpack(">II", payload[16:24]))
    print(json.dumps(out, indent=1, default=str))
    return out


@app.local_entrypoint()
def main():
    watch.remote()
