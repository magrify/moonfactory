"""Finish-run fix-up probe (2026-11-16): closes the three loose
ends from finish_smoke run 2.

1. Stripe: the smoke probe lacked the moonfactory-providers
   secret, so its session-expire calls used an empty key (401).
   This probe attaches the platform's exact secret list, lists
   today's still-open checkout sessions for the reused paid
   fixture, expires them, then creates one fresh top-up session
   through the platform and expires it properly (full id kept).
   No session is ever paid; no charge is possible.
2. Handle: set a unique handle on the reused never-paid free
   fixture, verify via /api/me, then set it to the fixture's
   derived display handle (creator-<id6>) to approximate its
   original unset state (the API has no unset path).
3. The accidental run-1 job (80510862a8c4ca89): poll it as its
   owner so the platform mirrors the worker state, then read the
   final record (status, settled cost) for the run report.

CPU-only; no generation is created by this probe.
"""

import base64
import hashlib
import hmac
import json
import os
import time

import modal

app = modal.App("moonfactory-finish-fixup")

image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

OWNER_EMAIL = "support@magrify.com"
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
def run_fixup(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    out = {}
    secret = os.environ["SESSION_SECRET"]
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    out["stripe_key_mode"] = (
        "live" if stripe_key.startswith(("sk_live_", "rk_live_")) else
        "test" if stripe_key.startswith(("sk_test_", "rk_test_")) else "off")

    def sess_for(rec_user):
        s = requests.Session()
        s.cookies.set("mf_session", make_session(rec_user["id"], secret))
        return s

    fixtures = []
    for key in list(users.keys()):
        if key.startswith("id:"):
            u = users.get(key)
            if u and (u.get("email") or "").endswith("@example.com"):
                fixtures.append(u)
    fixtures.sort(key=lambda u: u.get("created_at") or 0)
    free_fx = next((u for u in fixtures
                    if u.get("plan") == "free" and u.get("verified")
                    and float(u.get("net_paid_cents") or 0) == 0
                    and int(u.get("balance_cents") or 0) == 0), None)
    paid_fx = next((u for u in fixtures
                    if u.get("verified") and u.get("plan") == "creator"), None)
    out["fixtures"] = {"free_id": free_fx["id"] if free_fx else None,
                       "paid_id": paid_fx["id"] if paid_fx else None}

    # -- 3. accidental job: mirror + final record -------------------------
    acc = jobs.get(ACCIDENTAL_JOB)
    if acc:
        owner_of_job = users.get(f"id:{acc['user_id']}")
        if owner_of_job:
            s = sess_for(owner_of_job)
            r = s.get(f"{base}/api/jobs/{ACCIDENTAL_JOB}", timeout=60)
            out["accidental_poll_status"] = r.status_code
        time.sleep(2)
        acc = jobs.get(ACCIDENTAL_JOB)
        out["accidental_job_final"] = {
            k: acc.get(k) for k in (
                "status", "lane", "charged_cents", "cost_cents",
                "settled_cost_cents", "render_seconds", "bytes",
                "has_thumb", "error")}
    else:
        out["accidental_job_final"] = None

    # -- 2. handle round trip on the free fixture --------------------------
    if free_fx:
        s = sess_for(free_fx)
        uniq = f"finishsmoke{int(time.time()) % 100000}"
        r = s.post(f"{base}/api/me/handle", json={"handle": uniq}, timeout=30)
        me = s.get(f"{base}/api/me", timeout=30).json()
        ok = r.status_code == 200 and me.get("handle") == uniq
        derived = "creator-" + free_fx["id"][:6]
        r2 = s.post(f"{base}/api/me/handle",
                    json={"handle": derived}, timeout=30)
        out["handle"] = {"set_status": r.status_code, "verified": ok,
                         "restored_to": derived,
                         "restore_status": r2.status_code}

    # -- 1. stripe: expire today's open sessions for the paid fixture,
    #       then one clean create+expire ----------------------------------
    if paid_fx and stripe_key:
        s = sess_for(paid_fx)
        lr = requests.get(
            "https://api.stripe.com/v1/checkout/sessions",
            params={"limit": 100}, auth=(stripe_key, ""), timeout=30)
        out["stripe_list_status"] = lr.status_code
        expired, seen_open = [], 0
        if lr.status_code == 200:
            day_start = time.time() - 6 * 3600
            for sess in lr.json().get("data", []):
                if sess.get("status") != "open":
                    continue
                if (sess.get("client_reference_id") != paid_fx["id"]
                        and sess.get("customer_email") != paid_fx["email"]):
                    continue
                if float(sess.get("created") or 0) < day_start:
                    continue
                seen_open += 1
                er = requests.post(
                    "https://api.stripe.com/v1/checkout/sessions/"
                    f"{sess['id']}/expire",
                    auth=(stripe_key, ""), timeout=30)
                if er.status_code == 200:
                    expired.append(sess["id"])
        out["stripe_open_found"] = seen_open
        out["stripe_expired"] = expired

        r = s.post(f"{base}/api/checkout/topup",
                   json={"pack_cents": 500}, timeout=30)
        if r.status_code == 200 and r.json().get("url"):
            sid = r.json()["url"].rsplit("/", 1)[-1].split("?")[0]
            er = requests.post(
                f"https://api.stripe.com/v1/checkout/sessions/{sid}/expire",
                auth=(stripe_key, ""), timeout=30)
            out["stripe_fresh_create_expire"] = {
                "session": sid, "expire_status": er.status_code,
                "expired": er.status_code == 200
                and er.json().get("status") == "expired"}
        else:
            out["stripe_fresh_create_expire"] = {
                "error": f"{r.status_code} {r.text[:150]}"}
    return out


@app.local_entrypoint()
def main(base: str):
    print(json.dumps(run_fixup.remote(base), indent=1, default=str))
