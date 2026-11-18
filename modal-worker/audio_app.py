"""
Sinclip audio stage on Modal: ACE-Step 1.5 music bed generation.

Standalone app ("sinclip-audio") so the music stage can iterate without
touching the ComfyUI worker. Uses ACE-Step 1.5 in DiT-only mode
(acestep-v15-turbo, no language model): for an instrumental bed we supply
caption + metadata ourselves, so the LM planner is unnecessary weight.

The project is installed at image build time with uv (its documented
install route); generation runs through the project's own venv python as
a subprocess so dependency quirks stay inside the project's environment.

generate_bed(caption, seed) is the callable the Moonfactory platform
uses for per-job custom audio (a Creator-plan feature): pass a music
description, get back wav bytes. Called with no arguments it produces
the house bed, exactly as before. Every (caption, seed) pair is cached
in the Volume, so a repeat call costs a container start, not a render.

generate_voiceover(text, voice) is the platform's speech stage
(Jack's correction, 2026-11-16: "audio" means voiceover/speech too,
not just music): Kokoro TTS (Apache-2.0), cached per (text, voice)
pair in the Volume exactly like beds. The platform calls it for a
job's speech_script on lanes that cannot generate speech natively,
and mixes the wav over the bed/native audio at delivery.

Usage:
  modal run audio_app.py             # generate the house bed, save locally
  modal run audio_app.py --caption "lofi piano, soft rain" --seed 42
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import modal

app = modal.App("sinclip-audio")

VOLUME_NAME = "sinclip-models"
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

ACE_ROOT = "/opt/acestep"

ace_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "curl")
    .run_commands(
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "git clone --depth 1 https://github.com/ace-step/ACE-Step-1.5.git " + ACE_ROOT,
        "cd " + ACE_ROOT + " && $HOME/.local/bin/uv sync",
    )
)

HOUSE_CAPTION = (
    "gentle uplifting instrumental background music, soft piano melody, "
    "warm strings, light airy pads, calm and dreamy, floating over "
    "green hills under a bright sky, cinematic ambient bed, no vocals"
)
HOUSE_SEED = 12345

GEN_SCRIPT = r'''
import json, sys, traceback
sys.path.insert(0, "/opt/acestep")

from acestep.handler import AceStepHandler
from acestep.inference import GenerationParams, GenerationConfig, generate_music

with open("/tmp/ace_params.json") as fh:
    spec = json.load(fh)
caption = spec["caption"]
seed = int(spec["seed"])

handler = AceStepHandler()
handler.initialize_service(
    project_root="/opt/acestep",
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


def _bed_key(caption: str, seed: int) -> str:
    return hashlib.sha256(f"{caption}|{seed}".encode()).hexdigest()[:16]


@app.function(
    image=ace_image,
    gpu="A10G",
    volumes={"/sinclip-models": volume},
    timeout=1800,
)
def generate_bed(caption: str = HOUSE_CAPTION, seed: int = HOUSE_SEED) -> bytes:
    """Generate (or fetch from the Volume cache) a 10 s instrumental
    bed for the given caption. The house bed keeps its original path;
    custom beds are cached under audio/custom/ by caption+seed hash."""
    is_house = caption == HOUSE_CAPTION and seed == HOUSE_SEED
    dest_dir = Path("/sinclip-models/audio")
    dest = (dest_dir / "music_bed_first.wav") if is_house else (
        dest_dir / "custom" / f"{_bed_key(caption, seed)}.wav"
    )
    if dest.exists():
        print(f"cache hit {dest}", flush=True)
        return dest.read_bytes()
    Path("/tmp/ace_params.json").write_text(
        json.dumps({"caption": caption, "seed": seed})
    )
    script_path = Path("/tmp/gen_bed.py")
    script_path.write_text(GEN_SCRIPT)
    proc = subprocess.run(
        [f"{ACE_ROOT}/.venv/bin/python", str(script_path)],
        capture_output=True, text=True, timeout=1500,
    )
    print(proc.stdout[-6000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-6000:], flush=True)
        raise RuntimeError(f"ACE-Step subprocess failed rc={proc.returncode}")
    out_dir = Path("/tmp/ace_out")
    wavs = sorted(out_dir.rglob("*.wav"))
    if not wavs:
        raise RuntimeError(f"no wav produced; /tmp/ace_out contains: {list(out_dir.rglob('*'))}")
    data = wavs[0].read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    volume.commit()
    print(f"persisted {dest} ({len(data)} bytes)", flush=True)
    return data


# ---------------------------------------------------------------------------
# Voiceover stage (Kokoro TTS, Apache-2.0). Kokoro-82M runs fine on
# the same A10G image; its weights and voices download from the
# public Kokoro HF repo on first use and cache with the container.
# ---------------------------------------------------------------------------

tts_image = ace_image.apt_install("espeak-ng").pip_install(
    "kokoro>=0.9.2", "soundfile", "numpy",
)

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


def _voice_key(text: str, voice: str) -> str:
    return hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()[:16]


def _speak_cached(text: str, voice: str) -> bytes:
    """The Kokoro synthesis core, shared by generate_voiceover and
    the word check fix pass. Runs inside whichever function calls
    it; the Volume cache is the same either way."""
    text = (text or "").strip()
    if not text:
        raise ValueError("voiceover text is empty")
    dest_dir = Path("/sinclip-models/audio/voice")
    dest = dest_dir / f"{_voice_key(text, voice)}.wav"
    if dest.exists():
        print(f"cache hit {dest}", flush=True)
        return dest.read_bytes()
    Path("/tmp/vo_params.json").write_text(
        json.dumps({"text": text, "voice": voice})
    )
    script_path = Path("/tmp/gen_voiceover.py")
    script_path.write_text(VOICEOVER_SCRIPT)
    proc = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True, text=True, timeout=600,
    )
    print(proc.stdout[-3000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], flush=True)
        raise RuntimeError(f"Kokoro subprocess failed rc={proc.returncode}")
    out = Path("/tmp/voiceover.wav")
    if not out.exists():
        raise RuntimeError("Kokoro produced no wav")
    data = out.read_bytes()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    volume.commit()
    print(f"persisted {dest} ({len(data)} bytes)", flush=True)
    return data


@app.function(
    image=tts_image,
    gpu="A10G",
    volumes={"/sinclip-models": volume},
    timeout=900,
)
def generate_voiceover(text: str, voice: str = "af_heart") -> bytes:
    """Speak one script with Kokoro and return wav bytes (24 kHz
    mono). Cached in the Volume under audio/voice/ by (voice, text)
    hash, so a repeat call costs a container start, not a render."""
    return _speak_cached(text, voice)


# ---------------------------------------------------------------------------
# Word check stage (Jack's call, 2026-11-18: the UGC Studio word fix
# idea, rebuilt for Moonfactory). After a job's final audio exists,
# transcribe it word by word with faster-whisper, align the transcript
# against the job's speech_script, and report the wrong words. Where
# the platform's own Kokoro stage voiced the job, a same-voice clean
# track exists, so a wrong word can be repaired without a re-render:
# re-speak its sentence with Kokoro, cut the correct word out of the
# clean take, splice it into the final audio at the wrong word's
# window, and check again. One fix pass. Speech-native lanes have no
# same-voice clean track, so they get the check and the flag only.
# A take with more than 20% of its script words still wrong after
# any fix pass is marked take_failed. The stage never blocks
# delivery: the platform wrapper flags the job "unavailable" if this
# callable raises, and ships the video either way.
#
# The alignment, ratio, status and splice logic below is pure
# (stdlib + numpy) so the check suites can exercise it without
# Modal or a model download.
# ---------------------------------------------------------------------------

import difflib
import re

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
    """English words for an integer 0..9999 (the range a spoken
    line in a short clip plausibly contains)."""
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
    """Normalize one whitespace token to the word list it is heard
    as. The same transform runs on the script and on the
    transcript, so any consistent choice keeps the two sides
    comparable: case and punctuation go away, contractions expand,
    and numbers become the words a speaker would say."""
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
    """Expand raw transcript words ({word, start, end}) into the
    flat normalized word list alignment works on, each normalized
    word carrying its source token's timestamps."""
    flat = []
    for w in heard_words or []:
        for nw in _token_words(str(w.get("word", "")).strip().lower()):
            flat.append((nw, float(w.get("start", 0.0)),
                         float(w.get("end", 0.0))))
    return flat


