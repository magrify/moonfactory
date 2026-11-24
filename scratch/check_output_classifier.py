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

if failures:
    print("OUTPUT CLASSIFIER CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL OUTPUT CLASSIFIER CHECKS PASSED")
