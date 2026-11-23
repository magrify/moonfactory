"""Local checks for the 2026-11-18 generation time captions option.

Jack's decision: existing gallery videos get NO retroactive
captions. Instead the Create form carries an "Add captions"
box, unchecked by default; a job created with it checked gets
word timed captions burned into its delivered file during
finishing, in the Shorts lane's house style, timed from the
same faster-whisper transcription of the final audio the word
check stage already produces (or a fresh run of that same
transcription when the job has none). No speech or no
transcript still delivers, flagged on the job record. No price
change, no gallery change.

No Modal, no network, no generation: reads app.py and
audio_app.py, pins the wiring by source shape, and replays the
pure caption builders (AST extracted and executed) against
fabricated word timings.

Run:  modal-venv python scratch/check_captions_option.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)
AUDIO_SRC = (ROOT / "modal-worker" / "audio_app.py").read_text()
PRICING_SRC = (ROOT / "platform" / "pricing.py").read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def func_src(tree, src, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def literal(name):
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name
                for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at module level")


# -- the box -----------------------------------------------------------------

check(SRC.count('id="captions"') == 1,
      "the studio markup must carry exactly one captions box")
m = re.search(r'<input type="checkbox" id="captions"[^>]*>', SRC)
check(m is not None, "the captions box is not a checkbox input")
if m:
    check("checked" not in m.group(0),
          "the captions box must default to unchecked (opt in)")
check("Add captions" in SRC, "the box label is missing")
SUBLINE = "Captions are written into the video."
check(SUBLINE in SRC, "the box subline is missing")
i_sound = SRC.index('<p class="group-label">Sound</p>')
i_box = SRC.index('id="captions"')
i_details = SRC.index('<p class="group-label">Details</p>')
check(i_sound < i_box < i_details,
      "the box does not sit in the Sound group")

# Customer copy rule on everything this build shows: no colons,
# no semicolons, no hyphens or dashes.
for text in ("Add captions", SUBLINE):
    check(not re.search(r"[:;\u2010-\u2015-]", text),
          f"customer copy rule broken by {text!r}")

# -- the payload --------------------------------------------------------------

check(SRC.count("captions: $('captions').checked") == 2,
      "captions is not sent by both create payload branches")

# -- the caption style and lower third (2026-11-23) ---------------------------

check('id="caption-style"' in SRC, "the caption style pick is missing")
check('id="lower-third-name"' in SRC and 'id="lower-third-sub"' in SRC,
      "the lower third inputs are missing from the video form")
check(SRC.count("caption_style: $('caption-style').value") == 2,
      "the caption style is not sent by both create payload branches")
check(SRC.count("lower_third_name: $('lower-third-name').value.trim()") == 2,
      "the lower third name is not sent by both create payload branches")
check("caption_style = _clean_caption_style(body)" in SRC
      and "lower_third = _clean_lower_third(body)" in SRC,
      "the create endpoint does not clean the style and lower third")
check(SRC.count('"caption_style": caption_style,') == 3,
      "the two generation job records do not store the caption style "
      "(the third match is the clip lane's own older field)")
check(SRC.count('"lower_third": lower_third,') == 2,
      "both job creation paths do not store the lower third")
check(SRC.count('"lower_third_status": None,') == 2,
      "both job creation paths do not initialize lower_third_status")
check("caption_style=caption_style, lower_third=lower_third," in SRC,
      "the create endpoint does not pass the style and lower third to create_api_job")
check('"caption_style": job.get("caption_style")' in SRC,
      "the job payload does not expose the caption style")
check('"lower_third_status": job.get("lower_third_status")' in SRC,
      "the job payload does not expose lower_third_status")

# -- creation -----------------------------------------------------------------

check('captions = bool(body.get("captions"))' in SRC,
      "create_job does not read the captions flag from the body")
check(SRC.count('"captions_status": None,') == 4,
      "job creation paths do not all initialize captions_status")
check('"captions": bool(captions),' in SRC,
      "the per second job record does not store the flag")
check('"captions": captions,' in SRC,
      "the house job record does not store the flag")
sig = func_src(TREE, SRC, "create_api_job")
check("captions: bool = False" in sig,
      "create_api_job does not take the captions flag")
check("speech_voice=speech_voice, captions=captions," in SRC,
      "the create endpoint does not pass captions to create_api_job")
check('"captions": parent.get("captions")' in SRC,
      "regenerate does not carry the parent job's captions choice")
check('"captions": bool(job.get("captions"))' in SRC,
      "the job payload does not expose the captions flag")
check('"captions_status": job.get("captions_status")' in SRC,
      "the job payload does not expose captions_status")

# -- the finishing stage -------------------------------------------------------

stage = func_src(TREE, SRC, "_captions_for_job")
check(bool(stage), "_captions_for_job is missing from app.py")
check('want_captions = bool(job.get("captions"))' in stage
      and 'if not want_captions and not lower_third:' in stage,
      "the stage does not bail out untouched for jobs with neither captions nor a lower third")
check('job["lower_third_status"] = "applied"' in stage,
      "the stage never records the lower third as applied")
check('job["lower_third_status"] = "unavailable"' in stage,
      "the stage does not flag the lower third unavailable on failure")
check("lower_third=lower_third" in stage,
      "the stage does not pass the job's lower third to the ASS builder")
check('job["captions_status"] = "applied"' in stage,
      "the stage never records applied")
check('job["captions_status"] = "no_speech"' in stage,
      "the stage never records no_speech")
check('job["captions_status"] = "unavailable"' in stage,
      "the stage does not flag unavailable on failure")
check("except Exception:" in stage,
      "the stage can raise into the finishing path")
check('job.get("voiceover_status") in ("ready", "native")' in stage,
      "the speech gate does not cover voiceover and native jobs")
check('"sinclip-audio", "transcribe"' in stage,
      "the stage does not fall back to the sinclip-audio transcribe")
check("_CAPTION_TRANSCRIPTS.pop" in stage,
      "the stage does not reuse the word check transcription")
check("subtitles=" in stage and "libx264" in stage,
      "the stage does not burn the captions into the video")

wc = func_src(TREE, SRC, "_word_check_for_job")
check('result.pop("words", None)' in wc,
      "the word check wrapper drops the transcription words")
check('_CAPTION_TRANSCRIPTS[job["id"]] = heard_words' in wc,
      "the word check wrapper does not stash words for captions")

body = func_src(TREE, SRC, "ensure_processed")
check("_captions_for_job(job, out_path)" in body,
      "ensure_processed never runs the captions stage")
check(body.index("_captions_for_job(job, out_path)")
      > body.index("_word_check_for_job(job, out_path)"),
      "captions must burn after the word check, on the final audio")

# -- the audio worker -----------------------------------------------------------

check('"words": final_heard' in AUDIO_SRC,
      "word_check does not return the shipped audio's words")
check("final_heard = fixed_heard" in AUDIO_SRC,
      "word_check words do not follow the repaired track")
tr = func_src(ast.parse(AUDIO_SRC), AUDIO_SRC, "transcribe")
check(bool(tr), "the transcribe callable is missing from audio_app.py")
check("_transcribe_words(model, in_path)" in tr,
      "transcribe does not run the shared transcription")
check('raise ValueError("transcribe needs audio bytes")' in tr,
      "transcribe does not refuse empty audio")

# -- the pure builders, replayed -------------------------------------------------

ns = {}
for name in ("CAPTIONS_MAX_WORDS", "CAPTIONS_MIN_SECONDS",
             "VIDEO_CAPTION_STYLES", "VIDEO_CAPTION_STYLE_DEFAULT",
             "LOWER_THIRD_NAME_MAX", "LOWER_THIRD_SUB_MAX",
             "LOWER_THIRD_SECONDS"):
    ns[name] = literal(name)
check(ns["CAPTIONS_MAX_WORDS"] == 4,
      "caption chunking drifted from the Shorts lane's 4 words")
check(ns["CAPTIONS_MIN_SECONDS"] == 0.35,
      "caption minimum hold drifted from the Shorts lane's 0.35s")
check(ns["VIDEO_CAPTION_STYLES"] == ("classic", "karaoke", "pop"),
      "the video caption styles drifted")
for name in ("_caption_ass_time", "_caption_ass_escape",
             "caption_events", "_karaoke_events", "_lower_third_ass",
             "build_captions_ass", "_clean_caption_style",
             "_clean_lower_third"):
    exec(func_src(TREE, SRC, name), ns)
caption_events = ns["caption_events"]
build_captions_ass = ns["build_captions_ass"]


def heard(words, step=0.5):
    return [{"word": w, "start": i * step, "end": i * step + 0.4}
            for i, w in enumerate(words)]


events = caption_events(heard([f"w{i}" for i in range(9)]))
check(len(events) == 3, "9 words must chunk into 3 caption events")
check(events[0] == (0.0, 1.9, "w0 w1 w2 w3"),
      "the first chunk's span or text is wrong")
check(events[1] == (2.0, 3.9, "w4 w5 w6 w7"),
      "the second chunk's span or text is wrong")
check(events[2] == (4.0, 4.4, "w8"),
      "the last chunk's span or text is wrong")
short = caption_events([{"word": "hi", "start": 1.0, "end": 1.1}])
check(short == [(1.0, 1.35, "hi")],
      "a short chunk is not held to the minimum seconds")
check(caption_events([]) == [] and caption_events(None) == [],
      "no words must mean no caption events")
check(caption_events([{"word": "  ", "start": 0.0, "end": 0.4},
                      {"word": "real", "start": 0.5, "end": 0.9}])
      == [(0.5, 0.9, "real")],
      "blank transcript words are not skipped")

ass = build_captions_ass(heard(["hello", "wide", "world"]), 1080, 1920)
check("PlayResX: 1080" in ass and "PlayResY: 1920" in ass,
      "the ASS does not carry the video's own size")
check("Style: Captions,DejaVu Sans,72,&H00FFFFFF,&H00FFFFFF,"
      "&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,0,2,60,60,"
      "300,1" in ass,
      "at the shorts frame size the style is not the Shorts style")
check("Dialogue: 0,0:00:00.00,0:00:01.40,Captions,,0,0,0,,"
      "hello wide world" in ass,
      "the dialogue line does not match the word timings")

ass_small = build_captions_ass(heard(["hi"]), 720, 1280)
check(",DejaVu Sans,48," in ass_small,
      "the font size does not scale with the frame height")
ass_wide = build_captions_ass(heard(["hi"]), 1280, 720)
check(",DejaVu Sans,27," in ass_wide,
      "a landscape frame does not get its scaled font size")
esc = build_captions_ass(
    [{"word": "a{b}c", "start": 0.0, "end": 0.5}], 1080, 1920)
check("a(b)c" in esc and "a{b}c" not in esc,
      "ASS control characters in a word are not escaped")

kara = build_captions_ass(heard(["hello", "world"]), 1080, 1920,
                          style="karaoke")
check("{\\kf40}hello" in kara and "&H0000FFFF" in kara,
      "karaoke captions do not carry per word fill tags in the spoken color")
pop = build_captions_ass(heard(["hello"]), 1080, 1920, style="pop")
check("{\\fscx55\\fscy55\\t(0,170,\\fscx100\\fscy100)}hello" in pop,
      "pop captions do not scale each chunk in")
bogus = build_captions_ass(heard(["hello"]), 1080, 1920, style="bogus")
check(bogus == build_captions_ass(heard(["hello"]), 1080, 1920),
      "an unknown caption style does not fall back to classic")
LT = {"name": "Jane Doe", "sub": "Owner, Riverside Park"}
lt_only = build_captions_ass([], 1080, 1920, lower_third=LT)
check("Style: LowerName" in lt_only and "Jane Doe" in lt_only
      and "Riverside Park" in lt_only,
      "a lower third alone does not build its styles and words")
check("Dialogue: 0," not in lt_only,
      "a lower third alone must carry no caption dialogues")
check("Dialogue: 1,0:00:00.50,0:00:05.00,LowerName" in lt_only,
      "the lower third does not play across the first seconds")
both = build_captions_ass(heard(["hello"]), 1080, 1920, lower_third=LT)
check("Dialogue: 0," in both and "Dialogue: 1," in both,
      "captions and a lower third do not ride the same file")
clean_lt = ns["_clean_lower_third"]
check(clean_lt({"lower_third_name": "  Jane   Doe ",
                "lower_third_sub": " Owner "})
      == {"name": "Jane Doe", "sub": "Owner"},
      "the lower third cleaner does not tidy its words")
check(clean_lt({"lower_third_name": "   "}) is None,
      "a lower third with no name is not dropped")
check(clean_lt({"lower_third": {"name": "A B", "sub": ""}})
      == {"name": "A B", "sub": ""},
      "the lower third cleaner does not accept the object shape")
check(ns["_clean_caption_style"]({"caption_style": "pop"}) == "pop"
      and ns["_clean_caption_style"]({"caption_style": "nope"}) == "classic",
      "the caption style cleaner does not validate its pick")

# -- no price, no gallery reach ---------------------------------------------------

check("captions" not in PRICING_SRC,
      "captions leaked into pricing; it is not a charge")
check('"captions"' not in func_src(TREE, SRC, "ensure_poster"),
      "captions reached the poster path; gallery items stay untouched")

if failures:
    print("CAPTIONS OPTION CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL CAPTIONS OPTION CHECKS PASSED")
