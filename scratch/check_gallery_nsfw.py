"""Local checks for the NSFW gallery exclusion (Jack, 2026-11-18).

Rule: if a user makes an NSFW video, it does not show up in the
public gallery. A job is NSFW for gallery purposes when its lane
is the house NSFW lane OR its model is one of the uncensored
catalog models (UNCENSORED_VIDEO_MODELS, the flag /api/config
serves and the picker renders as the Uncensored marker). Either
signal excludes, whatever the owner's gallery opt-out setting,
and the read-level check in gallery_visible covers jobs finished
before the rule existed. At creation, NSFW jobs are also stamped
gallery_public False as belt and braces. The owner's own library
and job views are untouched.

No Modal calls, no network: execs the extracted is_paid /
gallery_visible defs against plain dicts, calls the module's own
job_is_nsfw, and pins the wiring in source.

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


# -- the classifier, exercised for real ----------------------------------

job_is_nsfw = app.job_is_nsfw
check(job_is_nsfw({"lane": "nsfw", "model": "wan22_uncut"}) is True,
      "nsfw lane job is not classified NSFW")
check(job_is_nsfw({"lane": "wan3_unc", "model": "wan3_unc"}) is True,
      "uncensored API model job is not classified NSFW")
check(job_is_nsfw({"lane": "production", "model": "wan2.2"}) is True,
      "house Wan 2.2 job is not classified NSFW (the house lane "
      "carries the uncensored flag: open weights, no moderation)")
check(job_is_nsfw({"lane": "wan3", "model": "wan3"}) is False,
      "moderated API model job is classified NSFW")
check(job_is_nsfw({"lane": "template", "model": "hyperframes"}) is False,
      "template job is classified NSFW")
check(job_is_nsfw({"lane": "image", "model": "flux2"}) is False,
      "image job is classified NSFW")
check(job_is_nsfw({}) is False, "empty job is classified NSFW")
check(job_is_nsfw(None) is False, "missing job is classified NSFW")
check(job_is_nsfw({"lane": "draft"}) is True,
      "legacy house job with no model field is not classified "
      "NSFW (it ran the default house model, Wan 2.2)")
check(job_is_nsfw({"lane": "production"}) is True,
      "legacy production job with no model field is not "
      "classified NSFW")
check(job_is_nsfw({"lane": "clip"}) is False,
      "clip job with no model field is classified NSFW")
check("wan22_uncut" in app.UNCENSORED_VIDEO_MODELS,
      "wan22_uncut missing from the uncensored set")
check("UNCENSORED_VIDEO_MODELS" in func_src("job_is_nsfw"),
      "job_is_nsfw does not read the config uncensored set")

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

gns = {"users": USERS, "is_paid": is_paid, "job_is_nsfw": job_is_nsfw}
exec(func_src("gallery_visible"), gns)
gallery_visible = gns["gallery_visible"]


def job(**kw):
    base = {"status": "done", "user_id": "u-free",
            "lane": "wan3", "model": "wan3"}
    base.update(kw)
    return base


# NSFW lane job, owner never opted out (free accounts cannot opt
# out at all): excluded.
check(gallery_visible(job(lane="nsfw", model="wan22_uncut")) is False,
      "nsfw lane job is visible in the gallery")
# Uncensored model job, paid owner, no opt-out: excluded.
check(gallery_visible(job(user_id="u-paid", lane="wan3_unc",
                          model="wan3_unc")) is False,
      "uncensored model job is visible in the gallery")
# Uncensored model job for an owner who opted out anyway: still
# excluded (the rule does not depend on the setting either way).
check(gallery_visible(job(user_id="u-out", lane="seedance2_unc",
                          model="seedance2_unc")) is False,
      "uncensored model job of an opted out owner is visible")
# House Wan 2.2 job: excluded through the model signal.
check(gallery_visible(job(lane="production", model="wan2.2")) is False,
      "house Wan 2.2 job is visible in the gallery")
# A creation-stamped job is excluded even if its signals were
# somehow ordinary.
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
