"""Finish-run fix-up 2 (2026-11-16): two micro-closures.

1. Expire the fresh smoke session with its CLEAN id (the fix-up
   probe's parse kept the URL fragment; Stripe answered 200 but
   the status check failed). Then list again and confirm zero
   open sessions remain for the fixture.
2. Mirror the accidental run-1 job through the LIST endpoint
   (there is no GET /api/jobs/{id}; mirroring happens on list)
   and read its final record.
CPU-only, no generation.
"""

import base64
import hashlib
import hmac
import json
import os
import time

import modal

app = modal.App("moonfactory-finish-fixup2")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")
users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

SESSION_TO_EXPIRE = "cs_live_acZ78Ldxjov6OxllGESdVmKUbOuDBAg0XrA8Jnx0fB1W0mUa1TjHD2YqeW"
ACCIDENTAL_JOB = "80510862a8c4ca89"


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("moonfactory-secrets"),
        modal.Secret.from_name("moonfactory-integrations"),
        modal.Secret.from_name("moonfactory-providers"),
    ],
    timeout=600,
)
def run_fixup2(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    out = {}
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")

    er = requests.post(
        f"https://api.stripe.com/v1/checkout/sessions/{SESSION_TO_EXPIRE}/expire",
        auth=(stripe_key, ""), timeout=30)
    out["expire_clean"] = {"status": er.status_code,
                           "session_status": (er.json().get("status")
                                              if er.status_code == 200 else None)}

    acc = jobs.get(ACCIDENTAL_JOB)
    job_owner = users.get(f"id:{acc['user_id']}") if acc else None
    paid_fx = None
    for key in list(users.keys()):
        if key.startswith("id:"):
            u = users.get(key)
            if u and u.get("verified") and u.get("plan") == "creator" \
                    and (u.get("email") or "").endswith("@example.com"):
                paid_fx = u
                break

    lr = requests.get("https://api.stripe.com/v1/checkout/sessions",
                      params={"limit": 100}, auth=(stripe_key, ""), timeout=30)
    open_left = []
    if lr.status_code == 200 and paid_fx:
        for sess in lr.json().get("data", []):
            if sess.get("status") == "open" and (
                    sess.get("client_reference_id") == paid_fx["id"]
                    or sess.get("customer_email") == paid_fx["email"]):
                open_left.append(sess["id"])
    out["stripe_open_remaining_for_fixture"] = open_left

    if job_owner:
        s = requests.Session()
        s.cookies.set("mf_session",
                      make_session(job_owner["id"], os.environ["SESSION_SECRET"]))
        r = s.get(f"{base}/api/jobs", timeout=60)
        out["list_status"] = r.status_code
        if r.status_code == 200:
            mine = [j for j in r.json().get("jobs", [])
                    if j.get("id") == ACCIDENTAL_JOB]
            out["accidental_in_list"] = mine[0] if mine else None
        time.sleep(2)
        acc = jobs.get(ACCIDENTAL_JOB)
        out["accidental_job_final"] = {
            k: acc.get(k) for k in (
                "status", "lane", "charged_cents", "cost_cents",
                "settled_cost_cents", "render_seconds", "bytes",
                "has_thumb", "error", "gallery_opt_out")}
    return out


@app.local_entrypoint()
def main(base: str):
    print(json.dumps(run_fixup2.remote(base), indent=1, default=str))
