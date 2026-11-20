"""Proof probe for the Moonfactory stock image lane (2026-11-20).

Runs inside Modal because this sandbox's egress proxy stalls POSTs
to *.modal.run (GETs pass). Mints an owner session with the
platform's own session rule (the _make_session HMAC shape in
app.py, over SESSION_SECRET from the platform secret), then drives
the live public API exactly like the studio does: challenge ->
proof of work -> POST /api/images -> read the job -> download the
file -> probe it.

Two creations are probed, in this order:
1. A nonsense description must fail clean: a 400 carrying the
   plain no match message (the platform rewrites API 404
   bodies, so the lane answers 400), no job record, no
   charge, and the owner's free counter unmoved.
2. "a lighthouse at sunset" on the Stock image lane must return
   a real Pexels photo: job done at once, charged 0, the file a
   real image, the owner's balance untouched and the free
   counter moved by exactly one (the free counter rule counts
   every generation, this lane included).

Run:  modal run scratch/stock_proof_app.py --base https://<platform-url>
"""
import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-stock-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

PROMPT = "a lighthouse at sunset"
# A digit string is the one probe Pexels truly returns zero
# photos for: its search fuzzy-matches even gibberish words,
# emoji and UUIDs (measured live 2026-11-20), so the plain
# nonsense words an earlier draft used came back matched.
NO_MATCH_PROMPT = "98765432109876543210"


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
    timeout=600,
)
def run_proof(base: str) -> dict:
    import os

    import requests

    base = base.rstrip("/")
    out = {"base": base, "steps": {}}
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
    stock = [m for m in r.json().get("image_models", [])
             if m.get("id") == "stock"]
    out["stock_config"] = stock
    assert stock, "the stock lane is not in the served image models"
    assert stock[0].get("picker_class") == "free", stock
    assert stock[0].get("label") == "Stock image", stock

    def bot_fields():
        c = s.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"],
                                         c["difficulty_bits"]),
        }

    # 1. The no-match path fails clean before anything is made.
    r = s.post(f"{base}/api/images",
               json={"prompt": NO_MATCH_PROMPT, "image_model": "stock",
                     "image_size": "large", **bot_fields()},
               timeout=60)
    out["steps"]["no_match_status"] = r.status_code
    out["no_match_body"] = r.text[:200]
    assert r.status_code == 400, r.text[:300]
    assert "no stock photo matches" in r.text, r.text[:300]
    owner = _owner()
    out["owner_after_no_match"] = {
        "balance_cents": int(owner.get("balance_cents", 0)),
        "free_jobs_total": int(owner.get("free_jobs_total") or 0),
    }
    assert out["owner_after_no_match"] == before, \
        "the no-match attempt moved the owner's counters"

    # 2. The real creation.
    r = s.post(f"{base}/api/images",
               json={"prompt": PROMPT, "image_model": "stock",
                     "image_size": "large", **bot_fields()},
               timeout=120)
    out["steps"]["create_status"] = r.status_code
    assert r.status_code == 200, r.text[:300]
    created = r.json()
    out["create"] = created
    job_id = created["job_id"]
    assert created.get("charged_cents") == 0, created

    r = s.get(f"{base}/api/jobs/{job_id}", timeout=30)
    out["steps"]["job_read"] = r.status_code
    assert r.status_code == 200, r.text[:200]
    out["job"] = {k: r.json().get(k)
                  for k in ("status", "kind", "has_file", "error")}
    assert r.json()["status"] == "done", r.json()

    r = s.get(f"{base}/api/jobs/{job_id}/file", timeout=120)
    out["steps"]["file"] = r.status_code
    assert r.status_code == 200, r.text[:200]
    path = Path("/tmp/stock-proof.jpg")
    path.write_bytes(r.content)
    out["file_bytes"] = len(r.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=120)
    info = json.loads(probe.stdout)
    out["streams"] = [
        {"codec": s2["codec_name"], "w": s2.get("width"),
         "h": s2.get("height")}
        for s2 in info.get("streams", [])
    ]
    assert out["streams"], "the delivered file is not a readable image"

    rec = jobs.get(job_id) or {}
    out["record"] = {
        "provider": rec.get("provider"),
        "image_model": rec.get("image_model"),
        "image_charge_cents": rec.get("image_charge_cents"),
        "charged_cents": rec.get("charged_cents"),
        "stock_page_url": rec.get("stock_page_url"),
        "stock_credit": rec.get("stock_credit"),
        "status": rec.get("status"),
    }
    assert rec.get("provider") == "pexels", rec.get("provider")
    assert rec.get("image_charge_cents") == 0

    owner = _owner()
    after = {
        "balance_cents": int(owner.get("balance_cents", 0)),
        "free_jobs_total": int(owner.get("free_jobs_total") or 0),
    }
    out["owner_after"] = after
    assert after["balance_cents"] == before["balance_cents"], \
        "the stock creation moved the owner's balance"
    assert after["free_jobs_total"] == before["free_jobs_total"] + 1, \
        "the stock creation did not count exactly once"
    out["ok"] = True
    return out


@app.local_entrypoint()
def main(base: str):
    result = run_proof.remote(base)
    print(json.dumps(result, indent=2))
