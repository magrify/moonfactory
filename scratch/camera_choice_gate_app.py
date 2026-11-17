"""Camera choice gate probe (2026-11-16).

Proves against the LIVE platform, with NO generation of any kind,
that a Fun Camera job body carrying a user-chosen camera_speed flows
through create_job's resolution path and is stopped at the
pre-dispatch gates exactly like a body without one — for several
distinct speeds. Every probe is shaped to be rejected BEFORE any
quote, hold, or dispatch (draft-lane 422 for Fun Camera, the free
account's paid gate on Production, image validation), and the probe
asserts the jobs Dict and the reused fixture's counters do not move.

Accounts: REUSES one existing strict-free test fixture from the
users Dict (plan free, verified, net_paid 0, balance 0 — unpaid by
every definition the platform uses). Never creates an account.
Sessions are minted with SESSION_SECRET (the established probe
pattern; no passwords used). CPU-only; runs inside Modal because
this sandbox's egress proxy stalls POSTs to *.modal.run.

Usage:
  modal run camera_choice_gate_app.py --base https://<platform-url>
"""

import base64
import hashlib
import hmac
import json
import os
import time

import modal

app = modal.App("moonfactory-camera-choice-gate")

image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

# A valid 1x1 PNG (passes the platform's shape check: PNG magic, < 10 MB).
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-secrets")],
    timeout=600,
)
def run_gate(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    checks = []
    out = {"base": base, "checks": checks}

    def rec(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})

    def bot_fields(sess):
        c = sess.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"], c["difficulty_bits"]),
        }

    # -- fixture: reuse a strict-free account, never create ------------
    free_fx = None
    for key in list(users.keys()):
        if not key.startswith("id:"):
            continue
        u = users.get(key)
        if (
            u
            and (u.get("email") or "").endswith("@example.com")
            and u.get("plan") == "free"
            and u.get("verified")
            and float(u.get("net_paid_cents") or 0) == 0
            and int(u.get("balance_cents") or 0) == 0
        ):
            free_fx = u
            break
    rec("free_fixture_found", free_fx is not None)
    if free_fx is None:
        return out

    sess = requests.Session()
    sess.cookies.set(
        "mf_session", make_session(free_fx["id"], os.environ["SESSION_SECRET"])
    )

    presets = sess.get(f"{base}/api/camera-presets", timeout=30).json()
    fun_ids = [
        p["id"]
        for p in presets.get("presets") or []
        if p.get("mechanism") == "workflow_patch" and p.get("available")
    ]
    rec("fun_presets_served", len(fun_ids) > 0, f"{len(fun_ids)} available")
    if not fun_ids:
        return out
    fun_id = "fun-pan-left" if "fun-pan-left" in fun_ids else fun_ids[0]

    def fixture_counters():
        u = users.get(f"id:{free_fx['id']}") or {}
        return (
            int(u.get("free_jobs_total") or 0),
            int(u.get("balance_cents") or 0),
            float(u.get("net_paid_cents") or 0),
        )

    jobs_before = len(list(jobs.keys()))
    counters_before = fixture_counters()

    def post_job(extra):
        body = {
            "model": "wan2.2",
            "prompt": "a red balloon over a quiet harbor",
            "camera_presets": [fun_id],
            "start_image_png_b64": TINY_PNG_B64,
            "aspect_ratio": "9:16",
            "segments": 1,
            **bot_fields(sess),
            **extra,
        }
        r = sess.post(f"{base}/api/jobs", json=body, timeout=60)
        try:
            detail = (r.json() or {}).get("detail", "")
        except Exception:
            detail = r.text[:200]
        return r.status_code, str(detail)

    # Fun Camera on the Draft lane is a clean 422 raised AFTER the
    # resolver runs — the speed in the body (or its absence) must not
    # change that, and nothing may dispatch.
    for label, extra in [
        ("speed_2.0", {"lane": "draft", "camera_speed": 2.0}),
        ("speed_1.0", {"lane": "draft", "camera_speed": 1.0}),
        ("speed_99_clamped", {"lane": "draft", "camera_speed": 99}),
        ("no_speed", {"lane": "draft"}),
    ]:
        status, detail = post_job(extra)
        rec(
            f"draft_lane_gate[{label}]",
            status == 422 and "Production lane" in detail,
            f"{status} {detail}",
        )

    # A non-numeric speed is a clean 422 from the parse, before dispatch.
    status, detail = post_job({"lane": "production", "camera_speed": "abc"})
    rec(
        "non_numeric_speed_422",
        status == 422 and "must be a number" in detail,
        f"{status} {detail}",
    )

    # Production lane + chosen speed 2.5: the free account's paid gate
    # stops it before dispatch (no 2xx, no job record).
    status, detail = post_job({"lane": "production", "camera_speed": 2.5})
    rec(
        "production_free_gate[speed_2.5]",
        status in (400, 403, 422),
        f"{status} {detail}",
    )

    # Invalid image + chosen speed: image validation 422, no dispatch.
    status, detail = post_job(
        {"lane": "production", "camera_speed": 2.0,
         "start_image_png_b64": "not-an-image"}
    )
    rec("invalid_image_422", status == 422, f"{status} {detail}")

    jobs_after = len(list(jobs.keys()))
    counters_after = fixture_counters()
    rec(
        "nothing_dispatched",
        jobs_after == jobs_before,
        f"jobs {jobs_before} -> {jobs_after}",
    )
    rec(
        "fixture_counters_unchanged",
        counters_after == counters_before,
        f"{counters_before} -> {counters_after}",
    )
    out["fun_preset_used"] = fun_id
    out["all_ok"] = all(c["ok"] for c in checks)
    return out


@app.local_entrypoint()
def main(base: str):
    out = run_gate.remote(base)
    for c in out["checks"]:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"] + f" — {c['detail']}")
    print("fun preset used:", out.get("fun_preset_used"))
    if not out.get("all_ok"):
        raise SystemExit(1)
    print("ALL GATE CHECKS PASS")
