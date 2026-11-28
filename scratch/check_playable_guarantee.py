"""Local checks for the playable guarantee (2026-11-28).

The black ingested source card came back a third time
(Jack, 2026-11-28: "are u fucking serious?") after two
"permanent" poster fixes. Both fixes verified the JPEG
layer, and the JPEG layer was genuinely fine: the source's
poster and gallery thumb existed, were real frames, and
served 200. What neither fix ever checked was the media
itself. The link-ingest routes hand back whatever codec
the source site served — for the two Shorts Jack hit,
AV1 picture with Opus sound in an mp4 wrapper — and
ensure_processed stores editclone files untouched on the
"exactly as it should play back" assumption, while
ensure_web_file only ever fixed the moov layout by stream
copy, byte identical streams. iPhone browsers cannot
decode AV1 or Opus in an mp4 at all, so every surface
that plays the file — the click player on the jobs list,
the thumbnail fallback video, the gallery player — sat
black while the poster painted perfectly, and every
server-side check kept passing.

The guarantee now lives in ensure_web_file, the one
funnel both file routes cross: what players are served
is always H.264 picture, AAC sound, index up front —
the delivered file when it already qualifies, a stream
copy remux when only the layout is wrong, a one time
H.264/AAC transcode when the stored streams are foreign.
The delivered file itself is never rewritten.

This pin asserts the funnel's shape in the source and
then drives the real helpers (extracted from app.py)
against real fixtures of the exact failing shape — a
tiny AV1+Opus mp4, the codec pair both of Jack's Shorts
carried — plus the three healthy shapes that must not
regress: H.264/AAC faststart (served as itself), H.264
with Opus sound (sound converted, picture copied), and
H.264/AAC with the index at the end (remux only).

No Modal calls, no network, no generation. The driven
helpers' volume object is a local stub.

Run:  modal-venv python scratch/check_playable_guarantee.py
"""
import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def fn(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return textwrap.dedent(ast.get_source_segment(SRC, node))
    raise AssertionError(f"function {name} not found")


def src_slice(start, end):
    i = SRC.index(start)
    j = SRC.index(end, i + len(start)) if end else len(SRC)
    return SRC[i:j]


# -- the funnel's shape -----------------------------------------------------

ewf = fn("ensure_web_file")
check("_web_playable(src)" in ewf,
      "ensure_web_file no longer asks whether the delivered "
      "file is playable before serving it as itself: a foreign "
      "codec reaches the player raw again")
check("libx264" in ewf and '"aac"' in ewf,
      "ensure_web_file lost its transcode to H.264/AAC: an "
      "AV1/Opus source has no playable conversion anymore")
check('"-c", "copy"' in ewf and '"+faststart"' in ewf,
      "ensure_web_file lost the stream copy remux: healthy "
      "files with the index at the end get re-encoded or "
      "served unplayable on phones")
check("return src" in ewf,
      "ensure_web_file must fall back to the delivered file "
      "when a conversion fails: delivery never breaks")

wc = fn("_web_codecs")
check("ffprobe" in wc and "codec_name" in wc,
      "_web_codecs no longer reads the real stream codecs")
wp = fn("_web_playable")
check('"h264"' in wp and '"aac"' in wp,
      "_web_playable no longer names the phone-decodable "
      "pair (H.264 picture, AAC sound)")

check(SRC.count("ensure_web_file(job, out_path)") == 2,
      "a file route stopped serving the web copy: the "
      "playable guarantee only holds while both the owner "
      "route and the gallery route cross ensure_web_file")

processed = src_slice("def ensure_processed", "def _moov_first")
i = processed.index('if job.get("provider") == "editclone":')
j = processed.index('if job.get("provider") == "assembly":', i)
ec = processed[i:j]
check("return out_path" in ec and "_process_file" not in ec,
      "the editclone branch of ensure_processed changed: "
      "the stored source must stay byte untouched (the "
      "guarantee converts the served copy, never the "
      "original the clone pipeline reads)")

# -- drive the real helpers ---------------------------------------------------

tmp = Path(tempfile.mkdtemp(prefix="playable-pin-"))


class _VolumeStub:
    def reload(self):
        pass

    def commit(self):
        pass


ns = {"Path": Path, "subprocess": subprocess, "json": json,
      "FILES_DIR": tmp, "data_volume": _VolumeStub()}
for name in ("_moov_first", "_web_codecs", "_web_playable",
             "ensure_web_file"):
    exec(fn(name), ns)
ensure_web_file = ns["ensure_web_file"]
web_codecs = ns["_web_codecs"]
moov_first = ns["_moov_first"]


def make_fixture(name, args, out):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=blue:s=64x64:d=1:r=10", "-f", "lavfi", "-i",
         "sine=frequency=440:d=1"] + args + [str(out)],
        check=True, capture_output=True, timeout=300)
    return out


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


