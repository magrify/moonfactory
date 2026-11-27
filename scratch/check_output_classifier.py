"""Local checks for the output classifier's gallery rule
(Jack, 2026-11-24).

Rule: a finished generation's public gallery listing is judged
by two signals stacked together, plus the standing bars. The
prompt scan (prompt_is_nsfw) now judges EVERY lane for gallery
purposes, regular included: a flagged prompt keeps any job out.
And the output classifier (Falconsai/nsfw_image_detection, run
at finishing over the actual picture or the video's sampled
frames) stamps a verdict on the record: a flagged verdict keeps
any generation out, regular or uncensored; a clean verdict plus
a clean prompt lists, uncensored-lane work included. There is
no blanket lane ban. The uncensored image-input bar and the
paid opt-out stack unchanged. A post-change job with no verdict
yet does not list (the gallery fails closed) while its delivery
to the owner never waits on the classifier; a legacy job with
no verdict is judged on the prompt rules alone. The verdict
never blocks creation, never refuses a user, and never appears
in a public payload.

No Modal calls, no network, no real model: gallery_visible is
exercised against plain dicts, classify_job_output runs with
its file-scores seam stubbed, and the wiring is pinned in
source. The real model's scores on real pictures are proven
separately (the local CPU proof), never here.

Run:  modal-venv python scratch/check_output_classifier.py
"""
import ast
import sys
import tempfile
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
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the constants -------------------------------------------------------

check(app.OUTPUT_CLASSIFIER_ID == "Falconsai/nsfw_image_detection",
      "the classifier id is not the Falconsai model")
check(app.OUTPUT_NSFW_FLAG_THRESHOLD == 0.7,
      "the flag threshold is not the named 0.7 constant")

# -- gallery_visible, exercised for real ---------------------------------

ns = {}
exec(func_src("is_paid"), ns)
is_paid = ns["is_paid"]

free_owner = {"id": "u-free", "plan": "free", "net_paid_cents": 0.0,
              "gallery_opt_out": False}
opted_out_owner = {"id": "u-out", "plan": "creator",
                   "net_paid_cents": 2000.0, "gallery_opt_out": True}
USERS = {f"id:{u['id']}": u for u in (free_owner, opted_out_owner)}

gns = {"users": USERS, "is_paid": is_paid,
       "job_is_nsfw": app.job_is_nsfw,
       "job_is_uncensored": app.job_is_uncensored,
       "prompt_is_nsfw": app.prompt_is_nsfw,
       "OUTPUT_CLASSIFIER_SINCE": app.OUTPUT_CLASSIFIER_SINCE}
exec(func_src("gallery_visible"), gns)
gallery_visible = gns["gallery_visible"]

SINCE = app.OUTPUT_CLASSIFIER_SINCE
NEW = SINCE + 1000.0
OLD = SINCE - 1000.0

CLEAN = {"output_nsfw_score": 0.05, "output_flagged": False,
         "output_classifier": app.OUTPUT_CLASSIFIER_ID,
         "output_classified_at": NEW}
FLAGGED = {"output_nsfw_score": 0.93, "output_flagged": True,
           "output_classifier": app.OUTPUT_CLASSIFIER_ID,
           "output_classified_at": NEW}
ERRORED = {"output_classifier": app.OUTPUT_CLASSIFIER_ID,
           "output_classified_at": NEW,
           "output_classifier_error": "weights missing"}


def job(**kw):
    base = {"status": "done", "user_id": "u-free",
            "lane": "wan3", "model": "wan3",
            "user_prompt": "a lighthouse at dusk",
            "created_at": NEW}
    base.update(kw)
    return base


# Clean prompt + clean verdict lists, on a regular lane and on
# an uncensored lane alike (no blanket lane ban).
check(gallery_visible(job(**CLEAN)) is True,
      "regular job with clean prompt and clean verdict hides")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          **CLEAN)) is True,
      "clean uncensored job with clean verdict does not list")
# A flagged verdict keeps any generation out, whatever the
# prompt said, on both lane families.
check(gallery_visible(job(**FLAGGED)) is False,
      "regular job with a flagged output verdict lists")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          **FLAGGED)) is False,
      "uncensored job with a flagged output verdict lists")
check(gallery_visible(job(created_at=OLD, **FLAGGED)) is False,
      "legacy job with a flagged output verdict lists")
# The prompt scan now judges every lane: a flagged prompt on a
# regular lane keeps the job out even with a clean verdict, and
# rejudges legacy jobs too.
check(gallery_visible(job(user_prompt="a nude woman",
                          **CLEAN)) is False,
      "regular job with a flagged prompt and clean verdict lists")
