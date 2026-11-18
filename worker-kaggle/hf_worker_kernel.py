# Moonfactory HyperFrames worker: a Kaggle kernel (script type).
#
# Renders the platform's free template lane (title cards, captions
# over a clip, stat cards, promo cards) with HyperFrames and sends
# the finished MP4 back. Kaggle datasets persist, so the whole
# toolchain (Node 22, the HyperFrames CLI, its Chromium, GSAP) is
# built once and carried in the magrify/moonfactory-hf-toolchain
# dataset; a session only extracts it and renders. The worker key
# rides in its own tiny private dataset (magrify/moonfactory-hf-key)
# because Kaggle secret attachments do not survive API pushes.
# The key is read into memory only: never printed, never copied
# anywhere else.
#
# The loop heartbeats while alive, claims queued jobs one at a
# time, and shuts itself down after ten idle minutes (plus a hard
# session cap) so the weekly free quota is spent on renders, not
# on waiting.

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from pathlib import Path

import requests

PLATFORM = "https://magrify--moonfactory-api.us-east.modal.run"
INPUT = Path("/kaggle/input")
WORKING = Path("/kaggle/working")
TOOLCHAIN_TAR = INPUT / "moonfactory-hf-toolchain" / "toolchain.tar.gz"
KEY_FILE = INPUT / "moonfactory-hf-key" / "worker_key.txt"
TOOLCHAIN_DIR = WORKING / "hf-toolchain"
IDLE_LIMIT_SECONDS = 600
SESSION_CAP_SECONDS = 55 * 60
POLL_SECONDS = 20
RENDER_TIMEOUT_SECONDS = 1200

STARTED = time.time()


def log(msg):
    print(f"[hf-worker] {msg}", flush=True)


def read_key() -> str:
    try:
        from kaggle_secrets import UserSecretsClient

        value = UserSecretsClient().get_secret("MOONFACTORY_TEMPLATE_KEY")
        if value:
            return value.strip()
    except Exception:
        pass
    return KEY_FILE.read_text().strip()


KEY = read_key()
HEADERS = {"X-API-Key": KEY}


def heartbeat():
    try:
        requests.post(f"{PLATFORM}/api/worker/hf/heartbeat",
                      headers=HEADERS, timeout=20)
    except Exception:
        pass


def setup_toolchain() -> dict:
    """Extract the persistent toolchain dataset and return the
    environment the renderer runs with. Nothing is installed:
    Node, the CLI and the browser all ride in the tar."""
    if not TOOLCHAIN_TAR.exists():
        raise RuntimeError(f"toolchain dataset missing at {TOOLCHAIN_TAR}")
    if not (TOOLCHAIN_DIR / "app").exists():
        TOOLCHAIN_DIR.mkdir(parents=True, exist_ok=True)
        log("extracting toolchain dataset")
        with tarfile.open(TOOLCHAIN_TAR, "r:gz") as tar:
            tar.extractall(TOOLCHAIN_DIR)
    node_bin = TOOLCHAIN_DIR / "node" / "bin"
    cache_dir = TOOLCHAIN_DIR / "hf-cache"
    try:
        os.chmod(cache_dir, 0o700)
    except Exception:
        pass
    env = dict(os.environ)
    env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
    env["HYPERFRAMES_CACHE_DIR"] = str(cache_dir)
    env["TMPDIR"] = str(WORKING / "hf-tmp")
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    cli = TOOLCHAIN_DIR / "app" / "node_modules" / ".bin" / "hyperframes"
    if not cli.exists():
        raise RuntimeError("hyperframes CLI missing from toolchain")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg missing from the Kaggle image")
    log("toolchain ready")
    return {"env": env, "cli": str(cli)}


def render_job(job: dict, tools: dict) -> Path:
    job_dir = WORKING / f"job-{job['id']}"
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True)
    (job_dir / "index.html").write_text(job["html"])
    shutil.copy(TOOLCHAIN_DIR / "gsap" / "gsap.min.js",
                job_dir / "gsap.min.js")
    (job_dir / "hyperframes.json").write_text(json.dumps({
        "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
        "paths": {"blocks": "compositions",
                  "components": "compositions/components",
                  "assets": "assets"},
        "media": {"autoProxy": False},
    }))
    if job.get("has_source"):
        log(f"job {job['id']}: downloading source clip")
        with requests.get(
                f"{PLATFORM}/api/worker/hf/jobs/{job['id']}/source",
                headers=HEADERS, timeout=300, stream=True) as resp:
            resp.raise_for_status()
            with open(job_dir / "source.mp4", "wb") as fh:
                for chunk in resp.iter_content(1024 * 1024):
                    fh.write(chunk)
    out_path = job_dir / "out.mp4"
    log(f"job {job['id']}: rendering")
    proc = subprocess.run(
        [tools["cli"], "render", "-o", str(out_path),
         "-q", "standard", "--workers", "2"],
        cwd=job_dir, env=tools["env"],
        capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS)
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"render failed ({proc.returncode}): {tail}")
    return out_path


def fail_job(job_id: str, message: str):
    try:
        requests.post(f"{PLATFORM}/api/worker/hf/jobs/{job_id}/fail",
                      headers=HEADERS, json={"error": message[:280]},
                      timeout=30)
    except Exception:
        pass


def main():
    tools = setup_toolchain()
    idle = 0
    heartbeat()
    log("poll loop started")
    while True:
        if time.time() - STARTED > SESSION_CAP_SECONDS:
            log("session cap reached, shutting down")
            return
        try:
            resp = requests.get(f"{PLATFORM}/api/worker/hf/jobs/next",
                                headers=HEADERS, timeout=30)
            resp.raise_for_status()
            job = resp.json().get("job")
        except Exception as exc:
            log(f"claim failed: {exc}")
            job = None
        if not job:
            idle += POLL_SECONDS
            if idle >= IDLE_LIMIT_SECONDS:
                log("idle limit reached, shutting down")
                return
            time.sleep(POLL_SECONDS)
            heartbeat()
            continue
        idle = 0
        try:
            out_path = render_job(job, tools)
            log(f"job {job['id']}: uploading result")
            with open(out_path, "rb") as fh:
                up = requests.post(
                    f"{PLATFORM}/api/worker/hf/jobs/{job['id']}/file",
                    headers=HEADERS, data=fh, timeout=600)
            up.raise_for_status()
            log(f"job {job['id']}: done")
        except Exception:
            message = traceback.format_exc()[-280:]
            log(f"job {job['id']}: failed: {message}")
            fail_job(job["id"], message)
        heartbeat()


if __name__ == "__main__":
    main()
