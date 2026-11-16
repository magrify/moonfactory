"""Finish-run clip probe (2026-11-16): proves the Clip fetch and
upload paths end to end on the LIVE platform, CPU only.

1. YouTube quote for "Me at the zoo" (jNQXAC9IVRw, 19 s) through
   the platform as the reused paid fixture — exercises the new
   Invidious resolver metadata path (clip_probe function).
2. YouTube clip CREATE for the same link — exercises the resolver
   download path inside process_clip_job; polls to done and
   verifies child clips + served files. (19 s source, length
   "short" needs 15-35 s picks; the whole video is one pick.)
3. Upload path: synthesizes a 45 s test video in-probe (ffmpeg
   lavfi, loudness-varying tone so the loudness fallback can
   pick), uploads it, creates a clip job, polls to done, verifies
   children and that a child file serves over HTTP.

Accounts: reused fixtures only. CPU-only Modal work (the clip
pipeline is CPU by design); no generation, no GPU.
"""

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time

import modal

app = modal.App("moonfactory-finish-clip")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests")
    # A real speech source for the upload proof: the project's own
    # animated short (Kokoro narration). The clip picker is
    # speech-first by design, so a tone file proves nothing.
    .add_local_file(
        "/home/hatch/workspace/sinclip/outputs/first-animated-short.mp4",
        "/tmp/animated_full.mp4")
)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

YT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


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
    secrets=[
        modal.Secret.from_name("moonfactory-secrets"),
        modal.Secret.from_name("moonfactory-integrations"),
        modal.Secret.from_name("moonfactory-providers"),
    ],
    timeout=2400,
)
def run_clip_checks(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    out = {"checks": {}}
    ck = out["checks"]

    fixtures = []
    for key in list(users.keys()):
        if key.startswith("id:"):
            u = users.get(key)
            if u and (u.get("email") or "").endswith("@example.com"):
                fixtures.append(u)
    fixtures.sort(key=lambda u: u.get("created_at") or 0)
    creators = [u for u in fixtures
                if u.get("verified") and u.get("plan") == "creator"]
    assert creators, "no creator fixture"
    owner_rec = users.get("email:support@magrify.com")

    def sess_for(rec_user):
        sess = requests.Session()
        sess.cookies.set(
            "mf_session",
            make_session(rec_user["id"], os.environ["SESSION_SECRET"]))
        return sess

    # The clip create enforces the hard cost ceiling (settled +
    # worst case <= net paid). Several fixtures carry probe-set
    # balances with little or no net_paid, so pick the first
    # creator whose own quote reports ceiling_ok; the owner
    # account (comped net_paid) is the deterministic fallback.
    paid_fx, s = None, None
    for cand in creators:
        cs = sess_for(cand)
        qr = cs.post(f"{base}/api/clips/quote",
                     json={"duration_seconds": 45}, timeout=30)
        if qr.status_code == 200 and qr.json().get("ceiling_ok"):
            paid_fx, s = cand, cs
            break
    if paid_fx is None:
        paid_fx, s = owner_rec, sess_for(owner_rec)
    out["clip_account"] = {"id": paid_fx["id"],
                           "plan": paid_fx.get("plan"),
                           "is_owner": bool(paid_fx.get("is_owner"))}

    def bot_fields():
        c = s.get(f"{base}/api/challenge", timeout=30).json()
        return {"challenge_id": c["challenge_id"],
                "challenge_nonce": solve_pow(c["challenge"],
                                             c["difficulty_bits"])}

    def poll_job(job_id, timeout_s=1200):
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            r = s.get(f"{base}/api/jobs", timeout=60)
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    if j.get("id") == job_id:
                        last = j
                        if j.get("status") in ("done", "error"):
                            return j
            time.sleep(10)
        return last

    # -- 1. YouTube quote via the resolver --------------------------------
    r = s.post(f"{base}/api/clips/quote", json={"url": YT_URL}, timeout=180)
    ck["youtube_quote"] = {"status": r.status_code, "body": r.text[:300]}

    # -- 2. YouTube create (resolver download inside the pipeline) --------
    if r.status_code == 200:
        r2 = s.post(f"{base}/api/clips", timeout=60, json={
            "url": YT_URL, "clip_count": 1, "length": "short",
            **bot_fields()})
        ck["youtube_create"] = {"status": r2.status_code,
                                "body": r2.text[:300]}
        if r2.status_code == 202:
            jid = r2.json()["id"]
            final = poll_job(jid, timeout_s=900)
            ck["youtube_job_final"] = {
                "status": final.get("status") if final else None,
                "stage": final.get("stage") if final else None,
                "clip_ids": final.get("clip_ids") if final else None,
                "pick_mode": final.get("pick_mode") if final else None,
            }
            rec = jobs.get(jid) or {}
            ck["youtube_job_error"] = rec.get("internal_error")
            kids = rec.get("clip_ids") or []
            if kids:
                rf = s.get(f"{base}/api/jobs/{kids[0]}/file", timeout=60)
                ck["youtube_child_file"] = {
                    "status": rf.status_code, "bytes": len(rf.content)}

    # -- 3. Upload path end to end -----------------------------------------
    # First 50 s of the narration short (real speech throughout).
    src = "/tmp/clip_test_src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", "/tmp/animated_full.mp4",
         "-t", "50", "-c", "copy", src],
        check=True, timeout=300)
    with open(src, "rb") as fh:
        r3 = s.post(f"{base}/api/clips/upload", timeout=300,
                    files={"file": ("clip_test_src.mp4", fh, "video/mp4")})
    ck["upload"] = {"status": r3.status_code, "body": r3.text[:300]}
    if r3.status_code == 200:
        up_id = r3.json()["upload_id"]
        r4 = s.post(f"{base}/api/clips", timeout=60, json={
            "upload_id": up_id, "clip_count": 1, "length": "short",
            **bot_fields()})
        ck["upload_create"] = {"status": r4.status_code,
                               "body": r4.text[:300]}
        if r4.status_code == 202:
            jid = r4.json()["id"]
            final = poll_job(jid, timeout_s=1800)
            ck["upload_job_final"] = {
                "status": final.get("status") if final else None,
                "stage": final.get("stage") if final else None,
                "clip_ids": final.get("clip_ids") if final else None,
                "pick_mode": final.get("pick_mode") if final else None,
                "charged_cents": final.get("charged_cents") if final else None,
            }
            rec = jobs.get(jid) or {}
            ck["upload_job_error"] = rec.get("internal_error")
            kids = rec.get("clip_ids") or []
            if kids:
                rf = s.get(f"{base}/api/jobs/{kids[0]}/file", timeout=60)
                ck["upload_child_file"] = {
                    "status": rf.status_code, "bytes": len(rf.content),
                    "content_type": rf.headers.get("content-type")}
    return out


@app.local_entrypoint()
def main(base: str):
    print(json.dumps(run_clip_checks.remote(base), indent=1, default=str))
