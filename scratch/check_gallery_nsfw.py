"""Local checks for the NSFW gallery exclusion (Jack, 2026-11-18;
prompt-judged since 2026-11-23).

Rule: if a user makes an NSFW generation, it does not show up
in the public gallery. Since 2026-11-23 the verdict is judged
by the user's own prompt, not by the lane: a job is NSFW for
gallery purposes when it ran on an uncensored lane or build
(the house NSFW lane, an UNCENSORED_VIDEO_MODELS model, or an
image lane flagged uncensored: job_is_uncensored sets the
scope) AND its stored user prompt scans NSFW
(prompt_is_nsfw). A clean prompt on an uncensored lane lists
exactly like a regular generation. One refinement, Jack's
later the same day: an in-scope job that took an uploaded
image as an input never lists either, however tame its prompt
reads, because the scan reads words, not pictures
(job_has_image_input, pinned in check_prompt_nsfw.py). The read-level check in
gallery_visible covers jobs finished before the rule existed,
rejudging them by their stored prompts; their blanket-era
creation stamps no longer rule them (the stamp still rules
every non-uncensored job it was set on). At creation, a job
whose prompt scans NSFW is also stamped gallery_public False
as belt and braces. The owner's own library and job views are
untouched. The scan's own word-level behavior is pinned in
check_prompt_nsfw.py; this file pins the scope signals, the
gallery wiring, and the stamp mechanics.

No Modal calls, no network: execs the extracted is_paid /
gallery_visible defs against plain dicts, calls the module's
own classifiers, and pins the wiring in source.

Run:  modal-venv python scratch/check_gallery_nsfw.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402

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


# -- the classifiers, exercised for real --------------------------------

job_is_nsfw = app.job_is_nsfw
job_is_uncensored = app.job_is_uncensored

# Scope: the signals that put a job under prompt judging.
check(job_is_uncensored({"lane": "nsfw", "model": "wan22_uncut"})
      is True, "nsfw lane job is out of the judged scope")
check(job_is_uncensored({"lane": "wan3_unc", "model": "wan3_unc"})
      is True, "uncensored API model job is out of the judged scope")
check(job_is_uncensored({"lane": "production", "model": "wan2.2"})
      is True, "house Wan 2.2 job is out of the judged scope "
      "(the house lane carries the uncensored flag: open "
      "weights, no moderation)")
check(job_is_uncensored({"lane": "wan3", "model": "wan3"}) is False,
      "moderated API model job is in the judged scope")
check(job_is_uncensored({"lane": "template", "model": "hyperframes"})
      is False, "template job is in the judged scope")
check(job_is_uncensored({"lane": "image", "model": "flux2"})
      is False, "regular image job is in the judged scope")
check(job_is_uncensored({}) is False,
      "empty job is in the judged scope")
check(job_is_uncensored(None) is False,
      "missing job is in the judged scope")
check(job_is_uncensored({"lane": "draft"}) is True,
      "legacy house job with no model field is out of the "
      "judged scope (it ran the default house model, Wan 2.2)")
check(job_is_uncensored({"lane": "production"}) is True,
      "legacy production job with no model field is out of "
      "the judged scope")
check(job_is_uncensored({"lane": "clip"}) is False,
      "clip job with no model field is in the judged scope")
check("wan22_uncut" in app.UNCENSORED_VIDEO_MODELS,
      "wan22_uncut missing from the uncensored set")
check("UNCENSORED_VIDEO_MODELS" in func_src("job_is_uncensored"),
      "job_is_uncensored does not read the config uncensored set")

# Verdict: in scope, the user's own prompt decides.
check(job_is_nsfw({"lane": "nsfw", "model": "wan22_uncut",
                   "user_prompt": "a nude woman dancing"}) is True,
      "explicit prompt on the nsfw lane is not classified NSFW")
check(job_is_nsfw({"lane": "nsfw", "model": "wan22_uncut",
                   "user_prompt": "balloons over a field"}) is False,
      "clean prompt on the nsfw lane is classified NSFW")
check(job_is_nsfw({"lane": "wan3_unc", "model": "wan3_unc",
                   "user_prompt": "an erotic scene"}) is True,
      "explicit prompt on an uncensored model is not "
      "classified NSFW")
check(job_is_nsfw({"lane": "production", "model": "wan2.2",
                   "user_prompt": "a lighthouse at dusk"}) is False,
      "clean prompt on house Wan 2.2 is classified NSFW")
check(job_is_nsfw({"lane": "wan3", "model": "wan3",
                   "user_prompt": "a nude woman"}) is False,
      "moderated API model job is classified by the prompt scan")
check(job_is_nsfw({"lane": "draft",
                   "user_prompt": "a porn scene"}) is True,
      "legacy house job with an explicit prompt is not "
      "classified NSFW")
check(job_is_nsfw({}) is False, "empty job is classified NSFW")
check(job_is_nsfw(None) is False, "missing job is classified NSFW")

# -- gallery_visible, exercised for real ---------------------------------

ns = {}
exec(func_src("is_paid"), ns)
is_paid = ns["is_paid"]

free_owner = {"id": "u-free", "plan": "free", "net_paid_cents": 0.0,
              "gallery_opt_out": False}
paid_owner = {"id": "u-paid", "plan": "creator", "net_paid_cents": 2000.0,
              "gallery_opt_out": False}
opted_out_owner = {"id": "u-out", "plan": "creator",
                   "net_paid_cents": 2000.0, "gallery_opt_out": True}
USERS = {f"id:{u['id']}": u
         for u in (free_owner, paid_owner, opted_out_owner)}

gns = {"users": USERS, "is_paid": is_paid, "job_is_nsfw": job_is_nsfw,
       "job_is_uncensored": job_is_uncensored}
exec(func_src("gallery_visible"), gns)
gallery_visible = gns["gallery_visible"]


def job(**kw):
    base = {"status": "done", "user_id": "u-free",
            "lane": "wan3", "model": "wan3",
            "user_prompt": "a lighthouse at dusk"}
    base.update(kw)
    return base


# NSFW lane job with an explicit prompt, owner never opted out
# (free accounts cannot opt out at all): excluded.
check(gallery_visible(job(lane="nsfw", model="wan22_uncut",
                          user_prompt="a nude woman dancing"))
      is False, "explicit nsfw lane job is visible in the gallery")
# NSFW lane job with a clean prompt: lists like any other job.
check(gallery_visible(job(lane="nsfw", model="wan22_uncut"))
      is True, "clean nsfw lane job does not list in the gallery")
# Uncensored model job, paid owner, explicit prompt: excluded.
check(gallery_visible(job(user_id="u-paid", lane="wan3_unc",
                          model="wan3_unc",
                          user_prompt="an erotic scene")) is False,
      "explicit uncensored model job is visible in the gallery")
# Uncensored model job for an owner who opted out anyway: still
# excluded (the opt-out hides it whatever the prompt says).
check(gallery_visible(job(user_id="u-out", lane="seedance2_unc",
                          model="seedance2_unc")) is False,
      "uncensored model job of an opted out owner is visible")
# The image-input bar: an uncensored job built on an uploaded
# image never lists, however tame its prompt (the scan reads
# words, not pictures). Seedance 2.5 Uncensored is
# image-to-video only, so its records carry the fact in the
# stored boolean or in the model alone.
check(gallery_visible(job(lane="seedance25_unc",
                          model="seedance25_unc",
                          has_start_image=True)) is False,
      "tame uncensored job with a start image is visible in "
      "the gallery")
check(job_is_nsfw({"lane": "seedance25_unc",
                   "model": "seedance25_unc",
                   "user_prompt": "a lighthouse at dusk"}) is True,
      "tame prompt on an image-required uncensored model is "
      "not barred by its image input")
# House Wan 2.2 job: excluded through the model signal when its
# prompt is explicit, listed when it is clean.
check(gallery_visible(job(lane="production", model="wan2.2",
                          user_prompt="a porn scene")) is False,
      "explicit house Wan 2.2 job is visible in the gallery")
check(gallery_visible(job(lane="production", model="wan2.2"))
      is True, "clean house Wan 2.2 job does not list")
# A creation-stamped regular job is excluded even if its signals
# were somehow ordinary.
check(gallery_visible(job(gallery_public=False)) is False,
      "gallery_public False stamp is not honored")
# A normal job lists.
check(gallery_visible(job()) is True,
      "normal job does not list in the gallery")
check(gallery_visible(job(user_id="u-paid")) is True,
      "normal job of a paid owner does not list")
# The paid opt-out still hides a normal job.
check(gallery_visible(job(user_id="u-out")) is False,
      "paid opt-out no longer hides a normal job")
# Unfinished and removed jobs still never list.
check(gallery_visible(job(status="running")) is False,
      "unfinished job lists in the gallery")
check(gallery_visible(job(removed=True)) is False,
      "admin removed job lists in the gallery")

# -- the read paths all run through the one gate --------------------------

for route_fn in ("gallery_list", "gallery_file", "gallery_poster",
                 "report_gallery_item"):
    check("gallery_visible" in func_src(route_fn),
          f"{route_fn} no longer gates on gallery_visible")

# -- creation stamps -------------------------------------------------------

check(SRC.count('job["gallery_public"] = False') == 3,
      "expected exactly three creation stamps (API path, house "
      "path, Krea 2 Verite queue)")
for create_fn in ("create_api_job", "create_job"):
    src = func_src(create_fn)
    check("job_is_nsfw(job)" in src
          and 'job["gallery_public"] = False' in src,
          f"{create_fn} does not stamp NSFW jobs at creation")
check('job["gallery_public"] = False' in func_src("_queue_krea2_on_kaggle"),
      "the Krea 2 Verite queue does not stamp its jobs out of "
      "the public gallery")

# -- the owner's own views are untouched ----------------------------------
# Refined 2026-11-23 for the delete confirm (Jack): list_jobs may
# READ the verdict once, as the read-only in_gallery stamp the
# confirm words itself by. It still never filters on it: every
# owned job lists, and no other gallery reference may appear.

_lj = func_src("list_jobs")
check("job_is_nsfw" not in _lj
      and _lj.count("gallery_visible(j)") == 1
      and 'view["in_gallery"] = gallery_visible(j)' in _lj,
      "the owner's job list now filters on gallery rules (only "
      "the read-only in_gallery stamp may read gallery_visible)")
check("gallery_visible" not in func_src("public_job")
      and "job_is_nsfw" not in func_src("public_job"),
      "public_job now filters on gallery rules")

if failures:
    print("GALLERY NSFW CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL GALLERY NSFW CHECKS PASSED")