check(gallery_visible(job(user_prompt="a nude woman",
                          created_at=OLD)) is False,
      "legacy regular job with a flagged prompt lists")
# Fail closed for the gallery: a post-change job with no verdict
# (or only an error stamp) does not list.
check(gallery_visible(job()) is False,
      "post-change job with no verdict lists")
check(gallery_visible(job(**ERRORED)) is False,
      "post-change job with only an error stamp lists")
# Legacy jobs with no verdict keep the prompt-only behavior.
check(gallery_visible(job(created_at=OLD)) is True,
      "legacy job with no verdict and a clean prompt hides")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          created_at=OLD)) is True,
      "legacy clean uncensored job with no verdict hides")
# The standing bars stack with both signals.
check(gallery_visible(job(user_id="u-out", **CLEAN)) is False,
      "paid opt-out no longer hides a clean-verdict job")
check(gallery_visible(job(lane="seedance25_unc",
                          model="seedance25_unc",
                          has_start_image=True, **CLEAN)) is False,
      "image-input-barred uncensored job with a clean verdict "
      "lists")

# -- classify_job_output, with the scoring seam stubbed -------------------

real_jobs = app.jobs
real_files_dir = app.FILES_DIR
real_scores = app._classifier_file_scores
tmp = Path(tempfile.mkdtemp())
app.jobs = {}
app.FILES_DIR = tmp
try:
    calls = {"n": 0, "scores": [0.12], "error": None}

    def stub_scores(path):
        calls["n"] += 1
        if calls["error"] is not None:
            raise calls["error"]
        return list(calls["scores"])

    app._classifier_file_scores = stub_scores

    img = tmp / "j1.png"
    img.write_bytes(b"\x89PNG fake")
    rec = {"id": "j1", "kind": "image"}
    app.classify_job_output(rec, img)
    check(rec.get("output_nsfw_score") == 0.12
          and rec.get("output_flagged") is False
          and rec.get("output_classifier")
          == app.OUTPUT_CLASSIFIER_ID
          and rec.get("output_classified_at"),
          "clean image verdict was not stamped")
    check(app.jobs.get("j1") is rec,
          "the stamped record was not saved")

    # A stamped job is never rescored.
    before = calls["n"]
    app.classify_job_output(rec, img)
    check(calls["n"] == before,
          "an already stamped job was rescored")

    # A video's verdict is its worst sampled frame.
    calls["scores"] = [0.05, 0.93, 0.2]
    vid = tmp / "j2.mp4"
    vid.write_bytes(b"fake mp4")
    rec2 = {"id": "j2", "kind": "generation"}
    app.classify_job_output(rec2, vid)
    check(rec2.get("output_nsfw_score") == 0.93
          and rec2.get("output_flagged") is True,
          "video verdict is not the max frame score")

    # The threshold itself flags (>=).
    calls["scores"] = [app.OUTPUT_NSFW_FLAG_THRESHOLD]
    rec3 = {"id": "j3", "kind": "image"}
    img3 = tmp / "j3.png"
    img3.write_bytes(b"\x89PNG fake")
    app.classify_job_output(rec3, img3)
    check(rec3.get("output_flagged") is True,
          "a score at the threshold does not flag")

    # Failure: no verdict, an error stamp, an attempt counted,
    # and the call itself never raises (delivery is untouched).
    calls["error"] = RuntimeError("weights missing")
    rec4 = {"id": "j4", "kind": "image"}
    img4 = tmp / "j4.png"
    img4.write_bytes(b"\x89PNG fake")
    app.classify_job_output(rec4, img4)
    check(rec4.get("output_nsfw_score") is None
          and "weights missing"
          in (rec4.get("output_classifier_error") or "")
          and rec4.get("output_classifier_attempts") == 1,
          "a classifier failure did not fail closed and log")
    # Attempts are capped: after the cap the seam is not retried.
    for _ in range(app.OUTPUT_CLASSIFIER_MAX_ATTEMPTS + 2):
        app.classify_job_output(rec4, img4)
    check(rec4.get("output_classifier_attempts")
          == app.OUTPUT_CLASSIFIER_MAX_ATTEMPTS,
          "classifier attempts are not capped")
    # A later success (fresh record) stamps and clears the error.
    calls["error"] = None
    calls["scores"] = [0.01]
    rec5 = {"id": "j5", "kind": "image",
            "output_classifier_attempts": 1,
            "output_classifier_error": "weights missing"}
    img5 = tmp / "j5.png"
    img5.write_bytes(b"\x89PNG fake")
    app.classify_job_output(rec5, img5)
    check(rec5.get("output_nsfw_score") == 0.01
          and "output_classifier_error" not in rec5,
          "a later success did not stamp and clear the error")

    # No file, no stamp, no error: the verdict waits for the
    # finishing stage that lands the file.
    rec6 = {"id": "j6", "kind": "image"}
    before = calls["n"]
    app.classify_job_output(rec6, tmp / "j6.png")
    check(calls["n"] == before
          and rec6.get("output_nsfw_score") is None
          and "output_classifier_error" not in rec6,
          "a missing file was stamped or errored")
    # The web remux is never judged (it is a derivative).
    web = tmp / "j7.web.mp4"
    web.write_bytes(b"fake")
    rec7 = {"id": "j7", "kind": "generation"}
    app.classify_job_output(rec7, web)
    check(calls["n"] == before
          and rec7.get("output_nsfw_score") is None,
          "the web remux was classified")
    # The default path resolves from the record and FILES_DIR.
    img8 = tmp / "j8.png"
    img8.write_bytes(b"\x89PNG fake")
    rec8 = {"id": "j8", "kind": "image"}
    app.classify_job_output(rec8)
    check(rec8.get("output_nsfw_score") == 0.01,
          "the default file path was not classified")
