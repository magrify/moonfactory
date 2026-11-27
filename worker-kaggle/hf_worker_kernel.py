# Moonfactory HyperFrames worker: a Kaggle kernel (script type).
#
# Renders the platform's free template lane (title cards, captions
# over a clip, stat cards, promo cards, logo stings) with
# HyperFrames and sends the finished MP4 back. Motion jobs (the
# animated story short) ride the same loop: the claim payload
# flags them, the worker stages the scene script that ships in
# the toolchain dataset beside the composition, and synthesizes
# the score with the audio kit that ships there too. The 3D
# motion templates and the photo presenter (2026-11-25) ride
# the loop the same way, each with its own scene bundle staged
# from the same dataset; a presenter job also pulls its
# voiceover down and computes the mouth curve from it before
# the render. Kaggle
# datasets persist, so the whole toolchain (Node 22, the
# HyperFrames CLI, its Chromium, GSAP) is built once and carried
# in the magrify/moonfactory-hf-toolchain dataset; a session
# only extracts it and renders. The worker key rides in its own
# tiny private dataset (magrify/moonfactory-hf-key) because
# Kaggle secret attachments do not survive API pushes. The key
# is read into memory only: never printed, never copied
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
TOOLCHAIN_DIR = WORKING / "hf-toolchain"


def find_dataset_file(slug: str, filename: str) -> Path:
    """Locate one attached dataset file. Kaggle's mount layout
    has moved over the years (today it is
    /kaggle/input/datasets/<owner>/<slug>/<file>), so search the
    input tree for the dataset folder instead of hardcoding one
    generation's path."""
    for hit in INPUT.rglob(filename):
        if hit.parent.name == slug:
            return hit
    raise RuntimeError(f"dataset {slug} is not attached (no {filename})")


def find_toolchain_mount() -> Path:
    """The toolchain dataset's mount folder. The toolchain
    rides as .tar.xz archives, which Kaggle leaves as raw
    files in the mount (it only unpacks .tar.gz uploads, and
    extracting locally is far faster than copying tens of
    thousands of small files off the mount). The Node archive
    is the marker to search by."""
    for hit in INPUT.rglob("node-v22.23.5-linux-x64.tar.xz"):
        return hit.parent
    raise RuntimeError("toolchain dataset is not attached "
                       "(no node archive)")


TOOLCHAIN_MOUNT = find_toolchain_mount()
KEY_FILE = find_dataset_file("moonfactory-hf-key", "worker_key.txt")
IDLE_LIMIT_SECONDS = 600
SESSION_CAP_SECONDS = 55 * 60
POLL_SECONDS = 20
RENDER_TIMEOUT_SECONDS = 1200
# Story shorts run far longer than the card templates (a town
# scene at 30 frames a second for up to about 47 seconds of
# film), so their renders get a bigger bound that still fits
# inside the session cap.
MOTION_RENDER_TIMEOUT_SECONDS = 2400

STARTED = time.time()


def log(msg):
    print(f"[hf-worker] {msg}", flush=True)


