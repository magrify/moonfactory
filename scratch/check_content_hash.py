"""Local checks for content fingerprinting, deletion tombstones,
legal holds, and creation IP capture (2026-11-24).

Jack approved automatic SHA-256 fingerprinting of every
generation: when a generation finishes, the SHA-256 of the
canonical stored ORIGINAL file is stamped on the job record as
content_sha256 (never the <id>.web.mp4 phone remux), hashing
failures never block delivery, and there is no backfill. The
fingerprint must survive removal: gallery hiding is display-only,
and user deletion leaves a minimal tombstone (job id, account id,
creation timestamp, model/lane, content_sha256 — nothing else).
For illegal-content removals a legal hold keeps the full
18 U.S.C. 2258A(b) case file instead, and the creating request's
IP is now stamped on the job record at creation (created_ip).

No Modal, no network, no generation: the stamp, tombstone and
hold helpers are extracted from platform/app.py and exercised
against fixture files and fake Dict stores, the public gallery
payload builder runs against a stamped fixture job, and the
placement of every call site is asserted in app.py source.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/check_content_hash.py
"""
import ast
import hashlib
import json
import sys
import tempfile
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


def func_src(name, tree=TREE, src=SRC):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"function not found: {name}")


def exec_func(name, namespace):
    ns = dict(namespace)
    exec(func_src(name), ns)
    return ns[name]


# ------------------------------------------------------------------
# Fixture stores and helpers, extracted from app.py
# ------------------------------------------------------------------

TMP = Path(tempfile.mkdtemp(prefix="mf-hash-"))
FILES = TMP / "files"
FILES.mkdir()

fake_jobs = {}
fake_tombstones = {}
fake_holds = {}
fake_users = {}

stamp = exec_func("stamp_content_hash", {
    "hashlib": hashlib, "Path": Path, "jobs": fake_jobs,
})
write_tombstone = exec_func("write_job_tombstone", {
    "tombstones": fake_tombstones,
})


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


ORIGINAL = b"original-generation-bytes" * 4096
REMUX = b"faststart-remux-bytes" * 4096

# ------------------------------------------------------------------
# 1. The helper stamps the correct SHA-256 of the original bytes
# ------------------------------------------------------------------

orig_path = FILES / "job0001.mp4"
orig_path.write_bytes(ORIGINAL)
job = {"id": "job0001", "status": "done", "kind": "video"}
fake_jobs["job0001"] = job
stamp(job, orig_path)
check(job.get("content_sha256") == sha(ORIGINAL),
      "stamp_content_hash stamps the SHA-256 of the original bytes")
check(fake_jobs["job0001"].get("content_sha256") == sha(ORIGINAL),
      "the stamp is saved back onto the job record")

# ------------------------------------------------------------------
# 2. A remux/derivative never changes the stamp
# ------------------------------------------------------------------

web_path = FILES / "job0001.web.mp4"
web_path.write_bytes(REMUX)
stamp(job, web_path)
check(job.get("content_sha256") == sha(ORIGINAL),
      "stamping from a .web.mp4 path is refused: the stamp survives the remux")

fresh = {"id": "job0002", "status": "done", "kind": "video"}
fake_jobs["job0002"] = fresh
stamp(fresh, FILES / "job0002.web.mp4")
check("content_sha256" not in fresh,
      "a fresh job stamped only from a remux path stays unstamped")

# ------------------------------------------------------------------
# 3. Hashing failure never blocks delivery: no raise, no stamp
# ------------------------------------------------------------------

ghost = {"id": "job0003", "status": "done", "kind": "video"}
fake_jobs["job0003"] = ghost
try:
    stamp(ghost, FILES / "job0003.mp4")          # file does not exist
    stamp(ghost, FILES)                          # a directory, not a file
    stamp(ghost, FILES / "job0003.web.mp4")      # missing remux
    ok = True
except Exception:
    ok = False
check(ok, "stamp_content_hash never raises on missing or bad paths")
check("content_sha256" not in ghost,
      "a job whose file never landed gets no stamp")

# ------------------------------------------------------------------
# 4. Re-stamping after the bytes change updates the hash
#    (short-drama retake semantics)
# ------------------------------------------------------------------

