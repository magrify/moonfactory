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
#   The engine is the genuine PyTorch kokoro package, the
#   Modal stage's own recipe, run as a subprocess under a
#   Python 3.12 uv venv (the package cannot install on
#   Kaggle's Python 3.13 at all); the ONNX export of the
#   same weights stays in the code as a fallback only, and
#   the log says which engine made each voiceover file.
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

KOKORO_ROOT = WORKING / "kokoro"
KOKORO_VENV_PYTHON = KOKORO_ROOT / ".venv" / "bin" / "python"
KOKORO_HF_HOME = WORKING / "kokoro-hf-cache"
# misaki's English G2P loads the en_core_web_sm spaCy
# model and tries to download it at first use through a
# package installer a uv venv does not carry; the model
# wheel is installed during setup instead, pinned to the
# spaCy 3.8 line the kokoro install resolves.
SPACY_MODEL_WHEEL = (
    "https://github.com/explosion/spacy-models/releases/"
    "download/en_core_web_sm-3.8.0/"
    "en_core_web_sm-3.8.0-py3-none-any.whl")

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

# The voiceover recipe, carried over from the Modal audio
# stage unchanged (modal-worker/audio_app.py
# VOICEOVER_SCRIPT): the same KPipeline call, the same
# voice and language selection, the same 24 kHz mono wav,
# run under the Python 3.12 venv python because the
# PyTorch kokoro package cannot install on this kernel's
# Python 3.13. The pipeline chunks the text into sentences
# itself, exactly as it did on Modal.
VOICEOVER_SCRIPT = r'''
import json, sys
import numpy as np
import soundfile as sf
from kokoro import KPipeline

with open("/tmp/vo_params.json") as fh:
    spec = json.load(fh)
text = spec["text"]
voice = spec["voice"] or "af_heart"
lang = "b" if voice.startswith("b") else "a"
pipeline = KPipeline(lang_code=lang)
chunks = []
for _graphemes, _phonemes, audio in pipeline(text, voice=voice):
    if audio is not None:
        chunks.append(np.asarray(audio, dtype=np.float32))
if not chunks:
    raise RuntimeError("Kokoro produced no audio")
data = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
sf.write("/tmp/voiceover.wav", data, 24000)
print(f"VOICEOVER {len(data)} samples", flush=True)
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

# This session's revision: a hash of this very script. The
# platform stamps every child with the revision of the
# kernel it currently ships and only hands children to a
# session whose revision matches, and it only treats a
# heartbeat as fresh when it carries the current revision:
# a stale session left over from an older push can neither
# claim work nor suppress the wake that starts its
# replacement (the first proof day left two broken sessions
# alive and claiming for the better part of an hour).
import hashlib

try:
    REV = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()[:12]
except Exception:
    REV = "unknown"
HEADERS = {"X-API-Key": KEY, "X-Audio-Rev": REV}


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
_KOKORO_ONNX = None
# Which speech engine this session speaks with: "pytorch"
# once the genuine kokoro setup has proven itself, "onnx"
# when the fallback had to carry the session. Set by
# setup_kokoro, read by make_voiceover, and named in the
# log line for every voiceover file.
_SPEECH_ENGINE = None
KOKORO_MODEL_URL = ("https://github.com/thewh1teagle/kokoro-onnx"
                    "/releases/download/model-files-v1.0/"
                    "kokoro-v1.0.onnx")
KOKORO_VOICES_URL = ("https://github.com/thewh1teagle/kokoro-onnx"
                     "/releases/download/model-files-v1.0/"
                     "voices-v1.0.bin")
KOKORO_MODEL_PATH = WORKING / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = WORKING / "voices-v1.0.bin"


_UV_LOCK = threading.Lock()
_UV_PATH = None


def uv_path() -> str:
    """The uv binary, installed on first need. Both engine
    setups want it from their own threads, so the lookup
    and the install are serialized behind a lock."""
    global _UV_PATH
    with _UV_LOCK:
        if _UV_PATH:
            return _UV_PATH
        found = shutil.which("uv")
        candidate = Path.home() / ".local" / "bin" / "uv"
        if found is None and candidate.exists():
            found = str(candidate)
        if found is None:
            r = subprocess.run(
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
                shell=True, capture_output=True, text=True)
            if r.returncode != 0 or not candidate.exists():
                raise RuntimeError(
                    f"uv install failed: {(r.stderr or '')[-300:]}")
            found = str(candidate)
        _UV_PATH = found
        return found


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
        uv = uv_path()
        r = subprocess.run(
            [uv, "sync"], cwd=str(ACE_ROOT),
            capture_output=True, text=True, timeout=1800)
        if r.returncode != 0 or not ACE_VENV_PYTHON.exists():
            raise RuntimeError(
                f"ACE-Step uv sync failed: {(r.stderr or '')[-300:]}")
    _ACE_READY = True
    log("ACE-Step ready")


def _download(url: str, path: Path):
    if path.exists() and path.stat().st_size > 1_000_000:
        return
    with requests.get(url, stream=True, timeout=900) as resp:
        if resp.status_code != 200:
            raise RuntimeError(
                f"download {url} answered {resp.status_code}")
        tmp = path.with_suffix(".part")
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        tmp.rename(path)


def setup_kokoro():
    """Set up the speech stage. The genuine PyTorch
    kokoro engine is primary: it is set up first and the
    session speaks with it. The ONNX runtime is set up
    only when the genuine setup fails, and the session
    log says so in plain words; if both fail, the error
    propagates and claimed voiceover jobs record it."""
    global _SPEECH_ENGINE
    try:
        setup_kokoro_pytorch()
    except Exception:
        log("genuine kokoro setup failed: "
            + traceback.format_exc()[-280:])
    else:
        _SPEECH_ENGINE = "pytorch"
        log("speech engine: pytorch-kokoro (the genuine engine)")
        return
    setup_kokoro_onnx()
    _SPEECH_ENGINE = "onnx"
    log("speech engine: onnx-fallback (the genuine kokoro "
        "setup failed; see the error above)")


def setup_kokoro_pytorch():
    """Install the genuine PyTorch kokoro package, the
    Modal image's own pins (kokoro>=0.9.2, soundfile,
    numpy), into a Python 3.12 uv venv: the package
    cannot install on this kernel's Python 3.13 at all
    (its releases declare Requires-Python <3.13, and the
    older tree pins numpy==1.26.4, which has no 3.13
    wheels), so the speech stage runs as a subprocess
    under the venv python, the same discipline the bed
    stage uses for ACE-Step. CPU torch goes in first,
    from PyTorch's CPU wheel index, so the multi-GB CUDA
    build never lands in the session; Kokoro-82M runs
    fine on CPU. The spaCy model misaki's English G2P
    loads is installed from its wheel for the same
    reason: left to itself it tries to download through
    a package installer a uv venv does not carry. A proof
    synthesis through the real subprocess closes the
    setup, so a broken speech setup fails the session's
    setup, not a claimed job."""
    uv = uv_path()
    if not KOKORO_VENV_PYTHON.exists():
        KOKORO_ROOT.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [uv, "venv", "--python", "3.12",
             str(KOKORO_ROOT / ".venv")],
            capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not KOKORO_VENV_PYTHON.exists():
            raise RuntimeError(
                f"kokoro venv create failed: "
                f"{(r.stdout + r.stderr)[-280:]}")
    heartbeat()

    def uv_pip(pkgs, index_url=None):
        cmd = [uv, "pip", "install", "--python",
               str(KOKORO_VENV_PYTHON)] + pkgs
        if index_url:
            cmd += ["--index-url", index_url]
        r = subprocess.run(cmd, capture_output=True,
                           text=True, timeout=1800)
        log(f"uv pip install {pkgs} -> rc={r.returncode}")
        if r.returncode != 0:
            raise RuntimeError(
                f"uv pip install {pkgs} failed: "
                f"{(r.stdout + r.stderr)[-280:]}")

    uv_pip(["torch"],
           index_url="https://download.pytorch.org/whl/cpu")
    heartbeat()
    uv_pip(["kokoro>=0.9.2", "soundfile", "numpy"])
    heartbeat()
    uv_pip([f"en-core-web-sm @ {SPACY_MODEL_WHEEL}"])
    made = run_voiceover_script("Hello.", "af_heart")
    if made.stat().st_size <= 10_000:
        raise RuntimeError(
            "Kokoro proof synthesis produced a stub wav")
    log("Kokoro ready (genuine PyTorch engine)")


def run_voiceover_script(text: str, voice: str) -> Path:
    """Speak one text with the genuine engine: the Modal
    stage's own script under the 3.12 venv python.
    Returns the wav it wrote."""
    Path("/tmp/vo_params.json").write_text(
        json.dumps({"text": text, "voice": voice}))
    script_path = Path("/tmp/gen_voiceover.py")
    script_path.write_text(VOICEOVER_SCRIPT)
    out_path = Path("/tmp/voiceover.wav")
    if out_path.exists():
        out_path.unlink()
    env = dict(os.environ)
    env["HF_HOME"] = str(KOKORO_HF_HOME)
    proc = subprocess.run(
        [str(KOKORO_VENV_PYTHON), str(script_path)],
        capture_output=True, text=True,
        timeout=GEN_TIMEOUT_SECONDS, env=env)
    print(proc.stdout[-3000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], flush=True)
        raise RuntimeError(
            f"Kokoro subprocess failed rc={proc.returncode}")
    if not out_path.exists() or out_path.stat().st_size <= 44:
        raise RuntimeError("Kokoro produced no wav")
    return out_path


def setup_kokoro_onnx():
    """The fallback speech setup: the Kokoro-82M weights'
    official ONNX export through onnxruntime, the only
    runtime that installs on this kernel's Python 3.13.
    Reached only when the genuine engine's setup failed;
    the session log names it as the fallback."""
    pip_install_checked(["kokoro-onnx", "soundfile", "numpy"])
    _download(KOKORO_MODEL_URL, KOKORO_MODEL_PATH)
    heartbeat()
    _download(KOKORO_VOICES_URL, KOKORO_VOICES_PATH)
    samples, sr = kokoro_onnx_engine().create(
        "Hello.", voice="af_heart")
    if sr != 24000 or samples is None or len(samples) == 0:
        raise RuntimeError("Kokoro ONNX proof synthesis failed")
    log("Kokoro ready (ONNX fallback)")


def kokoro_onnx_engine():
    global _KOKORO_ONNX
    if _KOKORO_ONNX is None:
        from kokoro_onnx import Kokoro

        _KOKORO_ONNX = Kokoro(str(KOKORO_MODEL_PATH),
                              str(KOKORO_VOICES_PATH))
    return _KOKORO_ONNX


def _voiceover_parts(text: str):
    """Split a narration text into sentence-sized parts
    under the model's per-call phoneme ceiling. Only the
    ONNX fallback needs this: the genuine engine's
    pipeline chunks the text itself, as it did on Modal.
    The parts are concatenated back into one wav."""
    import re

    parts = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) > 400:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts or [text]


def setup_engines():
    """Both engines, for any caller that wants the pair;
    the session itself sets them up independently (see
    main), so one engine's failure never blocks the other
    stage."""
    setup_ace()
    heartbeat()
    setup_kokoro()
    heartbeat()


def make_bed(job: dict) -> Path:
    """One ACE-Step bed from the job's caption and seed,
    through the project's own venv python, the Modal
    stage's subprocess discipline."""
    ensure_engine("bed")
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
    voice, 24 kHz mono wav. The genuine PyTorch engine
    speaks it whenever its setup proved itself; the ONNX
    fallback speaks only in a session whose genuine
    setup failed. Either way the log line names the
    engine that made the file."""
    ensure_engine("voiceover")
    text = (job.get("text") or "").strip()
    voice = job.get("voice") or "af_heart"
    if not text:
        raise ValueError("voiceover text is empty")
    if _SPEECH_ENGINE == "pytorch":
        made = run_voiceover_script(text, voice)
        out_path = WORKING / f"voiceover-{job['id']}.wav"
        shutil.copyfile(made, out_path)
        log(f"job {job['id']}: voiceover engine=pytorch-kokoro")
        return out_path
    if _SPEECH_ENGINE == "onnx":
        out_path = make_voiceover_onnx(job, text, voice)
        log(f"job {job['id']}: voiceover engine=onnx-fallback")
        return out_path
    raise RuntimeError("the speech engine was never set up")


def make_voiceover_onnx(job: dict, text: str,
                        voice: str) -> Path:
    """The fallback voiceover: the same weights through
    onnxruntime, the text chunked by hand under the
    model's per-call phoneme ceiling because the ONNX
    wrapper has no pipeline of its own."""
    import numpy as np
    import soundfile as sf

    engine = kokoro_onnx_engine()
    lang = "en-gb" if voice.startswith("b") else "en-us"
    chunks = []
    for part in _voiceover_parts(text):
        samples, _sr = engine.create(part, voice=voice, lang=lang)
        if samples is not None and len(samples):
            chunks.append(np.asarray(samples, dtype=np.float32))
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


_SETUP_THREADS = {}
_SETUP_ERRORS = {}


def _setup_one(name, fn):
    try:
        fn()
    except Exception:
        _SETUP_ERRORS[name] = traceback.format_exc()
        log(f"{name} setup failed: {_SETUP_ERRORS[name][-300:]}")
    heartbeat()


def ensure_engine(stage):
    """Wait for the background setup of the engine this
    stage needs; re-raise its failure so the claimed job
    records it. The engines set up independently, each in
    its own thread: a speech setup failure never blocks a
    bed, and an ACE-Step failure never blocks a voiceover
    (the first proof sessions taught both lessons)."""
    name = "ace" if stage == "bed" else "kokoro"
    thread = _SETUP_THREADS.get(name)
    if thread is not None:
        thread.join()
    if name in _SETUP_ERRORS:
        raise RuntimeError(_SETUP_ERRORS[name][-280:])
    if name == "ace":
        setup_ace()
    else:
        setup_kokoro()


def main():
    heartbeat()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    # Engine setup runs in the background from boot, one
    # thread per engine, so it overlaps the platform's
    # audio wait instead of eating it after the first
    # claim (the voice worker's lesson). A setup failure is
    # kept per engine and handed to the first claimed job
    # of that stage, whose record carries it back to the
    # platform; nothing fails blind.
    _SETUP_THREADS["ace"] = threading.Thread(
        target=_setup_one, args=("ace", setup_ace), daemon=True)
    _SETUP_THREADS["kokoro"] = threading.Thread(
        target=_setup_one, args=("kokoro", setup_kokoro),
        daemon=True)
    for thread in _SETUP_THREADS.values():
        thread.start()
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