# The exact failing shape: AV1 picture, Opus sound — the
# codec pair on both Shorts Jack hit (jobs 3c16b24a1eacf799
# and 7b5e4e136c92052f, probed on the volume 2026-11-28).
av1 = make_fixture("av1", ["-c:v", "libsvtav1", "-preset", "10",
                           "-crf", "50", "-pix_fmt", "yuv420p",
                           "-c:a", "libopus"],
                   tmp / "pinav1.mp4")
check(web_codecs(av1) == ("av1", "opus"),
      f"the AV1 fixture did not come out AV1+Opus "
      f"(got {web_codecs(av1)}): the pin is not testing "
      f"the failing shape")
before = sha(av1)
job = {"id": "pinav1"}
served = ensure_web_file(job, av1)
check(served.name == "pinav1.web.mp4" and served.exists(),
      "an AV1/Opus source is served as itself: its player "
      "sits black on an iPhone")
if served.exists():
    check(web_codecs(served) == ("h264", "aac"),
          f"the web copy of an AV1/Opus source came out "
          f"{web_codecs(served)}, not H.264/AAC")
    check(moov_first(served),
          "the web copy of an AV1/Opus source has its "
          "index at the end: phones stall on a black player")
check(sha(av1) == before,
      "ensure_web_file rewrote the delivered file itself: "
      "the stored original must stay byte untouched")
mtime = served.stat().st_mtime_ns if served.exists() else 0
again = ensure_web_file(job, av1)
check(again == served and served.exists()
      and served.stat().st_mtime_ns == mtime,
      "the web copy is rebuilt on every serve instead of "
      "being cached beside the delivered file")

# Healthy file, index up front: served as itself, no copy.
good = make_fixture("good", ["-c:v", "libx264", "-pix_fmt",
                             "yuv420p", "-c:a", "aac",
                             "-movflags", "+faststart"],
                    tmp / "pingood.mp4")
check(web_codecs(good) == ("h264", "aac") and moov_first(good),
      "the healthy fixture is not H.264/AAC faststart")
check(ensure_web_file({"id": "pingood"}, good) == good
      and not (tmp / "pingood.web.mp4").exists(),
      "a healthy faststart file grew a web copy or stopped "
      "being served as itself")

# Healthy picture, foreign sound: only the sound converts.
mixed = make_fixture("mixed", ["-c:v", "libx264", "-pix_fmt",
                               "yuv420p", "-c:a", "libopus",
                               "-movflags", "+faststart"],
                     tmp / "pinmixed.mp4")
check(web_codecs(mixed) == ("h264", "opus"),
      "the mixed fixture did not come out H.264+Opus")
served_mixed = ensure_web_file({"id": "pinmixed"}, mixed)
check(served_mixed.name == "pinmixed.web.mp4"
      and web_codecs(served_mixed) == ("h264", "aac"),
      "an H.264 file with Opus sound is not converted to "
      "AAC sound for the players")

# Healthy streams, index at the end: the remux only.
tail = make_fixture("tail", ["-c:v", "libx264", "-pix_fmt",
                             "yuv420p", "-c:a", "aac"],
                    tmp / "pintail.mp4")
check(web_codecs(tail) == ("h264", "aac") and not moov_first(tail),
      "the tail fixture did not come out H.264/AAC with "
      "the index at the end")
served_tail = ensure_web_file({"id": "pintail"}, tail)
check(served_tail.name == "pintail.web.mp4"
      and web_codecs(served_tail) == ("h264", "aac")
      and moov_first(served_tail),
      "a healthy file with the index at the end no longer "
      "gets its faststart web copy")

if failures:
    print("check_playable_guarantee: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_playable_guarantee: PASS")
