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

The hold's preservation is fully automated (Jack, 2026-11-24):
placing a hold seals the original file and a JSON copy of the
case record under the vault public key (PyNaCl SealedBox) and
uploads the ciphertext to the dedicated private R2 bucket
moonfactory-legal-holds; a verified export on a removal deletes
the plaintext from the platform volume; a daily sweep destroys
vaulted content one year past the CyberTipline confirmation
date unless a law-enforcement hold flag is set. The vault
transport is faked here (a dict bucket), the seal is real
(a throwaway keypair), and the app itself never holds the
private key.

No Modal, no network, no generation: the stamp, tombstone and
hold helpers are extracted from platform/app.py and exercised
against fixture files and fake Dict stores, the public gallery
payload builder runs against a stamped fixture job, and the
placement of every call site is asserted in app.py source.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/check_content_hash.py
"""
import ast
import base64
import datetime
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from nacl.public import PrivateKey, SealedBox

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


# place_legal_hold reads sibling ids off the per-user job
# index (perf pass 3), so its namespaces carry the index
# helpers and their stores.
INDEX_FUNCS = [
    "_job_index_entry", "_index_job", "_unindex_job",
    "_job_index_ensure", "_user_job_index", "_indexed_records",
    "_indexed_active_records", "_prune_active",
]


def index_ns(jobs):
    ns = {
        "jobs": jobs, "user_jobs": {}, "meta": {},
        "JOB_INDEX_ALL_KEY": "__all__",
        "JOB_INDEX_GLOBAL_CAP": 2500,
        "JOB_INDEX_FLAG": "job_index:v1",
    }
    for name in INDEX_FUNCS:
        exec(func_src(name), ns)
    return ns


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
avatar_url = exec_func("avatar_url", {})
gallery_item = exec_func("public_gallery_item", {
    "users": fake_users, "display_handle": display_handle,
    "avatar_url": avatar_url,
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
# Vault fixtures: a throwaway keypair (the production private key
# lives only in the Modal secret and is never used here), a dict
# standing in for the R2 bucket, and a fake volume.
# ------------------------------------------------------------------

TEST_PRIV = PrivateKey.generate()
TEST_PUB_B64 = base64.b64encode(bytes(TEST_PRIV.public_key)).decode()
seal = exec_func("_vault_seal", {
    "base64": base64, "LEGAL_HOLD_VAULT_PUBLIC_KEY": TEST_PUB_B64,
})


def unseal(data: bytes) -> bytes:
    return SealedBox(TEST_PRIV).decrypt(data)


class FakeVolume:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


vault_store = {}


def fake_vault_put(key, data):
    vault_store[key] = bytes(data)
    return len(data)


export_main = exec_func("_legal_hold_vault_export", {
    "legal_holds": fake_holds, "FILES_DIR": FILES, "json": json,
    "_now": lambda: 1700000099, "_vault_put": fake_vault_put,
    "_vault_seal": seal, "data_volume": FakeVolume(),
    "LEGAL_HOLD_VAULT_BUCKET": "moonfactory-legal-holds",
    "LEGAL_HOLD_ENCRYPTION":
        "PyNaCl SealedBox (XSalsa20-Poly1305), per-object ephemeral key",
})

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
    **index_ns(fake_jobs),
    "users": fake_users, "jobs": fake_jobs, "legal_holds": fake_holds,
    "FILES_DIR": FILES, "stamp_content_hash": stamp,
    "json": json, "_now": lambda: 1700000099,
    "_legal_hold_vault_export": export_main,
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
# Each placement now appends three entries for this file-less job:
# the hold itself, the records-only vault note, and the vault
# export entry.
place_hold(hold_job, "admin_gallery_remove")
check(len(fake_holds["job0030"]["actions"]) == 6
      and fake_holds["job0030"]["held_at"] == 1700000099,
      "a repeat hold appends to the action log and keeps the first held_at")
check(fake_holds["job0030"].get("vault", {}).get("record_key")
      == "holds/job0030/record.enc"
      and fake_holds["job0030"]["vault"]["content_key"] is None,
      "the file-less hold exported its case record to the vault")

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
for name in ("create_clip",
             "create_template", "create_short", "create_drama"):
    check('"created_ip": client_ip(request)' in func_src(name),
          f"{name} stamps created_ip from the creating request")
# The video and image creates were split into shared cores
# (2026-11-27, the customer API): the cores stamp the ip they
# are handed, and both wrappers (website and API) hand them
# client_ip(request), so the stamp is still the creating
# request's IP on every surface.
for name in ("create_job_core", "create_image_core"):
    check('"created_ip": ip' in func_src(name),
          f"{name} stamps created_ip from the creating request")
check('"created_ip": parent.get("created_ip")'
      in func_src("_drama_create_shot_job"),
      "drama shot children inherit the parent episode's creation IP")
check('"created_ip": parent.get("created_ip")'
      in func_src("_queue_clone_child"),
      "voice clone children inherit the parent job's creation IP")
check('"created_ip": job.get("created_ip")' in func_src("_short_ai_scenes"),
      "shorts picture children inherit the short's creation IP")
check(SRC.count("created_ip=client_ip(request)") == 1
      and "created_ip=ip" in func_src("create_job_core"),
      "both create_api_job call sites pass the creating IP "
      "(the drama retake route reads its request directly; the "
      "video create route passes the ip its core was handed, "
      "which both of its wrappers read from their request)")

# ------------------------------------------------------------------
# 11. Automated vault: seal round-trip, export on hold, failure
#     keeps plaintext, and the daily destruction sweep
# ------------------------------------------------------------------

# The seal round-trips and the ciphertext carries no plaintext.
sealed = seal(ORIGINAL)
check(ORIGINAL not in sealed and b"original-generation" not in sealed,
      "sealed vault bytes contain no plaintext")
check(unseal(sealed) == ORIGINAL,
      "decryption with the private key round-trips the original bytes")
check(len(base64.b64decode(
    "e/4t/ns06gyW6El3HblluZ/0bar82GSSMF2ru6TtYFA=")) == 32
    and "e/4t/ns06gyW6El3HblluZ/0bar82GSSMF2ru6TtYFA=" in SRC,
    "the pinned vault public key in app.py is a real 32-byte key")
check("LEGAL_HOLD_PRIVATE_KEY" not in SRC,
      "the app never reads the vault private key: it cannot decrypt")

holds2 = {}
store2 = {}
volume2 = FakeVolume()


def fake_put2(key, data):
    store2[key] = bytes(data)
    return len(data)


export2 = exec_func("_legal_hold_vault_export", {
    "legal_holds": holds2, "FILES_DIR": FILES, "json": json,
    "_now": lambda: 1700000200, "_vault_put": fake_put2,
    "_vault_seal": seal, "data_volume": volume2,
    "LEGAL_HOLD_VAULT_BUCKET": "moonfactory-legal-holds",
    "LEGAL_HOLD_ENCRYPTION":
        "PyNaCl SealedBox (XSalsa20-Poly1305), per-object ephemeral key",
})
jobs2 = {}
place2 = exec_func("place_legal_hold", {
    **index_ns(jobs2),
    "users": fake_users, "jobs": jobs2, "legal_holds": holds2,
    "FILES_DIR": FILES, "stamp_content_hash": stamp,
    "json": json, "_now": lambda: 1700000200,
    "_legal_hold_vault_export": export2,
})

# A removal hold on a job with a file: the file and the case
# record land in the vault as ciphertext, and the verified
# export takes the plaintext off the volume.
vault_path = FILES / "job0050.mp4"
vault_path.write_bytes(ORIGINAL)
job50 = {"id": "job0050", "user_id": "user-7", "kind": "video",
         "status": "done", "model": "wan2.2", "lane": "wan2.2",
         "prompt": "the vaulted prompt", "created_at": 1700000100,
         "content_sha256": sha(ORIGINAL),
         "created_ip": "192.0.2.55"}
jobs2["job0050"] = job50
place2(job50, "admin_gallery_remove")
hold50 = holds2.get("job0050") or {}
check(hold50.get("vault", {}).get("bucket")
      == "moonfactory-legal-holds",
      "the hold record names the vault bucket")
check(hold50.get("vault", {}).get("content_key")
      == "holds/job0050/content.enc"
      and hold50["vault"]["record_key"] == "holds/job0050/record.enc",
      "the hold record carries the vault object keys")
check(hold50.get("vault", {}).get("content_sha256") == sha(ORIGINAL)
      and "SealedBox" in (hold50.get("vault", {}).get("encryption")
                          or ""),
      "the hold record carries the fingerprint and encryption method")
check(store2.get("holds/job0050/content.enc") is not None
      and ORIGINAL not in store2["holds/job0050/content.enc"],
      "the vaulted content object is ciphertext, not the file")
check(unseal(store2["holds/job0050/content.enc"]) == ORIGINAL,
      "the vaulted content decrypts back to the original file")
record_copy = json.loads(unseal(store2["holds/job0050/record.enc"]))
check(record_copy["job"]["prompt"] == "the vaulted prompt"
      and record_copy["account"]["email"] == "casefile@example.com"
      and record_copy["content_sha256"] == sha(ORIGINAL),
      "the vaulted record decrypts to the full case file")
check(not vault_path.exists() and volume2.commits >= 1,
      "a verified export on a removal deletes the plaintext file")
check(any(a.get("action") == "vault export"
          for a in hold50.get("actions") or []),
      "the export is written into the hold's action log")

# A failed upload keeps the plaintext and logs the failure.
holds3 = {}


def boom_put(key, data):
    raise RuntimeError("simulated upload failure")


export3 = exec_func("_legal_hold_vault_export", {
    "legal_holds": holds3, "FILES_DIR": FILES, "json": json,
    "_now": lambda: 1700000300, "_vault_put": boom_put,
    "_vault_seal": seal, "data_volume": FakeVolume(),
    "LEGAL_HOLD_VAULT_BUCKET": "moonfactory-legal-holds",
    "LEGAL_HOLD_ENCRYPTION":
        "PyNaCl SealedBox (XSalsa20-Poly1305), per-object ephemeral key",
})
fail_path = FILES / "job0051.mp4"
fail_path.write_bytes(ORIGINAL)
holds3["job0051"] = {
    "job_id": "job0051", "job": {"id": "job0051", "kind": "video"},
    "account": {}, "content_sha256": sha(ORIGINAL), "actions": [],
}
export3({"id": "job0051", "kind": "video"}, True)
check(fail_path.exists(),
      "a failed vault upload leaves the plaintext file in place")
check(holds3["job0051"].get("vault") is None
      and any(a.get("action") == "vault export failed"
              for a in holds3["job0051"]["actions"]),
      "a failed vault upload is logged on the hold and sets no vault")

# The destruction sweep: only the eligible hold is destroyed.
due_fn = exec_func("_hold_destruction_due", {"datetime": datetime})
check(due_fn({"ncmec_confirmation_date": "2024-02-29",
              "destruction_state": "pending",
              "vault": {"content_key": "k"}},
             datetime.date(2025, 3, 1)) is True
      and due_fn({"ncmec_confirmation_date": "2024-02-29",
                  "destruction_state": "pending",
                  "vault": {"content_key": "k"}},
                 datetime.date(2025, 2, 28)) is False,
      "a February 29 confirmation runs one year to February 28")

sweep_store = {}


def fake_delete_sweep(key):
    sweep_store.pop(key, None)


holds4 = {}


def mk_hold(jid, conf, state="pending", le=False, vault=True,
            report="CT-123"):
    entry = {
        "job_id": jid, "actions": [],
        "ncmec_confirmation_date": conf,
        "cybertipline_report_number": report,
        "destruction_state": state,
        "law_enforcement_hold": le,
        "vault": None,
    }
    if vault:
        entry["vault"] = {
            "bucket": "moonfactory-legal-holds",
            "content_key": f"holds/{jid}/content.enc",
            "record_key": f"holds/{jid}/record.enc",
        }
        sweep_store[f"holds/{jid}/content.enc"] = b"c"
        sweep_store[f"holds/{jid}/record.enc"] = b"r"
    holds4[jid] = entry


mk_hold("due1", "2025-01-01")
mk_hold("notyet", "2025-06-01")
mk_hold("leflag", "2024-01-01", le=True)
mk_hold("noconf", None)
mk_hold("donestate", "2024-01-01", state="destroyed")
mk_hold("novault", "2024-01-01", vault=False)
sweep = exec_func("sweep_legal_hold_destructions", {
    "legal_holds": holds4, "_vault_delete": fake_delete_sweep,
    "_hold_destruction_due": due_fn, "_now": lambda: 1767225600,
    "datetime": datetime,
})
destroyed = sweep(today=datetime.date(2026, 1, 2))
check(destroyed == ["due1"],
      f"the sweep destroys only the eligible hold (got {destroyed})")
check("holds/due1/content.enc" not in sweep_store
      and "holds/due1/record.enc" not in sweep_store,
      "the due hold's vault objects are deleted")
check(holds4["due1"]["destruction_state"] == "destroyed"
      and holds4["due1"].get("content_destroyed_at") == 1767225600
      and any(a.get("action") == "vault content destroyed"
              and a.get("cybertipline_report_number") == "CT-123"
              for a in holds4["due1"]["actions"]),
      "the destruction is logged on the retained hold record")
for jid in ("notyet", "leflag", "noconf", "donestate"):
    check(f"holds/{jid}/content.enc" in sweep_store
          and holds4[jid]["destruction_state"]
          == ("destroyed" if jid == "donestate" else "pending"),
          f"the sweep never touches the {jid} hold")
check(holds4["novault"]["destruction_state"] == "pending",
      "a hold with no vaulted content is never swept")

# Vault pins: bucket, schedule, hookup, and non-exposure.
check('LEGAL_HOLD_VAULT_BUCKET = "moonfactory-legal-holds"' in SRC,
      "the vault bucket is the dedicated moonfactory-legal-holds")
check("def legal_hold_destruction_sweep" in SRC
      and "modal.Period(days=1)" in SRC,
      "the destruction sweep runs daily inside the app")
check("_legal_hold_vault_export(" in func_src("place_legal_hold")
      and 'trigger == "admin_gallery_remove"'
      in func_src("place_legal_hold"),
      "placing a hold runs the vault export, plaintext removal "
      "gated on the removal trigger")
check('"pynacl"' in SRC, "the app image installs pynacl")
for name in ("public_gallery_item", "gallery_list", "list_jobs",
             "public_job"):
    check("vault" not in func_src(name),
          f"{name} exposes no vault data")

# ------------------------------------------------------------------

if failures:
    print(f"FAIL ({len(failures)}):")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_content_hash: all checks passed")