RETAKE = b"retaken-generation-bytes" * 2048
orig_path.write_bytes(RETAKE)
stamp(job, orig_path)
check(job.get("content_sha256") == sha(RETAKE),
      "re-stamping after a retake updates the hash to the new bytes")

# ------------------------------------------------------------------
# 5. Ordinary deletion tombstone: exactly six keys, nothing else
# ------------------------------------------------------------------

tjob = {
    "id": "job0010", "user_id": "user-1", "created_at": 1700000000,
    "model": "wan2.2", "lane": "wan2.2", "kind": "video",
    "prompt": "a prompt that must NOT survive deletion",
    "content_sha256": sha(ORIGINAL),
}
write_tombstone(tjob)
tomb = fake_tombstones.get("job0010")
check(tomb is not None, "a stamped job leaves a tombstone on deletion")
check(tomb and set(tomb.keys()) == {
    "id", "user_id", "created_at", "model", "lane", "content_sha256"},
    "the tombstone holds exactly the six minimal fields")
check(tomb and tomb["content_sha256"] == sha(ORIGINAL),
      "the tombstone carries the job's fingerprint")
check(tomb and "prompt" not in tomb,
      "the tombstone carries no prompt text")

unstamped = {"id": "job0011", "user_id": "user-1",
             "created_at": 1700000001, "model": "wan2.2"}
write_tombstone(unstamped)
check("job0011" not in fake_tombstones,
      "an unstamped job leaves no tombstone")

img_job = {
    "id": "job0012", "user_id": "user-2", "created_at": 1700000002,
    "image_model": "flux_schnell", "kind": "image",
    "content_sha256": sha(RETAKE),
}
write_tombstone(img_job)
check(fake_tombstones["job0012"]["model"] == "flux_schnell"
      and fake_tombstones["job0012"]["lane"] == "flux_schnell",
      "an image job's tombstone falls back to image_model for model and lane")

# ------------------------------------------------------------------
# 6. The public gallery payload carries no fingerprint
# ------------------------------------------------------------------

sys.path.insert(0, str(ROOT / "platform"))
import pricing  # noqa: E402  (the real pricing module, as other checks use)

display_handle = exec_func("display_handle", {})
gallery_item = exec_func("public_gallery_item", {
    "users": fake_users, "display_handle": display_handle,
    "pricing": pricing, "MODEL_LABELS": {}, "DEFAULT_MODEL": "wan2.2",
    "HOUSE_IMAGE_LABEL": "Z-Image Turbo",
})
fake_users["id:user-9"] = {"id": "user-9", "handle": "tester"}
pub_job = {
    "id": "job0020", "user_id": "user-9", "kind": "video",
    "status": "done", "model": "wan2.2", "lane": "wan2.2",
    "created_at": 1700000003,
    "prompt": "a cat on a porch", "content_sha256": sha(ORIGINAL),
    "created_ip": "203.0.113.7", "gallery_public": True,
}
payload = gallery_item(pub_job)
check("content_sha256" not in payload,
      "public_gallery_item never carries content_sha256")
check("created_ip" not in payload,
      "public_gallery_item never carries created_ip")

# ------------------------------------------------------------------
# 7. Gallery hiding is display-only: nothing but the stamp helper
#    writes content_sha256, and the visibility flag never reads it
# ------------------------------------------------------------------

writer_funcs = set()
for node in ast.walk(TREE):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Subscript) \
                    and isinstance(sub.ctx, ast.Store) \
                    and isinstance(sub.slice, ast.Constant) \
                    and sub.slice.value == "content_sha256":
                writer_funcs.add(node.name)
check(writer_funcs == {"stamp_content_hash"},
      f"job['content_sha256'] is written only inside stamp_content_hash "
      f"(found: {sorted(writer_funcs)})")
check("content_sha256" not in func_src("gallery_visible"),
      "gallery_visible never touches the fingerprint: hiding is display-only")

# ------------------------------------------------------------------
# 8. Legal hold: the full 2258A(b) case file, surviving deletion
# ------------------------------------------------------------------

