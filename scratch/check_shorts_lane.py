"""Local checks for the faceless shorts lane (2026-11-18).
No Modal calls, no network: imports the platform module and the
shorts pipeline module, exercises the pure pieces (script
splitting, search terms, caption timing, stock response parsing
against canned payloads, the dark-safe topic gate), runs one
tiny real ffmpeg assembly from generated clips, and pins the
lane wiring in the source (zero price, free cap counting, paid
daily cap, voice gate, poll bound, studio surface).

Run:  modal-venv python scratch/check_shorts_lane.py
"""
import json
import os
import subprocess
import sys
import tempfile
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
SHORTS_SRC = (ROOT / "platform" / "shorts.py").read_text()

# --- constants ---------------------------------------------------------
check(app.SHORTS_DAILY_CAP_PAID == 20, "shorts paid daily cap is 20")
check(app.TEMPLATE_DAILY_CAP_PAID == 20,
      "shorts cap mirrors the titles cap")
check(app.SHORT_POLL_LIMIT_SECONDS == 3900,
      "short poll bound is 3900s, the clip bound shape")
check(app.SHORTS_STAGES == (
    "Finding footage", "Recording the narration",
    "Timing the captions", "Putting it together"),
    "four shorts stages exist")
for stage in app.SHORTS_STAGES:
    check(":" not in stage and ";" not in stage and "-" not in stage,
          f"stage copy follows the plain copy rule: {stage}")
check(shorts.WIDTH == 1080 and shorts.HEIGHT == 1920,
      "shorts are vertical 1080 x 1920")
check(set(app.SHORTS_MUSIC_STYLES) == {
    "house", "calm", "upbeat", "cinematic", "lofi", "acoustic",
    "jazz", "electronic", "corporate", "epic", "synthwave",
    "tropical", "none", "custom"},
    "the music choices are the platform bed, eleven styles, "
    "no music, and the custom pick")
check(app.SHORTS_MUSIC_STYLES["house"]["label"] == "Dreamy ambient",
      "the house bed pick is named for what the track is, "
      "a dreamy ambient bed")
check(app.SHORTS_CUSTOM_MUSIC == "custom"
      and app.SHORTS_MUSIC_STYLES["custom"]["label"]
      == "Custom, your own description",
      "the custom pick is the own description choice")
for music_id, entry in app.SHORTS_MUSIC_STYLES.items():
    check(all(mark not in entry["label"] for mark in (":", ";", "-")),
          f"music label follows the plain copy rule: {entry['label']}")
    if music_id not in ("house", "none", "custom"):
        check(bool(entry["caption"]),
              f"music style {music_id} carries its bed caption")
check(not app.SHORTS_MUSIC_STYLES["custom"]["caption"],
      "the custom pick carries no fixed caption, its bed "
      "comes from the user's own description")
check(shorts.SHORTS_MAX_LINES == 20, "script line cap is 20")
check(shorts.SHORTS_MAX_SCRIPT_CHARS == 2200,
      "script cap fits a full two minutes")

# --- length choice -------------------------------------------------------
check(shorts.SHORTS_LENGTH_TARGETS == (30, 60, 90, 120),
      "topic lengths run from 30 seconds to 2 minutes")
check(shorts.SHORTS_DEFAULT_LENGTH_SECONDS == 60,
      "the default topic length is 60 seconds")
check(shorts.length_plan(30) == (75, 4),
      "30 seconds plans 75 words in 4 lines")
check(shorts.length_plan(60) == (150, 7),
      "60 seconds plans 150 words in 7 lines")
check(shorts.length_plan(90) == (225, 10),
      "90 seconds plans 225 words in 10 lines")
check(shorts.length_plan(120) == (300, 14),
      "120 seconds plans 300 words in 14 lines")
check(abs(shorts.estimate_spoken_seconds(
    " ".join(["word"] * 150)) - 60.0) < 1e-9,
    "150 words estimate to one minute at the planning pace")
check(abs(shorts.estimate_spoken_seconds(
    " ".join(["word"] * 270)) - 108.0) < 1e-9,
    "270 words estimate to 108 seconds at the planning pace")
check(shorts.estimate_spoken_seconds("") == 0,
      "an empty script estimates to zero")
plan_prompt = shorts.script_prompt("the deep ocean", 120)
check("300 words" in plan_prompt and "14 lines" in plan_prompt
      and "the deep ocean" in plan_prompt,
      "the topic prompt carries the chosen length plan")
plan_prompt = shorts.script_prompt("the deep ocean", 30)
check("75 words" in plan_prompt and "4 lines" in plan_prompt,
      "the shortest plan reaches the prompt too")

# --- script splitting ----------------------------------------------------
lines = shorts.split_script(
    "The ocean covers most of our planet. Sunlight only reaches "
    "the top layer. Down deep it is cold and dark.")
check(lines == [
    "The ocean covers most of our planet.",
    "Sunlight only reaches the top layer.",
    "Down deep it is cold and dark."], "prose splits into sentences")
lined = shorts.split_script("First line here.\nSecond line follows.")
check(lined == ["First line here.", "Second line follows."],
      "author line breaks are honored")
long_line = ("word " * 90).strip() + "."
wrapped = shorts.split_script(long_line + " Short closer.")
check(all(len(ln) <= shorts.SHORTS_MAX_LINE_CHARS for ln in wrapped),
      "overlong lines wrap to the per line cap")