def _alignment_pairs(script_words, flat_heard) -> dict:
    """Map script word index -> (start, end) of the heard word it
    lines up with, for words the transcript actually produced
    (equal or replaced). Missing words are absent from the map."""
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
    """Align a transcript (raw word dicts with timestamps) against
    the intended script. Returns the script word count, the wrong
    words with the timestamps they were heard at, and the wrong
    ratio over script words. Extra heard words (insertions,
    stutters) never count against the script."""
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
    """The outcome vocabulary on the job record. The 20% line is
    strict: more than a fifth of the script still wrong fails the
    take; exactly a fifth is a flag, not a failure."""
    if initial_ratio == 0:
        return "passed"
    if final_ratio > TAKE_FAIL_RATIO:
        return "take_failed"
    if fix_applied and final_ratio == 0:
        return "fixed"
    return "flagged"


def sentence_spans(text) -> list:
    """Split a script into sentences, each with its span over the
    script's normalized word list. Per-sentence normalization
    concatenates to the whole-script normalization because the
    transform is token-local."""
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
    """Resample a word-length segment to the delivery track's rate
    and channel count. Linear interpolation is plenty for a single
    spoken word."""
    import numpy as np
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
    """Replace windows of the track with new segments. splices is a
    list of (start_s, end_s, segment) in track time; they are
    applied latest-first so earlier windows keep their timestamps.
    Each join is crossfaded inside the window over 5 ms so the
    splice does not click."""
    import numpy as np
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
    """One fix pass over the wrong words. speak_sentence(text)
    returns (samples, rate) of the same voice speaking the sentence
    clean; transcribe_samples(samples, rate) returns raw transcript
    word dicts. Returns (new_samples, applied_count). A word whose
    clean take cannot be produced or located is left alone."""
    import numpy as np
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