hold_job = {
    "id": "job0030", "user_id": "user-7", "kind": "video",
    "status": "done", "model": "wan2.2", "lane": "wan2.2",
    "prompt": "the prompt exactly as typed",
    "created_at": 1700000010, "charged_cents": 42,
    "has_start_image": True, "content_sha256": sha(RETAKE),
    "created_ip": "198.51.100.23",
}
sibling = {"id": "job0031", "user_id": "user-7", "kind": "image",
           "status": "done"}
stranger = {"id": "job0032", "user_id": "user-8", "kind": "video",
            "status": "done"}
fake_jobs.update({"job0030": hold_job, "job0031": sibling,
                  "job0032": stranger})
fake_users["id:user-7"] = {
    "id": "user-7", "email": "casefile@example.com",
    "created_at": 1690000000, "plan": "creator",
    "handle": "casefile", "verified": True,
}
place_hold = exec_func("place_legal_hold", {
    "users": fake_users, "jobs": fake_jobs, "legal_holds": fake_holds,
    "FILES_DIR": FILES, "stamp_content_hash": stamp,
    "json": json, "_now": lambda: 1700000099,
})
place_hold(hold_job, "admin_gallery_remove")
hold = fake_holds.get("job0030")
check(hold is not None, "place_legal_hold creates a hold record")
check(hold and hold["job"]["prompt"] == "the prompt exactly as typed",
      "the hold keeps the complete job record, prompt as typed")
check(hold and hold["job"]["has_start_image"] is True
      and hold["job"]["charged_cents"] == 42
      and hold["job"]["created_ip"] == "198.51.100.23",
      "the hold keeps input flags, charge data, and the creation IP")
check(hold and hold["account"]["email"] == "casefile@example.com"
      and hold["account"]["created_at"] == 1690000000
      and hold["account"]["plan"] == "creator",
      "the hold keeps the account identifiers: email, signup time, plan")
check(hold and hold["content_sha256"] == sha(RETAKE),
      "the hold carries the content fingerprint")
check(hold and hold["sibling_job_ids"] == ["job0031"],
      "the hold lists only the same account's other jobs for review")
check(hold and hold["actions"]
      and hold["actions"][0]["trigger"] == "admin_gallery_remove"
      and hold["actions"][0]["at"] == 1700000099,
      "the hold's action log records what triggered it and when")
check(hold and "cybertipline_report_number" in hold
      and hold["cybertipline_report_number"] is None
      and "ncmec_confirmation_date" in hold,
      "the hold has fields for the CyberTipline number and NCMEC date")

# A repeat hold appends to the action log instead of replacing it.
place_hold(hold_job, "admin_gallery_remove")
check(len(fake_holds["job0030"]["actions"]) == 2
      and fake_holds["job0030"]["held_at"] == 1700000099,
      "a repeat hold appends to the action log and keeps the first held_at")

# Deleting the job leaves the hold untouched (and the tombstone too).
del fake_jobs["job0030"]
write_tombstone(hold_job)
check(fake_holds["job0030"]["job"]["prompt"]
      == "the prompt exactly as typed",
      "the hold record survives deletion of the job")
check(fake_tombstones["job0030"]["content_sha256"] == sha(RETAKE),
      "the ordinary tombstone is also left for a held job")

# A hold on an unstamped finished job stamps it from its file first.
late_path = FILES / "job0040.mp4"
late_path.write_bytes(ORIGINAL)
late_job = {"id": "job0040", "user_id": "user-7", "kind": "video",
            "status": "done", "model": "wan2.2", "prompt": "late stamp"}
fake_jobs["job0040"] = late_job
place_hold(late_job, "admin_gallery_remove")
check(fake_holds["job0040"]["content_sha256"] == sha(ORIGINAL),
      "a hold backstop-stamps a finished job from its stored original")

# ------------------------------------------------------------------
# 9. Source pins: stores, hooks, placements, non-exposure
# ------------------------------------------------------------------

check('modal.Dict.from_name("moonfactory-tombstones"' in SRC,
      "the tombstones Dict is declared")
check('modal.Dict.from_name("moonfactory-legal-holds"' in SRC,
      "the legal holds Dict is declared")

