"""Pin for the SSRF and media cluster (security fixes,
2026-11-27), exercised against the real clipping module.

- ingest-ssrf-encoded-ip-no-dns-no-redirect-check:
  validate_ingest_url validates the RESOLVED address, so
  the inet_aton encodings the audit reproduced (127.1,
  2130706433, 0x7f000001, 0177.0.0.1, 0, 3232235521,
  2852039166, 167772165) are refused, and yt-dlp paths
  re-validate the final URL after redirects.
- media-decode-container-duration-lie: probe_media (and
  probe) measure duration from the stream's packets, so a
  Matroska header patched to declare 2 seconds over a real
  20 second file probes at the real length; the editclone
  ingest transcode carries -t at the Clone limit.
- media-decode-no-dimension-bound-before-ffmpeg-decode:
  the probes refuse frames over the photo path's 64M pixel
  cap or over 120 fps before any decode; make_poster
  scales its output.
- cf-source-bytes-user-url-fetch (source pin): the fetch
  validates the resolved host, follows redirects by hand
  with re-validation, refuses https to http downgrades,
  and streams under a byte cap.
"""
import ast
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "platform"))
import clipping  # noqa: E402

APP = (ROOT / "platform" / "app.py").read_text()


def check(label, cond):
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"ok: {label}")


def refused(url):
    try:
        clipping.validate_ingest_url(url)
        return False
    except ValueError:
        return True


# The audit's reproduced encodings, plus dotted controls.
for form in ("127.1", "2130706433", "0x7f000001", "0177.0.0.1",
             "0", "3232235521", "2852039166", "167772165",
             "127.0.0.1", "10.0.0.5", "169.254.169.254",
             "192.168.1.1", "localhost"):
    check(f"ingest refuses {form}",
          refused(f"https://{form}/video"))
check("ingest refuses a name that resolves privately",
      refused("https://localtest.me/video")
      or refused("https://127.0.0.1.nip.io/video"))
# A public literal passes without any DNS dependency.
check("ingest allows a public literal",
      clipping.validate_ingest_url("https://8.8.8.8/video")
      == "https://8.8.8.8/video")

CLIP = (ROOT / "platform" / "clipping.py").read_text()
check("yt-dlp paths re-validate the final URL",
      CLIP.count("validate_final_url(") >= 3)

# The duration lie, reproduced with a real patched file.
tmp = Path(tempfile.mkdtemp(prefix="mf-lietest-"))
real = tmp / "real.mkv"
subprocess.run(
    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
     "testsrc=duration=20:size=320x240:rate=10",
     "-c:v", "libx264", "-pix_fmt", "yuv420p",
     str(real), "-y"], check=True)
data = bytearray(real.read_bytes())
idx = data.find(b"\x44\x89")
assert idx > 0 and data[idx + 2] == 0x88
data[idx + 3:idx + 11] = struct.pack(">d", 2000.0)
lie = tmp / "lie.mkv"
lie.write_bytes(bytes(data))
info = clipping.probe_media(lie)
check("a duration-lying container probes at its real length",
      info["duration"] > 15.0)
info = clipping.probe_media(real)
check("an honest container still probes correctly",
      19.0 < info["duration"] < 21.0)

# The dimension bound at probe time.
huge = tmp / "huge.mkv"
subprocess.run(
    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
     "testsrc=duration=1:size=16000x16000:rate=1",
     "-frames:v", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
     str(huge), "-y"], check=True)
try:
    clipping.probe_media(huge)
    check("a 16000x16000 file is refused at probe time", False)
except RuntimeError as exc:
    check("a 16000x16000 file is refused at probe time",
          "too large" in str(exc))

check("make_poster scales its output",
      'scale=w=' in CLIP)
check("the editclone ingest transcode carries the -t bound",
      APP.count('"-t", str(EDITCLONE_MAX_SOURCE_SECONDS)') == 2)

# cf source fetch guards (source pin; the function rides the
# web function's request machinery).
tree = ast.parse(APP)
seg = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) \
            and node.name == "_cf_source_bytes":
        seg = ast.get_source_segment(APP, node)
check("cf source fetch validates the resolved host",
      "assert_public_host" in seg)
check("cf source fetch follows redirects by hand",
      "allow_redirects=False" in seg)
check("cf source fetch streams under a byte cap",
      "iter_content" in seg and "CF_SOURCE_MAX_BYTES" in seg)

# editclone frames are judged before hosting.
seg = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) \
            and node.name == "_editclone_source_frame_urls":
        seg = ast.get_source_segment(APP, node)
check("editclone frames are classified before hosting",
      "classify_input_images" in seg
      and "input_gate_refusal" in seg)

print("check_ssrf_media: all pins hold")