finally:
    app.jobs = real_jobs
    app.FILES_DIR = real_files_dir
    app._classifier_file_scores = real_scores

# Non-media files earn no verdict from the real seam.
wav = tmp / "j9.wav"
wav.write_bytes(b"fake wav")
check(app._classifier_file_scores(wav) == [],
      "a non-media file earns classifier scores")

# -- frame sampling -------------------------------------------------------

check(app._video_sample_times(10.0) == [0.5, 5.0, 9.5],
      "a 10s video does not sample first, middle, last")
check(app._video_sample_times(4.0) == [0.5, 2.0, 3.5],
      "a 4s video does not sample first, middle, last")
check(app._video_sample_times(1.0) == [0.5],
      "a 1s video's samples do not collapse to one")
check(app._video_sample_times(0) == [0.0],
      "an unprobed video does not fall back to its first frame")
check(len(app._video_sample_times(120.0)) == 3,
      "a long video samples more than three frames")

# -- the wiring -----------------------------------------------------------

for fn in ("process_clip_job", "process_short_job",
           "_submit_cloudflare_image", "_submit_ovhcloud_image",
           "_settle_provider_image", "ensure_processed",
           "ensure_image_file", "hf_job_file", "comfy_job_file",
           "_drama_finish_episode"):
    check("classify_job_output" in func_src(fn),
          f"{fn} does not stamp the output verdict at finishing")

_gv = func_src("gallery_visible")
check("output_flagged" in _gv and "OUTPUT_CLASSIFIER_SINCE" in _gv
      and "prompt_is_nsfw" in _gv,
      "gallery_visible does not read the verdict and the "
      "all-lane prompt scan")

# -- the verdict never reaches a public payload ---------------------------

for field in ("output_nsfw_score", "output_flagged",
              "output_classifier", "output_classified_at",
              "output_classifier_error"):
    check(field not in func_src("public_job"),
          f"public_job exposes {field}")
    check(field not in func_src("public_gallery_item"),
          f"public_gallery_item exposes {field}")

# =========================================================================
# The input side (Jack, 2026-11-24): the same classifier judges
# the still pictures a job takes IN at creation. The verdict
# stamps the record (input_nsfw_score / input_flagged /
# input_classifier / input_classified_at, or input_classifier_
# error for an unjudged set) and splits by lane:
#
# - REGULAR lane: a flagged upload refuses creation (no job, no
#   charge, no dispatch — the refusal is raised before the
#   hold, pinned in source below), and an upload set that could
#   not be judged refuses too (fail closed).
# - UNCENSORED lane: neither ever refuses; the verdict stamps
#   and the gallery bars do the rest.
# - The gallery: a flagged input verdict keeps the generation
#   out on EVERY lane, stacked on the uncensored image-input
#   bar. A clean input verdict changes nothing. A job with NO
#   input verdict (no judgeable picture in, or an unjudged set
#   on an uncensored lane) is judged by the other signals
#   alone — the input side never fails the gallery closed,
#   deliberately unlike the output side. That asymmetry is
#   pinned below, on purpose.
# =========================================================================