check(len(wrapped) >= 3, "wrapping makes more scenes")


def raises_value(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


check(raises_value(lambda: shorts.split_script("")),
      "empty script rejected")
check(raises_value(lambda: shorts.split_script("Just one line.")),
      "one line is not a short")
check(raises_value(lambda: shorts.split_script("x " * 1200)),
      "overlong script rejected")
check(raises_value(lambda: shorts.split_script(
    "\n".join(f"Line number {i}." for i in range(22)))),
    "too many lines rejected")
check(raises_value(lambda: shorts.length_plan(45)),
      "a length the lane does not offer is rejected")
twenty = shorts.split_script(
    "\n".join(f"Line number {i} speaks." for i in range(20)))
check(len(twenty) == 20, "twenty lines are accepted")

# --- split choices -----------------------------------------------------------
check(shorts.SPLIT_MODES == ("auto", "lines", "sentences", "paragraphs"),
      "the split choices are auto, lines, sentences, paragraphs")
mixed = "First part. Second part.\nThird line stands."
check(shorts.split_script(mixed, mode="lines") == [
    "First part. Second part.", "Third line stands."],
    "lines mode keeps each pasted line whole")
check(shorts.split_script(mixed, mode="sentences") == [
    "First part.", "Second part.", "Third line stands."],
    "sentences mode ignores line breaks and splits on sentences")
soft = "A sentence that\nwraps over two lines. Another one follows."
check(shorts.split_script(soft, mode="sentences") == [
    "A sentence that wraps over two lines.", "Another one follows."],
    "sentences mode joins soft wrapped lines before splitting")
paras = ("Para one speaks first. Still para one talking.\n\n"
         "Para two arrives. And para two continues.")
check(shorts.split_script(paras, mode="paragraphs") == [
    "Para one speaks first. Still para one talking.",
    "Para two arrives. And para two continues."],
    "paragraphs mode makes one scene of each paragraph")
long_para = " ".join(
    f"Sentence number {i} of a long paragraph." for i in range(12))
split_para = shorts.split_script(
    long_para + "\n\nShort closer.", mode="paragraphs")
check(len(split_para) > 2
      and all(len(ln) <= shorts.SHORTS_MAX_LINE_CHARS
              for ln in split_para),
      "an overlong paragraph splits into sentences")
check(shorts.split_script(mixed) == shorts.split_script(
    mixed, mode="auto"), "auto is the default split")
check(raises_value(lambda: shorts.split_script(mixed, mode="words")),
      "an unknown split choice is rejected")

# --- search terms ---------------------------------------------------------
terms = shorts.search_terms_for_line(
    "Sunlight only reaches the top layer of the water.", "the ocean")
check("sunlight" in terms and "water" in terms,
      "terms come from the line's content words")
check("the" not in terms.split() and "only" not in terms.split(),
      "stopwords stay out of the terms")
fallback = shorts.search_terms_for_line("It is on.", "deep ocean")
check("ocean" in fallback, "thin lines fall back to the topic")
check(shorts.search_terms_for_line("It is on.", "") == "nature",
      "the last resort term is nature")
check(shorts._query_ladder("deep water cold") == [
    "deep water cold", "deep water", "deep"],
    "the commons query ladder broadens step by step")
check(shorts._query_ladder("ocean") == ["ocean"],
      "a single word has no ladder")

# --- captions --------------------------------------------------------------
timings = [(0.0, 3.0), (3.35, 3.2), (6.9, 2.8)]
events = shorts.caption_events(lines, timings)
check(all(events[i][0] <= events[i + 1][0]
          for i in range(len(events) - 1)),
      "caption events are in order")
check(all(len(text.split()) <= shorts.CAPTION_MAX_WORDS
          for _s, _e, text in events),
      "caption chunks hold at most four words")
check(all(end <= 9.7 + 1e-6 for _s, end, _t in events),
      "captions stay inside the narration")
check(events[0][0] == 0.0, "captions start with the narration")
ass = shorts.build_captions_ass(lines, timings)
check("PlayResX: 1080" in ass and "PlayResY: 1920" in ass,
      "caption canvas is the short's shape")
check("Dialogue:" in ass and "The ocean covers most" in ass,
      "caption words are in the file")
evil = shorts.build_captions_ass(["A {brace} test.", "Second {x} line."],
                                 [(0.0, 1.0), (1.35, 1.0)])
check("{brace}" not in evil, "caption braces are escaped")
check(set(shorts.CAPTION_STYLES) == {"classic", "big", "quiet", "none",
                                     "karaoke", "pop"},
      "the caption looks are classic, big, quiet, karaoke, pop, and no captions")
kara_ass = shorts.build_captions_ass(lines, timings, style="karaoke")
check("{\\kf" in kara_ass and "&H0000FFFF" in kara_ass,
      "the karaoke look fills each word as it is spoken")
pop_ass = shorts.build_captions_ass(lines, timings, style="pop")
check("{\\fscx55\\fscy55\\t(0,170,\\fscx100\\fscy100)}" in pop_ass,
      "the pop look scales each caption chunk in")
check(all("LowerName" not in doc and "LowerSub" not in doc
          for doc in (ass, kara_ass, pop_ass)),
      "no caption look carries opening title styles, the "
      "overlay left the shorts builder entirely "
      "(Jack, 2026-11-24)")
try:
    shorts.build_captions_ass(lines, timings,
                              lower_third={"name": "Jane Doe"})
    lt_accepted = True
except TypeError:
    lt_accepted = False
check(not lt_accepted,
      "the captions builder takes no opening title words "
      "anymore, so nothing can draw the overlay")
check("lower_third" not in SHORTS_SRC,
      "the shorts module holds no opening title code at all")
check(shorts.snap_durations_to_beat([3.2, 4.0, 0.5], 120) == [3.5, 4.0, 0.5],
      "scene lengths snap up to whole beats")
check(shorts.snap_durations_to_beat([3.2], 0) == [3.2],
      "a zero tempo leaves scene lengths alone")
check(shorts.CAPTION_STYLES["none"]["label"] == "No captions",
      "the no captions look carries its plain name")
none_ass = shorts.build_captions_ass(lines, timings, style="none")
check("Dialogue:" not in none_ass,
      "the no captions look holds no caption events, so "
      "nothing burns in")
check("DejaVu Sans,72," in ass and ",300,1" in ass,
      "the classic look keeps the shipped caption style")
big_ass = shorts.build_captions_ass(lines, timings, style="big")
check("DejaVu Sans,96," in big_ass and ",430,1" in big_ass,
      "the big look runs larger and sits higher")
quiet_ass = shorts.build_captions_ass(lines, timings, style="quiet")
check("DejaVu Sans,58," in quiet_ass,
      "the quiet look runs smaller")
check(shorts.build_captions_ass(lines, timings, style="bogus") == ass,
      "an unknown caption look falls back to classic")
check(shorts.build_captions_ass(lines, timings) == ass,
      "classic is the default caption look")

# --- topic writing is dark-safe -------------------------------------------
saved_env = {}
for key in ("GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    saved_env[key] = os.environ.pop(key, None)
try:
    check(shorts.script_routes_configured() is False,
          "no keys means no topic route")
    try:
        shorts.script_from_topic("the ocean")
        check(False, "topic writing raises with no route configured")
    except RuntimeError:
        check(True, "topic writing raises with no route configured")
finally:
    for key, value in saved_env.items():
        if value is not None:
            os.environ[key] = value

# --- AI pictures: styles, prompts, planning fallback -------------------------
check(set(shorts.VISUAL_STYLES) == {
    "photo", "illustration", "anime", "painting", "sketch"},
    "the AI picture styles are a short fixed list")
for style_id, entry in shorts.VISUAL_STYLES.items():
    check(entry["label"] and entry["prefix"],
          f"picture style {style_id} carries a label and a prefix")
    check(all(mark not in entry["label"] for mark in (":", ";")),
          f"picture style label follows the plain copy rule: "
          f"{entry['label']}")
check(shorts.VISUAL_STYLE_DEFAULT in shorts.VISUAL_STYLES,
      "the default picture style is on the list")
photo_prompt = shorts.image_prompt_for_line(
    "Sunlight reaches the top layer.", "photo")
check(photo_prompt.startswith(
    shorts.VISUAL_STYLES["photo"]["prefix"]),
    "a line prompt opens with the style prefix")
check("Sunlight reaches the top layer" in photo_prompt,
      "a line prompt carries the line as its subject")
check(shorts.image_prompt_for_line("Anything.", "bogus")
      == shorts.image_prompt_for_line(
          "Anything.", shorts.VISUAL_STYLE_DEFAULT),
      "an unknown style falls back to the default style")
# Planning is best effort: with the text routes forced down, the
# prompts are the deterministic line prompts and nothing raises.
real_gemini_text = shorts._gemini_text
real_openrouter_text = shorts._openrouter_text
shorts._gemini_text = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("down"))
shorts._openrouter_text = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("down"))
_saved_gemini_key = os.environ.pop("GEMINI_API_KEY", None)
try:
    planned = shorts.image_prompts_for_lines(
        ["First line here.", "Second line follows."], "anime")
