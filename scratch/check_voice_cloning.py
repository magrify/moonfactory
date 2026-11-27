"""Local checks for the voice cloning lane (2026-11-21).

Jack's order: wire the commercially licensed voice cloning
from VoiceStudio's engine set into Moonfactory. The engine
is CosyVoice (FunAudioLLM/CosyVoice2-0.5B, Apache-2.0
weights, zero-shot cloning; measured 2026-11-22 at ECAPA
similarity 0.8599 against a 0.70 gate and Whisper WER 0.0
against a 0.05 gate, repo pinned to commit 0867ebc);
VoiceStudio's default cloning engine OmniVoice carries
CC-BY-NC weights and is never wired. These checks pin the
shape without any network, Modal, or Kaggle call:

- the clone pick is one extra id beside the pure Kokoro set
  (SPEECH_VOICES itself is unchanged, pinned by
  check_voice_picker), labeled "Voice cloning" in both the
  main Create voice picker and the shorts voice picker,
  each with its own reference clip fields;
- both create endpoints and the shorts create validate
  against ALL_SPEECH_VOICES, decode the reference clip
  through the one shape checker, and store it under the
  job id;
- the price matches the comparable paid lanes (the 1.9 cent
  nominal basis marked up once by the frontier minimum, 2
  cents, debited once per job), and the owner pays the
  Kaggle route's true raw cost, $0;
- the voiceover stage and the shorts narration loop route
  the clone pick to the Kaggle voice worker through the
  clone child machinery, never to Kokoro, and the word
  check never asks Kokoro to repair a clone's words;
- the worker endpoints mirror the template worker's claim
  shape under the same worker key, the fail endpoint gives
  the clone charge back, and the voice worker kernel ships
  in both Modal images;
- no OmniVoice reference exists anywhere in the platform
  or the worker kernel.

Run:  modal-venv python scratch/check_voice_cloning.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

SRC = (ROOT / "platform" / "app.py").read_text()
KERNEL = (ROOT / "worker-kaggle" / "voice_worker_kernel.py").read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


# The pick itself: one extra id, the Kokoro set untouched.
check(app.CLONE_VOICE_ID == "clone", "clone voice id changed")
check(app.ALL_SPEECH_VOICES == app.SPEECH_VOICES + ("clone",),
      "ALL_SPEECH_VOICES is not the Kokoro set plus the clone pick")
check("clone" not in app.SPEECH_VOICES,
      "the clone pick leaked into the pure Kokoro set")
check(app.SPEECH_VOICE_LABELS["clone"] == "Voice cloning",
      "clone pick label is not the plain name")
check(app.CLONE_WAIT_SECONDS >= 900,
      "clone wait is too short for a cold Kaggle voice session")

# The price: nominal basis marked up once, owner at raw $0.
check(pricing.VOICE_CLONE_RAW_CENTS == 1.9,
      "voice clone raw basis drifted from the comparable lanes")
check(pricing.voice_clone_price_cents() == 2,
      "customer clone price is not basis x frontier minimum (2 cents)")
check(pricing.voice_clone_price_cents(owner=True) == 0,
      "owner clone price is not the Kaggle raw cost ($0)")

# Reference decoding: one shape checker, the audio magics,
# the size cap.
check(callable(app._decode_clone_reference),
      "reference clip decoder missing")
import base64

wav = base64.b64encode(b"RIFF\x00\x00\x00\x00WAVEfmt ").decode()
raw, ext = app._decode_clone_reference(wav)
check(ext == "wav" and raw.startswith(b"RIFF"),
      "a WAV reference does not decode as wav")
for bad in ("", "not base64!!", base64.b64encode(b"\x00" * 32).decode()):
    try:
        app._decode_clone_reference(bad)
        check(False, f"bad reference {bad[:12]!r} was accepted")
    except ValueError:
        pass

# Create paths: validation against the full set, the clone
# branch, and the store under the job id on all three paths.
check(SRC.count("not in ALL_SPEECH_VOICES") == 2,
      "expected ALL_SPEECH_VOICES validation in create_job "
      "and shorts create")
check(SRC.count("_store_clone_reference(job, clone_reference,") == 3,
      "reference store is not wired on all three create paths")
check('detail="voice cloning needs a spoken line"' in SRC,
      "clone pick without a spoken line is not refused cleanly")
check("clone_reference=clone_reference" in SRC,
      "create_job does not pass the reference to create_api_job")

# Voice stages: the clone pick routes to the Kaggle voice
# worker, never to Kokoro, in both the video voiceover stage
# and the shorts narration loop.
check(SRC.count("_wait_clone_child(") >= 3,
      "clone wait helper is not used by both voice stages")
check('job.get("speech_voice") == CLONE_VOICE_ID' in SRC,
      "voiceover stage has no clone branch")
check("voice == CLONE_VOICE_ID" in SRC,
      "shorts narration loop has no clone branch")
check('job.get("speech_voice") != CLONE_VOICE_ID' in SRC,
      "word check would ask Kokoro to repair a clone's words")

# The child machinery: debit once, refund when nothing was
# delivered, children hidden from the library and the
# in-flight counters.
for name in ("_clone_debit_once", "_clone_refund_if_unsuccessful",
             "_queue_clone_child", "_wait_clone_child",
             "_wake_voice_kernel"):
    check(callable(getattr(app, name, None)), f"{name} missing")
check('"kind": "voice_clone"' in SRC,
      "clone children do not carry the voice_clone kind")
check('not j.get("clone_parent")' in SRC,
      "clone children are not hidden from the library list")
check(SRC.count('"voice_clone"))') >= 3,
      "clone children are not excluded from the in-flight counters")

# Worker endpoints: the template worker's claim shape under
# the voice prefix, the fail endpoint returning the charge.
for route in ('"/api/worker/voice/heartbeat"',
              '"/api/worker/voice/jobs/next"',
              '"/api/worker/voice/jobs/{job_id}/reference"',
              '"/api/worker/voice/jobs/{job_id}/file"',
              '"/api/worker/voice/jobs/{job_id}/fail"'):
    check(route in SRC, f"worker route {route} missing")
check("_clone_refund_if_unsuccessful(job[\"clone_parent\"])" in SRC,
      "voice fail endpoint does not return the clone charge")
check(SRC.count("voice_worker_kernel.py") >= 4,
      "voice worker kernel does not ship in both Modal images")
check(app.VOICE_KERNEL_SLUG == "magrify/moonfactory-voice-worker",
      "voice kernel slug changed")

# The kernel itself: CosyVoice, the platform's worker
# endpoints, and no non-commercial engine anywhere.
check("FunAudioLLM/CosyVoice2-0.5B" in KERNEL,
      "voice kernel does not load the CosyVoice2 weights")
check("0867ebcb8cbfdd74adce1e52daaff6c7d96782aa" in KERNEL,
      "voice kernel does not pin the measured repo commit")
check("CosyVoice2" in KERNEL and "load_wav" in KERNEL,
      "voice kernel does not use the pinned era loader "
      "and tensor reference")
check("engine setup in background" in KERNEL
      and "_setup_in_background" in KERNEL
      and "_SETUP_ERROR" in KERNEL,
      "voice kernel does not run engine setup in a "
      "background thread with the failure kept for the "
      "first claimed job")
check("wetext shim" in KERNEL or "pass-through shim" in KERNEL,
      "voice kernel has no wetext fallback shim")
check("FunAudioLLM/CosyVoice" in KERNEL,
      "voice kernel does not fetch the official CosyVoice repo")
for route in ("/api/worker/voice/heartbeat", "/api/worker/voice/jobs/next",
              "/reference", "/file", "/fail"):
    check(route in KERNEL, f"voice kernel never calls {route}")
check("k2-fsa" not in SRC and "k2-fsa" not in KERNEL
      and "import omnivoice" not in KERNEL.lower()
      and "from omnivoice" not in KERNEL.lower(),
      "OmniVoice (CC-BY-NC weights) is wired somewhere in the build")

# The engine's own dependency seam (fix, 2026-11-27): the
# pinned CosyVoice frontend imports the whisper module
# (cosyvoice/cli/frontend.py, "import whisper", OpenAI's
# package) at load time. The kernel's install list never
# carried it, and the load self-heal loop installed the
# bare PyPI name "whisper" (an unrelated package), so
# engine setup failed in every Kaggle session and no clone
# ever completed. The kernel must install openai-whisper
# up front, pinned, and the heal loop must map the module
# to that same pinned package.
check("openai-whisper==20250625" in KERNEL,
      "voice kernel does not install openai-whisper up front: "
      "the CosyVoice frontend imports the whisper module at "
      "load and engine setup fails without it (the pin is "
      "the 20250625 release because the 20231117 sdist no "
      "longer builds on Kaggle's current image)")
check('"whisper": "openai-whisper==20250625"' in KERNEL,
      "voice kernel load self-heal does not map the whisper "
      "module to openai-whisper (the bare PyPI name whisper "
      "is an unrelated package)")

# Studio markup and payloads: the plain pick in both voice
# pickers, a reference block in both forms, and the base64
# fields in both create payloads.
check(SRC.count('<option value="clone">Voice cloning</option>') == 2,
      "clone pick is not in both voice pickers")
for el in ('id="clone-opts"', 'id="clone-ref"', 'id="clone-ref-text"',
           'id="short-clone-opts"', 'id="short-clone-ref"',
           'id="short-clone-ref-text"'):
    check(el in SRC, f"studio markup missing {el}")
check(SRC.count("clone_reference_audio_b64") >= 4,
      "create payloads do not carry the reference clip")

# Customer copy rule on everything this build shows: no
# colons, no semicolons, no hyphens or dashes.
for text in ("Voice cloning",
             "A short clip of the voice to copy. A few seconds "
             "of clear speech works best.",
             "What the clip says (optional)",
             "voice cloning needs a spoken line",
             "the cloned voice could not be made"):
    check(not re.search(r"[:;\u2010-\u2015-]", text),
          f"customer copy rule broken by {text!r}")

if failures:
    print("VOICE CLONING CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL VOICE CLONING CHECKS PASSED")
