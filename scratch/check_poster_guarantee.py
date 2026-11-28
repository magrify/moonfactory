"""Local checks for the poster guarantee (2026-11-27).

Jack, on the black library card an ingested source showed:
"that keeps happening, fix it all permanently." The class
had four stacked holes, and this pin holds each one shut:

- the finishing funnel (classify_job_output, which every
  completion path calls) derived the gallery thumb only on
  the classifier's success path — its early returns (already
  scored, attempts capped, no readable frame) skipped the
  derivation, and most video paths never cut a poster frame
  at finishing at all;
- ensure_gallery_thumb trusted a warm container's volume
  view: a job finished by another container could look
  thumb-less forever, the stale-view miss the source check
  was fixed for in 1ca6ee8, still open in the thumb path;
- the serve routes disagreed: the gallery thumb route had
  no poster fallback, the owner's image thumb route 404'd
  instead of serving the finished original, and the poster
  route could only cut from the generation finishing chain,
  not from the plain stored file sources and clips keep;
- no card anywhere had a client fallback: one failed
  thumbnail load (404, cold container, a slow first derive)
  left the card black for good.

The pin asserts the funnel carries the guarantee in a
finally block, the helpers refresh the volume once on a
miss and can derive from the poster itself, every serve
route falls back instead of 404ing a finished item, every
card surface wires the thumbImgFail fallback, and then it
drives the helpers against a real tiny video and a
poster-only job to prove frames actually come out.

No Modal calls (the helpers' volume refresh is wrapped and
fails soft locally), no network, no generation.

Run:  modal-venv python scratch/check_poster_guarantee.py
"""
import inspect
import subprocess
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


# -- the finishing funnel carries the guarantee ---------------------------

fn = inspect.getsource(app.classify_job_output)
check("finally:" in fn,
      "classify_job_output lost its finally block: the poster "
      "guarantee must run on every exit, early returns included")
tail = fn.split("finally:", 1)[1] if "finally:" in fn else ""
check("ensure_gallery_thumb" in tail,
      "the funnel's finally block no longer derives the gallery thumb")
check("ensure_job_poster" in tail,
      "the funnel's finally block no longer cuts the poster frame")

# Every completion path still routes through the funnel: the
# four CPU processors by name, and the call-site count overall
# (14 sites at the guarantee's landing: drama children, the
# editclone actions, assembly, shorts, clip children, the
# generation finishers and the serve-time classification).
for proc in ("def process_clip_job", "def process_editclone_job",
             "def process_assembly_job", "def process_short_job"):
    body = src_slice(proc, "\n@app.function")
    check("classify_job_output(" in body,
          f"{proc} no longer routes through classify_job_output, "
          "so its jobs leave without the poster guarantee")
check(SRC.count("classify_job_output(") - 1 >= 14,
      "classify_job_output call sites dropped below 14: a "
      "finishing path was disconnected from the poster guarantee")

# -- the helpers ------------------------------------------------------------

gt = inspect.getsource(app.ensure_gallery_thumb)
check("data_volume.reload" in gt,
      "ensure_gallery_thumb no longer refreshes the volume on a "
      "miss: a warm container's stale view makes finished jobs "
      "look thumb-less")
check("poster" in gt and 'f"{job[\'id\']}.jpg"' in gt,
      "ensure_gallery_thumb lost its poster-as-source fallback: "
      "a job holding only its poster frame gets no derivative")

jp = inspect.getsource(app.ensure_job_poster)
check("data_volume.reload" in jp,
      "ensure_job_poster no longer refreshes the volume on a miss")
check('f"{job[\'id\']}.jpg"' in jp and "ffmpeg" in jp,
      "ensure_job_poster no longer cuts {job_id}.jpg with ffmpeg")

# -- the serve routes fall back instead of 404ing ---------------------------

thumb_route = src_slice('@web.get("/api/jobs/{job_id}/thumb")',
                        '@web.get("/api/gallery')
check("ensure_job_poster" in thumb_route,
      "the owner's thumb route lost its lazy poster backfill")
check("ensure_image_file" in thumb_route,
      "the owner's image thumb route lost its fallback to the "
      "finished original and 404s a finished picture again")

gallery_thumb = src_slice('@web.get("/gallery/{job_id}/thumb")',
                          '@web.post("/api/gallery')
check("ensure_job_poster" in gallery_thumb,
      "the gallery thumb route lost its poster fallback and "
      "404s finished videos that have a file")

poster_fn = src_slice("def ensure_poster", "def _media_cache")
check("ensure_job_poster" in poster_fn,
      "the poster route no longer tries the plain stored file "
      "before the generation finishing chain: sources and "
      "clips with no poster cannot be served one")