finally:
    shorts._gemini_text = real_gemini_text
    shorts._openrouter_text = real_openrouter_text
    if _saved_gemini_key is not None:
        os.environ["GEMINI_API_KEY"] = _saved_gemini_key
check(planned == [
    shorts.image_prompt_for_line("First line here.", "anime"),
    shorts.image_prompt_for_line("Second line follows.", "anime")],
    "with no planning route the prompts are the lines themselves")

# --- stock response parsing (canned payloads, no network) ------------------
canned_pexels = {
    "videos": [{
        "id": 1, "width": 1080, "height": 1920, "duration": 8,
        "url": "https://www.pexels.com/video/1",
        "user": {"name": "Pat Maker"},
        "video_files": [
            {"file_type": "video/mp4", "width": 640, "height": 360,
             "link": "https://files.example/small.mp4"},
            {"file_type": "video/mp4", "width": 1080, "height": 1920,
             "link": "https://files.example/good.mp4"},
        ],
    }, {
        "id": 2, "url": "https://www.pexels.com/video/2",
        "user": {"name": "No Files"}, "video_files": [],
    }],
}
real_get_json = shorts._get_json
shorts._get_json = lambda url, headers=None: canned_pexels
try:
    found = shorts.pexels_search_videos("ocean", "test-key")
finally:
    shorts._get_json = real_get_json
check(len(found) == 1, "pexels results without files are skipped")
check(found and found[0]["download_url"].endswith("good.mp4"),
      "the file nearest the frame width is chosen")
check(found and found[0]["credit"] == "Pat Maker",
      "the photographer credit is carried")