delete_src = func_src("delete_job")
check("write_job_tombstone(child)" in delete_src
      and delete_src.index("write_job_tombstone(child)")
      < delete_src.index("del jobs[cid]"),
      "delete_job tombstones each drama child before deleting it")
check("write_job_tombstone(job)" in delete_src
      and delete_src.index("write_job_tombstone(job)")
      < delete_src.index("del jobs[job_id]"),
      "delete_job tombstones the job before deleting it")
check("legal_holds" not in delete_src,
      "delete_job never touches legal holds: holds are exempt from deletion")
check("stamp_content_hash" in delete_src,
      "delete_job backstop-stamps unstamped finished jobs before tombstoning")

check("stamp_content_hash" not in func_src("ensure_web_file"),
      "the web remux path never stamps: the remux cannot overwrite the hash")

admin_remove_src = func_src("admin_gallery_remove")
check('place_legal_hold(job, "admin_gallery_remove")' in admin_remove_src,
      "the admin removal path places a legal hold")

for name in ("public_gallery_item", "gallery_list", "list_jobs",
             "public_job"):
    s = func_src(name)
    check("content_sha256" not in s and "created_ip" not in s
          and "tombstone" not in s and "legal_holds" not in s,
          f"{name} exposes no fingerprint, IP, tombstone, or hold data")

# Call-site placements: the stamp lands after the last step that
# can change the finished file, on every creation path.
proc_src = func_src("ensure_processed")
check("_captions_for_job(job, out_path)" in proc_src
      and "stamp_content_hash(job, out_path)" in proc_src
      and proc_src.index("_captions_for_job(job, out_path)")
      < proc_src.index("stamp_content_hash(job, out_path)"),
      "ensure_processed stamps after captions, the last finishing step")
short_src = func_src("process_short_job")
check("_short_word_check(job, out_path)" in short_src
      and "stamp_content_hash(job, out_path)" in short_src
      and short_src.index("_short_word_check(job, out_path)")
      < short_src.index("stamp_content_hash(job, out_path)"),
      "process_short_job stamps after the word check, which can swap the file")
for name in ("hf_job_file", "_drama_finish_episode",
             "_submit_cloudflare_image", "_submit_ovhcloud_image",
             "_settle_provider_image", "ensure_image_file",
             "process_clip_job"):
    check("stamp_content_hash" in func_src(name),
          f"{name} stamps its finished file")
comfy_src = func_src("comfy_job_file")
check(comfy_src.count("stamp_content_hash") == 1
      and "stamp_content_hash(job, out_path)" in comfy_src,
      "comfy_job_file stamps exactly once, its image branch (video raws "
      "stay unstamped intermediates, finished by ensure_processed)")

# ------------------------------------------------------------------
# 10. IP capture pins: every creation path stamps created_ip,
#     through the existing client_ip helper (x-forwarded-for first)
# ------------------------------------------------------------------

client_ip_src = func_src("client_ip")
check("x-forwarded-for" in client_ip_src,
      "client_ip follows the existing forwarded-for handling")
check('"created_ip": created_ip or None' in func_src("create_api_job")
      and "created_ip: str" in func_src("create_api_job"),
      "create_api_job records created_ip from its parameter")
for name in ("create_job", "create_image", "create_clip",
             "create_template", "create_short", "create_drama"):
    check('"created_ip": client_ip(request)' in func_src(name),
          f"{name} stamps created_ip from the creating request")
check('"created_ip": parent.get("created_ip")'
      in func_src("_drama_create_shot_job"),
      "drama shot children inherit the parent episode's creation IP")
check('"created_ip": parent.get("created_ip")'
      in func_src("_queue_clone_child"),
      "voice clone children inherit the parent job's creation IP")
check('"created_ip": job.get("created_ip")' in func_src("_short_ai_scenes"),
      "shorts picture children inherit the short's creation IP")
check(SRC.count("created_ip=client_ip(request)") == 2,
      "both create_api_job call sites pass the request IP "
      "(video create route and drama retake route)")

# ------------------------------------------------------------------

if failures:
    print(f"FAIL ({len(failures)}):")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_content_hash: all checks passed")
