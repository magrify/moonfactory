# Moonfactory voice cloning worker: a Kaggle kernel (script
# type).
#
# Speaks platform jobs in a cloned voice with CosyVoice
# (FunAudioLLM/CosyVoice2-0.5B, Apache-2.0 weights,
# zero-shot cloning), the commercially licensed cloning
# engine chosen from VoiceStudio's engine set on 2026-11-21
# (OmniVoice, its default, carries CC-BY-NC weights and is
# never run here). The engine recipe is the one measured on
# 2026-11-22 (ECAPA similarity 0.8599 against a 0.70 gate,
# Whisper WER 0.0 against a 0.05 gate): the CosyVoice repo
# pinned to commit 0867ebc (2025-09-12, the current
# weights' own era; the repo's HEAD code yields zero
# tokens for these weights), the CosyVoice2 class loader,
# and zero-shot calls carrying a 16 kHz waveform tensor. The shape mirrors the HyperFrames worker
# beside it: the platform queues voice_clone jobs, wakes this
# kernel with a version push when no heartbeat is fresh, and
# the kernel claims jobs one at a time through the platform's
# worker endpoints, synthesizes, and uploads the finished wav
# back. The worker key rides in this family's own tiny
# private dataset (magrify/moonfactory-voice-key); it is
# read into memory only.
#
# Setup (the CosyVoice repo, its Python deps, and the model
# snapshot) happens once per session before the poll loop,
# heartbeating as it goes: a cold session takes minutes to
# become useful, and the platform's wait for a cloned
# voiceover is sized for that. The loop then claims jobs one
# at a time and shuts itself down after ten idle minutes
# (plus a hard session cap) so the weekly free quota is
# spent on synthesis, not on waiting.
#
# A job carries the text to speak and a reference clip the
# user supplied. When the job carries no transcript of the
# reference, the worker transcribes the clip itself with
# faster-whisper and uses that as the zero-shot prompt text;
# if transcription comes back empty it falls back to
# CosyVoice's cross-lingual mode, which clones from the
# audio alone.

import json
import os
import re
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
SYNTH_TIMEOUT_SECONDS = 1500

STARTED = time.time()


def log(msg):
    print(f"[voice-worker] {msg}", flush=True)


def find_dataset_file(slug: str, filename: str) -> Path:
    for hit in INPUT.rglob(filename):
        if hit.parent.name == slug:
            return hit
    raise RuntimeError(f"dataset {slug} is not attached (no {filename})")


KEY_FILE = find_dataset_file("moonfactory-voice-key", "worker_key.txt")


def read_key() -> str:
    try:
        from kaggle_secrets import UserSecretsClient

        # Per-family key first (MOONFACTORY_VOICE_KEY), then
        # the shared legacy name, then the dataset key file.
        for secret_name in ("MOONFACTORY_VOICE_KEY",
                            "MOONFACTORY_TEMPLATE_KEY"):
            value = UserSecretsClient().get_secret(secret_name)
            if value:
                return value.strip()
    except Exception:
        pass
    return KEY_FILE.read_text().strip()


KEY = read_key()
HEADERS = {"X-API-Key": KEY}

def job_headers(claim_token=None):
    """Headers for one claimed job's calls: the worker key
    plus the per-job claim token when the claim issued one.
    A job claimed without a token (platform legacy mode)
    sends the key alone, exactly as before."""
    headers = dict(HEADERS)
    if claim_token:
        headers["X-Claim-Token"] = claim_token
    return headers