canned_commons = {
    "query": {"pages": {"7": {
        "title": "File:Ocean waves.webm",
        "canonicalurl": "https://commons.wikimedia.org/wiki/File:Ocean",
        "videoinfo": [{
            "derivatives": [
                {"src": "https://upload.example/small.webm",
                 "width": 320, "type": "video/webm"},
                {"src": "https://upload.example/big.mp4",
                 "width": 1024, "type": "video/mp4"},
            ],
        }],
        "imageinfo": [{"extmetadata": {
            "Artist": {"value": "Jo Maker"},
            "LicenseShortName": {"value": "CC BY 4.0"},
        }}],
    }}},
}
shorts._get_json = lambda url, headers=None: canned_commons
try:
    found_c = shorts.commons_search_videos("ocean waves")
finally:
    shorts._get_json = real_get_json
check(len(found_c) == 1, "commons results parse")
check(found_c and found_c[0]["download_url"].endswith("big.mp4"),
      "the mp4 derivative nearest the frame is chosen")
check(found_c and found_c[0]["license"] == "CC BY 4.0",
      "the commons license name is carried")
check(found_c and found_c[0]["credit"] == "Jo Maker",
      "the commons artist is carried")

# --- one tiny real assembly --------------------------------------------------
if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        clips = []
        for i, color in enumerate(("navy", "teal")):
            clip = work / f"src_{i}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i",
                 f"color=c={color}:s=640x360:d=3:r=30",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                capture_output=True, check=True)
            clips.append(clip)
        wavs = []
        durations = []
        for i, freq in enumerate((440, 520)):
            wav = work / f"line_{i}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i",
                 f"sine=frequency={freq}:duration=0.9",
                 "-ar", "24000", "-ac", "1", str(wav)],
                capture_output=True, check=True)
            wavs.append(wav)
            durations.append(0.9)
        narration = shorts.build_narration_track(wavs, durations, work)
        scene_durations = [
            durations[0] + shorts.LINE_GAP_SECONDS,
            durations[1] + shorts.TAIL_SECONDS,
        ]
        ass_path = work / "captions.ass"
        ass_path.write_text(shorts.build_captions_ass(
            ["First tiny scene speaks.", "Second tiny scene speaks."],
            [(0.0, 0.9), (scene_durations[0], 0.9)]), encoding="utf-8")
        bed = work / "bed.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "sine=frequency=220:duration=3", "-ar", "24000",
             "-ac", "1", str(bed)], capture_output=True, check=True)
        out = work / "out.mp4"
        info = shorts.assemble_short(
            clips, scene_durations, narration, ass_path, out, work,
            bed_path=bed, watermark=True)
        check(info["width"] == 1080 and info["height"] == 1920,
              "assembled short is 1080 x 1920")
        check(info["has_audio"], "assembled short carries audio")
        check(abs(info["duration"] - sum(scene_durations)) < 0.35,
              "assembled length matches the scenes")
        # No captions: the assembly takes no captions file at
        # all and still lands a whole short.
        out_none = work / "out_none.mp4"
        info_none = shorts.assemble_short(
            clips, scene_durations, narration, None, out_none,
            work, bed_path=bed, watermark=True)
        check(info_none["width"] == 1080
              and info_none["height"] == 1920
              and info_none["has_audio"],
              "a short assembled with no captions file lands whole")
        # One AI still, animated: the scene clip must land at
        # the frame shape and the scene's length, ready to ride
        # the same assembly the stock clips ride.
        still = work / "still.png"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             "color=c=purple:s=720x1280:d=0.1:r=1",
             "-frames:v", "1", str(still)],
            capture_output=True, check=True)
        for idx in (0, 1, 2):
            scene_mp4 = work / f"ai_scene_{idx}.mp4"
            shorts.animate_image_scene(still, 0.9, scene_mp4, idx)
            sinfo = shorts.probe(scene_mp4)
            check(sinfo["width"] == 1080 and sinfo["height"] == 1920,
                  f"animated scene {idx} is 1080 x 1920")
            check(abs(sinfo["duration"] - 0.9) < 0.2,
                  f"animated scene {idx} runs its scene length")

# --- lane wiring in the source ----------------------------------------------
lane = SRC.split("# -- faceless shorts lane", 1)[1].split(
    '@web.get("/api/jobs/{job_id}/thumb")', 1)[0]
check('"charged_cents": 0' in lane, "short jobs charge zero")
check("_place_hold" not in lane, "the shorts lane places no holds")
check("FREE_CLIPS_TOTAL" in lane,
      "unpaid accounts count against the free lifetime cap")
check("FREE_PLATFORM_TOTAL_CEILING" in lane,
      "platform free ceiling applies")
check("freecount:total" in lane,
      "free jobs increment the platform counter")
check("SHORTS_DAILY_CAP_PAID" in lane, "paid accounts hit the daily cap")
check("process_short_job.spawn" in lane, "creation spawns the runner")
check("picking a voice is a paid feature" in lane,
      "voice picking keeps the paid gate")
check("SPEECH_VOICES" in lane, "the picker set is the platform set")
check("script_from_topic" in lane,
      "topic mode rides the wired text routes")
check("split_script" in lane, "scripts are validated at creation")
check("topic, length_seconds" in lane,
      "topic writing gets the picked length")
check("length_seconds" in lane and "length_plan" in lane,
      "creation validates the picked length")
check("short_target_seconds" in lane,
      "job records carry the chosen length target")
