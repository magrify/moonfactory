"""WhisperX retiming of the caption words (2026-11-27).

Pins the wiring the caption timing eval bought (measured
proof: scratch/whisperx-eval/README.md — WhisperX word
starts averaged 0.027-0.041s from the nearest waveform
energy onset against faster-whisper's 0.093-0.098s). The
platform's word check / transcribe stages run on the
Kaggle audio worker, so the retime lives in the kernel:
_align_words retimes only the words that leave each
stage, the install is best effort behind the checked
faster-whisper install with the production pins repeated,
and every failure path returns the original timings.

Run:  modal-venv python scratch/check_whisperx_align.py
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
KERNEL = (ROOT / "worker-kaggle" / "audio_worker_kernel.py").read_text()

# The kernel and its embedded stage script both parse.
ast.parse(KERNEL)
script = re.search(r"WORDCHECK_SCRIPT = r'''(.*?)'''", KERNEL, re.S).group(1)
ast.parse(script)

# The aligner: present, fallback-shaped, and honest about
# what it refuses to guess (a word sequence that does not
# line up keeps the original timings).
assert "def _align_words(audio_path, heard) -> list:" in script
assert "whisperx.load_align_model(" in script
assert "whisperx.align(" in script
aligner = script[script.index("def _align_words"):script.index("def _load_model")]
assert "except Exception:" in aligner and "return heard" in aligner
assert aligner.count("return heard") >= 3

# Only the words that leave are retimed: transcribe mode's
# words, and word check mode's final heard words against
# the audio they describe (the repaired track when a fix
# was spliced in). The evaluation and splice inputs keep
# the original pass's timings.
assert 'heard = _align_words(source, heard)' in script
assert 'final_heard = _align_words(final_audio, final_heard)' in script
assert 'final_audio = "/tmp/wc_fixed.wav"' in script
fix_at = script.index("new_samples, applied = run_word_fix(")
region = script[fix_at:script.index("status = decide_status")]
assert region.count("_align_words(") == 1, region.count("_align_words(")

# The install: best effort, after the checked install,
# with the production pins repeated for the resolver, and
# a failure there never raises.
setup = KERNEL[KERNEL.index("def setup_whisper():"):]
setup = setup[:setup.index("def make_wordcheck")]
assert '"whisperx", "faster-whisper==1.2.1", "av<19"' in setup
assert 'log(f"uv pip install whisperx (best effort) -> rc={r.returncode}")' in setup
wx_at = setup.index('"whisperx"')
raise_at = setup.index("raise RuntimeError")
assert raise_at < wx_at  # the only raise guards faster-whisper

# The eval record exists with its numbers.
ev = (ROOT / "scratch" / "whisperx-eval" / "README.md").read_text()
assert "0.0266" in ev and "0.0975" in ev and "0.0407" in ev

print("whisperx align pins OK")