IN_CLEAN = {"input_nsfw_score": 0.04, "input_flagged": False,
            "input_classifier": app.OUTPUT_CLASSIFIER_ID,
            "input_classified_at": NEW}
IN_FLAGGED = {"input_nsfw_score": 0.91, "input_flagged": True,
              "input_classifier": app.OUTPUT_CLASSIFIER_ID,
              "input_classified_at": NEW}
IN_ERRORED = {"input_classifier": app.OUTPUT_CLASSIFIER_ID,
              "input_classified_at": NEW,
              "input_classifier_error": "weights missing"}

# A flagged upload keeps the generation out of the gallery on
# every lane, even with a clean prompt and a clean output
# verdict — and rejudges legacy records that carry the stamp.
check(gallery_visible(job(**CLEAN, **IN_FLAGGED)) is False,
      "regular job with a flagged input verdict lists")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          **CLEAN, **IN_FLAGGED)) is False,
      "uncensored job with a flagged input verdict lists")
check(gallery_visible(job(created_at=OLD, **IN_FLAGGED)) is False,
      "legacy job with a flagged input verdict lists")
# The two bars agree on an uncensored lane: an image-input job
# there is out by the standing bar AND by the verdict bar.
check(gallery_visible(job(lane="seedance25_unc",
                          model="seedance25_unc",
                          has_start_image=True,
                          **CLEAN, **IN_FLAGGED)) is False,
      "uncensored image-input job with a flagged input lists")
# A clean input verdict changes nothing anywhere.
check(gallery_visible(job(**CLEAN, **IN_CLEAN)) is True,
      "regular job with a clean input verdict hides")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          **CLEAN, **IN_CLEAN)) is True,
      "uncensored job with a clean input verdict hides")
# THE DELIBERATE ASYMMETRY: an input set that could not be
# judged (error stamp, no score) on an uncensored lane does
# NOT hide the job — the output verdict still gates it — while
# a post-change job with no OUTPUT verdict hides above. The
# input side never fails the gallery closed.
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          **CLEAN, **IN_ERRORED)) is True,
      "uncensored job with an unjudged input set hides: the "
      "input side failed the gallery closed")
check(gallery_visible(job(**ERRORED, **IN_CLEAN)) is False,
      "the output side stopped failing closed (see the "
      "asymmetry pin above)")

# -- classify_input_images, with the scoring seam stubbed -----------------

real_session = app._output_classifier
real_bytes_score = app._classifier_bytes_score
try:
    app._output_classifier = lambda: object()  # noqa: E731
    state = {"scores": {}, "default": 0.1, "error": None}

    def stub_bytes_score(data, session=None):
        if state["error"] is not None:
            raise state["error"]
        return state["scores"].get(data, state["default"])

    app._classifier_bytes_score = stub_bytes_score

    import base64 as _b64

    def b64(raw):
        return _b64.b64encode(raw).decode()

    def data_url(raw):
        return "data:image/png;base64," + b64(raw)

    # No uploads at all: no verdict.
    check(app.classify_input_images() is None,
          "a job with no inputs earned an input verdict")
    # Remote reference URLs only: no verdict (the named gap —
    # the platform never fetches them at creation).
    check(app.classify_input_images(
        data_urls=("https://example.com/ref.png",)) is None,
        "a remote-URL-only job earned an input verdict")
    # One clean base64 upload: a clean verdict at its score.
    v = app.classify_input_images(b64_images=(b64(b"clean"),))
    check(v == {"score": 0.1, "flagged": False},
          f"clean upload verdict wrong: {v}")
    # The max governs across several uploads, in either slot.
    state["scores"] = {b"one": 0.2, b"two": 0.88, b"three": 0.3}
    v = app.classify_input_images(
        b64_images=(b64(b"one"),),
        data_urls=(data_url(b"two"), data_url(b"three")))
    check(v == {"score": 0.88, "flagged": True},
          f"max-across-uploads verdict wrong: {v}")
    state["scores"] = {}
    # A score at the threshold flags (>=), same as the output.
    state["default"] = app.OUTPUT_NSFW_FLAG_THRESHOLD
    v = app.classify_input_images(b64_images=(b64(b"edge"),))
    check(v["flagged"] is True,
          "an input score at the threshold does not flag")
    state["default"] = 0.1
    # A bare base64 picture (the drama carry frame's shape) is
    # judged; a short string is not a picture and is skipped.
    long_raw = bytes(range(256)) * 2
    v = app.classify_input_images(data_urls=(b64(long_raw),))
    check(v == {"score": 0.1, "flagged": False},
          f"bare base64 picture verdict wrong: {v}")
    # A scoring failure leaves the whole set unjudged.
    state["error"] = RuntimeError("weights missing")
    v = app.classify_input_images(b64_images=(b64(b"clean"),))
    check(v["score"] is None and "weights missing" in v["error"],
          f"a classifier failure was not an unjudged verdict: {v}")
    state["error"] = None
    # One unreadable picture leaves the whole set unjudged too:
    # never a partial clean.
    def picky_bytes_score(data, session=None):
        if data == b"broken":
            raise RuntimeError("cannot identify image file")
        return 0.1

    app._classifier_bytes_score = picky_bytes_score
    v = app.classify_input_images(
        b64_images=(b64(b"fine"), b64(b"broken")))
    check(v["score"] is None and "cannot identify" in v["error"],
          f"a partly unreadable set was judged clean: {v}")
    app._classifier_bytes_score = stub_bytes_score
    # An undecodable payload is unjudged, not a crash.
    v = app.classify_input_images(
        data_urls=("data:image/png;base64,abc",))
    check(v is not None and v["score"] is None,
          f"an undecodable upload crashed or judged: {v}")