check("estimate_spoken_seconds" in lane,
      "script mode records the spoken estimate of the script")
check('"length_targets"' in lane,
      "the config offers the length targets")
check('"visuals"' in lane and '"short_visuals"' in lane,
      "creation takes and records the pictures choice")
check("VISUAL_STYLES" in lane,
      "creation validates the picture style against the list")
check('"visual_styles"' in lane,
      "the config offers the picture styles")
check("split_mode" in lane and '"short_split"' in lane,
      "creation takes and records the split choice")
check('"split_modes"' in lane,
      "the config offers the split choices")
check('"short_music"' in lane and "SHORTS_MUSIC_STYLES" in lane,
      "creation takes, validates, and records the music choice")
check('"music_styles"' in lane,
      "the config offers the music choices")
check("custom music is a paid feature" in lane,
      "custom music keeps the paid gate at creation, so a "
      "free creation with a custom description is refused")
check(lane.index("custom music is a paid feature")
      < lane.index("process_short_job.spawn"),
      "the custom music gate lands before any dispatch")
check("SHORTS_CUSTOM_MUSIC" in lane
      and "CUSTOM_AUDIO_MAX_CHARS" in lane,
      "creation reads the custom pick and caps its "
      "description at the custom audio cap")
check('"music_prompt": music_prompt or None' in lane,
      "a paid custom pick stores its description on the "
      "job record as its music prompt")
check('"music_prompt": parent.get("music_prompt")' in SRC,
      "a retried short keeps its custom music description")
check('"short_caption_style"' in lane and "CAPTION_STYLES" in lane,
      "creation takes, validates, and records the caption style")
check('"short_beat_sync": beat_sync,' in lane,
      "creation records the beat sync pick")
check('"short_lower_third"' not in lane,
      "creation records no opening title on the job, the "
      "feature left shorts (Jack, 2026-11-24)")
check('"overlay"' in lane and '("stock", "ai", "overlay")' in lane,
      "creation accepts the B-roll overlays pictures choice")
check('id="short-beat-sync"' in SRC,
      "the shorts form carries the beat sync box")
check('id="short-lt-name"' not in SRC
      and 'id="short-lt-sub"' not in SRC,
      "the shorts form carries no opening title inputs "
      "(Jack, 2026-11-24)")
check('<option value="overlay">B-roll overlays</option>' in SRC,
      "the pictures picker offers B-roll overlays")
check("beat_sync: $('short-beat-sync').checked" in SRC,
      "the submit sends the beat sync pick")
check("lower_third_name: $('short-lt-name')" not in SRC
      and "lower_third_sub: $('short-lt-sub')" not in SRC,
      "the submit sends no opening title words")
check(all("bpm" in entry for entry in app.SHORTS_MUSIC_STYLES.values()),
      "every music choice carries its tempo for the beat sync")
check('"caption_styles"' in lane,
      "the config offers the caption styles")
check("short_parent" in SRC,
      "AI picture jobs carry the short parent mark")
check('not j.get("short_parent")' in SRC,
      "the library skips a short's picture jobs")
check("gallery_visible" in SRC and 'job.get("short_parent")' in SRC,
      "the gallery skips a short's picture jobs")

processor = SRC.split("def process_short_job", 1)[1].split(
    "# ---------------------------------------------------------------------------\n# Config", 1)[0]
check('"voiceover", {"text": line, "voice": voice}' in processor,
      "narration calls the platform Kokoro stage through the "
      "Kaggle audio seam (2026-11-24 audio move; the stage "
      "itself is pinned by check_audio_kaggle)")
check("find_stock_clip" in processor, "the runner finds stock per line")
check("_short_ai_scenes" in processor,
      "the runner can make AI pictures per line")
check("animate_image_scene" in processor,
      "the runner animates the AI pictures into scenes")
check("visuals_fallback" in processor,
      "a failed picture run falls back to stock on the record")
check("_wake_comfy_kernel" in SRC,
      "the kernel wake lives at module level for the processors")
check("_short_bed_for_job" in processor,
      "the runner resolves the picked music bed")
check("_audio_bytes" in SRC and "shorts_{style}" in SRC,
      "named beds ride the platform bed stage on the Kaggle "
      "audio worker, cached by style (2026-11-24 audio move)")
bed_fn = SRC.split("def _short_custom_bed_for_job", 1)[1].split(
    "def process_short_job", 1)[0]
check("SHORTS_CUSTOM_MUSIC" in bed_fn
      and "return _short_custom_bed_for_job(job)" in bed_fn,
      "the custom pick routes the short's bed to its own "
      "per job make")
check("{job['id']}.wav" in bed_fn
      and "instrumental, no vocals" in bed_fn
      and 'job["music_fallback"] = True' in bed_fn,
      "the custom bed is made from the description with the "
      "instrumental close, cached per job, with the house "
      "bed fallback flagged on failure")
check("build_captions_ass" in processor, "the runner times captions")
check('!= "none"' in processor and "ass_path = None" in processor,
      "the runner writes no captions file for the no captions "
      "pick, so the assembly burns no captions in")
check("short_lower_third" not in processor,
      "the runner never reads a stored opening title, so "
      "one left on an old job record stays inert")
check("_snap_scenes_to_bed_beats" in processor
      and "short_beat_sync" in processor,
      "the runner snaps scene lengths to the beat when the pick is on")
