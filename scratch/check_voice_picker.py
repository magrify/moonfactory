"""Local checks for the 2026-11-18 voice picker.

Jack's order: when creating a video and choosing which model to
use, users need to be able to choose between the different
voices Moonfactory can offer. The picker lists the full English
Kokoro set with plain friendly names, sends speech_voice exactly
as the API already accepts, and changes no price. Voiceover
stays paid only.

Extended 2026-11-21 for the voice cloning pick: the picker
gains one extra option, "Voice cloning" (the CosyVoice
zero-shot cloning lane), appended after the Kokoro set.
SPEECH_VOICES stays exactly the verified Kokoro set; the
clone pick joins only through ALL_SPEECH_VOICES, which is
what create validation now targets, and its label is stamped
onto SPEECH_VOICE_LABELS at runtime beside the literal.

No Modal, no network, no generation, no audio: reads app.py,
extracts the SPEECH_VOICES / SPEECH_VOICE_LABELS /
DEFAULT_SPEECH_VOICE literals by AST, replays the create_job
validation predicate against every id, and asserts the studio
markup carries the picker wired to speech_voice.

The EXPECTED list below is the verified primary source set:
hexgrad's own Kokoro-TTS demo choices (hexgrad/Kokoro-TTS
Space app.py CHOICES), matching the Kokoro-82M VOICES.md list,
read 2026-11-18. An id that is not in EXPECTED must not ship.

Run:  modal-venv python scratch/check_voice_picker.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def literal(name):
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at module level")


SPEECH_VOICES = literal("SPEECH_VOICES")
SPEECH_VOICE_LABELS = literal("SPEECH_VOICE_LABELS")
DEFAULT_SPEECH_VOICE = literal("DEFAULT_SPEECH_VOICE")

# The verified full English Kokoro set, in picker order:
# American women, American men, British women, British men.
EXPECTED = (
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah",
    "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)

check(tuple(SPEECH_VOICES) == EXPECTED,
      "SPEECH_VOICES is not exactly the verified Kokoro English set")
check(len(SPEECH_VOICES) == 28, "expected 28 English voices")
for prefix, count in (("af_", 11), ("am_", 9), ("bf_", 4), ("bm_", 4)):
    check(sum(1 for v in SPEECH_VOICES if v.startswith(prefix)) == count,
          f"expected {count} voices with prefix {prefix}")
check(DEFAULT_SPEECH_VOICE == "af_heart", "default voice changed")
check(SPEECH_VOICES[0] == DEFAULT_SPEECH_VOICE,
      "default voice is not first in the picker order")
check(set(SPEECH_VOICE_LABELS) == set(SPEECH_VOICES),
      "SPEECH_VOICE_LABELS does not cover exactly SPEECH_VOICES")
# The clone pick's label is stamped at runtime beside the
# literal (2026-11-21); the literal above stays the Kokoro set.
check('SPEECH_VOICE_LABELS[CLONE_VOICE_ID] = "Voice cloning"' in SRC,
      "clone pick label is not stamped beside SPEECH_VOICE_LABELS")
ALL_VOICES = list(SPEECH_VOICES) + ["clone"]

# Create validation: the predicate create_job applies today is
# "reject when the value is non-empty and not in
# ALL_SPEECH_VOICES" (the Kokoro set plus the clone pick).
# Pin the source shape, then replay the predicate per id.
check('speech_voice and speech_voice not in ALL_SPEECH_VOICES' in SRC,
      "create_job no longer validates speech_voice against ALL_SPEECH_VOICES")
check('detail="unknown speech voice"' in SRC,
      "create_job unknown voice rejection changed")


def rejected(voice):
    return bool(voice) and voice not in ALL_VOICES


for v in ALL_VOICES:
    check(not rejected(v), f"verified voice {v} would be rejected")
for bad in ("af_heart2", "heart", "AF_HEART", "bf_emma ",
            "not_a_voice", "af_santa_claus"):
    check(rejected(bad), f"unknown voice {bad!r} would be accepted")
check(not rejected(""), "empty speech_voice must stay allowed (default)")

# The voiceover stage still falls back to the unchanged default,
# and the audio worker's own defaults still agree with it.
check('job.get("speech_voice") or DEFAULT_SPEECH_VOICE' in SRC,
      "voiceover fallback no longer uses DEFAULT_SPEECH_VOICE")
AUDIO_SRC = (ROOT / "modal-worker" / "audio_app.py").read_text()
check('voice: str = "af_heart"' in AUDIO_SRC
      and 'or "af_heart"' in AUDIO_SRC,
      "audio worker default voice drifted from af_heart")

# Studio markup: the picker exists, sits in the Sound group right
# after the spoken line field, and its options are exactly the
# voice set with the plain labels, never the raw ids as text.
m = re.search(r'<select id="speech-voice">(.*?)</select>', SRC, re.S)
check(m is not None, "studio markup has no speech-voice picker")
if m:
    options = re.findall(
        r'<option value="([a-z0-9_]+)"( selected)?>([^<]+)</option>',
        m.group(1))
    check([o[0] for o in options] == ALL_VOICES,
          "picker option values do not match the voice set in order")
    check([o[2] for o in options]
          == [SPEECH_VOICE_LABELS[v] for v in SPEECH_VOICES]
          + ["Voice cloning"],
          "picker option labels do not match the voice labels")
    check(options and options[0][0] == DEFAULT_SPEECH_VOICE
          and options[0][1] == " selected",
          "picker does not preselect the default voice")
    check(all("_" not in o[2] for o in options),
          "a raw voice id shows as visible picker text")
    i_speech = SRC.index('id="speech"')
    i_picker = SRC.index('id="speech-voice"')
    i_note = SRC.index('id="voice-note"')
    check(i_speech < i_picker < i_note,
          "picker is not in the spoken line area of the Sound group")

# Wiring: both create payloads send the pick the same way the
# spoken line is sent, and the paid gate disables it alongside
# the spoken line for unpaid accounts.
send = "speech_voice: $('speech').disabled ? '' : $('speech-voice').value"
check(SRC.count(send) == 2,
      "speech_voice is not sent by both create payload branches")
check("$('speech-voice').disabled = false;" in SRC,
      "paid accounts do not get the picker enabled")
check("$('speech-voice').disabled = true;" in SRC,
      "unpaid accounts do not get the picker disabled")

# The speech native note: both wordings present, switched on the
# catalog's native_speech flag inside updateModelUI.
check("vEntry.audio.native_speech" in SRC.replace(" ", "")
      or "vEntry && vEntry.audio && vEntry.audio.native_speech" in SRC,
      "voice note does not read the model native_speech flag")
NOTE_NATIVE = ("This model talks with its own voice. Your voice pick "
               "applies when Moonfactory speaks the line.")
NOTE_PLATFORM = "Moonfactory speaks your line with the voice you pick."
check(NOTE_NATIVE in SRC, "speech native voice note wording missing")
check(NOTE_PLATFORM in SRC, "platform voice note wording missing")

# Customer copy rule on everything this build shows: no colons,
# no semicolons, no hyphens or dashes in labels or notes.
for text in list(SPEECH_VOICE_LABELS.values()) + ["Voice cloning",
                                                  NOTE_NATIVE, NOTE_PLATFORM]:
    check(not re.search(r"[:;\u2010-\u2015-]", text),
          f"customer copy rule broken by {text!r}")

if failures:
    print("VOICE PICKER CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL VOICE PICKER CHECKS PASSED")
