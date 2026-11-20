"""Probe: find a Pexels photo query that truly returns zero
results (the first stock proof learned Pexels fuzzy-matches
plain gibberish words), and delete the orphan job that probe
created on the owner account (53e6f05cd9a045b2).

Run:  modal run scratch/stock_probe_app.py --base https://<platform-url>
"""
import hashlib
import hmac
import json
import time

import modal

app = modal.App("moonfactory-stock-probe")

image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

ORPHAN_JOB = "53e6f05cd9a045b2"
CANDIDATES = [
    "xq7zvkw9plm2asdf",
    "98765432109876543210",
    "3f9a1b2c-4d5e-6f70-8192-a3b4c5d6e7f8",
    "zzzzqqqqxxxxvvvv",
    "asdfqwerzxcvplmokn",
    "the and of a to in is",
    "😀😀😀😀",
]


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
    secrets=[modal.Secret.from_name("moonfactory-providers"),
             modal.Secret.from_name("moonfactory-secrets")],
    timeout=300,
)
def run_probe(base: str) -> dict:
    import os
    import urllib.parse

    import requests

    out = {"counts": {}}
    key = os.environ["PEXELS_API_KEY"]
    for q in CANDIDATES:
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
            {"query": q, "per_page": 6})
        r = requests.get(url, headers={"Authorization": key}, timeout=30)
        if r.status_code == 200:
            out["counts"][q] = len(r.json().get("photos") or [])
        else:
            out["counts"][q] = f"http {r.status_code}"

    rec = jobs.get(ORPHAN_JOB)
    out["orphan_before"] = None if not rec else {
        "status": rec.get("status"), "provider": rec.get("provider"),
        "prompt": rec.get("prompt"),
        "stock_page_url": rec.get("stock_page_url"),
    }
    owner = _owner()
    s = requests.Session()
    s.cookies.set("mf_session",
                  _session_token(owner["id"], os.environ["SESSION_SECRET"]))
    r = s.delete(f"{base.rstrip('/')}/api/jobs/{ORPHAN_JOB}", timeout=60)
    out["orphan_delete_status"] = r.status_code
    out["orphan_after"] = jobs.get(ORPHAN_JOB)
    return out


@app.local_entrypoint()
def main(base: str):
    print(json.dumps(run_probe.remote(base), indent=2, ensure_ascii=False))