check("scene_durations=scene_durations" in processor,
      "the narration track is built against the scene lengths")
check("overlays=overlay_specs" in processor
      and '"overlay-ai"' in processor and '"short_broll"' in processor,
      "the runner fetches B-roll per line and lays it over the scenes")
check("assemble_short" in processor, "the runner assembles the short")
check("_short_word_check" in processor,
      "the runner word checks the finished short")
check('"the short could not be made, nothing was charged"' in processor,
      "failures charge nothing")

mirror = SRC.split("def mirror_status", 1)[1].split(
    "def _poll_limit_for", 1)[0]
check('provider") == "shorts"' in mirror, "mirror has the shorts branch")
check("SHORT_POLL_LIMIT_SECONDS" in mirror,
      "mirror enforces the short bound")
processed = SRC.split("def ensure_processed", 1)[1].split(
    "def ensure_poster", 1)[0]
check('provider") == "shorts"' in processed,
      "finished shorts serve as made")
check('"Faceless short"' in SRC, "the library label exists")
check(SRC.count('"/platform/shorts.py"') == 2,
      "shorts.py ships in both images that run it")
check("PEXELS_API_KEY" in SHORTS_SRC, "the stock key is named in the module")
check('"Authorization"' in SHORTS_SRC, "the pexels key rides its header")
check("api.pexels.com" in SHORTS_SRC, "the pexels api base is wired")
check("commons.wikimedia.org" in SHORTS_SRC,
      "the commons fallback is wired")

# --- studio surface ------------------------------------------------------------
check('id="tab-shorts"' not in SRC, "the studio has no Shorts tab")
check('id="shorts-pane"' not in SRC, "the studio has no Shorts pane")
check("showTab('shorts')" not in SRC, "nothing routes to a shorts tab")
check('id="make-short"' not in SRC, "the pane's own button is gone")
check("const SHORTS_ID = 'faceless_shorts'" in SRC,
      "the picker entry has a stable id")
catalog = SRC.split("function buildCatalog()", 1)[1].split(
    "function applyModelGates()", 1)[0]
check("sh.value = SHORTS_ID" in catalog,
      "buildCatalog appends the faceless shorts entry")
check("sh.textContent = 'Faceless shorts'" in catalog,
      "the entry is labeled with its plain name, the group marks it free")
check("freeGroup.appendChild(sh)" in catalog,
      "the entry lands in the picker Free group")
check("const keep = sel.value || SHORTS_ID;" in catalog,
      "a first catalog build lands on the faceless shorts entry")
check('<option value="faceless_shorts">Faceless shorts</option>'
      in SRC,
      "the served picker opens on the faceless shorts entry")
check(catalog.index("freeGroup.appendChild(sh)")
      < catalog.index("freeGroup.appendChild(o)"),
      "faceless shorts sit in the Free group ahead of the motion templates")
check("o.value !== SHORTS_ID" in SRC,
      "the paid gate never disables the free entry")
check("sel.value !== SHORTS_ID" in SRC,
      "the paid gate never resets a free account off the entry")
check('id="shorts-fields"' in SRC, "the create card carries the shorts form")
check("if (m === SHORTS_ID) { showShortsForm(); return; }" in SRC,
      "picking the entry swaps the form in updateModelUI")
check("if (model === SHORTS_ID) return buildShort();"
      in SRC, "the Create button routes shorts to the shorts build")
check("function buildShort()" in SRC,
      "the shorts build survives as a named function")
check('id="short-script"' in SRC and 'id="short-topic"' not in SRC,
      "the form takes a script only, the topic box is gone "
      "(script only form, 2026-11-20)")
check('id="short-mode-topic"' not in SRC
      and 'id="short-mode-script"' not in SRC,
      "the topic and script toggle buttons are gone")
check('id="short-topic-wrap"' not in SRC
      and 'id="short-script-wrap"' in SRC,
      "the script wrap is the one wrap the form carries")
check('id="short-draft-model"' in SRC
      and 'id="short-help-btn"' in SRC
      and ">Improve my script</button>" in SRC,
      "the form carries the draft picker and its Improve my "
      "script button under the script box")
check("<label>Improve my script</label>" in SRC,
      "the shorts picker is labeled Improve my script")
check('id="short-voice"' in SRC, "the form carries the voice picker")
check("payload.visuals" in SRC and "payload.style" in SRC,
      "the submit sends the pictures choice and the style")
check('id="botcheck5"' in SRC and "'botcheck5'" in SRC,
      "the form has its bot check widget")
shorts_swap = SRC.split("function showShortsForm()", 1)[1].split(
    "function updateModelUI()", 1)[0]
check("$('botcheck2')" in shorts_swap,
      "the shorts form hides the video form's bot check widget")
check("loadShortsConfig" in SRC, "the form loads its config")
check("/api/shorts" in SRC, "the form posts to the shorts route")
pane = SRC.split('id="shorts-fields"', 1)[1].split('id="short-msg"', 1)[0]
check('id="short-visuals"' in pane and 'id="short-style"' in pane,
      "the form carries the pictures choice and the style picker")
style_select = pane.split('id="short-style"', 1)[1].split(
    '</select>', 1)[0]
style_values = {
    part.split('"')[0]
    for part in style_select.split('<option value="')[1:]
}
check(style_values == set(shorts.VISUAL_STYLES),
      "the style picker offers exactly the picture style list")
