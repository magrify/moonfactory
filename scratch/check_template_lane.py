"""Local checks for the HyperFrames template lane (2026-11-17).
No Modal calls, no network, no Kaggle calls: imports the platform
module and exercises the pure builders and field cleaning the live
routes use, plus source level wiring checks for the gates the
brief demands (zero price, free cap counting, paid daily cap,
the poll bound).

Run:  modal-venv python scratch/check_template_lane.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()

# --- constants --------------------------------------------------------
check(app.TEMPLATE_POLL_LIMIT_SECONDS == 3600,
      "template poll bound is 3600s")
check(app.TEMPLATE_DAILY_CAP_PAID == 20,
      "paid daily cap is 20")
check(app.TEMPLATE_MAX_SOURCE_SECONDS == 60,
      "captions source cap is 60s")
check(set(app.HF_TEMPLATES) == {"title", "captions", "stat", "promo",
                                 "story", "sting", "explainer",
                                 "explainer_pop", "explainer_energy",
                                 "kinetic", "intro", "outro", "chart",
                                 "turntable3d", "globe3d", "citypin3d",
                                 "house3d", "bars3d", "presenter",
                                 "showcase3d", "logodraw", "diagram",
                                 "particles", "vectorscene"},
      "starter templates, the motion wing and the motion pack templates exist")
check(app.HF_TEMPLATES["story"]["name"] == "Animated story short",
      "story template carries its studio name")
check(app.HF_TEMPLATES["story"].get("motion") is True,
      "story template is flagged as a motion job")
check(app.HF_TEMPLATES["sting"]["name"] == "Logo sting",
      "sting template carries its studio name")
check(float(app.HF_TEMPLATES["sting"]["duration"]) == 4.0,
      "sting runs four seconds")

# --- field cleaning -----------------------------------------------------
clean = app.hf_clean_fields("title", {"headline": "Hello", "subline": ""})
check(clean["headline"] == "Hello", "headline passes through")
check(clean["color"] == "#6366f1", "color defaults to the accent")
check(clean["subline"] == "", "empty optional field allowed")


def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


check(raises(lambda: app.hf_clean_fields("title", {"headline": ""})),
      "empty headline rejected")
check(raises(lambda: app.hf_clean_fields("title",
                                         {"headline": "x" * 91})),
      "overlong headline rejected")
check(raises(lambda: app.hf_clean_fields(
    "title", {"headline": "Hi", "color": "red"})),
      "non hex color rejected")
check(raises(lambda: app.hf_clean_fields("nope", {})),
      "unknown template rejected")
check(raises(lambda: app.hf_clean_fields("promo", {"subline": "x"})),
      "promo without headline rejected")
check(raises(lambda: app.hf_clean_fields("stat", {"stat": "42"})),
      "stat without label rejected")

# --- the motion wing: story fields and plan ------------------------------
STORY_BODY = {
    "title": "The Miller of Willow Ford",
    "lines": ("The miller woke before the sun.\n"
              "A letter had come in the night.\n"
              "By noon the whole town knew."),
}
story_clean = app.hf_clean_fields("story", STORY_BODY)
check(story_clean["title"] == "The Miller of Willow Ford",
      "story title passes through")
check(story_clean["lines"].count("\n") == 2,
      "story lines are stored newline joined")
check(raises(lambda: app.hf_clean_fields(
    "story", {"title": "T", "lines": "one\ntwo"})),
      "fewer than three story lines rejected")
check(raises(lambda: app.hf_clean_fields(
    "story", {"title": "T", "lines": "\n".join(f"line {i}" for i in range(9))})),
      "more than eight story lines rejected")
check(raises(lambda: app.hf_clean_fields(
    "story", {"title": "T", "lines": "a\n" + "x" * 121 + "\nb"})),
      "a story line over 120 characters rejected")
check(raises(lambda: app.hf_clean_fields(
    "story", {"lines": "a\nb\nc"})),
      "story without a title rejected")
check(raises(lambda: app.hf_clean_fields("sting", {"subline": "x"})),
      "sting without headline rejected")
listed = app.hf_clean_fields(
    "story", {"title": "T", "lines": [" one ", "", "two", "three"]})
check(listed["lines"] == "one\ntwo\nthree",
      "story lines accept a list and drop blank rows")

plan = app.hf_story_plan("T", ["one", "two", "three"])
check(len(plan) == 5, "plan is a title scene, one scene per line, a closing scene")
check(plan[0]["kind"] == "title" and plan[0]["light"] == "day"
      and plan[0]["program"] == 0, "plan opens on the title scene")
check(plan[-1]["kind"] == "outro" and plan[-1]["light"] == "golden"
      and plan[-1]["program"] == 6, "plan closes in golden light")
check(plan[-2]["light"] == "golden", "the last story line plays in golden light")
check(all(plan[i]["b"] == plan[i + 1]["a"] for i in range(len(plan) - 1)),
      "plan scenes are contiguous")
check(all(abs(s["a"] * 30 - round(s["a"] * 30)) < 1e-6
          and abs(s["b"] * 30 - round(s["b"] * 30)) < 1e-6 for s in plan),
      "plan boundaries land on whole frames")
long_plan = app.hf_story_plan("T", ["word " * 30] * 8)
check(long_plan[-1]["b"] <= 68.0, "the longest story stays inside the render bound")
short_plan = app.hf_story_plan("T", ["a", "b", "c"])
check(short_plan[-1]["b"] >= 20.0, "the shortest story still plays as a short film")

# --- composition building ----------------------------------------------
title_html = app.build_hf_composition(
    "title", {"headline": "Big Day", "subline": "come see",
              "color": "#ff8800"})
check('data-width="1080"' in title_html
      and 'data-height="1920"' in title_html,
      "title composition is vertical 1080 x 1920")
check("Big Day" in title_html, "headline is in the composition")
check("#ff8800" in title_html, "accent color is in the composition")
check("__timelines" in title_html, "timeline is registered")

evil = app.build_hf_composition(
    "title", {"headline": "<script>alert(1)</script>", "color": "#6366f1"})
check("<script>alert" not in evil, "user html never lands raw")
check("&lt;script&gt;" in evil, "user text is escaped")

cap_html = app.build_hf_composition(
    "captions", {"caption": "watch this", "color": "#6366f1"},
    source_duration=30.0)
check("source.mp4" in cap_html, "captions composition uses the clip")
check('data-duration="30"' in cap_html, "captions length follows the clip")
cap_long = app.build_hf_composition(
    "captions", {"caption": "watch this", "color": "#6366f1"},
    source_duration=90.0)
check('data-duration="60"' in cap_long, "captions length clamps at 60s")

stat_html = app.build_hf_composition(
    "stat", {"stat": "12000", "label": "happy guests",
             "color": "#6366f1"})
check("12000" in stat_html and "happy guests" in stat_html,
      "stat card carries its words")

promo_html = app.build_hf_composition(
    "promo", {"headline": "Sale", "subline": "this week",
              "cta": "Book now", "color": "#6366f1"})
check("Book now" in promo_html, "promo card carries the button words")

# --- the motion wing: compositions ---------------------------------------
story_fields = app.hf_clean_fields("story", STORY_BODY)
story_html = app.build_hf_composition("story", story_fields)
story_plan = app.hf_story_plan(story_fields["title"],
                               story_fields["lines"].split("\n"))
check('id="gl"' in story_html, "story composition carries the scene canvas")
check('data-fps="24"' in story_html,
      "story composition renders at 24 frames a second, the rate the free worker can carry")
check("motion-scene.js" in story_html,
      "story composition loads the scene script")
check('src="mix.wav"' in story_html,
      "story composition carries the score the worker writes")
check("window.__motion" in story_html,
      "story composition carries its props for the scene script")
check(f'data-duration="{story_plan[-1]["b"]:g}"' in story_html,
      "story composition length follows the plan")
check("The Miller of Willow Ford" in story_html,
      "story title is in the composition")
check(all(ln in story_html for ln in story_fields["lines"].split("\n")),
      "every story line is in the composition")
check("__timelines" in story_html, "story timeline is registered")
evil_story = app.build_hf_composition("story", app.hf_clean_fields(
    "story", {"title": "<b>Hi</b>", "lines": "a <script>x</script>\nb\nc"}))
check("<script>x</script>" not in evil_story.replace(
    '<script src="gsap.min.js"></script>', "").replace(
    '<script src="motion-scene.js"></script>', ""),
      "story words never land as raw html")

sting_html = app.build_hf_composition(
    "sting", {"headline": "Willow Ford Bakery", "subline": "fresh daily",
              "color": "#e8833a"})
check("sting-ring" in sting_html, "sting composition draws its ring")
check("Willow" in sting_html and "Bakery" in sting_html,
      "sting composition carries its words")
check('data-duration="4"' in sting_html, "sting composition runs four seconds")
check("strokeDashoffset" in sting_html, "sting ring is animated on the timeline")

# --- the motion pack (2026-11-23): explainers, kinetic, intro/outro, data --
exp = app.hf_clean_fields("explainer", {
    "headline": "How it works",
    "points": "One booking page\nGuests book direct\nNo commission"})
check(exp["points"].count("\n") == 2,
      "explainer points are stored newline joined")
check(raises(lambda: app.hf_clean_fields(
    "explainer", {"points": "a\nb"})),
      "explainer without a headline rejected")
one_point = app.hf_clean_fields(
    "explainer", {"headline": "H", "points": "a"})
check(one_point["points"] == "a",
      "an explainer with a single point is allowed")
check(raises(lambda: app.hf_clean_fields(
    "explainer", {"headline": "H", "points": "  \n  "})),
      "explainer with no points rejected")
check(raises(lambda: app.hf_clean_fields(
    "explainer", {"headline": "H",
                  "points": "\n".join(f"p{i}" for i in range(5))})),
      "explainer with five points rejected")
check(raises(lambda: app.hf_clean_fields(
    "explainer_pop", {"headline": "H", "points": "ok\n" + "x" * 91})),
      "explainer point over 90 characters rejected")

kin = app.hf_clean_fields("kinetic", {"words": "Book more\nStay longer"})
check(kin["words"].count("\n") == 1, "kinetic words are stored newline joined")
check(app.hf_kinetic_duration(2) == 4.5,
      "kinetic length follows the line count")
check(raises(lambda: app.hf_clean_fields("kinetic", {"words": "one"})),
      "kinetic with one line rejected")
check(raises(lambda: app.hf_clean_fields(
    "kinetic", {"words": "\n".join(f"w{i}" for i in range(7))})),
      "kinetic with seven lines rejected")
check(raises(lambda: app.hf_clean_fields("kinetic", {"words": "a\n" + "x" * 43})),
      "kinetic line over 42 characters rejected")

check(raises(lambda: app.hf_clean_fields("intro", {"subline": "x"})),
      "intro without headline rejected")
check(raises(lambda: app.hf_clean_fields("outro", {"cta": "Book now"})),
      "outro without headline rejected")
outro_clean = app.hf_clean_fields(
    "outro", {"headline": "H", "cta": "Book now"})
check(outro_clean["cta"] == "Book now",
      "the outro call to action passes through when given")

parsed = app.hf_parse_chart_data("Month, Bookings\nJan, 120\nFeb: 240\nMar 90")
check(parsed == [["Jan", 120.0], ["Feb", 240.0], ["Mar", 90.0]],
      "chart data parses label/value rows and skips a header row")
check(app.hf_parse_chart_data("A, $1,200\nB, 45%") == [["A", 1200.0],
                                                        ["B", 45.0]],
      "chart values drop money and percent dressing")
check(raises(lambda: app.hf_parse_chart_data("A, -5\nB, 3")),
      "negative chart values rejected")
check(raises(lambda: app.hf_parse_chart_data("only one row, 5")),
      "a chart with one point rejected")
check(raises(lambda: app.hf_parse_chart_data("words\nmore words")),
      "chart rows with no numbers rejected")
chart_clean = app.hf_clean_fields("chart", {"title": "Bookings",
                                            "data": "Jan, 120\nFeb, 240"})
check(chart_clean["points"] == [["Jan", 120.0], ["Feb", 240.0]],
      "chart fields carry the parsed points")
check(raises(lambda: app.hf_clean_fields("chart", {"data": "A, 1\nB, 2"})),
      "chart without a title rejected")

exp_html = app.build_hf_composition("explainer", exp)
check("expl-head" in exp_html and "book direct" in exp_html,
      "explainer composition carries its headline and points")
pop_html = app.build_hf_composition(
    "explainer_pop", app.hf_clean_fields(
        "explainer_pop", {"headline": "H", "points": "a\nb"}))
check("expl-row-0" in pop_html and "border-radius:999px" in pop_html,
      "explainer pop lays its points out as pills")
energy_html = app.build_hf_composition(
    "explainer_energy", app.hf_clean_fields(
        "explainer_energy", {"headline": "H", "points": "a\nb"}))
check('data-duration="7"' in energy_html,
      "explainer energy runs seven seconds")
kin_html = app.build_hf_composition("kinetic", kin)
check('data-duration="4.5"' in kin_html,
      "kinetic composition length follows the line count")
check("Book more" in kin_html and "Stay longer" in kin_html,
      "kinetic composition carries every line")
intro_html = app.build_hf_composition(
    "intro", {"headline": "Willow Ford", "subline": "stay a while",
              "color": "#6366f1"})
check("intro-dot" in intro_html and 'data-duration="5"' in intro_html,
      "intro composition draws its mark and runs five seconds")
check("autoAlpha: 0" not in intro_html.split("tl.to(")[-1],
      "intro holds its lockup instead of exiting")
outro_html = app.build_hf_composition(
    "outro", {"headline": "Willow Ford", "subline": "stay a while",
              "cta": "Book your stay", "color": "#6366f1"})
check("Book your stay" in outro_html and 'id="outro-cta"' in outro_html,
      "outro composition carries the call to action")
chart_html = app.build_hf_composition("chart", chart_clean)
check("chart-bar" in chart_html and ">120<" in chart_html
      and ">240<" in chart_html,
      "chart composition draws bars with their values")
check("Bookings" in chart_html, "chart composition carries its title")
evil_chart = app.build_hf_composition(
    "chart", {"title": "<b>T</b>", "points": [["<i>L</i>", 5.0],
                                               ["B", 6.0]],
              "data": "x", "color": "#6366f1"})
check("<i>L</i>" not in evil_chart and "&lt;i&gt;L&lt;/i&gt;" in evil_chart,
      "chart labels never land as raw html")

# --- lane wiring in the source ------------------------------------------
lane = SRC.split("# -- template lane", 1)[1].split(
    '@web.get("/api/jobs/{job_id}/thumb")', 1)[0]
check('"charged_cents": 0' in lane, "template jobs charge zero")
check("_place_hold" not in lane, "template lane places no holds")
check("FREE_CLIPS_TOTAL" in lane,
      "unpaid accounts count against the free lifetime cap")
check("FREE_PLATFORM_TOTAL_CEILING" in lane,
      "platform free ceiling applies")
check("freecount:total" in lane,
      "free jobs increment the platform counter")
check("TEMPLATE_DAILY_CAP_PAID" in lane,
      "paid accounts hit the daily cap")
check("TEMPLATE_WORKER_KEY" in lane,
      "worker endpoints use the dedicated key")
check("templateupload:" in lane, "captions uploads are tracked")

mirror = SRC.split("def mirror_status", 1)[1].split("def _poll_limit_for", 1)[0]
check('provider") == "hyperframes"' in mirror,
      "mirror has the hyperframes branch")
check("TEMPLATE_POLL_LIMIT_SECONDS" in mirror,
      "mirror enforces the template bound")
poll = SRC.split("def _poll_limit_for", 1)[1].split("def ", 1)[0]
check("hyperframes" in poll and "TEMPLATE_POLL_LIMIT_SECONDS" in poll,
      "poll limit table knows the lane")
check("moonfactory-kaggle" in SRC, "api function carries the kaggle secret")
check("hf_worker_kernel.py" in SRC, "worker script ships in the api image")

# --- the motion wing: claim payload and studio surface -------------------
check('payload["motion"] = True' in SRC,
      "story claims are flagged as motion jobs for the worker")
check('payload["audio_plan"]' in SRC,
      "story claims carry the audio plan for the score")
check('template_id == "story"' in SRC,
      "story job length comes from the plan at create time")
check("const MOTION_TEMPLATES = [" in SRC
      and "{ id: 'story', name: 'Animated story short' }" in SRC
      and "{ id: 'sting', name: 'Logo sting' }" in SRC,
      "the Create picker carries the motion templates under their studio names")
check("freeGroup.label = 'Free Models'" in SRC
      and "sel.appendChild(freeGroup)" in SRC
      and "freeGroup.appendChild(o)" in SRC,
      "the picker carries the motion templates in the Free Models group at the top")
check('id="motion-fields-story"' in SRC and 'id="motion-fields-sting"' in SRC,
      "the Create form has field blocks for both motion templates")
check('id="mf-story-lines"' in SRC and 'id="mf-story-title"' in SRC
      and 'id="mf-sting-headline"' in SRC and 'id="mf-color"' in SRC,
      "the Create form takes the story lines, the titles and the accent color")
check("function applyMotionUI()" in SRC
      and "isMotionTemplate(sel.value)" in SRC,
      "picking a motion template swaps the composer fields and stays open to free accounts")
for tid, tname in (("explainer", "Explainer"),
                   ("explainer_pop", "Explainer pop"),
                   ("explainer_energy", "Explainer energy"),
                   ("kinetic", "Kinetic type"),
                   ("intro", "Logo intro"),
                   ("outro", "Logo outro"),
                   ("chart", "Data video")):
    check(app.HF_TEMPLATES[tid]["name"] == tname,
          f"{tid} carries its studio name")
    check(f"{{ id: '{tid}', name: '{tname}' }}" in SRC,
          f"the Create picker carries {tid} under its studio name")
check('id="motion-fields-explainer"' in SRC
      and 'id="motion-fields-kinetic"' in SRC
      and 'id="motion-fields-intro"' in SRC
      and 'id="motion-fields-outro"' in SRC
      and 'id="motion-fields-chart"' in SRC,
      "the Create form has field blocks for every motion pack template")
check('id="mf-chart-data"' in SRC and 'id="mf-chart-file"' in SRC
      and 'id="mf-kinetic-words"' in SRC and 'id="mf-outro-cta"' in SRC,
      "the data video form takes pasted rows or a CSV file, the kinetic form its lines, the outro its call to action")
check("payload.data = $('mf-chart-data').value" in SRC
      and "payload.words = $('mf-kinetic-words').value" in SRC
      and "payload.cta = $('mf-outro-cta').value" in SRC,
      "the Create button sends the motion pack fields to the template lane")
check("payload.title = $('mf-story-title').value" in SRC
      and "payload.headline = $('mf-sting-headline').value" in SRC
      and SRC.count("endpoint: '/api/templates'") >= 2,
      "the Create button sends motion picks to the template lane")
check('id="tpl-fields-story"' not in SRC and 'value="story"' not in SRC,
      "the Titles pane no longer carries the motion templates")

# --- kernel -------------------------------------------------------------
kernel = (ROOT / "worker-kaggle" / "hf_worker_kernel.py").read_text()
check("moonfactory-hf-toolchain" in kernel,
      "kernel mounts the persistent toolchain dataset")
check("moonfactory-hf-key" in kernel,
      "kernel reads the worker key from its dataset")
check("import modal" not in kernel and "modal deploy" not in kernel,
      "kernel has no Modal fallback")
check("IDLE_LIMIT_SECONDS = 600" in kernel,
      "kernel shuts down after ten idle minutes")
check("/api/worker/hf/heartbeat" in kernel, "kernel heartbeats")
check("print(KEY" not in kernel and "print(key" not in kernel.lower()
      .replace("print(key file", ""), "kernel never prints the key")
check("motion-scene.js" in kernel, "kernel stages the motion scene script")
check("motion_audio.py" in kernel, "kernel synthesizes the story score")
check("MOTION_RENDER_TIMEOUT_SECONDS = 2400" in kernel,
      "motion renders get the bigger timeout")
check('job.get("motion")' in kernel, "kernel branches on the motion flag")
check("sys.executable" in kernel,
      "score synthesis runs on the kernel python")

if failures:
    print("TEMPLATE LANE CHECK FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL TEMPLATE LANE CHECKS PASSED")
