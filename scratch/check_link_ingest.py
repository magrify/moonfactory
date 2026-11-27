"""Local checks for the link ingest route chain (2026-11-27).

Pins the shape of the paste-a-link fix so a later edit cannot
quietly break it:

- the CPU media image carries the pinned fetch stack:
  yt-dlp[default,curl-cffi]==2026.11.23, the bgutil PO token
  plugin at 2.0.2 with its provider source cloned at the same
  tag into /opt/bgutil, and Deno pinned at v2.9.8;
- YouTube resolves through the yt-dlp route chain first
  (default clients, then impersonation, then the mweb client
  with a bgutil script PO token pointed at /opt/bgutil/server)
  and Invidious remains, as the last resort only;
- with a fake yt_dlp module standing in for the extractor, a
  Shorts URL answers metadata from the chain, a download lands
  its file and the Clone duration cap still refuses an
  over-long source, a route that only answers with the PO
  token configuration proves the chain walks to it, and total
  failure (chain down, Invidious down) raises the plain
  LINK_DEAD ValueError the ingest endpoint turns into its 422;
- the upload path is untouched: the upload endpoint and its
  meta record are still in app.py, and the fetch functions
  keep their signatures;
- the cross-container retry layer is in place: the ingest
  probe fans out over parallel fresh containers (YouTube's
  answer is a per-container egress draw) and a dead-link
  download failure re-dispatches the ingest job to a fresh
  container a bounded number of times.

No Modal calls, no network beyond the URL guards' own DNS
checks (the same ones check_editclone_section already makes),
no generation.

Run:  modal-venv python scratch/check_link_ingest.py
"""
import inspect
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import clipping  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
CLIP_SRC = (ROOT / "platform" / "clipping.py").read_text()
NOTICES = (ROOT / "THIRD-PARTY-NOTICES.md").read_text()

# -- the pinned fetch stack in the image ---------------------------------

for needle in ("yt-dlp[default,curl-cffi]==2026.11.23",
               "bgutil-ytdlp-pot-provider==2.0.2",
               "dl.deno.land/release/v2.9.8",
               "bgutil-ytdlp-pot-provider.git",
               "--branch 2.0.2",
               "/opt/bgutil"):
    check(needle in SRC, f"the media image lost a pin: {needle}")
check("bgutil" in NOTICES and "yt-dlp" in NOTICES,
      "THIRD-PARTY-NOTICES.md does not carry the fetch stack")
check(clipping.YTDLP_POT_SERVER_HOME == "/opt/bgutil/server",
      "the PO token provider home moved off /opt/bgutil/server")

# -- the chain's shape -----------------------------------------------------

check(len(clipping._ytdlp_overlays(True)) == 3,
      "the YouTube chain is not three routes deep")
check(len(clipping._ytdlp_overlays(False)) == 2,
      "the any-site chain is not two routes deep")
pot_overlay = clipping._ytdlp_overlays(True)[-1]
check(pot_overlay["extractor_args"]["youtube"]["player_client"]
      == ["mweb"],
      "the last YouTube route is not the mweb client")
check(pot_overlay["extractor_args"]["youtubepot-bgutilscript"]
      ["server_home"] == ["/opt/bgutil/server"],
      "the PO token route does not point at the provider source")
check(isinstance(clipping._ytdlp_impersonate(), dict),
      "the impersonation layer does not degrade to an option dict")
for fn_name in ("fetch_info", "fetch_any_info"):
    fn_src = inspect.getsource(getattr(clipping, fn_name))
    check("_extract_info_any" in fn_src
          and "_invidious_info" in fn_src,
          f"{fn_name} does not walk the chain before Invidious")
for fn_name in ("download_video", "download_any_video"):
    fn_src = inspect.getsource(getattr(clipping, fn_name))
    check("_download_any" in fn_src
          and "_invidious_download" in fn_src,
          f"{fn_name} does not walk the chain before Invidious")
check(list(inspect.signature(clipping.fetch_any_info).parameters)
      == ["url"],
      "fetch_any_info changed its signature")
check(list(inspect.signature(clipping.download_any_video)
           .parameters)
      == ["url", "job_dir", "max_seconds", "max_bytes"],
      "download_any_video changed its signature")

# -- the cross-container retry layer in app.py ----------------------------

# Whether YouTube answers is a per-container egress draw, so
# the probe fans out and the download re-dispatches; pin both
# so a later edit cannot quietly return ingest to a coin flip.
check("_editclone_probe_any" in SRC
      and "editclone_probe.spawn(url)" in SRC
      and "EDITCLONE_PROBE_ATTEMPTS" in SRC,
      "the parallel ingest probe fan-out is gone")
check("info = _editclone_probe_any(source_url)" in SRC,
      "the ingest endpoint no longer reads through the fan-out")
check("ec_fetch_attempts" in SRC
      and "EDITCLONE_FETCH_ATTEMPTS" in SRC
      and "process_editclone_job.spawn(job_id)" in SRC
      and "if not redispatched:" in SRC,
      "the fresh-container download re-dispatch is gone")

# -- the upload path is untouched ------------------------------------------

check('"/api/editclone/upload"' in SRC and '"ecupload:"' in SRC,
      "the Edit/Clone upload route or its record moved")