finally:
    app._output_classifier = real_session
    app._classifier_bytes_score = real_bytes_score

# -- stamp_input_verdict ---------------------------------------------------

rec = {"id": "in1"}
app.stamp_input_verdict(rec, {"score": 0.91, "flagged": True})
check(rec.get("input_nsfw_score") == 0.91
      and rec.get("input_flagged") is True
      and rec.get("input_classifier") == app.OUTPUT_CLASSIFIER_ID
      and rec.get("input_classified_at"),
      "a flagged input verdict was not stamped")
rec = {"id": "in2"}
app.stamp_input_verdict(rec, {"score": None, "error": "boom"})
check(rec.get("input_nsfw_score") is None
      and "input_flagged" not in rec
      and rec.get("input_classifier_error") == "boom",
      "an unjudged set was stamped as a verdict")
rec = {"id": "in3"}
app.stamp_input_verdict(rec, None)
check(not any(k.startswith("input_") for k in rec),
      "a job with no inputs was stamped")

# -- input_gate_refusal: the lane split ------------------------------------

REG = {"model": "wan3", "lane": "wan3"}
UNC = {"model": "wan22_uncut", "lane": "wan22_uncut"}
FLAGV = {"score": 0.91, "flagged": True}
CLEANV = {"score": 0.1, "flagged": False}
ERRORV = {"score": None, "error": "weights missing"}

# Flagged + regular refuses, with the lane-fit message, and
# the message carries no score and no class name.
r = app.input_gate_refusal(REG, FLAGV)
check(r == app.INPUT_REFUSAL_FLAGGED,
      "flagged upload on a regular lane does not refuse")
check("0.91" not in r and "score" not in r.lower(),
      "the flagged refusal echoes a score")
# Unjudged + regular refuses too (fail closed), try-again words.
check(app.input_gate_refusal(REG, ERRORV)
      == app.INPUT_REFUSAL_UNJUDGED,
      "unjudged upload on a regular lane does not refuse")
# Flagged or unjudged + uncensored never refuses: legal NSFW
# is the lane's purpose and the job generates.
check(app.input_gate_refusal(UNC, FLAGV) is None,
      "flagged upload on an uncensored lane refuses creation")
check(app.input_gate_refusal(UNC, ERRORV) is None,
      "unjudged upload on an uncensored lane refuses creation")
# Clean, or nothing to judge, never refuses anywhere.
check(app.input_gate_refusal(REG, CLEANV) is None,
      "clean upload on a regular lane refuses")
check(app.input_gate_refusal(REG, None) is None,
      "a job with no inputs refuses on a regular lane")
# The split follows the platform's uncensored scope, image
# edit lanes included.
check(app.input_gate_refusal(
    {"image_model": "qwen_edit_unc", "lane": "image",
     "model": "qwen_edit_unc"}, FLAGV) is None,
      "flagged source on the uncensored edit lane refuses")
check(app.input_gate_refusal(
    {"image_model": "seedream40", "lane": "image",
     "model": "seedream40"}, FLAGV)
    == app.INPUT_REFUSAL_FLAGGED,
    "flagged source on a regular edit lane does not refuse")
