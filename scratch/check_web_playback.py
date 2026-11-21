"""Videos must play in phone browsers (Jack, 2026-11-21: a short
he had just generated sat on a black player in Chrome on iOS).

Root cause pinned against his actual file: the delivered mp4
carried its index (moov) at the very end, behind 7.8 MB of media
data, and the file route served it as an attachment download.
Phone browsers will not start that inline. The fix is pinned
here: players are served a web copy with the index up front,
inline; the library and clip cards carry a poster and
playsinline; shorts are assembled faststart from birth.
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
need(SRC.count('playsinline preload="metadata" poster="/api/jobs/') == 2,
     "library and clip cards carry poster and playsinline")
need('"-movflags", "+faststart",' in SHORTS,
     "shorts assembly writes faststart from birth")
need('f"{job_id}.web.mp4"' in SRC,
     "delete cleans up the web copy")

if fails:
    print("FAIL")
    for f in fails:
        print(" -", f)
    sys.exit(1)
print("PASS check_web_playback")
