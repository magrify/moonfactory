"""Local checks for the 2026-11-18 word check stage.

Jack's call: take UGC Studio's word fix idea for Moonfactory.
After a job's final audio exists, it is transcribed word by word
and aligned against the job's speech_script. Jobs the platform
voiced itself (Kokoro) can be repaired by splicing the correct
word out of a clean re-spoken sentence; native lanes are checked
and flagged only. More than 20% of the script still wrong fails
the take. A stage failure flags the job "unavailable" and never
blocks delivery, and the check is never a charge.

No Modal, no network, no generation: the pure alignment, ratio,
status and splice functions are extracted from
modal-worker/audio_app.py and exercised against plain data and
fake speak/transcribe callables, and the platform wrapper's
placement and failure handling are asserted in app.py source.

Run:  python3 scratch/check_word_check.py
(system python carries numpy; the modal-venv does not)
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import numpy as np  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


AUDIO_SRC = (ROOT / "modal-worker" / "audio_app.py").read_text()
AUDIO_TREE = ast.parse(AUDIO_SRC)
APP_SRC = (ROOT / "platform" / "app.py").read_text()
APP_TREE = ast.parse(APP_SRC)
PRICING_SRC = (ROOT / "platform" / "pricing.py").read_text()


def audio_ns():
    wanted_funcs = {
        "_int_words", "_token_words", "normalize_words",
        "_flat_heard", "_alignment_pairs", "evaluate_script",
        "decide_status", "sentence_spans", "_match_format",
        "apply_splices", "run_word_fix",
    }
    wanted_assigns = {"_CONTRACTIONS", "_ONES", "_TENS",
                      "TAKE_FAIL_RATIO"}
    ns = {}
    import difflib
    import re
    ns["difflib"] = difflib
    ns["re"] = re
    for node in AUDIO_TREE.body:
        if isinstance(node, ast.FunctionDef) \
                and node.name in wanted_funcs:
            exec(ast.get_source_segment(AUDIO_SRC, node), ns)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets
                     if isinstance(t, ast.Name)}
            if names & wanted_assigns:
                exec(ast.get_source_segment(AUDIO_SRC, node), ns)
    return ns


NS = audio_ns()
normalize_words = NS["normalize_words"]
evaluate_script = NS["evaluate_script"]
decide_status = NS["decide_status"]
sentence_spans = NS["sentence_spans"]
apply_splices = NS["apply_splices"]
run_word_fix = NS["run_word_fix"]


def heard(words, step=0.5):
    return [{"word": w, "start": i * step, "end": i * step + 0.4}
            for i, w in enumerate(words)]


# -- normalization ---------------------------------------------------------

check(normalize_words("Hello, WORLD!") == ["hello", "world"],
      "case and punctuation survive normalization")
check(normalize_words("don't go") == normalize_words("do not go")
      == ["do", "not", "go"],
      "contraction does not expand the same on both sides")
check(normalize_words("it's here") == ["it", "is", "here"],
      "it's does not expand")
check(normalize_words("room 1000") == ["room", "one", "thousand"],
      "a round number does not become its spoken words")
check(normalize_words("in 2026 we") ==
      ["in", "two", "thousand", "twenty", "six", "we"],
      "a year does not become its spoken words")
check(normalize_words("paid 3.5 dollars") ==
      ["paid", "three", "point", "five", "dollars"],
      "a decimal does not become its spoken words")
check(normalize_words("over 1,000 views") ==
      ["over", "one", "thousand", "views"],
      "a comma number does not become its spoken words")

# -- alignment and ratio math ----------------------------------------------

r = evaluate_script("the quick brown fox jumps",
                    heard(["the", "quick", "brown", "fox", "jumps"]))
check(r["total"] == 5 and r["wrong"] == [] and r["ratio"] == 0.0,
      f"a clean take reads as wrong: {r}")

r = evaluate_script("the quick brown fox jumps",
                    heard(["the", "quick", "brown", "wolf", "jumps"]))
check(len(r["wrong"]) == 1 and r["wrong"][0]["expected"] == "fox"
      and r["wrong"][0]["heard"] == "wolf"
      and r["wrong"][0]["at"] == 1.5,
      f"a swapped word is not pinned to its timestamp: {r}")
check(abs(r["ratio"] - 0.2) < 1e-9, f"ratio math off: {r['ratio']}")

r = evaluate_script("the quick brown fox jumps",
                    heard(["the", "quick", "fox", "jumps"]))
check(len(r["wrong"]) == 1 and r["wrong"][0]["expected"] == "brown"
      and r["wrong"][0]["heard"] is None,
      f"a dropped word is not flagged as missing: {r}")

r = evaluate_script("the quick brown fox",
                    heard(["the", "very", "quick", "brown", "fox"]))
check(r["wrong"] == [] and r["ratio"] == 0.0,
      f"an extra heard word counts against the script: {r}")

r = evaluate_script("room one thousand",
                    heard(["room", "1000"]))
check(r["wrong"] == [], f"spoken number forms disagree: {r}")

r = evaluate_script("do not go", heard(["don't", "go"]))
check(r["wrong"] == [], f"contraction forms disagree: {r}")

# -- the 20% line and the outcome vocabulary --------------------------------

check(NS["TAKE_FAIL_RATIO"] == 0.20, "the take fail line moved")
check(decide_status(0.0, 0.0, False) == "passed",
      "a clean take is not passed")
check(decide_status(0.2, 0.2, False) == "flagged",
      "exactly a fifth wrong must flag, not fail")
check(decide_status(0.25, 0.25, False) == "take_failed",
      "over a fifth wrong must fail the take")
check(decide_status(0.4, 0.0, True) == "fixed",
      "a fix that clears every word is not fixed")
check(decide_status(0.4, 0.1, True) == "flagged",
      "a partial fix inside the line should flag what remains")
check(decide_status(0.6, 0.3, True) == "take_failed",
      "a fix that stays over the line must still fail the take")
check(decide_status(0.1, 0.1, False) == "flagged",
      "an unfixable lane with a wrong word should flag, not fail")

# -- sentence spans ----------------------------------------------------------

spans = sentence_spans("One two. Three four five! Six?")
check([(s["start"], s["end"]) for s in spans] ==
      [(0, 2), (2, 5), (5, 6)],
      f"sentence spans drift: {spans}")
joined = []
for s in spans:
    joined += normalize_words(s["text"])
check(joined == normalize_words("One two. Three four five! Six?"),
      "per sentence normalization does not add back up")

# -- the splice itself --------------------------------------------------------

track = np.zeros((1000, 1), dtype=np.float32)
out = apply_splices(track, 1000, [(0.2, 0.4, np.ones((300, 1),
                                                     dtype=np.float32))])
check(len(out) == 1100, f"splice length math off: {len(out)}")
check(abs(float(out[250, 0]) - 1.0) < 1e-6,
      "the spliced segment is not in the window")
check(float(out[100, 0]) == 0.0 and float(out[1050, 0]) == 0.0,
      "the splice leaked outside its window")

# -- one fix pass, with fake same-voice clean track ---------------------------

RATE = 100
WORDS = ["zero", "one", "two", "fox", "four",
         "five", "six", "seven", "eight", "nine"]
SCRIPT = " ".join(WORDS)


def fake_track(mark_index=None, mark_value=0.0):
    t = np.zeros((500, 1), dtype=np.float32)
    if mark_index is not None:
        t[mark_index * 50:(mark_index + 1) * 50] = mark_value
    return t


def fake_speak(text):
    # The clean take of the one sentence: every word present, the
    # correct word's block carries the value 1.0.
    return fake_track(3, 1.0), RATE


def fake_transcribe_clean(samples, rate):
    return heard(WORDS)


final = fake_track(3, 0.5)  # the take said something else at word 3
wrong = [{"expected": "fox", "heard": "wolf", "at": 1.5,
          "end": 1.9, "index": 3}]
fixed, applied = run_word_fix(final, RATE, SCRIPT, wrong,
                              fake_speak, fake_transcribe_clean)
check(applied == 1, f"fix pass applied {applied} splices, want 1")
check(abs(float(fixed[165, 0]) - 1.0) < 0.05,
      "the correct word is not sitting in the fixed window")
check(len(fixed) == 500 - 44 + 40,
      f"fixed track length off: {len(fixed)}")


def fake_transcribe_missing(samples, rate):
    return heard([w for w in WORDS if w != "fox"])


fixed2, applied2 = run_word_fix(final, RATE, SCRIPT, wrong,
                                fake_speak, fake_transcribe_missing)
check(applied2 == 0 and len(fixed2) == 500,
      "a word the clean take cannot produce must be left alone")


def dead_speak(text):
    raise RuntimeError("tts down")


fixed3, applied3 = run_word_fix(final, RATE, SCRIPT, wrong,
                                dead_speak, fake_transcribe_clean)
check(applied3 == 0 and len(fixed3) == 500,
      "a dead clean track must leave the take untouched")

# -- placement and the unavailable path (platform source) ---------------------


def func_src(tree, src, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


body = func_src(APP_TREE, APP_SRC, "_word_check_for_job")
check(bool(body), "_word_check_for_job is missing from app.py")
check('job["word_check_status"] = "unavailable"' in body,
      "the wrapper does not flag unavailable on stage failure")
check("except Exception:" in body,
      "the wrapper can raise into the finishing path")
check('job.get("voiceover_status") == "ready"' in body,
      "the fix pass is not limited to platform voiced jobs")
check('"sinclip-audio", "word_check"' in body,
      "the wrapper does not call the sinclip-audio word check")

body = func_src(APP_TREE, APP_SRC, "ensure_processed")
check("_word_check_for_job(job, out_path)" in body,
      "ensure_processed never runs the word check")
check(body.index("_word_check_for_job(job, out_path)")
      > body.index("_process_file(raw_path, out_path"),
      "the word check runs before the final mux, not after it")

check(APP_SRC.count('"word_check_status": None,') == 3,
      "job creation paths do not all initialize word_check_status")
check('"word_check_status": job.get("word_check_status")' in APP_SRC,
      "the job payload does not expose word_check_status")
check('"word_check_listen_at": job.get("word_check_listen_at")'
      in APP_SRC,
      "the job payload does not expose the listen list")

check('raise ValueError("word check needs a speech script")'
      in AUDIO_SRC, "word_check does not refuse an empty script")
check('raise ValueError("word check needs audio bytes")'
      in AUDIO_SRC, "word_check does not refuse empty audio")
check("faster-whisper==1.2.1" in AUDIO_SRC
      and '"av<19"' in AUDIO_SRC,
      "the word check image lost its faster-whisper pin")
check("word_check" not in PRICING_SRC,
      "the word check leaked into pricing; it is not a charge")

if failures:
    print("FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL WORD CHECKS PASSED")