def read_key() -> str:
    try:
        from kaggle_secrets import UserSecretsClient

        # Per-family key first (MOONFACTORY_HF_KEY), then the
        # shared legacy name, then the dataset key file below.
        for secret_name in ("MOONFACTORY_HF_KEY",
                            "MOONFACTORY_TEMPLATE_KEY"):
            value = UserSecretsClient().get_secret(secret_name)
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
    if not (TOOLCHAIN_DIR / "app").exists():
        TOOLCHAIN_DIR.mkdir(parents=True, exist_ok=True)
        log("staging toolchain from the dataset mount")
        # The archives extract straight onto the working disk:
        # fast, and the staged tree is writable, which the
        # renderer's browser cache needs.
        for name in ("node-v22.23.5-linux-x64.tar.xz",
                     "app.tar.xz", "hf-cache.tar.xz",
                     "syslibs.tar.xz"):
            with tarfile.open(TOOLCHAIN_MOUNT / name, "r:*") as tar:
                tar.extractall(TOOLCHAIN_DIR)
        (TOOLCHAIN_DIR / "gsap").mkdir(parents=True, exist_ok=True)
        shutil.copy(TOOLCHAIN_MOUNT / "gsap.min.js",
                    TOOLCHAIN_DIR / "gsap" / "gsap.min.js")
        # Motion wing assets ride as loose files in the same
        # dataset (the scene script and the score kit). A
        # toolchain dataset from before the motion wing simply
        # has none of them; motion jobs then fail with a clear
        # reason while title jobs keep rendering.
        motion_dir = TOOLCHAIN_DIR / "motion"
        motion_dir.mkdir(parents=True, exist_ok=True)
        for name in ("motion-scene.js", "motion_audio.py",
                     "dsp.py", "instruments.py",
                     "forge3d-scene.js", "presenter-scene.js",
                     "mouth_curve.py"):
            src = TOOLCHAIN_MOUNT / name
            if src.exists():
                shutil.copy(src, motion_dir / name)
        # The Node archive carries its versioned folder name;
        # the rest of the toolchain expects it at node/.
        versioned = TOOLCHAIN_DIR / "node-v22.23.5-linux-x64"
        if versioned.exists() and not (TOOLCHAIN_DIR / "node").exists():
            versioned.rename(TOOLCHAIN_DIR / "node")
        shutil.rmtree(TOOLCHAIN_DIR / "node" / "include",
                      ignore_errors=True)
        # Kaggle's dataset extraction strips execute bits, so
        # every binary the renderer runs gets its bit back:
        # anything in a bin folder, plus the known binaries by
        # name wherever they sit.
        for path in TOOLCHAIN_DIR.rglob("*"):
            try:
                if path.is_file() and (
                        path.parent.name in ("bin", ".bin")
                        or path.name in ("node", "esbuild",
                                         "chrome-headless-shell",
                                         "hyperframes")):
                    path.chmod(path.stat().st_mode | 0o111)
            except Exception:
                pass
    node_bin = TOOLCHAIN_DIR / "node" / "bin"
    cache_dir = TOOLCHAIN_DIR / "hf-cache"
    try:
        os.chmod(cache_dir, 0o700)
    except Exception:
        pass
    env = dict(os.environ)
    env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
    # Kaggle's image lacks four libraries the headless browser
    # links (libatk, libatk-bridge, libatspi, libXcomposite);
    # they ride in the toolchain dataset and are offered to the
    # loader here. Nothing else is missing: ldd on the browser
    # binary inside Kaggle named exactly those four.
    syslibs = TOOLCHAIN_DIR / "usr" / "lib" / "x86_64-linux-gnu"
    if syslibs.exists():
        env["LD_LIBRARY_PATH"] = (
            f"{syslibs}:{env.get('LD_LIBRARY_PATH', '')}")
    env["HYPERFRAMES_CACHE_DIR"] = str(cache_dir)
    env["TMPDIR"] = str(WORKING / "hf-tmp")
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    # The CLI is invoked through node on its real entry file.
    # The node_modules/.bin shortcut cannot be used: it is a
    # symlink locally, and Kaggle's dataset extraction turns
    # symlinks into empty files.
    entry = (TOOLCHAIN_DIR / "app" / "node_modules" / "hyperframes"
             / "bin" / "hyperframes.mjs")
    if not entry.exists() or entry.stat().st_size == 0:
        raise RuntimeError("hyperframes entry missing from toolchain")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg missing from the Kaggle image")
    motion_dir = TOOLCHAIN_DIR / "motion"
    motion_ready = (motion_dir / "motion-scene.js").exists() \
        and (motion_dir / "motion_audio.py").exists()
    forge_ready = (motion_dir / "forge3d-scene.js").exists()
    presenter_ready = (motion_dir / "presenter-scene.js").exists() \
        and (motion_dir / "mouth_curve.py").exists()
    log("toolchain ready")
    return {"env": env, "node": str(node_bin / "node"),
            "entry": str(entry), "motion": motion_ready,
            "forge3d": forge_ready, "presenter": presenter_ready,
            "motion_dir": str(motion_dir)}


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
        # The source lands under its real name when the claim
        # names one (the presenter's photo keeps its suffix, so
        # the scene can load it as a picture); a clip source
        # keeps the standing source.mp4 name.
        source_name = job.get("source_name") or "source.mp4"
        log(f"job {job['id']}: downloading source")
        with requests.get(
                f"{PLATFORM}/api/worker/hf/jobs/{job['id']}/source",
                headers=HEADERS, timeout=300, stream=True) as resp:
            resp.raise_for_status()
            with open(job_dir / source_name, "wb") as fh:
                for chunk in resp.iter_content(1024 * 1024):
                    fh.write(chunk)
    render_timeout = RENDER_TIMEOUT_SECONDS
    if job.get("motion"):
        # The story short: stage the scene script the
        # composition loads, then synthesize the score from the
        # plan the platform sent (music and scene change sounds
        # on the scene cuts), written beside the composition as
        # mix.wav where its audio clip points.
        if not tools["motion"]:
            raise RuntimeError("motion assets missing from the "
                               "toolchain dataset")
        shutil.copy(Path(tools["motion_dir"]) / "motion-scene.js",
                    job_dir / "motion-scene.js")
        plan = job.get("audio_plan") or {}
        duration = float(plan.get("duration") or 30.0)
        scenes = ",".join(str(float(s)) for s in plan.get("scenes") or [0])
        log(f"job {job['id']}: synthesizing the score")
        audio = subprocess.run(
            [sys.executable,
             str(Path(tools["motion_dir"]) / "motion_audio.py"),
             str(duration), scenes, str(job_dir / "mix.wav")],
            cwd=tools["motion_dir"], capture_output=True, text=True,
            timeout=900)
        if audio.returncode != 0 or not (job_dir / "mix.wav").exists():
            tail = (audio.stderr or audio.stdout or "")[-400:]
            raise RuntimeError(f"score synthesis failed: {tail}")
        render_timeout = MOTION_RENDER_TIMEOUT_SECONDS
    if job.get("forge3d"):
        # A 3D motion template: stage the dispatcher bundle the
        # composition loads. The scenes are light (no post
        # chain) but still WebGL, so they take the motion bound.
        if not tools["forge3d"]:
            raise RuntimeError("3d scene assets missing from the "
                               "toolchain dataset")
        shutil.copy(Path(tools["motion_dir"]) / "forge3d-scene.js",
                    job_dir / "forge3d-scene.js")
        render_timeout = MOTION_RENDER_TIMEOUT_SECONDS
    if job.get("presenter"):
        # The photo presenter: stage its scene bundle, pull the
        # finished voiceover the platform's Kokoro stage made,
        # and compute the mouth curve from it before rendering.
        if not tools["presenter"]:
            raise RuntimeError("presenter assets missing from the "
                               "toolchain dataset")
        shutil.copy(Path(tools["motion_dir"]) / "presenter-scene.js",
                    job_dir / "presenter-scene.js")
        log(f"job {job['id']}: downloading voiceover")
        with requests.get(
                f"{PLATFORM}/api/worker/hf/jobs/{job['id']}/voice",
                headers=HEADERS, timeout=300, stream=True) as resp:
            resp.raise_for_status()
            with open(job_dir / "voice.wav", "wb") as fh:
                for chunk in resp.iter_content(1024 * 1024):
                    fh.write(chunk)
        log(f"job {job['id']}: computing the mouth curve")
        curve = subprocess.run(
            [sys.executable,
             str(Path(tools["motion_dir"]) / "mouth_curve.py"),
             str(job_dir / "voice.wav"), "24",
             str(job_dir / "mouth-curve.js")],
            cwd=tools["motion_dir"], capture_output=True, text=True,
            timeout=300)
        if curve.returncode != 0 or not (job_dir / "mouth-curve.js").exists():
            tail = (curve.stderr or curve.stdout or "")[-400:]
            raise RuntimeError(f"mouth curve failed: {tail}")
        render_timeout = MOTION_RENDER_TIMEOUT_SECONDS
    out_path = job_dir / "out.mp4"
    log(f"job {job['id']}: rendering")
    proc = subprocess.run(
        [tools["node"], tools["entry"], "render", "-o", str(out_path),
         "-q", "standard", "--workers", "2"],
        cwd=job_dir, env=tools["env"],
        capture_output=True, text=True, timeout=render_timeout)
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
    heartbeat()
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
