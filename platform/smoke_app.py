"""End-to-end smoke test for the Moonfactory platform.

Runs inside Modal because this sandbox's egress proxy stalls POSTs to
*.modal.run (GETs pass). The client here talks to the platform's public
URL over the real internet, exactly like a user's browser would:

  signup (with proof-of-work bot check) -> read the verification email
  from the operator outbox -> verify -> login -> submit ONE draft job
  -> poll to done -> download the file -> ffprobe it (video + audio).

Usage:
  modal run smoke_app.py --base https://<platform-url>
"""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-smoke")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("requests")
)


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
    timeout=3000,
)
def run_smoke(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    out = {"base": base, "steps": {}}
    s = requests.Session()

    r = s.get(f"{base}/healthz", timeout=30)
    out["steps"]["healthz"] = r.status_code
    assert r.status_code == 200, r.text[:200]

    r = s.get(base + "/", timeout=30)
    out["steps"]["landing_bytes"] = len(r.content)
    assert "Moonfactory" in r.text

    email = f"smoke-{int(time.time())}@example.com"
    password = "smoke-test-password-1"

    def bot_fields():
        c = s.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"], c["difficulty_bits"]),
        }

    r = s.post(f"{base}/api/signup",
               json={"email": email, "password": password, **bot_fields()},
               timeout=30)
    out["steps"]["signup"] = r.status_code
    assert r.status_code == 200, r.text[:300]

    admin = os.environ["ADMIN_KEY"]
    r = s.get(f"{base}/api/admin/outbox", headers={"X-Admin-Key": admin}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    link = None
    for item in reversed(r.json()["items"]):
        if item["to"] == email:
            m = re.search(r"/api/verify\?token=([A-Za-z0-9_\-]+)", item["body"])
            if m:
                link = f"{base}/api/verify?token={m.group(1)}"
                break
    assert link, "verification email not found in outbox"
    out["steps"]["verify_email_found"] = True

    r = s.get(link, timeout=30, allow_redirects=False)
    out["steps"]["verify"] = r.status_code
    assert r.status_code in (303, 200), r.text[:200]

    r = s.post(f"{base}/api/login",
               json={"email": email, "password": password}, timeout=30)
    out["steps"]["login"] = r.status_code
    assert r.status_code == 200, r.text[:300]
    assert r.json()["user"]["verified"] is True

    r = s.post(f"{base}/api/jobs",
               json={"lane": "draft",
                     "prompt": ("a red hot air balloon slowly rising into the sky "
                                "over rolling green hills, white clouds drifting, "
                                "gentle forward camera movement, bright daylight"),
                     **bot_fields()},
               timeout=60)
    out["steps"]["job_create"] = r.status_code
    assert r.status_code == 202, r.text[:300]
    job_id = r.json()["id"]
    out["job_id"] = job_id

    status = None
    deadline = time.time() + 2400
    while time.time() < deadline:
        r = s.get(f"{base}/api/jobs", timeout=30)
        mine = [j for j in r.json()["jobs"] if j["id"] == job_id]
        if mine:
            status = mine[0]["status"]
            if status in ("done", "error"):
                out["job_error"] = mine[0].get("error")
                break
        time.sleep(30)
    out["steps"]["job_final_status"] = status
    assert status == "done", f"job ended as {status}: {out.get('job_error')}"

    r = s.get(f"{base}/api/jobs/{job_id}/file", timeout=300)
    out["steps"]["file"] = r.status_code
    assert r.status_code == 200, r.text[:200]
    path = Path("/tmp/smoke.mp4")
    path.write_bytes(r.content)
    out["file_bytes"] = len(r.content)
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    info = json.loads(probe.stdout)
    out["streams"] = [
        {"type": s2["codec_type"], "codec": s2["codec_name"],
         "w": s2.get("width"), "h": s2.get("height")}
        for s2 in info["streams"]
    ]
    out["duration"] = float(info["format"]["duration"])
    kinds = {s2["codec_type"] for s2 in info["streams"]}
    assert "video" in kinds and "audio" in kinds, f"missing streams: {out['streams']}"
    out["ok"] = True
    return out


@app.local_entrypoint()
def main(base: str):
    result = run_smoke.remote(base)
    print(json.dumps(result, indent=2))
