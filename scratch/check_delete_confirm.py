"""Local checks for the library delete confirmation wording
(Jack, 2026-11-23).

Rule: the delete confirm may only claim the item comes out of
the public gallery when the item is really in it. NSFW
generations never list in the gallery (since 2026-11-23 that
verdict is prompt-judged: an uncensored-lane job whose own
prompt scans NSFW, or one that took an uploaded image as an
input, which the word scan can never judge), so their confirm
reads studio only, while a clean-prompt text-only uncensored
job really is in the gallery and its confirm says so. The pick rides on in_gallery, which list_jobs
stamps onto every job it sends as gallery_visible's own
verdict, the very function the gallery feed lists by. The noun
follows the item kind too: images are asked about as images.

No Modal calls, no network: execs the extracted is_paid /
gallery_visible defs against plain dicts, and runs the served
confirm builder in node against every combination.

Run:  modal-venv python scratch/check_delete_confirm.py
"""
import ast
import subprocess
import sys
import tempfile
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


# -- the stamp: list_jobs sets in_gallery to gallery_visible's verdict --
# (read in list_jobs, not inside public_job: the serializer stays
# free of gallery rules, as check_gallery_nsfw pins.)

check('view["in_gallery"] = gallery_visible(job, user)'
      in func_src("list_jobs_core"),
      "list_jobs does not stamp in_gallery from gallery_visible")

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

gns = {"users": USERS, "is_paid": is_paid, "job_is_nsfw": app.job_is_nsfw,
       "job_is_uncensored": app.job_is_uncensored,
       "prompt_is_nsfw": app.prompt_is_nsfw,
       "OUTPUT_CLASSIFIER_SINCE": app.OUTPUT_CLASSIFIER_SINCE}
exec(func_src("gallery_visible"), gns)
gallery_visible = gns["gallery_visible"]


def job(**kw):
    base = {"id": "j1", "status": "done", "user_id": "u-free",
            "lane": "wan3", "model": "wan3", "prompt": "a test prompt",
            "created_at": 1700000000}
    base.update(kw)
    return base


# in_gallery is defined as exactly this call, so the verdicts the
# stamp will carry are these verdicts.
check(gallery_visible(job()) is True,
      "normal done job would not be stamped in_gallery")
check(gallery_visible(job(lane="nsfw", model="wan22_uncut",
                          prompt="a nude woman dancing",
                          user_prompt="a nude woman dancing")) is False,
      "explicit nsfw lane job would be stamped in_gallery")
check(gallery_visible(job(lane="wan3_unc", model="wan3_unc",
                          prompt="an erotic scene",
                          user_prompt="an erotic scene")) is False,
      "explicit uncensored model job would be stamped in_gallery")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut"))
      is True,
      "clean-prompt text-only uncensored job would not be stamped "
      "in_gallery (it really lists, so its confirm says so)")
check(gallery_visible(job(lane="seedance25_unc",
                          model="seedance25_unc",
                          prompt="a lighthouse at dusk",
                          user_prompt="a lighthouse at dusk",
                          has_start_image=True)) is False,
      "tame-prompt uncensored job with an uploaded start image "
      "would be stamped in_gallery (it never lists, so its "
      "confirm must read studio only)")
check(gallery_visible(job(gallery_public=False)) is False,
      "creation stamped job would be stamped in_gallery")
check(gallery_visible(job(user_id="u-out")) is False,
      "opted out owner's job would be stamped in_gallery")
check(gallery_visible(job(status="error")) is False,
      "error job would be stamped in_gallery")
check(gallery_visible(job(kind="image", lane="image", model="flux2"))
      is True, "normal done image job would not be stamped in_gallery")
check(gallery_visible(job(kind="image", lane="image", model="flux2",
                          gallery_public=False)) is False,
      "creation stamped image job would be stamped in_gallery")

# -- the button carries the stamp and the kind ---------------------------

check('data-ingallery="\' + (j.in_gallery ? \'1\' : \'0\') + \'"' in SRC,
      "the Delete button does not carry data-ingallery from j.in_gallery")
check('data-delkind="\' + (j.kind || \'generation\') + \'"' in SRC,
      "the Delete button does not carry data-delkind from j.kind")

# -- the served confirm builder, run in node for every combination -------

HTML = app.APP_HTML
_start = HTML.find("const noun = b.dataset.delkind")
_end = HTML.find("+ '.';", _start)
check(_start != -1 and _end != -1,
      "the confirm builder was not found in the served page")
if _start != -1 and _end != -1:
    builder = HTML[_start:_end + len("+ '.';")]
    cases = [
        ({"delkind": "generation", "ingallery": "1"},
         "Delete this video? It is removed from your studio and "
         "from the gallery."),
        ({"delkind": "generation", "ingallery": "0"},
         "Delete this video? It is removed from your studio."),
        ({"delkind": "image", "ingallery": "1"},
         "Delete this image? It is removed from your studio and "
         "from the gallery."),
        ({"delkind": "image", "ingallery": "0"},
         "Delete this image? It is removed from your studio."),
    ]
    harness = "const cases = %s;\n" % [
        {"dataset": d, "want": w} for d, w in cases]
    harness += "for (const c of cases) { const b = {dataset: c.dataset};\n"
    harness += builder + "\n"
    harness += "if (ask !== c.want) { console.error('MISMATCH', " \
               "JSON.stringify(c.dataset), '->', ask); process.exit(1); }\n"
    harness += "console.log('ok', ask); }\n"
    with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        harness_path = fh.name
    try:
        out = subprocess.run(["node", harness_path], capture_output=True,
                             text=True, timeout=60)
        check(out.returncode == 0,
              f"the served confirm builder disagrees: {out.stderr.strip()}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"the served confirm builder runs in node: {exc}")

# The old fixed wording is gone as a literal.
check("confirm('Delete this video? It is removed from your studio "
      "and from the gallery.')" not in SRC,
      "the old fixed delete confirm is still in the source")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_delete_confirm")
