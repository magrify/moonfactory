"""Local checks for the 2026-11-28 Kaggle session hygiene.

Clone proof 2 queued ~2 hours behind two batch GPU sessions
nobody could name, because the kernel list shapes the proof
run tried (mine / userName / group) are all rejected by the
current Kaggle API. This suite pins the fix's shape without
any network, Modal, or Kaggle call:

- the census reads the account through the shapes verified
  live that day (list with the "user" field, status with
  userName + kernelSlug), and the rejected shapes appear
  nowhere in the census helper;
- the worker kernel set is exactly the four platform
  families, mapped to their slug constants;
- hygiene clears only running sessions, never touches a
  worker whose platform heartbeat is fresh, never touches
  the blocked family's own session (its wake push is the
  remedy), clears a non-worker with the no-op script, and
  revives a hung worker by pushing its own script;
- the comfy wake consults its own session status before
  pushing, skips the push while a session is queued, runs
  hygiene on the batch session limit, and retries once;
- the voice and audio wakes carry the same queued guard;
- the mirror's queued bound consults the census before
  failing a queued comfy job;
- the Comfy kernel tries --async-offload ahead of today's
  known-good --lowvram set, in the template and in the
  generated kernel alike, with the plain set as the final
  fallback.

Run:  modal-venv python scratch/check_kaggle_hygiene.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PLATFORM = (ROOT / "platform" / "app.py").read_text()
TEMPLATE = (ROOT / "worker-kaggle"
            / "comfy_kernel_template.py").read_text()
KERNEL = (ROOT / "worker-kaggle"
          / "comfy_worker_kernel.py").read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def body_of(text, def_name, next_def="\ndef "):
    start = text.index(f"def {def_name}")
    end = text.index(next_def, start + 1)
    return text[start:end]


# --- census shapes -------------------------------------------------------
census = body_of(PLATFORM, "kaggle_session_census",
                 "\ndef _kaggle_family_heartbeat_fresh")
check('"/api/v1/kernels/list"' in census
      and '"user": KAGGLE_ACCOUNT_USER' in census,
      "the census lists the account's kernels with the "
      "verified 'user' field")
check('"mine"' not in census and '"userName"' not in census,
      "the census never sends the rejected list fields")
status_fn = body_of(PLATFORM, "kaggle_kernel_status",
                    "\ndef kaggle_session_census")
check('"/api/v1/kernels/status"' in status_fn
      and '"userName": KAGGLE_ACCOUNT_USER' in status_fn
      and '"kernelSlug": slug' in status_fn,
      "the status read uses the verified userName/kernelSlug "
      "shape")
check('KAGGLE_ACCOUNT_USER = "magrify"' in PLATFORM,
      "the account user is pinned to magrify")
for fam, const in (("comfy", "COMFY_KERNEL_SLUG"),
                   ("voice", "VOICE_KERNEL_SLUG"),
                   ("audio", "AUDIO_KERNEL_SLUG"),
                   ("hf", "HF_KERNEL_SLUG")):
    check(f'"{fam}": {const}' in PLATFORM,
          f"the worker kernel set maps {fam} to its slug")
check('meta["kaggle:session_census"] = census' in census,
      "the census is cached in meta for read paths")

# --- hygiene discipline --------------------------------------------------
hygiene = body_of(PLATFORM, "kaggle_session_hygiene",
                  "\ndef kaggle_own_session_status")
check('entry["status"] != "running"' in hygiene,
      "hygiene only ever clears running sessions; queued "
      "sessions hold no slot and are left alone")
check("_kaggle_family_heartbeat_fresh(family)" in hygiene,
      "a worker with a fresh platform heartbeat is never "
      "cleared")
check('family == blocked_family' in hygiene,
      "the blocked family's own session is left to its wake "
      "push")
check("KAGGLE_CLEAR_SCRIPT" in hygiene,
      "a non-worker squatter is cleared with the no-op "
      "script push (the push is what cancels its session)")
check("cleared >= 4" in hygiene,
      "one hygiene pass acts on at most four kernels")
delete_fn = body_of(PLATFORM, "kaggle_delete_kernel",
                    "\ndef kaggle_session_census")
check("KernelsApiService/DeleteKernel" in delete_fn,
      "kernel deletion goes through the RPC service (the "
      "REST delete path answers 400)")
check("kaggle_delete_kernel(" in hygiene,
      "hygiene deletes kernels whose sessions no push can "
      "reach")
check('entry["status"] == "queued"' in hygiene
      and "_family_has_queued_work(family)" in hygiene,
      "a queued session is deleted only when its family has "
      "no work waiting on it")
check('meta["kaggle:last_hygiene"] = report' in hygiene,
      "every hygiene pass records its census and actions "
      "in meta")

# --- wake integration ----------------------------------------------------
wake = body_of(PLATFORM, "_wake_comfy_kernel",
               "\n# ----")
check('kaggle_own_session_status(token, "comfy")' in wake,
      "the comfy wake reads its own Kaggle session status "
      "before pushing")
check('own == "queued"' in wake,
      "the comfy wake does not push over a queued session")
check('"session count" in detail.lower()' in wake,
      "a batch session limit refusal runs hygiene")
check(wake.count("kaggle_push(") >= 2,
      "the comfy wake retries its push once after hygiene")
for fn, fam in (("_wake_voice_kernel", "voice"),
                ("_wake_audio_kernel", "audio")):
    w = body_of(PLATFORM, fn, "\ndef ")
    check(f'kaggle_own_session_status(token, "{fam}")'
          ' == "queued"' in w,
          f"the {fam} wake carries the queued guard")

# --- mirror integration --------------------------------------------------
mirror_at = PLATFORM.index(
    "# Kaggle ComfyUI jobs (the house lanes, 2026-11-19)")
mirror = PLATFORM[mirror_at:mirror_at + 5200]
check("kaggle_own_session_status(" in mirror
      and mirror.index("kaggle_own_session_status(")
      < mirror.index("_fail_stale_job(job)"),
      "the mirror consults the census before failing a "
      "queued comfy job at its bound")
check("state in KAGGLE_ACTIVE_STATUSES" in mirror,
      "a job whose kernel session is queued or running is "
      "not failed at the queued bound")
check("COMFY_QUEUED_LIMIT_SECONDS * 3" in mirror,
      "an unreadable census buys three bounds of grace, "
      "not an instant fail")

# --- kernel launch flags -------------------------------------------------
for name, text in (("template", TEMPLATE), ("kernel", KERNEL)):
    check("COMFY_FLAG_SETS" in text
          and '("--lowvram", "--async-offload")' in text,
          f"the comfy {name} tries async offload first")
    at = text.index("COMFY_FLAG_SETS")
    sets = text[at:at + 200]
    check(sets.index('"--async-offload"')
          < sets.rindex('"--lowvram"'),
          f"the comfy {name}'s plain lowvram set stays the "
          "final fallback")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_kaggle_hygiene")
