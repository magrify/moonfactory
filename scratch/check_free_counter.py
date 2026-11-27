"""Local checks for the unified free counter (2026-11-20).

Jack's free counter rule: EVERY generation counts toward
the 5 free per account, lifetime — every video, every
faceless short, every photo, and a drama episode counts as
one video. No generation type is exempt.

Behavior is proven the way check_owner_bypass proves its
rules: the counter defs are extracted from app.py and exec'd
against plain dicts, and each create flow's source is pinned
for its enforcement (before dispatch) and its count (at the
acceptance commit). No Modal calls, no network, no renders.

Audit summary this suite pins (what was true before the
rule): the lifetime counter was enforced and incremented
only on the free rails of the house video lanes, the house
photo lane, templates, and shorts; paid generations on
those same flows did not count, provider shelf photos and
API lane videos never counted, and drama never touched the
counter at all. Clipping stays uncounted: it is a paid-only
transform of an existing video, not one of the generation
types the rule names.

Run:  modal-venv python scratch/check_free_counter.py
"""
import ast
import re
import sys
import time
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


# --- the limit -----------------------------------------------------------------
m = re.search(r'FREE_CLIPS_TOTAL = int\(os\.environ\.get\('
              r'"FREE_CLIPS_TOTAL", "(\d+)"\)\)', SRC)
check(m and m.group(1) == "5", "the free lifetime limit is 5")
FREE_LIMIT = 5

# --- behavior: the extracted counter defs ---------------------------------------
ns = {"users": {}, "meta": {}, "time": time}
for name in ("_period", "save_user", "usage",
             "free_jobs_total", "count_generation"):
    src = func_src(name)
    check(bool(src), f"{name} exists in app.py")
    exec(src, ns)

free_jobs_total = ns["free_jobs_total"]
count_generation = ns["count_generation"]
meta = ns["meta"]


def fresh_user():
    return {"id": "u1", "email": "u1@example.com", "plan": "free",
            "usage": None, "free_jobs_total": 0}


# One shared counter across types: a video (free), a short
# (paid rail), a photo (paid rail), a drama episode, and a
# title video (free) fill the same 5.
user = fresh_user()
count_generation(user, free_job=True)          # a free video
check(free_jobs_total(user) == 1, "a free video counts once")
check(meta.get("freecount:total") == 1,
      "a free job moves the platform free ceiling")
count_generation(user)                          # a paid short
count_generation(user)                          # a paid photo
check(free_jobs_total(user) == 3,
      "paid generations count on the same counter")
check(meta.get("freecount:total") == 1,
      "paid generations do not touch the platform free ceiling")
count_generation(user)                          # a drama episode
count_generation(user, free_job=True)           # a free title video
check(free_jobs_total(user) == 5,
      "five generations of mixed types fill the counter")
check(free_jobs_total(user) >= FREE_LIMIT,
      "the enforcement predicate trips at 5 for every type")
check(int(user["usage"]["free_jobs"]) == 5,
      "the period mirror counts every type too")

# A legacy account migrates from its period record, then
# counts on top of it.
legacy = {"id": "u2", "email": "u2@example.com", "plan": "free",
          "usage": {"period": ns["_period"](), "free_jobs": 3,
                    "included_seconds": 0.0,
                    "included_cost_cents": 0.0}}
check(free_jobs_total(legacy) == 3,
      "a legacy account carries its period count over")
count_generation(legacy)
check(free_jobs_total(legacy) == 4,
      "counting continues from the migrated total")

# --- enforcement and counting, pinned per create flow ---------------------------
check(SRC.count("free_jobs_total(user) >= FREE_CLIPS_TOTAL") == 5,
      "the before dispatch enforcement stands on the four "
      "free eligible flows (video, photo, titles, shorts); "
      "photo enforces it twice, once per rail (house and the "
      "free shelf lanes such as Cloudflare)")

video = func_src("create_job_core")
check("free_jobs_total(user) >= FREE_CLIPS_TOTAL" in video,
      "video create enforces the counter before dispatch")
check("count_generation(user)" in video,
      "paid house videos count toward the 5")

api_video = func_src("create_api_job")
check("count_generation(user)" in api_video,
      "API lane videos count toward the 5")

image = func_src("create_image_core")
check("free_jobs_total(user) >= FREE_CLIPS_TOTAL" in image,
      "photo create enforces the counter before dispatch")
check(image.count("count_generation(user)") == 1
      and "count_generation(user, free_job=served_free)" in image,
      "photos count on both photo rails (shelf and house); the "
      "shelf call passes its served_free flag, so a free lane "
      "serving a free account also moves the platform ceiling")

short = func_src("create_short")
check("free_jobs_total(user) >= FREE_CLIPS_TOTAL" in short,
      "shorts create enforces the counter before dispatch")
check("count_generation(user)" in short,
      "paid shorts count toward the 5")

template = func_src("create_template")
check("free_jobs_total(user) >= FREE_CLIPS_TOTAL" in template,
      "titles create enforces the counter before dispatch")
check("count_generation(user)" in template,
      "paid title and motion videos count toward the 5")

drama_create = func_src("create_drama")
check("count_generation(user)" in drama_create,
      "a drama episode counts as one video")
check(drama_create.count("count_generation(user)") == 1,
      "an episode counts exactly once, at creation")
for name in ("resume_drama",):
    check("count_generation" not in func_src(name),
          f"{name} never counts the same episode again")
retake = ""
for node in ast.walk(TREE):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
            and "retake" in node.name:
        retake = ast.get_source_segment(SRC, node) or ""
check("count_generation" not in retake,
      "a shot retake never counts the same episode again")

clip = func_src("create_clip")
check("count_generation" not in clip,
      "clipping stays uncounted (paid transform, not a "
      "generation type the rule names)")

helper = func_src("count_generation")
check("free_jobs_total" in helper and "freecount:total" in helper,
      "the helper moves the lifetime counter and, for free "
      "jobs only, the platform ceiling")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_free_counter")
