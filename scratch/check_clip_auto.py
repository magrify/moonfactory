"""Local checks for the 2026-11-27 Clip form pass (Jack's orders).

Three orders, one pin:
1. "How many clips" and "Clip length" each gain an Auto option,
   and Auto is the default on both. Auto must work, not just
   exist: Auto length sizes each clip to its own moment inside
   15 to 90 seconds (the picker's chosen end cleaned to the
   closest complete-sentence end, clipping._snap's "nearest"
   mode), and Auto count resolves from the source, up to
   clipping.auto_clip_cap(duration) picker candidates with
   only the strong ones kept (score at or above
   clipping.AUTO_MIN_SCORE, the single best always kept).
2. Every Clip length label states its seconds, taken from the
   pipeline's real LENGTH_RANGES, Auto included.
3. Caption style gains "No captions" as the top choice and
   the default: a real style value (clipping.NO_CAPTION_STYLE)
   the create path validates through clipping.CAPTION_STYLES
   and render_clip honors by skipping the caption burn
   entirely (no word-by-word captions, no hook pill).

No Modal, no network, no generation: reads app.py's source,
imports clipping for real, replays find_clips against a
stubbed picker, and replays render_clip against a stubbed
ffmpeg runner.

Run:  modal-venv python scratch/check_clip_auto.py
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
sys.path.insert(0, str(ROOT / "platform"))
import clipping  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


# --- the pipeline's own constants --------------------------------
check(clipping.LENGTH_RANGES == {"short": (15, 35), "medium": (30, 60),
                                 "long": (45, 90), "auto": (15, 90)},
      "LENGTH_RANGES changed or auto is not the 15 to 90 union")
check(list(clipping.CAPTION_STYLES) == ["none", "bounce", "hype", "clean"],
      "CAPTION_STYLES order is not none, bounce, hype, clean")
check(list(clipping.CAPTION_STYLES.values())
      == ["No captions", "Bounce", "Hype", "Clean"],
      "CAPTION_STYLES labels changed")
check(set(clipping.STYLES) == {"bounce", "hype", "clean"},
      "the drawn caption styles changed")
check(clipping.NO_CAPTION_STYLE == "none"
      and clipping.NO_CAPTION_STYLE not in clipping.STYLES,
      "No captions must be a real value outside the drawn styles")
check(clipping.AUTO_MIN_SCORE == 60, "AUTO_MIN_SCORE changed")

# Auto count cap: about one clip per five minutes of source,
# at least one, never past the per-job cap.
for duration, want in ((0, 1), (60, 1), (300, 1), (301, 2), (900, 3),
                       (1800, 6), (100000, 6)):
    check(clipping.auto_clip_cap(duration) == want,
          f"auto_clip_cap({duration}) is not {want}")

# --- the form ------------------------------------------------------
def options(select_id):
    m = re.search(rf'<select id="{select_id}">(.*?)</select>', SRC, re.S)
    if not check(m is not None, f"form has no {select_id} select"):
        return []
    return re.findall(r'<option value="([^"]+)"( selected)?>([^<]+)</option>',
                      m.group(1))


count_opts = options("clip-count")
check([o[0] for o in count_opts]
      == ["auto", "1", "2", "3", "4", "5", "6"],
      "clip count options are not Auto then 1 to 6")
check(count_opts and count_opts[0][1] == " selected"
      and all(not o[1] for o in count_opts[1:]),
      "Auto is not the only preselected clip count")

lo, hi = clipping.LENGTH_RANGES["auto"]
auto_label = f"Auto ({lo} to {hi} seconds, picked per clip)"
expected_lengths = [("auto", auto_label, " selected")]
for name in ("short", "medium", "long"):
    l2, h2 = clipping.LENGTH_RANGES[name]
    expected_lengths.append(
        (name, f"{name.capitalize()} ({l2} to {h2} seconds)", ""))
length_opts = options("clip-length")
check([(o[0], o[2], o[1]) for o in length_opts] == expected_lengths,
      "clip length options do not match LENGTH_RANGES with seconds shown")
for _, label, _ in expected_lengths:
    check("seconds" in label, f"length label {label!r} states no seconds")

style_opts = options("clip-style")
check([o[0] for o in style_opts] == list(clipping.CAPTION_STYLES),
      "caption style options are not the CAPTION_STYLES order")
check([o[2] for o in style_opts] == list(clipping.CAPTION_STYLES.values()),
      "caption style labels do not match CAPTION_STYLES")
check(style_opts and style_opts[0][0] == "none"
      and style_opts[0][1] == " selected"
      and all(not o[1] for o in style_opts[1:]),
      "No captions is not the first and only preselected caption style")

# Customer copy rule on the new visible text: no colons, no
# semicolons, no hyphens or dashes.
NOTE = ("Auto lets the picker decide from the video itself how many "
        "clips to make and how long each one runs.")
check(NOTE in SRC, "the Auto explainer line is missing from the form")
for text in [o[2] for o in length_opts] + ["No captions", NOTE]:
    check(not re.search(r"[:;\u2010-\u2015-]", text),
          f"customer copy rule broken by {text!r}")

# --- wiring through app.py -----------------------------------------
check("clip_count: $('clip-count').value === 'auto' ? 'auto' "
      ": Number($('clip-count').value)" in SRC,
      "the form does not send the auto clip count through")
check('raw_count.strip().lower() == "auto"' in SRC,
      "create_clip does not accept the auto clip count")
check('"length must be auto, short, medium or long"' in SRC,
      "create_clip length rejection does not name auto")
check("caption_style not in clipping.CAPTION_STYLES" in SRC,
      "create_clip does not validate caption style via CAPTION_STYLES")
check('body.get("caption_style") or clipping.NO_CAPTION_STYLE' in SRC,
      "create_clip default caption style is not No captions")
check("clipping.auto_clip_cap(duration)" in SRC
      and "clipping.AUTO_MIN_SCORE" in SRC
      and "min_score=pick_min_score" in SRC,
      "the pipeline does not resolve Auto count via cap plus min_score")
check('"length_ranges"' in SRC
      and '"caption_styles": dict(clipping.CAPTION_STYLES)' in SRC,
      "/api/config does not carry the length ranges and caption styles")

# --- the real picking logic, replayed ------------------------------
def sentence(start, n_words):
    out = []
    for i in range(n_words):
        out.append({"w": "end." if i == n_words - 1 else "tok",
                    "s": float(start + i), "e": float(start + i + 1)})
    return out


WORDS = sentence(0, 20) + sentence(20, 30) + sentence(50, 60)
DB = [-30.0] * 120
DURATION = 120.0


def cand(start, end, score):
    return {"start": start, "end": end, "score": score,
            "category": "story", "hook": "WAIT FOR IT",
            "title": "t", "description": "", "hashtags": [],
            "highlight_words": [], "reason": "pin"}


CANDS = [cand(0, 23, 90), cand(20, 47, 75), cand(55, 105, 40)]
real_pick = clipping._gemini_pick
try:
    clipping._gemini_pick = lambda prompt, key: list(CANDS)
    auto, info = clipping.find_clips(
        WORDS, DB, DURATION, max_clips=6, length="auto", api_key="pin")
    check(info["mode"] == "ai", "auto picking did not use the picker")
    check([(c["start"], c["end"]) for c in auto]
          == [(0.0, 20.0), (20.0, 50.0), (55.0, 110.0)],
          "auto clips do not sit on the moments' own sentence ends")
    spans = [c["end"] - c["start"] for c in auto]
    check(all(15 <= s <= 90 for s in spans) and len(set(spans)) == 3,
          "auto clip lengths are not per-clip inside 15 to 90 seconds")

    strong, _ = clipping.find_clips(
        WORDS, DB, DURATION, max_clips=6, length="auto",
        api_key="pin", min_score=clipping.AUTO_MIN_SCORE)
    check([(c["start"], c["end"]) for c in strong]
          == [(0.0, 20.0), (20.0, 50.0)],
          "auto count kept a moment below the score bar")

    clipping._gemini_pick = lambda prompt, key: [
        cand(0, 23, 50), cand(20, 47, 40), cand(55, 105, 30)]
    best_only, _ = clipping.find_clips(
        WORDS, DB, DURATION, max_clips=6, length="auto",
        api_key="pin", min_score=clipping.AUTO_MIN_SCORE)
    check(len(best_only) == 1 and best_only[0]["score"] == 50,
          "auto count did not fall back to the single best moment")

    clipping._gemini_pick = lambda prompt, key: list(CANDS)
    medium, _ = clipping.find_clips(
        WORDS, DB, DURATION, max_clips=6, length="medium", api_key="pin")
    check([(c["start"], c["end"]) for c in medium]
          == [(0.0, 50.0), (55.0, 110.0)],
          "fixed medium picking changed shape (auto must differ from it)")
finally:
    clipping._gemini_pick = real_pick

# _snap: nearest keeps the picker's end, extend stretches it.
clip_in = {"start": 0, "end": 23}
check(clipping._snap(clip_in, WORDS, 15, 90, DURATION, mode="nearest")
      == (0.0, 20.0), "_snap nearest did not keep the closest sentence end")
check(clipping._snap(clip_in, WORDS, 15, 90, DURATION, mode="extend")
      == (0.0, 50.0), "_snap extend behavior changed")

# --- the renderer, replayed -----------------------------------------
SMALL_WORDS = [{"w": "Hello", "s": 0.0, "e": 0.5},
               {"w": "world.", "s": 0.5, "e": 1.0}]
real_run = clipping._run
real_build = clipping.build_ass
runs = []


def fake_run(cmd, timeout=3600, cwd=None):
    runs.append(cmd)
    Path(cwd, cmd[-1]).write_bytes(b"x")
    return ""


def filter_arg(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


try:
    clipping._run = fake_run

    def boom(*a, **k):
        raise AssertionError("build_ass ran for No captions")

    clipping.build_ass = boom
    out_dir = Path(tempfile.mkdtemp())
    res = clipping.render_clip(
        "src.mp4", out_dir, "pin_none",
        {"start": 0, "end": 10, "style": "none", "layout": "blur",
         "hook": "A HOOK", "highlight_words": []},
        SMALL_WORDS, 1920, 1080, None)
    check("ass=" not in filter_arg(runs[0]),
          "No captions still chains the caption filter")
    check((out_dir / res["file"]).exists(),
          "No captions render produced no file")
    runs.clear()

    clipping.build_ass = real_build
    res = clipping.render_clip(
        "src.mp4", out_dir, "pin_bounce",
        {"start": 0, "end": 10, "style": "bounce", "layout": "blur",
         "hook": "A HOOK", "highlight_words": []},
        SMALL_WORDS, 1920, 1080, None)
    check("ass=" in filter_arg(runs[0]),
          "Bounce no longer burns captions in")
finally:
    clipping._run = real_run
    clipping.build_ass = real_build

if failures:
    print("CLIP AUTO CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL CLIP AUTO CHECKS PASSED")
