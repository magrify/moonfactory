# Moonfactory audio worker: a Kaggle kernel (script type).
#
# Makes the platform's two self-contained audio stages on
# Kaggle's free GPU quota instead of the Modal audio app
# (Jack's order, 2026-11-24: Modal is almost maxed out, use
# Kaggle for more things):
#
# - music beds with ACE-Step 1.5 in DiT-only mode
#   (acestep-v15-turbo, no language model), the exact recipe
#   the Modal stage ran: caption + fixed metadata in, a 10 s
#   instrumental wav out;
# - voiceovers with Kokoro (Apache-2.0), the platform's
#   speech stage, speaking one text in one voice at 24 kHz.
#
# The shape mirrors the voice cloning worker beside it: the
# platform queues audio_stage child jobs, wakes this kernel
# with a version push when no heartbeat is fresh, and the
# kernel claims jobs one at a time through the platform's
# worker endpoints, makes the wav, and uploads it back. The
# worker key rides in the same tiny private dataset the
# other workers use (magrify/moonfactory-hf-key); it is read
# into memory only.
#
# Setup (the ACE-Step repo and its uv environment, the
# Kokoro package and weights) happens once per session in a
# background thread before the poll loop needs it,
# heartbeating as it goes: a cold session takes minutes to
# become useful, and the platform's audio waits are sized
# for that. The loop then claims jobs one at a time and
# shuts itself down after ten idle minutes (plus a hard
# session cap) so the weekly free quota is spent on audio,
# not on waiting.
#
# The engines' weights download from Hugging Face at
# session start, the platform's standing single-copy rule:
# no model copy lives in a Kaggle dataset.

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import requests

PLATFORM = "https://magrify--moonfactory-api.us-east.modal.run"
INPUT = Path("/kaggle/input")
WORKING = Path("/kaggle/working")

IDLE_LIMIT_SECONDS = 600
SESSION_CAP_SECONDS = 55 * 60
POLL_SECONDS = 20
GEN_TIMEOUT_SECONDS = 1500

STARTED = time.time()

ACE_ROOT = WORKING / "acestep"
ACE_VENV_PYTHON = ACE_ROOT / ".venv" / "bin" / "python"

# The bed recipe, carried over from the Modal audio stage
# unchanged (modal-worker/audio_app.py GEN_SCRIPT): the
# same handler, the same config, the same generation
# parameters, so a bed made here is the bed the Modal stage
# would have made from the same caption and seed.
GEN_SCRIPT = r'''
import json, os, sys, traceback
sys.path.insert(0, os.environ["ACE_ROOT"])

from acestep.handler import AceStepHandler
from acestep.inference import GenerationParams, GenerationConfig, generate_music

with open("/tmp/ace_params.json") as fh:
    spec = json.load(fh)
caption = spec["caption"]
seed = int(spec["seed"])

handler = AceStepHandler()
handler.initialize_service(
    project_root=os.environ["ACE_ROOT"],
    config_path="acestep-v15-turbo",
    device="cuda",
)

params = GenerationParams(
    caption=caption,
    lyrics="[Instrumental]",
    instrumental=True,
    bpm=92,
    keyscale="C Major",
    timesignature="4",
    duration=10,
    inference_steps=8,
    shift=3.0,
    seed=seed,
    thinking=False,
    use_cot_metas=False,
    use_cot_caption=False,
    use_cot_lyrics=False,
    use_cot_language=False,
)
config = GenerationConfig(batch_size=1, audio_format="wav", use_random_seed=False, seeds=[seed])

try:
    result = generate_music(handler, None, params, config, save_dir="/tmp/ace_out")
except Exception:
    traceback.print_exc()
    raise

if not result.success:
    raise RuntimeError(f"ACE-Step generation failed: {result.error}")
path = result.audios[0]["path"]
print(f"GENERATED {path}", flush=True)
'''


def log(msg):
    print(f"[audio-worker] {msg}", flush=True)


