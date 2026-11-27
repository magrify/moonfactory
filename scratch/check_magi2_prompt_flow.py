"""Local checks for the MAGI-2 Create flow build (2026-11-26,
from working MAGI-2 users' practice relayed by the owner).

Pins the flow's shape so a later edit cannot quietly break
it:

- the prompt cap is per model: MAGI-2 takes 15,000
  characters, every other model keeps 2000, and the create
  validation and the served config both read the one helper;
- the reference instruction composition: one line per
  reference in the fixed order the references are sent
  (images, then videos, then audios, each numbered within
  its kind), the picked character's images named at the head
  of the image lines, a marked video carrying the character
  sheet line, notes folded to one line and capped, and a
  prompt with nothing to say going out exactly as written;
- the composition rides the existing create path inside
  create_job for the magi2 pick only, and parked pending
  creations drop attached MAGI-2 reference files by name;
- the Improve MAGI-2 mode in shorts: a structured rewrite
  instruction with a tenth of a second timeline, a Chinese
  variant that writes the whole rewrite in Chinese, and a
  cleaner that keeps the section lines at the lane's cap;
- the studio carries the counter, the structure insert, the
  tips line, the references block, and the Chinese toggle,
  and buildCreate sends the reference arrays with their
  notes and the sheet pick.

No Modal calls, no network, no generation, no draft calls:
the improve pieces are pinned as pure functions only.

Run:  modal-venv python scratch/check_magi2_prompt_flow.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import shorts  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the per model prompt cap ------------------------------------------

check(app.PROMPT_MAX_CHARS == 2000,
      "the shared prompt cap moved off 2000")
check(app.MAGI2_PROMPT_MAX_CHARS == 15000,
      "the MAGI-2 prompt cap is not 15000")
check(app.prompt_max_chars("magi2") == 15000,
      "prompt_max_chars does not give magi2 its 15000")
for other in ("seedance", "wan2.2", "veo3", "kling40"):
    check(app.prompt_max_chars(other) == 2000,
          f"prompt_max_chars changed the cap for {other}")
create_src = func_src("create_job")
check("prompt_max_chars(model)" in create_src,
      "create_job does not validate against the per model cap")
check('"prompt_max_chars": prompt_max_chars(m["id"])' in SRC,
      "the served config does not carry prompt_max_chars")

# -- reference instruction composition ---------------------------------

compose = app.compose_magi2_reference_prompt

plain = compose("A prompt.", ["https://x.test/a.jpg"], [], [], {})
check(plain == "A prompt.",
      "a prompt with nothing to say changed in composition")

noted = compose(
    "A prompt.",
    ["https://x.test/a.jpg", "https://x.test/b.jpg"],
    ["https://x.test/v.mp4"], ["https://x.test/s.mp3"],
    {"images": ["the hero", ""], "videos": ["the chase"],
     "audios": []})
lines = noted.split("\n")
check(lines[0] == "Image 1, the hero",
      "the first image line lost its instruction")
check(lines[1] == "Image 2, a reference image",
      "an image with no instruction lost its filler line")
check(lines[2] == "Video 1, the chase",
      "the video line lost its instruction or its order")
check(lines[3] == "Audio 1, a reference audio",
      "the audio line lost its filler line or its order")
check(noted.endswith("\n\nA prompt."),
      "the composed lines do not ride ahead of the prompt")

charred = compose(
    "A prompt.",
    ["https://x.test/c.jpg", "https://x.test/u.jpg"], [], [],
    {"images": ["the hero"]},
    character_name="Mara", character_images=1)
check(charred.split("\n")[0] == "Image 1, the character Mara",
      "the character's image line does not name the character")
check(charred.split("\n")[1] == "Image 2, the hero",
      "the user's image note shifted off its own reference")

sheeted = compose(
    "A prompt.", [], ["https://x.test/v.mp4"], [], {},
    character_name="Mara", sheet_video_index=0)
check(sheeted.split("\n")[0] ==
      "Video 1, the character sheet for Mara, it shows how "
      "the character moves and acts",
      "the character sheet line is not the sheet line")
sheeted_plain = compose(
    "A prompt.", [], ["https://x.test/v.mp4"], [],
    {"videos": ["use the walk"]}, sheet_video_index=0)
check(sheeted_plain.split("\n")[0] ==
      "Video 1, the character sheet, it shows how the "
      "character moves and acts, use the walk",
      "the sheet line lost the user's own note")
bad_sheet = compose(
    "A prompt.", [], ["https://x.test/v.mp4"], [], {},
    sheet_video_index=4)
check(bad_sheet == "A prompt.",
      "an out of range sheet pick composed lines anyway")

folded = compose(
    "A prompt.", ["https://x.test/a.jpg"], [], [],
    {"images": ["one\ntwo  " + "x" * 400]})
first = folded.split("\n")[0]
check("\n" not in first.split(", ", 1)[1] and "one two" in first,
      "a note with a line break was not folded to one line")
check(len(first) <= len("Image 1, ") + 300,
      "a note was not capped at 300 characters")
malformed = compose(
    "A prompt.", ["https://x.test/a.jpg"], [], [],
    {"images": "not a list"})
check(malformed == "A prompt.",
      "malformed notes composed lines instead of reading as none")

check("compose_magi2_reference_prompt" in create_src,
      "create_job never composes the MAGI-2 reference lines")
check('model == "magi2"' in create_src,
      "the composition is not scoped to the magi2 pick")
pending_src = func_src("_clean_pending_create")
check("your reference files" in pending_src,
      "parked creations do not name dropped MAGI-2 reference files")

# -- Improve, MAGI-2 mode (pure functions only) -------------------------

check(shorts.MAGI2_HELP_MAX_CHARS == 15000,
      "the MAGI-2 help cap is not the lane's 15000")
en = shorts.magi2_help_instruction("a cat crosses a room")
check("Timeline" in en and "tenth of a second" in en,
      "the MAGI-2 help instruction lost its timeline")
check("Character" in en and "References" in en,
      "the MAGI-2 help instruction lost its sections")
zh = shorts.magi2_help_instruction("a cat crosses a room", "zh")
check("in Chinese" in zh,
      "the Chinese toggle's instruction does not write in Chinese")
cleaned = shorts._clean_helped_magi2(
    '"Character\nMara walks in\n\nTimeline\n0.0s she enters"')
check(cleaned.startswith("Character\nMara walks in"),
      "the MAGI-2 cleaner folded the section lines away")
long = shorts._clean_helped_magi2("word " * 4000)
check(len(long) <= 15000,
      "the MAGI-2 cleaner does not cap at 15000")
help_src = func_src("prompt_help")
check('"magi2"' in help_src and "language" in help_src,
      "the prompt help endpoint does not take the magi2 kind")

# -- the studio pieces ---------------------------------------------------

for needle, what in (
        ('id="prompt-count"', "the prompt counter"),
        ('id="magi2-tools"', "the MAGI-2 tools block"),
        ('id="magi2-tips"', "the MAGI-2 tips line"),
        ("video character sheet for each character",
         "the tips line's five practices"),
        ('id="magi2-structure-btn"', "the structure button"),
        ("Use the MAGI-2 prompt structure", "the structure label"),
        ('id="magi2-refs"', "the references block"),
        ('id="magi2-help-opts"', "the MAGI-2 help options"),
        ('id="prompt-help-zh"', "the Chinese toggle"),
        ("MAGI2_PROMPT_STRUCTURE", "the scaffold text"),
        ("0.0s the opening frame", "the scaffold timeline rows"),
        ("character sheet", "the character sheet wording")):
    check(needle in SRC, f"{what} is missing from the studio")
check("const magi2Fields = model === 'magi2' "
      "? magi2CreateFields() : {};" in SRC
      and "...magi2Fields }" in SRC,
      "buildCreate does not build and spread the MAGI-2 fields")
check("reference_notes" in SRC and "character_sheet_video" in SRC,
      "the reference notes or sheet pick never reach the body")

if failures:
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_magi2_prompt_flow")
