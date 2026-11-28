"""Local checks for the has_thumb flag fix (2026-11-28).

Clone proof 2's finished clone (job b6b3cea548f24875)
served a real 46,130-byte poster and a real 10,158-byte
thumb while the public job view reported has_thumb false.
Root cause: public_job read the flag straight off the
record's has_thumb stamp, and the stamp is written by hand
in only a few finishing paths — the Edit/Clone actions
(ingest, hook, character) and the assembly finisher. A
generation job, an Edit/Clone clone included, finishes
through classify_job_output, whose finally block derives
the real {job_id}.gthumb.jpg and {job_id}.jpg and discards
the results; nothing ever stamped its record, so the flag
lied about a picture both routes were already serving.

The fix derives the flag the way the serve paths do:
job_has_thumb reads the volume for exactly the files the
thumb and poster routes can paint from (the derived
thumb, the poster frame, or the finished media file both
are cut from on demand), with the record's stamp still
counting on its own for the window where a warm
container's volume view lags the finisher's commit.

This pin holds the shape: public_job must derive through
job_has_thumb and never again read the bare stamp, and it
drives job_has_thumb against a fixture volume covering
every shape that fooled it — above all the clone's own
shape, a finished generation job whose poster and thumb
sit on the volume with no stamp on the record.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_has_thumb.py
"""
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


def src_slice(start, end):
    i = SRC.index(start)
    j = SRC.index(end, i + len(start)) if end else len(SRC)
    return SRC[i:j]


# -- the presenter derives, it does not read the stamp ---------------------

pub = src_slice("def public_job", "def _api_job_params")
check('"has_thumb": job_has_thumb(job)' in pub,
      "public_job no longer derives has_thumb through "
      "job_has_thumb: the flag reads the record's stamp "
      "again and finished generation jobs lie thumb-less")
check('bool(job.get("has_thumb"))' not in pub,
      "public_job reads the bare has_thumb stamp again: "
      "that read is the lie this fix removed")

# -- the helper tests what the serve paths test ------------------------------

helper = src_slice("def job_has_thumb", "# -------")
for suffix in ('".gthumb.jpg"', '".jpg"', '".mp4"', '".png"'):
    check(suffix in helper,
          f"job_has_thumb no longer tests for {suffix}: the "
          "serve paths can paint from that file, so a job "
          "holding only it would read thumb-less again")
check('job.get("status") != "done"' in helper,
      "job_has_thumb lost its finished-job gate: an "
      "unfinished job with stray files would claim a thumb")

# -- drive the helper against a fixture volume -------------------------------

tmp = Path(tempfile.mkdtemp(prefix="hasthumb-pin-"))
app.FILES_DIR = tmp


def put(name, data=b"x" * 64):
    (tmp / name).write_bytes(data)


# The clone's own shape: finished generation job, poster
# and thumb derived on the volume, NO stamp on the record.
put("pinclone.mp4")
put("pinclone.jpg")
put("pinclone.gthumb.jpg")
check(app.job_has_thumb({"id": "pinclone", "kind": "generation",
                         "status": "done"}) is True,
      "a finished generation job with poster and thumb on "
      "the volume reads has_thumb false (the clone proof 2 lie)")

# Media file only: the routes cut the poster and thumb
# from it on demand, so the card paints.
put("pinmedia.mp4")
check(app.job_has_thumb({"id": "pinmedia", "kind": "generation",
                         "status": "done"}) is True,
      "a finished job holding only its media file reads "
      "has_thumb false, but the thumb route derives from it")

# Poster only (the character builder's shape).
put("pinchar.jpg")
check(app.job_has_thumb({"id": "pinchar", "kind": "ec_character",
                         "status": "done"}) is True,
      "a finished poster-only job reads has_thumb false")

# Derived thumb only.
put("pinthumb.gthumb.jpg")
check(app.job_has_thumb({"id": "pinthumb", "kind": "generation",
                         "status": "done"}) is True,
      "a finished job holding only its derived thumb reads "
      "has_thumb false")

# Image job: the finished PNG is what its thumb derives from.
put("pinimg.png")
check(app.job_has_thumb({"id": "pinimg", "kind": "image",
                         "status": "done"}) is True,
      "a finished image job holding its PNG reads has_thumb false")

# Nothing on the volume and no stamp: the flag must say so.
check(app.job_has_thumb({"id": "pinghost", "kind": "generation",
                         "status": "done"}) is False,
      "a finished job with no picture anywhere reads "
      "has_thumb true")

# An empty file is not a picture.
put("pinempty.mp4", b"")
check(app.job_has_thumb({"id": "pinempty", "kind": "generation",
                         "status": "done"}) is False,
      "a zero-byte media file counts as a thumb")

# Unfinished jobs claim nothing from stray files.
put("pinrun.mp4")
check(app.job_has_thumb({"id": "pinrun", "kind": "generation",
                         "status": "running"}) is False,
      "a running job reads has_thumb true")

# The stamp still counts on its own: the finisher that set
# it had just made the poster, and a warm container's
# volume view can lag the commit behind it.
check(app.job_has_thumb({"id": "pinstamp", "kind": "generation",
                         "status": "done",
                         "has_thumb": True}) is True,
      "a stamped job reads has_thumb false: the stamp must "
      "survive a lagging volume view")

# A broken record never raises.
check(app.job_has_thumb({"status": "done"}) is False,
      "job_has_thumb must answer False, not raise, on a "
      "record with no id")

if failures:
    print("check_has_thumb: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_has_thumb: PASS")
