"""Local checks for the prompt-judged NSFW gallery rule
(Jack, 2026-11-23).

Rule: an uncensored-lane generation is NSFW for public gallery
purposes only when the user's own prompt scans NSFW. Not every
uncensored generation is NSFW, so the blanket lane rule is
gone: a clean prompt on an uncensored lane lists in the gallery
exactly like a regular generation (paid opt-out and the other
gallery rules still apply), and an explicit prompt stays out.
The scan is rule based and deterministic (whole-token and
phrase matching over one word-list block in app.py, with a
light leet decode and a squeezed pass for spacing dodges), it
reads the record's user_prompt (never a server-composed
prompt: the Krea 2 Verite trigger prepend must not flag every
Krea job), and gallery_visible classifies live, so jobs made
under the blanket rule are rejudged by their stored prompts:
their old creation stamps no longer rule them.

No Modal calls, no network: exercises the module's own scan
and classifiers against plain dicts, execs the extracted
gallery_visible against them, and pins the wiring in source.

Run:  modal-venv python scratch/check_prompt_nsfw.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

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


scan = app.prompt_is_nsfw

# -- the scan: explicit prompts flag --------------------------------

for text in (
    "a nude woman on a beach",
    "topless sunbather, golden hour",
    "two people having sex in a hotel room",
    "an explicit pornographic scene",
    "a woman masturbating",
    "hentai girl with big boobs",
    "an onlyfans model shoot",
    "spread legs on the bed",
    "a cumshot compilation",
    "deep throat scene",
    "underage girl in a swimsuit",
    "child porn",
    # dodges: leet speak and letter spacing
    "b00bs out",
    "s3x in the city",
    "n u d e portrait",
    "p.o.r.n stills",
    "a n a l s e x scene",
    "h4rdcore video",
):
    check(scan(text) is True, f"explicit prompt not flagged: {text!r}")

# -- the scan: ordinary prompts stay clean ---------------------------

for text in (
    "a red hot air balloon rising over green hills",
    "the morning train to Fort Worth carried 128 passengers",
    "a portrait of a woman in a garden",
    "a bunny eating a carrot",
    "bikini models walking on a beach",
    "silk lingerie folded on a bed",
    # Scunthorpe cases: a listed string inside an ordinary word
    # is not a hit, and borderline anatomy words with innocent
    # uses are not single terms.
    "a cock crowing at dawn on a farm",
    "a sperm whale diving under a boat",
    "chicken breast on a plate with grapes",
    "a documentary about the Essex coast",
    "the therapist's office on main street",
    "a happy class of film students",
    "a peacock in a cucumber garden",
    "the sexton keeps the document archive",
    "happiness and brass bands in the park",
    "",
    None,
):
    check(scan(text) is False, f"ordinary prompt flagged: {text!r}")

# -- the scope: which jobs are judged at all --------------------------

job_is_uncensored = app.job_is_uncensored
check(job_is_uncensored({"lane": "nsfw", "model": "wan22_uncut"})
      is True, "nsfw lane job is out of scope")
check(job_is_uncensored({"lane": "seedance25_unc",
                         "model": "seedance25_unc"}) is True,
      "Seedance 2.5 Uncensored job is out of scope")
check(job_is_uncensored({"lane": "production", "model": "wan2.2"})
      is True, "house Wan 2.2 job is out of scope")
check(job_is_uncensored({"lane": "draft"}) is True,
      "legacy house job with no model field is out of scope "
      "(it ran the default house model)")
for lane_id, lane in pricing.IMAGE_LANES.items():
    job = {"kind": "image", "lane": "image", "model": lane_id,
           "image_model": lane_id}
    check(job_is_uncensored(job) is bool(lane.get("uncensored")),
          f"image lane {lane_id} scope disagrees with its "
          "uncensored flag")
check(job_is_uncensored({"lane": "wan3", "model": "wan3"}) is False,
      "moderated API model job is in scope")
check(job_is_uncensored({"lane": "template", "model": "hyperframes"})
      is False, "template job is in scope")
check(job_is_uncensored({"kind": "image", "lane": "image",
                         "model": None, "prompt": "a nude portrait"})
      is False, "house image job (Z-Image Turbo) is in scope")
check(job_is_uncensored({}) is False, "empty job is in scope")
check(job_is_uncensored(None) is False, "missing job is in scope")

# -- the verdict: scope + the user's own prompt -----------------------

job_is_nsfw = app.job_is_nsfw
check(job_is_nsfw({"lane": "wan22_uncut", "model": "wan22_uncut",
                   "user_prompt": "a lighthouse at dusk"}) is False,
      "clean prompt on the Uncut lane is classified NSFW")
check(job_is_nsfw({"lane": "wan22_uncut", "model": "wan22_uncut",
                   "user_prompt": "a nude woman dancing"}) is True,
      "explicit prompt on the Uncut lane is not classified NSFW")
check(job_is_nsfw({"lane": "ltx25_unc", "model": "ltx25_unc",
                   "user_prompt": "a cat in a library"}) is False,
      "clean prompt on LTX 2.5 Uncensored is classified NSFW")
check(job_is_nsfw({"lane": "seedance25_unc",
                   "model": "seedance25_unc",
                   "user_prompt": "an erotic dance"}) is True,
      "explicit prompt on Seedance 2.5 Uncensored is not "
      "classified NSFW")
# Out of scope: the prompt is never even read.
check(job_is_nsfw({"lane": "wan3", "model": "wan3",
                   "user_prompt": "a nude woman"}) is False,
      "regular lane job is classified by the prompt scan")
# The Krea shape: the composed prompt carries the lane's trigger
# words; the scan reads user_prompt, so a tame Krea job is clean
# and an explicit one flags on the user's words alone.
krea = {"kind": "image", "lane": "image", "model": "krea2_verite",
        "image_model": "krea2_verite",
        "prompt": "verite realism, nsfw, a portrait in a garden",
        "user_prompt": "a portrait in a garden"}
check(job_is_nsfw(krea) is False,
      "Krea job flagged by its own trigger prepend")
check(job_is_nsfw({**krea, "user_prompt": "a nude portrait"})
      is True, "explicit Krea prompt is not classified NSFW")
# Records that predate user_prompt fall back to prompt.
check(job_is_nsfw({"lane": "nsfw", "model": "wan22_uncut",
                   "prompt": "balloons over a field"}) is False,
      "legacy job with a clean stored prompt is classified NSFW")
check(job_is_nsfw({"lane": "nsfw", "model": "wan22_uncut",
                   "prompt": "a porn scene"}) is True,
      "legacy job with an explicit stored prompt is not "
      "classified NSFW")
check(job_is_nsfw({"lane": "nsfw", "model": "wan22_uncut"})
      is False,
      "job with no prompt stored anywhere is classified NSFW "
      "(there is nothing to judge)")

# -- gallery_visible: the wiring, exercised for real ------------------

ns = {}
exec(func_src("is_paid"), ns)
is_paid = ns["is_paid"]

free_owner = {"id": "u-free", "plan": "free", "net_paid_cents": 0.0,
              "gallery_opt_out": False}
paid_owner = {"id": "u-paid", "plan": "creator",
              "net_paid_cents": 2000.0, "gallery_opt_out": False}
opted_out_owner = {"id": "u-out", "plan": "creator",
                   "net_paid_cents": 2000.0, "gallery_opt_out": True}
USERS = {f"id:{u['id']}": u
         for u in (free_owner, paid_owner, opted_out_owner)}

gns = {"users": USERS, "is_paid": is_paid,
       "job_is_nsfw": job_is_nsfw,
       "job_is_uncensored": job_is_uncensored}
exec(func_src("gallery_visible"), gns)
gallery_visible = gns["gallery_visible"]


def job(**kw):
    base = {"status": "done", "user_id": "u-free",
            "lane": "wan3", "model": "wan3",
            "user_prompt": "a lighthouse at dusk"}
    base.update(kw)
    return base


# Clean prompt on an uncensored lane lists, exactly like a
# regular generation.
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut"))
      is True, "clean Uncut job does not list in the gallery")
check(gallery_visible(job(lane="seedance25_unc",
                          model="seedance25_unc")) is True,
      "clean Seedance 2.5 Uncensored job does not list")
# Explicit prompt on an uncensored lane stays out.
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          user_prompt="a nude woman dancing"))
      is False, "explicit Uncut job lists in the gallery")
check(gallery_visible(job(lane="nsfw", model="wan22_uncut",
                          user_prompt="a porn scene")) is False,
      "explicit NSFW lane job lists in the gallery")
# Legacy rejudging: a blanket-era creation stamp no longer rules
# an uncensored job; its stored prompt does.
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          gallery_public=False)) is True,
      "legacy stamped clean-prompt job does not list")
check(gallery_visible(job(lane="wan22_uncut", model="wan22_uncut",
                          gallery_public=False,
                          user_prompt="a nude woman dancing"))
      is False, "legacy stamped explicit-prompt job lists")
# A regular job's creation stamp still rules it.
check(gallery_visible(job(gallery_public=False)) is False,
      "regular job's gallery_public False stamp is not honored")
# The paid opt-out still hides a clean uncensored job.
check(gallery_visible(job(user_id="u-out", lane="wan22_uncut",
                          model="wan22_uncut")) is False,
      "paid opt-out no longer hides a clean uncensored job")
# A clean uncensored image lists; an explicit one does not.
check(gallery_visible(job(kind="image", lane="image",
                          model="zimage_unc",
                          image_model="zimage_unc",
                          user_prompt="a bunny eating a carrot"))
      is True, "clean Z-Image uncensored image does not list")
check(gallery_visible(job(kind="image", lane="image",
                          model="zimage_unc",
                          image_model="zimage_unc",
                          user_prompt="a nude portrait")) is False,
      "explicit Z-Image uncensored image lists in the gallery")

# -- source pins -------------------------------------------------------

check("NSFW_PROMPT_TERMS" in SRC and "NSFW_PROMPT_PHRASES" in SRC
      and "NSFW_PROMPT_SQUEEZED_TERMS" in SRC,
      "the scan's word lists are not in app.py")
check("user_prompt" in func_src("job_is_nsfw")
      and "prompt_is_nsfw" in func_src("job_is_nsfw"),
      "job_is_nsfw does not judge by the stored user prompt")
check("job_is_uncensored" in func_src("gallery_visible"),
      "gallery_visible does not carve uncensored jobs out of "
      "the creation stamp rule")
krea_src = func_src("_queue_krea2_on_kaggle")
check("if job_is_nsfw(job):" in krea_src
      and 'job["gallery_public"] = False' in krea_src,
      "the Krea queue does not stamp by the prompt scan")

if failures:
    print("PROMPT NSFW CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_prompt_nsfw")
