"""Image-shelf boundary probe (2026-11-17): proves the bot-check
fix and the new size wiring at the provider boundary, with no paid
generation and no worker dispatch.

Runs inside Modal because this sandbox's egress proxy stalls POSTs
to *.modal.run (GETs pass). It reuses one existing paid test
fixture (@example.com), mints its session with the platform's own
signing (the established probe pattern), solves the proof-of-work
bot check exactly like the smoke test, and submits two shelf image
jobs on $0-balance provider lanes (Wiro Seedream 5.5, SpicyAPI
Z-Image). Expected: both get PAST the bot check, reach the provider
stage, fail clean on the providers' $0 balances, and refund the
submit-time charge in full. It also reads a live quote for every
lane/size to verify per-size pricing end to end.

CPU-only; the house lane and all video lanes are never touched.

Usage:
  modal run scratch/image_boundary_probe_app.py \
      --base https://magrify--moonfactory-api.us-east.modal.run
"""

import base64
import hashlib
import hmac
import json
import time

import modal

app = modal.App("moonfactory-image-boundary-probe")

image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

SUBMITS = [
    {"prompt": "a red barn at dawn, light fog",
     "image_model": "seedream55_unc", "image_size": "1K"},
    {"prompt": "a lighthouse at dusk, waves crashing",
     "image_model": "zimage_unc", "image_size": "1024x1536"},
]

QUOTES = [
    ("house", None),
    ("nano_banana_21", "1K"), ("nano_banana_21", "2K"),
    ("nano_banana_21", "4K"),
    ("nano_banana_pro", "1K"), ("nano_banana_pro", "2K"),
    ("nano_banana_pro", "4K"),
    ("nano_banana_2", "1K"), ("nano_banana_2", "2K"),
    ("nano_banana_2", "4K"),
    ("gpt_image_26", "1024x1024"),
    ("seedream55_unc", "1K"), ("seedream55_unc", "1.5K"),
    ("seedream55_unc", "2K"),
    ("seedream5pro_unc", "1K"), ("seedream5pro_unc", "1.5K"),
    ("seedream5pro_unc", "2K"),
    ("zimage_unc", "1024x1024"), ("zimage_unc", "1024x1536"),
    ("zimage_unc", "1536x1024"), ("zimage_unc", "1536x1536"),
]


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def run_probe(base: str) -> dict:
    import os

    import requests

    base = base.rstrip("/")
    out = {"base": base}

    # Pick an existing paid fixture with balance and ceiling room.
    fixture = None
    for key in list(users.keys()):
        if not key.startswith("id:"):
            continue
        u = users.get(key)
        if not u or not (u.get("email") or "").endswith("@example.com"):
            continue
        if u.get("is_owner") or not u.get("verified") or u.get("past_due"):
            continue
        paid = (u.get("plan") == "creator"
                or float(u.get("net_paid_cents") or 0.0) > 0)
        headroom = (float(u.get("net_paid_cents") or 0.0)
                    - float(u.get("cost_settled_cents") or 0.0))
        if paid and int(u.get("balance_cents") or 0) >= 20 and headroom >= 3.0:
            fixture = u
            break
    assert fixture, "no suitable paid fixture found"
    out["fixture_email"] = fixture["email"]
    out["balance_before"] = int(fixture.get("balance_cents") or 0)

    secret = os.environ["SESSION_SECRET"]
    s = requests.Session()
    s.cookies.set("mf_session", make_session(fixture["id"], secret))

    r = s.get(f"{base}/api/me", timeout=30)
    out["me_status"] = r.status_code
    assert r.status_code == 200, r.text[:200]

    def bot_fields():
        c = s.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"], c["difficulty_bits"]),
        }

    quotes = {}
    for model_id, size in QUOTES:
        body = {"prompt": "quote probe"}
        if model_id == "house":
            body["aspect_ratio"] = "1:1"
        else:
            body["image_model"] = model_id
            body["image_size"] = size
        r = s.post(f"{base}/api/images/quote", json=body, timeout=30)
        key = f"{model_id}/{size or '1:1'}"
        quotes[key] = ({"status": r.status_code,
                        "price_cents": r.json().get("price_cents")}
                       if r.status_code == 200 else
                       {"status": r.status_code, "body": r.text[:160]})
    out["quotes"] = quotes

    submits = []
    for spec in SUBMITS:
        r = s.post(f"{base}/api/images",
                   json={**spec, **bot_fields()}, timeout=60)
        entry = {"model": spec["image_model"], "size": spec["image_size"],
                 "status": r.status_code, "body": r.text[:240]}
        submits.append(entry)
    out["submits"] = submits

    after = users.get(f"id:{fixture['id']}")
    out["balance_after"] = int(after.get("balance_cents") or 0)
    out["balance_restored"] = out["balance_after"] == out["balance_before"]
    return out


@app.local_entrypoint()
def main(base: str):
    result = run_probe.remote(base)
    print(json.dumps(result, indent=2))