check('id="short-split"' in pane, "the form carries the split picker")
split_select = pane.split('id="short-split"', 1)[1].split(
    '</select>', 1)[0]
split_values = {
    part.split('"')[0]
    for part in split_select.split('<option value="')[1:]
}
check(split_values == set(shorts.SPLIT_MODES),
      "the split picker offers exactly the split choices")
check("payload.split" in SRC,
      "the submit sends the split choice")
# The script box placeholder follows the split pick (Jack,
# 2026-11-23: with the pick on sentences the box still talked
# about lines). One wording per split choice, each following
# the plain copy rule, and the box opens on the auto wording
# because auto is the default pick.
split_placeholders = {
    "auto": "Write your script here. It is split into scenes "
            "for you. The voice reads it exactly as written.",
    "lines": "One line for each scene. The voice reads it "
             "exactly as written.",
    "sentences": "Write your script here. Each sentence "
                 "becomes its own scene. The voice reads it "
                 "exactly as written.",
    "paragraphs": "One paragraph for each scene. The voice "
                  "reads it exactly as written.",
}
for mode, wording in split_placeholders.items():
    check(f"{mode}: '{wording}'" in SRC,
          f"the placeholder map carries the {mode} wording")
    check(all(mark not in wording for mark in (":", ";", "-")),
          f"the {mode} placeholder follows the plain copy rule")
check('placeholder="' + split_placeholders["auto"] + '"' in pane,
      "the script box opens on the auto placeholder, "
      "the default pick's wording")
check("function syncShortSplitHint()" in SRC,
      "the split hint sync lives in the studio")
check("$('short-split').addEventListener('change', "
      "syncShortSplitHint)" in SRC,
      "changing the split pick refreshes the script box placeholder")
check("syncShortSplitHint();" in shorts_swap,
      "showing the shorts form syncs the placeholder to the pick")
check('id="short-music"' in pane, "the form carries the music picker")
music_select = pane.split('id="short-music"', 1)[1].split(
    '</select>', 1)[0]
music_values = {
    part.split('"')[0]
    for part in music_select.split('<option value="')[1:]
}
check(music_values == set(app.SHORTS_MUSIC_STYLES),
      "the music picker offers exactly the music choices")
music_order = [
    part.split('"')[0]
    for part in music_select.split('<option value="')[1:]
]
check(music_order == [
    "none", "custom", "house", "calm", "upbeat", "cinematic",
    "lofi", "acoustic", "jazz", "electronic", "corporate",
    "epic", "synthwave", "tropical",
], "the picker leads with No music and the custom pick, then "
   "the platform bed and the styles (Jack, 2026-11-24)")
check('<option value="none" selected>No music</option>' in pane,
      "no music is the preselected pick (Jack, 2026-11-24)")
check('<option value="house">Dreamy ambient</option>' in pane,
      "the platform bed pick carries its dreamy ambient name "
      "and no longer carries the default")
check('(body.get("music") or "none")' in SRC,
      "a short created with no music pick defaults to no music "
      "on the server")
check('"music": parent.get("short_music") or "none"' in SRC,
      "a retried short with no stored style defaults to no music")
check('job.get("short_music") or "none"' in SRC,
      "the bed lookup falls back to no music, never the house bed")
check('<option value="custom">Custom, your own description'
      '</option>' in pane,
      "the music picker offers the custom pick")
check('id="short-music-custom"' in pane
      and 'id="short-music-custom-wrap"' in pane,
      "the form carries the custom music words box")
check("payload.music_prompt = $('short-music-custom')"
      ".value.trim()" in SRC,
      "the submit sends the custom music description")
check("function shortShowMusicCustom()" in SRC
      and "$('short-music').addEventListener('change', "
      "shortShowMusicCustom)" in SRC,
      "the custom words box opens on the custom pick")
check("$('short-music')" in SRC,
      "the submit sends the music choice")
check("$('short-music').value = 'none';" in SRC
      and SRC.index("$('short-music').value = 'none';")
      > SRC.index("function shortShowMusicCustom()"),
      "the picker is reset to no music in script at form "
      "init, not only preselected in the markup, so a phone "
      "browser restoring the last pick cannot land on a "
      "style or the custom pick (Jack, 2026-11-24)")
check("shortShowMusicCustom();" in SRC,
      "the init reset syncs the custom words box to hidden")
check('id="short-music" autocomplete="off"' in pane,
      "the music picker also opts out of browser form "
      "restoration in its markup")
check('<label>Opening title (optional)</label>' not in pane
      and '<label>Smaller line under it (optional)</label>'
      not in pane,
      "the opening title fields are gone from the shorts "
      "form (Jack, 2026-11-24)")
check('placeholder="Your name or a headline for the open"'
      not in pane
      and 'placeholder="One short line under the title"'
      not in pane,
      "the opening title placeholders went with the fields")
check("like the name banner on a news clip" not in pane,
      "the opening title helper text went with the fields")
check("Lower third" not in pane,
      "no TV jargon is left in the shorts form")
check('id="short-captions"' in pane,
      "the form carries the caption style picker")
caption_select = pane.split('id="short-captions"', 1)[1].split(
    '</select>', 1)[0]
caption_values = {
    part.split('"')[0]
    for part in caption_select.split('<option value="')[1:]
}
check(caption_values == set(shorts.CAPTION_STYLES),
      "the caption picker offers exactly the caption styles")
check("$('short-captions')" in SRC,
      "the submit sends the caption style")