# -- the client fallback is wired on every card surface ---------------------

check(SRC.count("function thumbImgFail") == 2,
      "thumbImgFail must be defined on both pages that render "
      "cards (studio and public gallery)")
check(SRC.count('onerror="thumbImgFail(this)"') >= 6,
      "a string-built card thumbnail lost its onerror fallback")
for builder in ("function mfLibCard", "function ecRenderLibrary",
                "function ecRenderLibraryPicked",
                "async function loadGallery"):
    body = src_slice(builder, "\nfunction " if builder !=
                     "async function loadGallery" else "\n</script>")
    check("thumbImgFail" in body,
          f"{builder} no longer wires the thumbnail fallback")

# -- drive the helpers -------------------------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="poster-pin-"))
app.FILES_DIR = tmp
try:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=red:s=64x64:d=1", "-pix_fmt", "yuv420p",
         str(tmp / "pinposter.mp4")],
        check=True, capture_output=True, timeout=60)
    job = {"id": "pinposter", "kind": "generation"}
    poster = app.ensure_job_poster(job)
    check(poster is not None and poster.exists()
          and poster.stat().st_size > 0,
          "ensure_job_poster made no poster from a real video")
    if poster is not None and poster.exists():
        check(poster.read_bytes()[:2] == b"\xff\xd8",
              "the poster ensure_job_poster made is not a JPEG")
    thumb = app.ensure_gallery_thumb(job)
    check(thumb is not None and thumb.exists()
          and thumb.stat().st_size > 0,
          "ensure_gallery_thumb made no derivative from a real video")
    check(app.ensure_job_poster({"id": "pinposter", "kind": "image"})
          is None,
          "ensure_job_poster must refuse image jobs (they have "
          "no poster frame; their thumb route serves the file)")
    # A poster-only job (the character builder's shape: just
    # {id}.jpg on the volume) still gets its derivative.
    char = {"id": "pinchar", "kind": "edit_character"}
    (tmp / "pinchar.jpg").write_bytes(poster.read_bytes())
    cthumb = app.ensure_gallery_thumb(char)
    check(cthumb is not None and cthumb.exists()
          and cthumb.stat().st_size > 0,
          "a poster-only job gets no gallery thumb: its card "
          "renders without a picture")
    # Nothing on the volume at all: None, never an exception.
    ghost = {"id": "pinghost", "kind": "generation"}
    check(app.ensure_job_poster(ghost) is None,
          "ensure_job_poster must return None for a file-less job")
    check(app.ensure_gallery_thumb(ghost) is None,
          "ensure_gallery_thumb must return None for a file-less job")
    # The pinposter.mp4 above is silent (a lavfi color source
    # has no audio track): clipping.probe refuses it, which is
    # why silent house renders carried no duration and their
    # cards showed (?s). The finishing funnel's stamp reads
    # the same file with the tolerant probe.
    dur_job = {"id": "pindur", "kind": "generation"}
    (tmp / "pindur.mp4").write_bytes(
        (tmp / "pinposter.mp4").read_bytes())
    app._stamp_job_duration(dur_job)
    check(bool(dur_job.get("duration_seconds"))
          and 0.5 < dur_job["duration_seconds"] < 1.5,
          "a silent finished video gets no duration stamp: "
          "its card label shows (?s)")
finally:
    pass

# -- the fallback swap is sized like the thumbnail it replaces --------------

check(".job video.thumb{width:140px" in SRC,
      "the fallback video on a job card is unsized and "
      "balloons past the 140px thumbnail")
check(".libcard video.thumb" in SRC,
      "the fallback video on a picker card is unsized and "
      "renders at the video's intrinsic size")

# -- the duration stamp and the display fallback ----------------------------

check("_stamp_job_duration" in inspect.getsource(
        app.classify_job_output),
      "the finishing funnel no longer stamps a missing "
      "duration: finished silent jobs show (?s) again")
pub = src_slice("def public_job", "def _api_job_params")
check("result_duration" in pub and "billed_seconds" in pub,
      "the public job view no longer falls back to the "
      "reported or billed length when the stamp is missing")
comfy_file = src_slice(
    '@web.post("/api/worker/comfy/jobs/{job_id}/file")',
    '@web.post("/api/worker/comfy/jobs/{job_id}/fail")')
check("probe_media" in comfy_file,
      "the house worker upload lost its tolerant-probe "
      "fallback: silent renders land with no duration again")

if failures:
    print("check_poster_guarantee: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_poster_guarantee: PASS")