# -- behaviour with a fake extractor ---------------------------------------

# The sandbox resolver answers every name with the egress
# proxy's 198.18/15 addresses, which ipaddress does not count
# as global; stand in a public DNS answer so the behaviour
# checks test the chain, not the sandbox's resolver (the same
# stand-in check_editclone_section uses). Production resolvers
# return the real records.
import socket as _socket
clipping.socket.getaddrinfo = lambda host, port, *a, **k: [  # noqa: E731
    (_socket.AF_INET, _socket.SOCK_STREAM, 6, "",
     ("93.184.216.34", 0))]

SHORT = "https://www.youtube.com/shorts/5gqrla4k4to"
TIKTOK = "https://www.tiktok.com/@zachking/video/6768504823336815877"

seen_opts = []
mode = {"fail_until": None, "probe_duration": 10.0}


class FakeYDL:
    def __init__(self, opts):
        self.opts = opts
        seen_opts.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _gate(self):
        """Fail until the configured route shows up, per mode."""
        until = mode["fail_until"]
        if until is None:
            return
        ea = self.opts.get("extractor_args") or {}
        if until == "remote_components" and \
                "remote_components" not in self.opts:
            raise RuntimeError("bot wall")
        if until == "pot" and "youtubepot-bgutilscript" not in ea:
            raise RuntimeError("bot wall")

    def extract_info(self, url, download=False):
        self._gate()
        return {"title": "Bro threw in a fun fact #shorts",
                "uploader": "someone", "duration": 10,
                "webpage_url": url, "formats": []}

    def download(self, urls):
        self._gate()
        out = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 2048)


fake = types.ModuleType("yt_dlp")
fake.YoutubeDL = FakeYDL
real_ytdlp = sys.modules.get("yt_dlp")
sys.modules["yt_dlp"] = fake
real_probe_media = clipping.probe_media
clipping.probe_media = lambda p: {  # noqa: E731 - test double
    "width": 720, "height": 1280,
    "duration": mode["probe_duration"]}
try:
    # The chain answers a Shorts URL on the first route.
    seen_opts.clear()
    info = clipping.fetch_any_info(SHORT)
    check(info["title"] == "Bro threw in a fun fact #shorts"
          and info["duration"] == 10.0,
          f"the chain did not answer the Shorts metadata: {info}")
    check(len(seen_opts) == 1,
          "the first route answered but the chain kept walking")

    # A wall on the plain routes walks to the impersonation route.
    mode["fail_until"] = "remote_components"
    seen_opts.clear()
    info = clipping.fetch_any_info(SHORT)
    check(info["duration"] == 10.0 and len(seen_opts) == 2,
          "the chain did not fall through to impersonation")

    # A wall everywhere but the PO token route walks to it.
    mode["fail_until"] = "pot"
    seen_opts.clear()
    info = clipping.fetch_any_info(SHORT)
    check(info["duration"] == 10.0 and len(seen_opts) == 3,
          "the chain did not fall through to the PO token route")
    check(seen_opts[-1]["extractor_args"]
          ["youtubepot-bgutilscript"]["server_home"]
          == ["/opt/bgutil/server"],
          "the PO token attempt lost the provider home")

    # A non-YouTube link answers through the same machinery.
    mode["fail_until"] = None
    info = clipping.fetch_any_info(TIKTOK)
    check(info["duration"] == 10.0,
          "the any-site info path broke for a TikTok link")

    # A download lands its file inside the caps.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        dest = clipping.download_any_video(
            SHORT, Path(td), max_seconds=600, max_bytes=1024 ** 3)
        check(dest.exists() and dest.name == "source.mp4",
              f"the download did not land: {dest}")
        # And the duration cap still refuses an over-long source.
        mode["probe_duration"] = 700.0
        try:
            clipping.download_any_video(
                SHORT, Path(td), max_seconds=600,
                max_bytes=1024 ** 3)
            check(False, "an over-long source was accepted")
        except ValueError as exc:
            check("longer than the Clone limit" in str(exc),
                  f"the duration cap answered wrong: {exc}")
        mode["probe_duration"] = 10.0

    # Total failure: chain down and Invidious down is the plain
    # dead-link ValueError, word for word.
    mode["fail_until"] = "pot"

    class DeadYDL(FakeYDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("bot wall")

        def download(self, urls):
            raise RuntimeError("bot wall")

    fake.YoutubeDL = DeadYDL
    real_inv_info = clipping._invidious_info
    clipping._invidious_info = lambda vid: (_ for _ in ()).throw(
        ValueError(clipping.LINK_DEAD_MESSAGE))
    try:
        clipping.fetch_any_info(SHORT)
        check(False, "a dead link was answered")
    except ValueError as exc:
        check(str(exc) == clipping.LINK_DEAD_MESSAGE,
              f"total failure is not the plain message: {exc}")
    finally:
        clipping._invidious_info = real_inv_info
finally:
    clipping.probe_media = real_probe_media
    if real_ytdlp is not None:
        sys.modules["yt_dlp"] = real_ytdlp
    else:
        sys.modules.pop("yt_dlp", None)

if failures:
    print("FAIL check_link_ingest")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_link_ingest")