def find_dataset_file(slug: str, filename: str) -> Path:
    for hit in INPUT.rglob(filename):
        if hit.parent.name == slug:
            return hit
    raise RuntimeError(f"dataset {slug} is not attached (no {filename})")


KEY_FILE = find_dataset_file("moonfactory-hf-key", "worker_key.txt")


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
        requests.post(f"{PLATFORM}/api/worker/audio/heartbeat",
                      headers=HEADERS, timeout=20)
    except Exception:
        pass


def heartbeat_loop():
    while True:
        time.sleep(60)
        heartbeat()


def pip_install(pkgs, only_binary=False):
    cmd = [sys.executable, "-m", "pip", "install", "-q"]
    if only_binary:
        cmd += ["--only-binary", ":all:"]
    cmd += pkgs
    r = subprocess.run(cmd, capture_output=True, text=True)
    tail = (r.stdout + r.stderr)[-300:]
    log(f"pip install {pkgs} -> rc={r.returncode} {tail}")
    if r.returncode == 0:
        # A package installed mid-process is invisible to
        # the import system until its caches are invalidated
        # (the directory listing was already scanned): the
        # first proof session's Kokoro import died on
        # exactly this, with pip reporting success.
        importlib.invalidate_caches()
    return r.returncode


def pip_install_checked(pkgs):
    """pip_install that raises with pip's own tail, so a
    real resolution failure reaches the platform as the
    job's error instead of a later import error."""
    if pip_install(pkgs) != 0:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + pkgs,
            capture_output=True, text=True)
        raise RuntimeError(
            f"pip install {pkgs} failed: "
            f"{(r.stdout + r.stderr)[-280:]}")


_ACE_READY = False
_KOKORO_PIPELINES = {}