voice_select = pane.split('id="short-voice"', 1)[1].split(
    '</select>', 1)[0]
pane_voices = {
    part.split('"')[0]
    for part in voice_select.split('<option value="')[1:]
}
check(pane_voices == set(app.SPEECH_VOICES) | {"clone"},
      "the form picker offers exactly the platform voice set "
      "plus the voice cloning pick")
check('id="short-length"' not in pane,
      "the topic length picker left the form with topic mode")
check('id="short-script-estimate"' in pane,
      "script mode carries the spoken length estimate line")
length_note = "Your video length is estimated from your script length."
check(length_note in pane,
      "script mode says the length is estimated from the script length")
check(all(mark not in length_note for mark in (":", ";", "-", "–", "—")),
      "the length note follows the plain copy rule")
check('maxlength="2200"' in pane,
      "the script box fits a full two minutes")
check("payload.length_seconds" not in SRC,
      "the submit sends no length, the script is the length")
check("function shortScriptEstimate()" in SRC,
      "the estimate math lives in the studio")
check("addEventListener('input', shortScriptEstimate)" in SRC,
      "typing in the script box refreshes the estimate")
# The estimate, run for real: the function is lifted out of the
# page the app actually serves (APP_HTML, after the Python string
# layer has had its say) and executed in node against hand worked
# inputs at the planning pace of 150 words a minute (2.5 words a
# second, rounded). This pins the formula itself, not the source
# text: the 2026-11-19 bug, where the served pattern degraded to
# runs of the letter S and a 270 word script read "About 1
# seconds", passed every source text pin above and only shows up
# when the served code runs.
_fn_start = app.APP_HTML.find("function shortScriptEstimate")
_fn_end = app.APP_HTML.find("\n}", _fn_start)
_estimate_fn = app.APP_HTML[_fn_start:_fn_end + 3]
_estimate_harness = (
    "const store = { 'short-script': { value: '' },"
    " 'short-script-estimate': { textContent: '' } };\n"
    "const $ = (id) => store[id];\n"
    + _estimate_fn + "\n"
    "function run(text) {\n"
    "  store['short-script'].value = text;\n"
    "  store['short-script-estimate'].textContent = '<unset>';\n"
    "  shortScriptEstimate();\n"
    "  return store['short-script-estimate'].textContent;\n"
    "}\n"
    "const w = (n) => Array(n).fill('word').join(' ');\n"
    "const cases = ['', w(75), w(150), w(270), w(3), w(153)];\n"
    "console.log(JSON.stringify(cases.map(run)));\n"
)
with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False) as _fh:
    _fh.write(_estimate_harness)
    _harness_path = _fh.name
try:
    _estimate_out = subprocess.run(
        ["node", _harness_path], capture_output=True, text=True,
        timeout=60)
    _estimate_results = json.loads(_estimate_out.stdout)
except Exception as exc:  # noqa: BLE001 - a failed run is a failure
    _estimate_results = None
    check(False, f"the served estimate runs in node: {exc}")
if _estimate_results is not None:
    check(_estimate_results == [
        "",
        "About 30 seconds of speaking.",
        "About 1 minute of speaking.",
        "About 1 minute 48 seconds of speaking.",
        "About 1 second of speaking.",
        "About 1 minute 1 second of speaking.",
    ], "the served estimate reads empty, 75, 150, 270, 3 and 153 "
       "words at the planning pace, singular seconds included")
# The split hint sync, run for real the same way: the map and
# the function are lifted out of the served page and executed
# in node against every split choice, plus an unknown one,
# which must fall back to the auto wording.
_map_start = app.APP_HTML.find("const SHORT_SPLIT_PLACEHOLDERS")
_sync_start = app.APP_HTML.find("function syncShortSplitHint")
_sync_end = app.APP_HTML.find("\n}", _sync_start)
_sync_src = app.APP_HTML[_map_start:_sync_end + 3]
_sync_harness = (
    "const store = { 'short-split': { value: 'auto' },"
    " 'short-script': { placeholder: '' } };\n"
    "const $ = (id) => store[id];\n"
    + _sync_src + "\n"
    "const out = {};\n"
    "for (const mode of ['auto', 'lines', 'sentences', "
    "'paragraphs', 'bogus']) {\n"
    "  store['short-split'].value = mode;\n"
    "  syncShortSplitHint();\n"
    "  out[mode] = store['short-script'].placeholder;\n"
    "}\n"
    "console.log(JSON.stringify(out));\n"
)
with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False) as _fh:
    _fh.write(_sync_harness)
    _sync_path = _fh.name
try:
    _sync_out = subprocess.run(
        ["node", _sync_path], capture_output=True, text=True,
        timeout=60)
    _sync_results = json.loads(_sync_out.stdout)
except Exception as exc:  # noqa: BLE001 - a failed run is a failure
    _sync_results = None
    check(False, f"the served split hint sync runs in node: {exc}")
if _sync_results is not None:
    _expected_sync = dict(split_placeholders)
    _expected_sync["bogus"] = split_placeholders["auto"]
    check(_sync_results == _expected_sync,
          "the served sync sets the placeholder from the split "
          "pick for every choice, auto wording on an unknown one")
check(";" not in pane.split("<p class=\"muted\">")[1].split("</p>")[0],
      "form copy follows the plain copy rule")

if failures:
    print("SHORTS LANE CHECK FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL SHORTS LANE CHECKS PASSED")
