"""Videos must play in phone browsers (Jack, 2026-11-21: a short
he had just generated sat on a black player in Chrome on iOS).

Root cause pinned against his actual file: the delivered mp4
carried its index (moov) at the very end, behind 7.8 MB of media
data, and the file route served it as an attachment download.
Phone browsers will not start that inline. The fix is pinned
here: players are served a web copy with the index up front,
inline; players carry a poster and playsinline; shorts are
assembled faststart from birth.

Superseded in part 2026-11-24 (Jack): listings are thumbnails
only — a card carries NO video element and fetches no media
until the click, so the old pin on preload="metadata" in card
markup is gone on his explicit order. What the Nov 21 fix
actually guaranteed survives untouched: the file routes serve
the faststart web copy inline, and the player the click builds
carries playsinline and the poster and plays the web copy.
The pins below assert both halves: no eager media anywhere in
a listing, and the click-built player keeps the old shape.
"""
import sys

SRC = open("platform/app.py").read()
SHORTS = open("platform/shorts.py").read()

fails = []


def need(cond, label):
    if not cond:
        fails.append(label)


need("def _moov_first(path: Path) -> bool:" in SRC,
     "_moov_first helper exists")
need("def ensure_web_file(job: dict, src: Path) -> Path:" in SRC,
     "ensure_web_file exists")
need('"-movflags", "+faststart"' in SRC,
     "web remux moves the index up front")
need(SRC.count("ensure_web_file(job, out_path)") == 2,
     "both file routes (own library and gallery) serve the web copy")
need(SRC.count('resp.headers["content-disposition"]') == 2,
     "both file routes serve inline, not attachment")
need('"/api/jobs/{job_id}/poster"' in SRC,
     "authed poster route for own jobs exists")
need('preload="metadata"' not in SRC,
     "no listing preloads media metadata (thumbnails only)")
need(SRC.count('data-play="') == 2,
     "library and clip cards render a thumbnail play box, "
     "not a video element")
need("v.setAttribute('playsinline', '')" in SRC
     and "v.poster = '/api/jobs/' + id + '/poster'" in SRC
     and "v.src = '/api/jobs/' + id + '/file'" in SRC
     and "play.replaceWith(v)" in SRC,
     "the click builds the player with playsinline, the "
     "poster, and the file route, and swaps it in")
need('data-open="' in SRC,
     "image cards open the full picture only on the click")
need('"-movflags", "+faststart",' in SHORTS,
     "shorts assembly writes faststart from birth")
need('".web.mp4"' in SRC and "_unlink_job_files" in SRC,
     "delete cleans up the web copy")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS check_web_playback")