def setup_ace():
    """Install ACE-Step 1.5 the project's own documented
    route (clone, uv sync), exactly as the Modal image
    build did. Runs once per session."""
    global _ACE_READY
    if _ACE_READY:
        return
    if not ACE_ROOT.exists():
        r = subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ace-step/ACE-Step-1.5.git",
             str(ACE_ROOT)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"ACE-Step clone failed: {(r.stderr or '')[-300:]}")
    heartbeat()
    if not ACE_VENV_PYTHON.exists():
        uv = shutil.which("uv")
        if uv is None:
            r = subprocess.run(
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
                shell=True, capture_output=True, text=True)
            uv = str(Path.home() / ".local" / "bin" / "uv")
            if r.returncode != 0 or not Path(uv).exists():
                raise RuntimeError(
                    f"uv install failed: {(r.stderr or '')[-300:]}")
        r = subprocess.run(
            [uv, "sync"], cwd=str(ACE_ROOT),
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not ACE_VENV_PYTHON.exists():
            raise RuntimeError(
                f"ACE-Step uv sync failed: {(r.stderr or '')[-300:]}")
    _ACE_READY = True
    log("ACE-Step ready")


def setup_kokoro():
    """Install Kokoro and prove a pipeline builds, so a
    broken speech setup fails the session's setup, not a
    claimed job. The weights download from the public
    Kokoro HF repo on first pipeline creation."""
    pip_install_checked(["kokoro>=0.9.2", "soundfile", "numpy"])
    kokoro_pipeline("af_heart")
    log("Kokoro ready")


def kokoro_pipeline(voice: str):
    lang = "b" if (voice or "").startswith("b") else "a"
    if lang not in _KOKORO_PIPELINES:
        from kokoro import KPipeline

        _KOKORO_PIPELINES[lang] = KPipeline(lang_code=lang)
    return _KOKORO_PIPELINES[lang]


def setup_engines():
    setup_ace()
    heartbeat()
    setup_kokoro()
    heartbeat()


def make_bed(job: dict) -> Path:
    """One ACE-Step bed from the job's caption and seed,
    through the project's own venv python, the Modal
    stage's subprocess discipline."""
    ensure_engine()
    caption = job.get("caption") or ""
    seed = int(job.get("seed") or 0)
    Path("/tmp/ace_params.json").write_text(
        json.dumps({"caption": caption, "seed": seed}))
    script_path = Path("/tmp/gen_bed.py")
    script_path.write_text(GEN_SCRIPT)
    out_dir = Path("/tmp/ace_out")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    env = dict(os.environ)
    env["ACE_ROOT"] = str(ACE_ROOT)
    proc = subprocess.run(
        [str(ACE_VENV_PYTHON), str(script_path)],
        capture_output=True, text=True,
        timeout=GEN_TIMEOUT_SECONDS, env=env)
    print(proc.stdout[-3000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], flush=True)
        raise RuntimeError(
            f"ACE-Step subprocess failed rc={proc.returncode}")
    wavs = sorted(out_dir.rglob("*.wav"))
    if not wavs:
        raise RuntimeError("ACE-Step produced no wav")
    return wavs[0]


def make_voiceover(job: dict) -> Path:
    """One Kokoro voiceover: the job's text in the job's
    voice, 24 kHz mono wav, the Modal stage's recipe."""
    ensure_engine()
    import numpy as np
    import soundfile as sf

    text = (job.get("text") or "").strip()
    voice = job.get("voice") or "af_heart"
    if not text:
        raise ValueError("voiceover text is empty")
    pipeline = kokoro_pipeline(voice)
    chunks = []
    for _graphemes, _phonemes, audio in pipeline(text, voice=voice):
        if audio is not None:
            chunks.append(np.asarray(audio, dtype=np.float32))
    if not chunks:
        raise RuntimeError("Kokoro produced no audio")
    data = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
    out_path = WORKING / f"voiceover-{job['id']}.wav"
    sf.write(str(out_path), data, 24000)
    return out_path


def fail_job(job_id: str, message: str):
    try:
        requests.post(
            f"{PLATFORM}/api/worker/audio/jobs/{job_id}/fail",
            headers=HEADERS, json={"error": message[:280]},
            timeout=30)
    except Exception:
        pass


_SETUP_THREAD = None
_SETUP_ERROR = None


def _setup_in_background():
    global _SETUP_ERROR
    try:
        setup_engines()
    except Exception:
        _SETUP_ERROR = traceback.format_exc()
        log(f"engine setup failed: {_SETUP_ERROR[-300:]}")


def ensure_engine():
    """Wait for the background engine setup; re-raise its
    failure so the claimed job records it."""
    if _SETUP_THREAD is not None:
        _SETUP_THREAD.join()
    if _SETUP_ERROR is not None:
        raise RuntimeError(_SETUP_ERROR[-280:])


def main():
    global _SETUP_THREAD
    heartbeat()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    # Engine setup runs in the background from boot, so it
    # overlaps the platform's audio wait instead of eating
    # it after the first claim (the voice worker's lesson).
    # A setup failure is kept and handed to the first
    # claimed job, whose record carries it back to the
    # platform; nothing fails blind.
    _SETUP_THREAD = threading.Thread(
        target=_setup_in_background, daemon=True)
    _SETUP_THREAD.start()
    idle = 0
    heartbeat()
    log("poll loop started (engine setup in background)")
    while True:
        if time.time() - STARTED > SESSION_CAP_SECONDS:
            log("session cap reached, shutting down")
            return
        try:
            resp = requests.get(
                f"{PLATFORM}/api/worker/audio/jobs/next",
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
            stage = job.get("stage")
            if stage == "bed":
                out_path = make_bed(job)
            elif stage == "voiceover":
                out_path = make_voiceover(job)
            else:
                raise RuntimeError(f"unknown audio stage {stage!r}")
            log(f"job {job['id']}: uploading result")
            with open(out_path, "rb") as fh:
                up = requests.post(
                    f"{PLATFORM}/api/worker/audio/jobs/"
                    f"{job['id']}/file",
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