# The refusal is NOT the illegal gate: its source never
# touches the refusal records or the suspension ladder.
_gr = func_src("input_gate_refusal")
check("refusals" not in _gr and "suspended" not in _gr
      and "record_illegal_refusal" not in _gr,
      "the input refusal writes to the illegal-gate records")

# -- the presenter carve-out (Jack, 2026-11-25) -----------------------------
#
# The photo presenter is the one exemption from the regular
# lane rule: it exists to animate the user's own photo, so a
# flagged verdict (the classifier reading the photo as a real
# person) passes on that entry only. The same flagged verdict
# on a regular lane still refuses (pinned above), an unjudged
# presenter photo still fails closed, and the verdict still
# stamps, so the gallery bar above still applies to it.

check(app.presenter_input_refusal(FLAGV) is None,
      "a flagged photo refuses on the presenter entry")
check(app.presenter_input_refusal(ERRORV)
      == app.INPUT_REFUSAL_UNJUDGED,
      "an unjudged photo does not fail closed on the presenter")
check(app.presenter_input_refusal(CLEANV) is None,
      "a clean photo refuses on the presenter entry")
check(app.presenter_input_refusal(None) is None,
      "a verdictless presenter creation refuses")
# The carve-out is the flag only: its source never carries
# the flagged refusal and never touches the illegal-gate
# records, and the shared gate's rule is unchanged by it.
_pr = func_src("presenter_input_refusal")
check("INPUT_REFUSAL_FLAGGED" not in _pr
      and "refusals" not in _pr
      and "record_illegal_refusal" not in _pr,
      "the presenter carve-out reaches past the flag")
check(app.input_gate_refusal(REG, FLAGV)
      == app.INPUT_REFUSAL_FLAGGED,
      "the regular-lane rule moved with the carve-out")
# The presenter branch of create_template is the only caller:
# it classifies, applies its own refusal, and still stamps
# the verdict on the job; the illegal-content gate runs on
# the template's words before any of it.
_tpl = func_src("create_template")
check("presenter_input_refusal(presenter_verdict)" in _tpl
      and "stamp_input_verdict(job, presenter_verdict)" in _tpl,
      "the presenter branch lost its own gate or its stamp")
check(_tpl.index("enforce_prompt_gate")
      < _tpl.index("presenter_input_refusal"),
      "the illegal gate no longer runs before the presenter "
      "photo gate")
for fn in ("create_api_job", "create_job_core", "create_image_core",
           "create_drama", "create_short", "create_clip"):
    check("presenter_input_refusal" not in func_src(fn),
          f"{fn} picked up the presenter carve-out")

# -- the input wiring -------------------------------------------------------

for fn in ("create_api_job", "create_job_core", "create_image_core",
           "create_drama"):
    src = func_src(fn)
    check("classify_input_images" in src
          and "input_gate_refusal" in src
          and "stamp_input_verdict" in src,
          f"{fn} does not judge, gate and stamp its uploads")
check("classify_input_images" in func_src("_drama_create_shot_job")
      and "stamp_input_verdict" in func_src("_drama_create_shot_job"),
      "drama shot children do not take their own input stamps")

# The refusal lands before the money on every gated path.
_api = func_src("create_api_job")
check(_api.index("input_gate_refusal")
      < _api.index("Hold the cap before the provider call"),
      "the api-job input refusal lands after the hold")
_img = func_src("create_image_core")
check(_img.index("input_gate_refusal")
      < _img.index('user["balance_cents"] = '
                   'int(user.get("balance_cents", 0)) - charge'),
      "the image input refusal lands after the charge")
_job = func_src("create_job_core")
check(_job.index("return create_api_job(")
      < _job.index("input_gate_refusal")
      < _job.index("u = usage(user)"),
      "the house input refusal is not between the per-second "
      "dispatch and the charge section")
_drm = func_src("create_drama")
check(_drm.index("input_gate_refusal")
      < _drm.index("_drama_hold_rails"),
      "the drama input refusal lands after the hold rails")

check("input_flagged" in _gv,
      "gallery_visible does not read the input verdict")

# -- the input verdict never reaches a public payload ----------------------

for field in ("input_nsfw_score", "input_flagged",
              "input_classifier", "input_classified_at",
              "input_classifier_error"):
    check(field not in func_src("public_job"),
          f"public_job exposes {field}")
    check(field not in func_src("public_gallery_item"),
          f"public_gallery_item exposes {field}")
    check(field not in func_src("gallery_list"),
          f"gallery_list exposes {field}")

if failures:
    print("OUTPUT CLASSIFIER CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL OUTPUT CLASSIFIER CHECKS PASSED")