wordcheck_image = tts_image.pip_install(
    "faster-whisper==1.2.1", "av<19",
)

WHISPER_CACHE = "/sinclip-models/hf-cache"


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


@app.function(
    image=wordcheck_image,
    volumes={"/sinclip-models": volume},
    timeout=1800,
    memory=2048,
    cpu=2.0,
)
def word_check(audio: bytes, script: str, voice: str = "") -> dict:
    """Check a finished job's audio against its speech_script.

    audio is the job's final audio track (wav bytes, any rate or
    channel count the delivery carries). voice is the job's Kokoro
    voice when the platform voiced the job itself; only then does a
    same-voice clean track exist and the fix pass run. Returns the
    outcome, the final wrong words with their timestamps, and, when
    a fix was spliced in and the take is deliverable, the repaired
    audio as wav bytes in the input's own format. Raises on any
    stage failure; the platform flags that "unavailable" and
    delivers the video untouched."""
    import io

    import numpy as np
    import soundfile as sf
    from faster_whisper import WhisperModel

    script = (script or "").strip()
    if not script:
        raise ValueError("word check needs a speech script")
    if not audio:
        raise ValueError("word check needs audio bytes")
    in_path = Path("/tmp/wc_in.wav")
    in_path.write_bytes(audio)
    samples, rate = sf.read(str(in_path), dtype="float32",
                            always_2d=True)
    model = WhisperModel("base", device="cpu", compute_type="int8",
                         download_root=WHISPER_CACHE)
    try:
        volume.commit()
    except Exception:
        pass

    def transcribe_samples(s, r):
        p = Path("/tmp/wc_seg.wav")
        sf.write(str(p), s, r)
        return _transcribe_words(model, p)

    heard = _transcribe_words(model, in_path)
    first = evaluate_script(script, heard)
    final = first
    fixed_audio = None
    applied = 0
    if first["wrong"] and voice:
        def speak_sentence(text):
            data = _speak_cached(text, voice)
            s, r = sf.read(io.BytesIO(data), dtype="float32",
                           always_2d=True)
            return s, r

        new_samples, applied = run_word_fix(
            samples, rate, script, first["wrong"], speak_sentence,
            transcribe_samples)
        if applied:
            final = evaluate_script(
                script, transcribe_samples(new_samples, rate))
            if final["ratio"] <= TAKE_FAIL_RATIO:
                buf = io.BytesIO()
                sf.write(buf, new_samples, rate, format="WAV",
                         subtype="PCM_16")
                fixed_audio = buf.getvalue()
    status = decide_status(first["ratio"], final["ratio"],
                           bool(applied))
    return {
        "status": status,
        "wrong": [
            {"expected": w["expected"], "heard": w["heard"],
             "at": w["at"]}
            for w in final["wrong"]
        ],
        "listen_at": [w["at"] for w in final["wrong"]],
        "wrong_ratio": round(final["ratio"], 4),
        "initial_wrong_ratio": round(first["ratio"], 4),
        "audio": fixed_audio,
    }


@app.local_entrypoint()
def main(caption: str = "", seed: int = 0):
    if caption:
        use_seed = seed or 12345
        data = generate_bed.remote(caption, use_seed)
        out = Path(__file__).parent.parent / "outputs" / "custom_bed_test.wav"
    else:
        data = generate_bed.remote()
        out = Path(__file__).parent.parent / "outputs" / "music_bed_first.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"saved {out} ({len(data)} bytes)")