def heartbeat():
    try:
        requests.post(f"{PLATFORM}/api/worker/voice/heartbeat",
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
    return r.returncode


_MODEL = None
_WHISPER = None

# Pinned specs for the load self-healing loop in setup_engine,
# keyed by importable module name. The whisper entry matters:
# the module is OpenAI's package (openai-whisper), and the
# bare PyPI name "whisper" is an unrelated package whose
# install succeeds without ever providing the module (the
# 2026-11-22 sessions burned all ten heal attempts on it).
LOAD_MISS_PINS = {"x_transformers": "x-transformers==2.32.4",
                  "whisper": "openai-whisper==20250625"}


def setup_engine():
    """Install deps, fetch the CosyVoice code and weights,
    and load the model. Runs once per session."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    # The pinned repo commit's own dependency set, as
    # installed and measured in the 2026-11-22 test (Kaggle's
    # torch stays: it ships no older wheel for its Python).
    for pkg in (["conformer==0.3.2"], ["diffusers==0.29.0"],
                ["transformers==4.51.3"], ["modelscope==1.40.3"],
                ["wetext==0.0.4"], ["pyworld==0.3.4"],
                ["pyarrow==18.1.0"], ["librosa==0.10.2"],
                ["lightning==2.2.4"], ["hyperpyyaml==1.2.3"],
                ["omegaconf==2.4.0"], ["hydra-core==1.3.8"],
                ["inflect==7.6.0"],
                ["onnxruntime==1.32.0"], ["gdown==6.5.2"],
                ["soundfile==0.14.0"],
                ["faster-whisper==1.2.1"],
                # The pinned frontend imports the whisper
                # module at load (cosyvoice/cli/frontend.py),
                # OpenAI's package: without it the model load
                # dies and no clone can ever be made (the
                # lane's standing failure until 2026-11-27).
                # The 20250625 release, not the era's 20231117:
                # that sdist no longer builds on Kaggle's
                # current image (its setup.py needs the
                # removed pkg_resources at wheel build time,
                # so every install exits rc=1), while the
                # newer release ships modern packaging.
                ["openai-whisper==20250625"],
                ["matplotlib==3.11.2"],
                ["networkx==3.1"], ["rich==15.0.0"], ["wget==3.2"],
                ["protobuf==7.36.2"], ["tensorboard==2.21.0"],
                ["onnx==1.23.2"]):
        pip_install(pkg)
    heartbeat()
    # The pinned frontend normalizes text with the wetext
    # package, whose kaldifst dependency may have no wheel
    # for Kaggle's Python. If the real package will not
    # import, drop in a pass-through shim under the same
    # name: the frontend still spells numbers out itself
    # (spell_out_number runs after the normalizer), so
    # cloning keeps working, just without wetext's extra
    # cleanup rules.
    try:
        import wetext  # noqa: F401
        log("wetext available")
    except Exception as exc:
        log(f"wetext unavailable ({exc}); writing the shim")
        shim_dir = WORKING / "shims"
        shim_dir.mkdir(exist_ok=True)
        (shim_dir / "wetext.py").write_text(
            '"""Pass-through stand-in for the wetext package\n'
            '(see voice_worker_kernel.setup_engine)."""\n\n\n'
            "class Normalizer:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        pass\n\n"
            "    def normalize(self, text):\n"
            "        return text\n")
        if str(shim_dir) not in sys.path:
            sys.path.insert(0, str(shim_dir))
    repo = WORKING / "CosyVoice"
    if not repo.exists():
        # Full clone, then pin to the weights' era commit:
        # the repo's HEAD code has drifted from these
        # weights (its LLM yields zero tokens for them),
        # and the measured recipe is this commit.
        r = subprocess.run(
            ["git", "clone",
             "https://github.com/FunAudioLLM/CosyVoice.git",
             str(repo)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"CosyVoice clone failed: {(r.stderr or '')[-300:]}")
        r = subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q",
             "0867ebcb8cbfdd74adce1e52daaff6c7d96782aa"],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"CosyVoice pin failed: {(r.stderr or '')[-300:]}")
    matcha = repo / "third_party" / "Matcha-TTS"
    if not (matcha / "matcha").exists():
        # The repo carries Matcha-TTS as a git submodule: a
        # plain clone leaves the directory present but empty,
        # and the flow code imports the matcha package at
        # model load. Clear whatever is there and clone the
        # real code in, then prove the import before loading.
        if matcha.exists():
            shutil.rmtree(matcha)
        r = subprocess.run(
            ["git", "clone",
             "https://github.com/shivammehta25/Matcha-TTS.git",
             str(matcha)], capture_output=True, text=True)
        if r.returncode == 0:
            r = subprocess.run(
                ["git", "-C", str(matcha), "checkout", "-q",
                 "bd4d90d93214b37f7a159cf205ae85762c2c10aa"],
                capture_output=True, text=True)
        if r.returncode != 0 or not (matcha / "matcha").exists():
            raise RuntimeError(
                f"Matcha-TTS clone failed: {(r.stderr or '')[-300:]}")
    sys.path.append(str(repo))
    sys.path.append(str(matcha))
    import matcha  # noqa: F401  (fail here, not inside the load)
    from huggingface_hub import snapshot_download
    model_dir = snapshot_download(
        "FunAudioLLM/CosyVoice2-0.5B")
    heartbeat()
    # Load, self-healing: the repo's code imports packages
    # Kaggle does not preinstall, so a missing module
    # installs and retries instead of failing the session.
    # The loader at the pinned commit is the CosyVoice2
    # class itself (no AutoModel exists there), with the
    # JIT and TensorRT engines off: the snapshot ships
    # plain checkpoints, not compiled engines.
    def try_load():
        from cosyvoice.cli.cosyvoice import CosyVoice2
        return CosyVoice2(model_dir, load_jit=False,
                          load_trt=False, fp16=True), "CosyVoice2"

    for attempt in range(10):
        try:
            _MODEL, cls_name = try_load()
            log(f"CosyVoice loaded via {cls_name}")
            break
        except Exception as exc:
            # The import errors surface wrapped (pydoc's
            # ErrorDuringImport), so read the missing module
            # out of the message chain and install it.
            blob = str(exc)
            cause = exc.__cause__ or exc.__context__
            if cause is not None:
                blob += " " + str(cause)
            m = re.search(r"No module named '([A-Za-z0-9_\.]+)'",
                          blob)
            if not m:
                raise
            name = m.group(1).split(".")[0]
            # Self-healing installs resolve through a pinned
            # allowlist only: an unlisted missing module
            # re-raises instead of installing whatever PyPI
            # serves today under a bare name.
            spec = LOAD_MISS_PINS.get(name)
            if spec is None:
                raise
            log(f"load miss: installing {spec} (module {name}), "
                f"attempt {attempt + 1}")
            pip_install([spec])
            heartbeat()
    if _MODEL is None:
        raise RuntimeError("CosyVoice load kept failing on "
                           "missing modules")
    heartbeat()
    return _MODEL


