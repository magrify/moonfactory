"""Local checks for the upload lifecycle and download
bounds (2026-11-27).

Audit findings pinned here:
  * retained-uploads-unbounded (HIGH): upload records
    carry their bytes, the three upload endpoints enforce
    a per-account retained-bytes cap while streaming (with
    the partial file cleaned on refusal), job deletion
    unlinks the upload source through the shared manifest,
    and a scheduled reaper removes consumed sources after a
    grace hour and unconsumed sources after a week.
  * invidious-download-unbounded (MEDIUM): the Invidious
    stream aborts at the caller's byte cap while streaming.
  * clips-download-no-size-bound (MEDIUM): both yt-dlp
    download branches carry max_filesize, and a failed clip
    download cleans its work directory.

Run:  modal-venv python scratch/check_upload_lifecycle.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)
CLIP = (ROOT / "platform" / "clipping.py").read_text()


def func_src(name, tree=TREE, src=SRC):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


for name in ("upload_clip_source", "upload_editclone_source",
             "upload_template_source"):
    body = func_src(name)
    check("_upload_quota_allows(user, size)" in body,
          f"{name} enforces the retained-bytes cap while streaming")
    check('"bytes": size' in body,
          f"{name} records the upload's bytes")
check(bool(func_src("reap_retained_uploads")),
      "the upload reaper exists")
check("retained_upload_reaper" in SRC
      and "modal.Period(hours=6)" in SRC,
      "the reaper runs on a schedule")
check("UPLOAD_TTL_SECONDS = 7 * 24 * 3600" in SRC,
      "unconsumed uploads live a week at most")
check("upload_path" in func_src("_job_file_manifest"),
      "job deletion unlinks the upload source via the manifest")

# --- behavior: the reaper against fakes ---------------------------------------
import tempfile
import time

root = Path(tempfile.mkdtemp())
old_file = root / "old.mp4"
old_file.write_bytes(b"x")
consumed_file = root / "consumed.mp4"
consumed_file.write_bytes(b"x")
active_file = root / "active.mp4"
active_file.write_bytes(b"x")
now = time.time()
meta = {
    "templateupload:old": {"user_id": "u1", "path": str(old_file),
                           "created_at": now - 8 * 24 * 3600},
    "clipupload:done": {"user_id": "u1", "path": str(consumed_file),
                        "created_at": now - 7200},
    "clipupload:active": {"user_id": "u1", "path": str(active_file),
                          "created_at": now - 7200},
    "verify:unrelated": {"user_id": "u9"},
}
jobs = {
    "j1": {"id": "j1", "upload_path": str(consumed_file),
           "status": "done"},
    "j2": {"id": "j2", "upload_path": str(active_file),
           "status": "running"},
}


class FakeDict(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


class Volume:
    def commit(self):
        pass


ns = {"meta": FakeDict(meta), "jobs": FakeDict(jobs),
      "_now": lambda: now, "Path": Path,
      "_user_job_index": lambda uid: {
          "ids": [[j["id"], 0, "video", False] for j in jobs.values()]},
      "data_volume": Volume(),
      "UPLOAD_RECORD_PREFIXES": ("templateupload:", "clipupload:",
                                 "ecupload:"),
      "UPLOAD_TTL_SECONDS": 7 * 24 * 3600,
      "UPLOAD_CONSUMED_GRACE_SECONDS": 3600}
exec(func_src("reap_retained_uploads"), ns)
removed = ns["reap_retained_uploads"]()
check("templateupload:old" in removed and not old_file.exists(),
      "an unconsumed upload past the TTL is reaped")
check("clipupload:done" in removed and not consumed_file.exists(),
      "a consumed upload is reaped after the grace hour")
check("clipupload:active" not in removed and active_file.exists(),
      "an upload an active job still needs is kept")

# --- clipping bounds --------------------------------------------------------------
ctree = ast.parse(CLIP)
inv = func_src("_invidious_download", ctree, CLIP)
check("DownloadTooLarge" in inv and "written > max_bytes" in inv,
      "the Invidious stream aborts at the caller's cap")
dv = func_src("download_video", ctree, CLIP)
da = func_src("_download_any", ctree, CLIP)
# The size bound rides the shared route chain download_video
# delegates to (2026-11-27 link ingest fix); the cleanup on
# failure stays in download_video itself.
check('"max_filesize"' in da and "shutil.rmtree" in dv,
      "the clip download is size bounded and cleans up on failure")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_upload_lifecycle: all checks passed")
