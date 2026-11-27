"""Local checks for the legal hold fixes (2026-11-27).

Audit findings pinned here:
  * admin-restore-resurrects-legal-hold-content (HIGH):
    restore refuses a job under an active hold, gallery
    visibility fails closed under a hold, the vault export
    scrubs the live record's provider references, and the
    only way back is the explicit release flow.
  * admin-remove-repeat-erases-vault-content-key (MEDIUM):
    a records-only repeat export preserves the vault record
    (content key included) instead of nulling it.
  * legal-hold-export-leaves-thumbnail-derivatives and
    delete-job-leaves-file-derivatives (MEDIUM/LOW): one
    shared per-job file manifest drives the export's
    plaintext removal (with post-removal verification) and
    job deletion, thumbnails, poster and GIF included.

Run:  modal-venv python scratch/check_legal_hold_restore.py
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


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


restore = func_src("admin_gallery_restore")
check("legal_hold_active" in restore,
      "restore consults the legal hold state")
check("409" in restore, "restore refuses a held job")
check(bool(func_src("admin_gallery_release_hold")),
      "an explicit hold release flow exists")
check("legal_hold_active" in func_src("gallery_visible"),
      "gallery visibility fails closed under a hold")

export = func_src("_legal_hold_vault_export")
check("existing_vault" in export
      and 'existing_vault.get("content_key")' in export,
      "a records-only repeat export preserves the vault record")
check("_unlink_job_files" in export and "survivors" in export,
      "the export removes the full manifest and verifies "
      "absence before logging the guarantee")
check("result_url" in export and "worker_job_id" in export,
      "the export scrubs the live record's provider references")

delete = func_src("delete_job")
check("_unlink_job_files" in delete,
      "job deletion removes the shared manifest")
check("legal_hold_active" in delete,
      "job deletion preserves a held job's files")

# --- behavior: the manifest and the hold predicate ------------------------------
import tempfile

FILES_DIR = Path(tempfile.mkdtemp())
ns = {"FILES_DIR": FILES_DIR, "Path": Path,
      "_JOB_FILE_SUFFIXES": (
          ".mp4", ".web.mp4", ".raw.mp4", ".png", ".jpg",
          ".gthumb.jpg", ".gthumb.tmp.jpg", ".poster.tmp.jpg",
          ".gif", ".carry.png")}
exec(func_src("_job_file_manifest"), ns)
exec(func_src("_unlink_job_files"), ns)
names = {p.name for p in ns["_job_file_manifest"]("j1")}
for want in ("j1.mp4", "j1.web.mp4", "j1.raw.mp4", "j1.png",
             "j1.jpg", "j1.gthumb.jpg", "j1.gif", "j1.carry.png",
             "j1.wav", "j1.source.wav"):
    check(want in names, f"the manifest covers {want}")

holds = {"j1": {"job_id": "j1"},
         "j2": {"job_id": "j2", "released": True}}


class _FakeDict:
    def get(self, key, default=None):
        return holds.get(key, default)


ns2 = {"legal_holds": _FakeDict()}
exec(func_src("legal_hold_active"), ns2)
check(ns2["legal_hold_active"]("j1") is True,
      "an unreleased hold is active")
check(ns2["legal_hold_active"]("j2") is False,
      "a released hold is not active")
check(ns2["legal_hold_active"]("nope") is False,
      "no hold record means no hold")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_legal_hold_restore: all checks passed")
