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


# The word check recipe (2026-11-26): the Modal audio
# app's word_check / transcribe logic (modal-worker/
# audio_app.py), ported unchanged in substance: the same
# normalization, alignment, ratio and splice code, the
# same faster-whisper base model pass, and the same
# Kokoro fix pass, speaking through the venv's own
# KPipeline. It runs under the Python 3.12 venv python
# like the voiceover recipe, because faster-whisper's
# stack shares that venv's constraints; the model runs
# on the session's GPU (float16) when it can and falls
# back to CPU int8, the Modal stage's own configuration.
# The kernel drives it through /tmp/wc_params.json and
# reads /tmp/wc_result.json plus an optional repaired
# /tmp/wc_fixed.wav.
WORDCHECK_SCRIPT = r'''
import difflib, json, re
import numpy as np
import soundfile as sf

TAKE_FAIL_RATIO = 0.20

_CONTRACTIONS = {
    "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "cannot", "cannot": "cannot", "won't": "will not",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not",
    "weren't": "were not", "couldn't": "could not",
    "shouldn't": "should not", "wouldn't": "would not",
    "it's": "it is", "that's": "that is", "there's": "there is",
    "what's": "what is", "here's": "here is", "let's": "let us",
    "you're": "you are", "we're": "we are", "i'm": "i am",
    "i've": "i have", "we'll": "we will", "you'll": "you will",
    "he's": "he is", "she's": "she is",
}

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen",
    "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty",
    "seventy", "eighty", "ninety",
]


def _int_words(n: int) -> list:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        out = [_TENS[n // 10]]
        if n % 10:
            out.append(_ONES[n % 10])
        return out
    if n < 1000:
        out = [_ONES[n // 100], "hundred"]
        if n % 100:
            out += _int_words(n % 100)
        return out
    out = _int_words(n // 1000) + ["thousand"]
    if n % 1000:
        out += _int_words(n % 1000)
    return out


def _token_words(token: str) -> list:
    t = token.strip().lower().strip(".,!?;:\"()[]{}")
    if not t:
        return []
    if t in _CONTRACTIONS:
        return _CONTRACTIONS[t].split()
    t = t.replace(",", "").strip(".,!?;:\"()[]{}")
    if not t:
        return []
    if re.fullmatch(r"\d+", t):
        try:
            return _int_words(int(t))
        except Exception:
            return [t]
    if re.fullmatch(r"\d+\.\d+", t):
        whole, _, frac = t.partition(".")
        out = []
        try:
            out += _int_words(int(whole))
            out.append("point")
            for d in frac:
                out.append(_ONES[int(d)])
            return out
        except Exception:
            return [t]
    return [t.replace("'", "")]


def normalize_words(text: str) -> list:
    out = []
    for token in (text or "").split():
        out += _token_words(token)
    return out


def _flat_heard(heard_words) -> list:
    flat = []
    for w in heard_words or []:
        for nw in _token_words(str(w.get("word", "")).strip().lower()):
            flat.append((nw, float(w.get("start", 0.0)),
                         float(w.get("end", 0.0))))
    return flat


def _alignment_pairs(script_words, flat_heard) -> dict:
    heard_words = [h[0] for h in flat_heard]
    sm = difflib.SequenceMatcher(a=script_words, b=heard_words,
                                 autojunk=False)
    pairs = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                pairs[i1 + k] = (flat_heard[j1 + k][1],
                                 flat_heard[j1 + k][2])
        elif tag == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                pairs[i1 + k] = (flat_heard[j1 + k][1],
                                 flat_heard[j1 + k][2])
    return pairs


def evaluate_script(script, heard_words) -> dict:
    script_words = normalize_words(script)
    flat = _flat_heard(heard_words)
    heard_list = [h[0] for h in flat]
    sm = difflib.SequenceMatcher(a=script_words, b=heard_list,
                                 autojunk=False)
    wrong = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or tag == "insert":
            continue
        for k in range(i1, i2):
            off = k - i1
            heard_word = None
            at = 0.0
            end = 0.0
            if tag == "replace" and j1 + off < j2:
                heard_word = flat[j1 + off][0]
                at = flat[j1 + off][1]
                end = flat[j1 + off][2]
            elif j1 > 0:
                at = end = flat[j1 - 1][2]
            elif j2 < len(flat):
                at = end = flat[j2][1]
            wrong.append({
                "expected": script_words[k],
                "heard": heard_word,
                "at": round(float(at), 2),
                "end": round(float(end), 2),
                "index": k,
            })
    total = len(script_words)
    return {
        "total": total,
        "wrong": wrong,
        "ratio": (len(wrong) / total) if total else 0.0,
    }


def decide_status(initial_ratio: float, final_ratio: float,
                  fix_applied: bool) -> str:
    if initial_ratio == 0:
        return "passed"
    if final_ratio > TAKE_FAIL_RATIO:
        return "take_failed"
    if fix_applied and final_ratio == 0:
        return "fixed"
    return "flagged"


def sentence_spans(text) -> list:
    spans = []
    idx = 0
    for m in re.finditer(r"[^.!?]+[.!?]*", text or ""):
        piece = m.group(0).strip()
        if not piece:
            continue
        n = len(normalize_words(piece))
        if n == 0:
            continue
        spans.append({"text": piece, "start": idx, "end": idx + n})
        idx += n
    return spans


def _match_format(seg, src_rate, dst_rate, dst_channels):
    seg = np.asarray(seg, dtype=np.float32)
    if seg.ndim == 1:
        seg = seg[:, None]
    if len(seg) and src_rate != dst_rate:
        n_out = max(1, int(round(len(seg) * dst_rate / src_rate)))
        xs = np.linspace(0, len(seg) - 1, n_out)
        base = np.arange(len(seg))
        seg = np.stack(
            [np.interp(xs, base, seg[:, c]) for c in range(seg.shape[1])],
            axis=1)
    if seg.shape[1] != dst_channels:
        if seg.shape[1] == 1:
            seg = np.repeat(seg, dst_channels, axis=1)
        else:
            mono = seg.mean(axis=1, keepdims=True)
            seg = np.repeat(mono, dst_channels, axis=1) \
                if dst_channels > 1 else mono
    return seg.astype(np.float32)


def apply_splices(samples, rate, splices):
    out = np.asarray(samples, dtype=np.float32)
    if out.ndim == 1:
        out = out[:, None]
    for start_s, end_s, repl in sorted(
            splices, key=lambda s: s[0], reverse=True):
        repl = np.asarray(repl, dtype=np.float32)
        if repl.ndim == 1:
            repl = repl[:, None]
        a = max(0, min(len(out), int(round(start_s * rate))))
        b = max(0, min(len(out), int(round(end_s * rate))))
        if b < a:
            b = a
        seg = repl.copy()
        f = min(int(0.005 * rate), len(seg) // 2, b - a)
        if f > 0:
            ramp = np.linspace(0.0, 1.0, f, dtype=np.float32)[:, None]
            seg[:f] = seg[:f] * ramp + out[a:a + f] * (1.0 - ramp)
            seg[-f:] = seg[-f:] * (1.0 - ramp) + out[b - f:b] * ramp
        out = np.concatenate([out[:a], seg, out[b:]], axis=0)
    return out


def run_word_fix(samples, rate, script, wrong, speak_sentence,
                 transcribe_samples):
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim == 1:
        samples = samples[:, None]
    channels = samples.shape[1]
    script_words = normalize_words(script)
    spans = sentence_spans(script)
    by_sentence = {}
    for w in wrong:
        for si, sp in enumerate(spans):
            if sp["start"] <= w["index"] < sp["end"]:
                by_sentence.setdefault(si, []).append(w)
                break
    splices = []
    for si, wlist in by_sentence.items():
        sp = spans[si]
        try:
            clean, clean_rate = speak_sentence(sp["text"])
            clean_heard = transcribe_samples(clean, clean_rate)
        except Exception:
            continue
        clean = np.asarray(clean, dtype=np.float32)
        if clean.ndim == 1:
            clean = clean[:, None]
        sent_words = script_words[sp["start"]:sp["end"]]
        pairs = _alignment_pairs(sent_words, _flat_heard(clean_heard))
        for w in wlist:
            local = w["index"] - sp["start"]
            if local not in pairs:
                continue
            cs, ce = pairs[local]
            seg = clean[int(round(cs * clean_rate)):
                        int(round(ce * clean_rate))]
            if not len(seg):
                continue
            seg = _match_format(seg, clean_rate, rate, channels)
            splices.append((max(0.0, w["at"] - 0.02),
                            max(0.0, w["end"] + 0.02), seg))
    if not splices:
        return samples, 0
    return apply_splices(samples, rate, splices), len(splices)


def _transcribe_words(model, audio_path) -> list:
    segments, _info = model.transcribe(
        str(audio_path), word_timestamps=True, language="en",
        beam_size=1)
    heard = []
    for seg in segments:
        for w in (seg.words or []):
            heard.append({"word": w.word, "start": float(w.start),
                          "end": float(w.end)})
    return heard


def _load_model():
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(
            "base", device="cuda", compute_type="float16",
            download_root="/tmp/whisper-cache")
    except Exception:
        return WhisperModel(
            "base", device="cpu", compute_type="int8",
            download_root="/tmp/whisper-cache")


def main():
    with open("/tmp/wc_params.json") as fh:
        spec = json.load(fh)
    mode = spec["mode"]
    source = spec["source"]
    model = _load_model()
    heard = _transcribe_words(model, source)
    if mode == "transcribe":
        with open("/tmp/wc_result.json", "w") as fh:
            json.dump({"words": heard}, fh)
        print(f"TRANSCRIBE {len(heard)} words", flush=True)
        return
    script = (spec.get("script") or "").strip()
    voice = spec.get("voice") or ""
    if not script:
        raise ValueError("word check needs a speech script")
    samples, rate = sf.read(source, dtype="float32",
                            always_2d=True)

    def transcribe_samples(s, r):
        p = "/tmp/wc_seg.wav"
        sf.write(p, s, r)
        return _transcribe_words(model, p)

    first = evaluate_script(script, heard)
    final = first
    final_heard = heard
    applied = 0
    if first["wrong"] and voice:
        holder = {}

        def speak_sentence(text):
            if "pipeline" not in holder:
                from kokoro import KPipeline
                lang = "b" if voice.startswith("b") else "a"
                holder["pipeline"] = KPipeline(lang_code=lang)
            chunks = []
            for _g, _p, audio in holder["pipeline"](
                    text, voice=voice):
                if audio is not None:
                    chunks.append(
                        np.asarray(audio, dtype=np.float32))
            if not chunks:
                raise RuntimeError("Kokoro produced no audio")
            data = np.concatenate(chunks) \
                if len(chunks) > 1 else chunks[0]
            return data[:, None], 24000

        new_samples, applied = run_word_fix(
            samples, rate, script, first["wrong"], speak_sentence,
            transcribe_samples)
        if applied:
            fixed_heard = transcribe_samples(new_samples, rate)
            final = evaluate_script(script, fixed_heard)
            if final["ratio"] <= TAKE_FAIL_RATIO:
                sf.write("/tmp/wc_fixed.wav", new_samples, rate,
                         format="WAV", subtype="PCM_16")
                final_heard = fixed_heard
    status = decide_status(first["ratio"], final["ratio"],
                           bool(applied))
    result = {
        "status": status,
        "wrong": [
            {"expected": w["expected"], "heard": w["heard"],
             "at": w["at"]}
            for w in final["wrong"]
        ],
        "listen_at": [w["at"] for w in final["wrong"]],
        "wrong_ratio": round(final["ratio"], 4),
        "initial_wrong_ratio": round(first["ratio"], 4),
        "words": final_heard,
    }
    with open("/tmp/wc_result.json", "w") as fh:
        json.dump(result, fh)
    print(f"WORDCHECK {status}", flush=True)


main()
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

        # Per-family key first (MOONFACTORY_AUDIO_KEY), then
        # the shared legacy name, then the dataset key file.
        for secret_name in ("MOONFACTORY_AUDIO_KEY",
                            "MOONFACTORY_TEMPLATE_KEY"):
            value = UserSecretsClient().get_secret(secret_name)
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
            # uv itself is pinned: a bare curl|sh installer
            # would run whatever the site serves today.
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q",
                 "uv==0.13.10"],
                capture_output=True, text=True)
            found = shutil.which("uv")
            if found is None and candidate.exists():
                found = str(candidate)
            if r.returncode != 0 or found is None:
                raise RuntimeError(
                    f"uv install failed: {(r.stderr or '')[-300:]}")
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
            ["git", "clone",
             "https://github.com/ace-step/ACE-Step-1.5.git",
             str(ACE_ROOT)], capture_output=True, text=True)
        if r.returncode == 0:
            r = subprocess.run(
                ["git", "-C", str(ACE_ROOT), "checkout", "-q",
                 "ca1e85fe9430179831e6bc6be790c332190a3866"],
                capture_output=True, text=True)
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
    Modal image's own pins (kokoro==0.9.4, soundfile==0.14.0,
    numpy==2.5.5), into a Python 3.12 uv venv: the package
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

    uv_pip(["torch==2.15.0"],
           index_url="https://download.pytorch.org/whl/cpu")
    heartbeat()
    uv_pip(["kokoro==0.9.4", "soundfile==0.14.0", "numpy==2.5.5"])
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
    pip_install_checked(["kokoro-onnx==0.6.1", "soundfile==0.14.0",
                         "numpy==2.5.5"])
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


_WHISPER_READY = False


def setup_whisper():
    """Install faster-whisper into the Python 3.12 venv
    the speech stage already uses (the Modal word check's
    own pins: faster-whisper==1.2.1, av==18.1.1), so the word
    check and transcribe stages can run here. The venv
    usually exists already (the Kokoro setup made it);
    its setup thread is joined first, without inheriting
    a speech failure: the check needs the venv, not the
    speech engine. Runs once per session."""
    global _WHISPER_READY
    if _WHISPER_READY:
        return
    thread = _SETUP_THREADS.get("kokoro")
    if thread is not None:
        thread.join()
    uv = uv_path()
    if not KOKORO_VENV_PYTHON.exists():
        KOKORO_ROOT.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [uv, "venv", "--python", "3.12",
             str(KOKORO_ROOT / ".venv")],
            capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not KOKORO_VENV_PYTHON.exists():
            raise RuntimeError(
                f"whisper venv create failed: "
                f"{(r.stdout + r.stderr)[-280:]}")
    r = subprocess.run(
        [uv, "pip", "install", "--python",
         str(KOKORO_VENV_PYTHON),
         "faster-whisper==1.2.1", "av==18.1.1",
         "soundfile==0.14.0", "numpy==2.5.5"],
        capture_output=True, text=True, timeout=1800)
    log(f"uv pip install faster-whisper -> rc={r.returncode}")
    if r.returncode != 0:
        raise RuntimeError(
            f"uv pip install faster-whisper failed: "
            f"{(r.stdout + r.stderr)[-280:]}")
    _WHISPER_READY = True
    log("faster-whisper ready (word check / transcribe)")


def make_wordcheck(job: dict):
    """One word check stage (wordcheck or transcribe):
    download the job's final audio through the source
    endpoint, run the check script under the 3.12 venv
    python (the Modal stage's own logic, ported in
    WORDCHECK_SCRIPT), upload any repaired wav through
    the file endpoint, and post the outcome through the
    result endpoint, which is what marks the child
    done."""
    setup_whisper()
    job_id = job["id"]
    src_path = WORKING / f"wc-source-{job_id}.wav"
    resp = requests.get(
        f"{PLATFORM}/api/worker/audio/jobs/{job_id}/source",
        headers=job_headers(job.get("claim_token")), timeout=600)
    resp.raise_for_status()
    src_path.write_bytes(resp.content)
    Path("/tmp/wc_params.json").write_text(json.dumps({
        "mode": job.get("stage") or "",
        "source": str(src_path),
        "script": job.get("script") or "",
        "voice": job.get("voice") or "",
    }))
    script_path = Path("/tmp/run_wordcheck.py")
    script_path.write_text(WORDCHECK_SCRIPT)
    for stale in (Path("/tmp/wc_result.json"),
                  Path("/tmp/wc_fixed.wav")):
        if stale.exists():
            stale.unlink()
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
            f"word check subprocess failed rc={proc.returncode}")
    result_path = Path("/tmp/wc_result.json")
    if not result_path.exists():
        raise RuntimeError("word check produced no result")
    result = json.loads(result_path.read_text())
    fixed_path = Path("/tmp/wc_fixed.wav")
    if fixed_path.exists():
        log(f"job {job_id}: uploading repaired wav")
        with open(fixed_path, "rb") as fh:
            up = requests.post(
                f"{PLATFORM}/api/worker/audio/jobs/"
                f"{job_id}/file",
                headers=job_headers(job.get("claim_token")),
                data=fh, timeout=600)
        up.raise_for_status()
    posted = requests.post(
        f"{PLATFORM}/api/worker/audio/jobs/{job_id}/result",
        headers=job_headers(job.get("claim_token")),
        json=result, timeout=60)
    posted.raise_for_status()
    log(f"job {job_id}: word check result posted")


def fail_job(job_id: str, message: str, claim_token=None):
    try:
        requests.post(
            f"{PLATFORM}/api/worker/audio/jobs/{job_id}/fail",
            headers=job_headers(claim_token),
            json={"error": message[:280]},
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
            stage = job.get("stage")
            if stage == "bed":
                out_path = make_bed(job)
            elif stage == "voiceover":
                out_path = make_voiceover(job)
            elif stage in ("wordcheck", "transcribe"):
                # These stages deliver for themselves: any
                # repaired wav through the file endpoint and
                # the outcome through the result endpoint.
                make_wordcheck(job)
                out_path = None
            else:
                raise RuntimeError(f"unknown audio stage {stage!r}")
            if out_path is not None:
                log(f"job {job['id']}: uploading result")
                with open(out_path, "rb") as fh:
                    up = requests.post(
                        f"{PLATFORM}/api/worker/audio/jobs/"
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