def transcribe_reference(ref_path: Path) -> str:
    """The words spoken in the reference clip, by
    faster-whisper (base). Empty string when nothing usable
    comes back."""
    global _WHISPER
    try:
        if _WHISPER is None:
            from faster_whisper import WhisperModel
            _WHISPER = WhisperModel(
                "base", device="cuda", compute_type="float16")
        segments, _info = _WHISPER.transcribe(
            str(ref_path), language="en")
        text = " ".join(seg.text.strip() for seg in segments)
        return re.sub(r"\s+", " ", text).strip()
    except Exception as exc:
        log(f"reference transcription failed: {exc}")
        return ""


def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def synth_job(job: dict) -> Path:
    """Clone the job's reference voice speaking the job's
    text; returns the finished wav path."""
    import torch
    import torchaudio
    model = ensure_engine()
    job_id = job["id"]
    job_dir = WORKING / f"job-{job_id}"
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True)
    raw_ref = job_dir / "reference_in"
    with requests.get(
            f"{PLATFORM}/api/worker/voice/jobs/{job_id}/reference",
            headers=job_headers(job.get("claim_token")),
            timeout=300, stream=True) as resp:
        resp.raise_for_status()
        with open(raw_ref, "wb") as fh:
            for chunk in resp.iter_content(1024 * 1024):
                fh.write(chunk)
    # Normalize the reference to a 24 kHz mono wav the engine
    # reads the same way every time, whatever the user
    # uploaded (wav, mp3, m4a, phone recording).
    ref_wav = job_dir / "reference.wav"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw_ref), "-ac", "1",
         "-ar", "24000", str(ref_wav)],
        capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not ref_wav.exists():
        raise RuntimeError(
            f"reference conversion failed: {(proc.stderr or '')[-300:]}")
    prompt_text = (job.get("reference_text") or "").strip()
    if not prompt_text:
        prompt_text = transcribe_reference(ref_wav)
        log(f"job {job_id}: reference transcribed as "
            f"{prompt_text!r}")
    # At the pinned commit the zero-shot calls take the
    # reference as a 16 kHz waveform tensor, not a path.
    from cosyvoice.utils.file_utils import load_wav
    prompt_16k = load_wav(str(ref_wav), 16000)
    sr = int(getattr(model, "sample_rate", 24000))
    chunks = []
    for sentence in split_sentences(job.get("text") or ""):
        if prompt_text:
            gen = model.inference_zero_shot(
                sentence, prompt_text, prompt_16k, stream=False)
        else:
            gen = model.inference_cross_lingual(
                sentence, prompt_16k, stream=False)
        for out in gen:
            chunks.append(out["tts_speech"])
    if not chunks:
        raise RuntimeError("CosyVoice produced no audio")
    speech = torch.cat(chunks, dim=1)
    out_path = job_dir / "clone.wav"
    torchaudio.save(str(out_path), speech, sr)
    return out_path


def fail_job(job_id: str, message: str, claim_token=None):
    try:
        requests.post(
            f"{PLATFORM}/api/worker/voice/jobs/{job_id}/fail",
            headers=job_headers(claim_token),
            json={"error": message[:280]},
            timeout=30)
    except Exception:
        pass


_SETUP_THREAD = None
_SETUP_ERROR = None


def _setup_in_background():
    global _SETUP_ERROR
    try:
        setup_engine()
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
    return setup_engine()


def main():
    global _SETUP_THREAD
    heartbeat()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    # Engine setup runs in the background from boot, so it
    # overlaps the platform's clone wait instead of eating
    # it after the first claim (two proof shorts died on
    # exactly that serialization, and a third on the queue
    # alone). A setup failure is kept and handed to the
    # first claimed job, whose record carries it back to
    # the platform; nothing fails blind.
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
                f"{PLATFORM}/api/worker/voice/jobs/next",
                headers={**HEADERS, "X-Claim-Bind": "1"},
                timeout=30)
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
            out_path = synth_job(job)
            log(f"job {job['id']}: uploading result")
            with open(out_path, "rb") as fh:
                up = requests.post(
                    f"{PLATFORM}/api/worker/voice/jobs/"
                    f"{job['id']}/file",
                    headers=job_headers(job.get("claim_token")),
                    data=fh, timeout=600)
            up.raise_for_status()
            log(f"job {job['id']}: done")
        except Exception:
            message = traceback.format_exc()[-280:]
            log(f"job {job['id']}: failed: {message}")
            fail_job(job["id"], message, job.get("claim_token"))
        heartbeat()


if __name__ == "__main__":
    main()
