"""Local checks for the guided movie flow (Jack, 2026-11-27).

The flow in Jack's own step labels, in both the Create tab
and the Clone tab: (1) "Create your AI model" on the Nano
Banana Pro image lane, saved into the character store;
(2) "Make a start frame", an image job on the same lane
carrying the character plus a product photo as reference
pictures; (3) "Generate clips", normal video jobs from that
start frame on the picked model; (4) "Put them together",
the new assembly step: the user's own finished clips, in
the user's order, joined into one movie in the library.

Pins:

- the four steps render in both tabs from ONE shared
  implementation (one template, one wiring function, two
  mount points), the Clone tab's own video swap flow is
  untouched beside it, and the tab bar stays Create / Clip /
  Clone;
- steps 1 to 3 ride the normal creation paths (the image
  endpoint on the nano_banana_pro lane, the character
  endpoint, the video endpoint with the start frame attached
  by job id), so the gates on those paths apply unchanged;
- the lane's reference pictures go through create_image_core
  only on a lane that declares them, are resolved from the
  character store server side, and are judged by the input
  classifier with the same gate as an edit lane's source;
- assembly accepts only the user's own done video jobs
  (assembly_pick_refusal, exercised here on plain records):
  another account's job and a missing job read as unknown,
  an unfinished pick refuses, a still or working-material
  pick refuses, one clip alone refuses with the plain
  message, and more than the cap refuses;
- the assembly job stores its picks in the order given,
  the worker renders them in that order through
  clipping.render_assembly, and the finished movie is a
  normal library job (the Movie label, a poster, the
  content hash and output classification at finish, the
  file route serving it untouched);
- assembly charges nothing: the creation core stamps
  charged_cents 0, never touches a balance, never counts a
  free generation, and the worker carries no charge either;
- a movie built from a gallery-hidden clip stays hidden:
  the creation core stamps assembly_from_hidden from the
  sources' own records and gallery_visible honors it.

The render itself is proven with real local ffmpeg renders
in scratch/proof_movie_assembly.py. No Modal calls, no
network, no generation.

Run:  modal-venv python scratch/check_movie_flow.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import clipping  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)
html = app.APP_HTML


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# -- the flow stands in both tabs, one implementation ---------------------

check('id="mf1-root"' in html and 'id="mf2-root"' in html,
      "a flow mount point is missing from a tab")
check('id="botcheck-mf1"' in html and 'id="botcheck-mf2"' in html,
      "a flow bot check container is missing")
create_block = html[html.index('id="create-pane"'):
                    html.index('id="upgrade-pane"')]
clone_block = html[html.index('id="clone-pane"'):]
check('id="mf1-root"' in create_block,
      "the flow does not live in the Create pane")
check('id="mf2-root"' in clone_block,
      "the flow does not live in the Clone pane")
check('id="ec-clone"' in clone_block and 'id="ec-library"' in clone_block,
      "the Clone tab's own swap flow lost a control")
check(html.count('id="tab-') == 3
      and '<button class="btn" id="tab-create">Create</button>' in html
      and '<button class="btn secondary" id="tab-clip">Clip</button>' in html
      and '<button class="btn secondary" id="tab-clone">Clone</button>' in html,
      "the tab bar is not Create, Clip, Clone")
check(html.count("<h2>Make a movie</h2>") == 2,
      "the flow card does not head both tabs")

def js_func_src(name):
    start = html.find(f"function {name}(")
    if start < 0:
        return ""
    end = html.find("\nfunction ", start + 1)
    return html[start:end if end > 0 else len(html)]


template = js_func_src("mfRootHtml")
for label in ("Create your AI model", "Make a start frame",
              "Generate clips", "Put them together"):
    check(label in template,
          f"the step label {label!r} is missing from the flow")
    check(html.count(label) >= 1,
          f"the step label {label!r} never reaches the page")
check("for (const p of ['mf1', 'mf2'])" in html
      and "root.innerHTML = mfRootHtml(p)" in html
      and "mfWireFlow(p)" in html,
      "the two tabs do not share the one flow implementation")
check("function mfRootHtml" in html and html.count("mfRootHtml") >= 2,
      "the flow template is not the single source of the steps")
check("'botcheck-mf1', 'botcheck-mf2'" in html,
      "the flow bot checks never render a Turnstile widget")
check("typeof mfRefresh === 'function'" in html,
      "showTab does not refresh the flow")

# -- steps 1 to 3 ride the normal creation paths ---------------------------

check("const MF_IMAGE_LANE = 'nano_banana_pro';" in html,
      "the flow's image lane is not Nano Banana Pro")
check(pricing.IMAGE_LANES["nano_banana_pro"].get("references") is True,
      "the Nano Banana Pro lane does not declare reference pictures")
check("image_model: MF_IMAGE_LANE" in html,
      "the flow's image steps do not post the Nano Banana Pro lane")
check("await api('/api/characters'" in html,
      "step 1 does not save through the character endpoint")
check("character_id: characterId" in html
      and "reference_images: [productUrl]" in html,
      "step 2 does not send the character and the product photo")
check("start_image_job_id: mfState[p].frameJobId" in html,
      "step 3 does not attach the start frame by job id")
check("await api('/api/jobs'" in html,
      "step 3 does not create through the video endpoint")
check("await api('/api/assemblies'" in html
      and "job_ids: picks" in html,
      "step 4 does not post its picks to the assembly endpoint")

core = func_src("create_image_core")
check('lane.get("references")' in core,
      "image creation does not gate references on the lane flag")
check("_find_character" in core,
      "image creation does not resolve the character server side")
check("reference_b64" in core and "extra_refs" in core
      and "classify_input_images" in core,
      "reference pictures skip the input classifier")
check("input_gate_refusal" in core,
      "reference pictures skip the input gate")
submit = func_src("_submit_provider_image")
check("reference_images" in submit
      and 'lane.get("references")' in submit
      and 'image_input["images"]' in submit,
      "the VidLab submit does not carry the reference pictures")
check("this image route takes no reference pictures" in submit,
      "a non VidLab route would silently drop reference pictures")

# -- assembly: the pick rules, exercised on plain records ------------------

UID = "user-1"


def rec(**kw):
    base = {"user_id": UID, "status": "done", "kind": "generation"}
    base.update(kw)
    return base


check(app.ASSEMBLY_MIN_CLIPS == 2 and app.ASSEMBLY_MAX_CLIPS == 12,
      "the assembly pick bounds moved")
check(app.assembly_pick_refusal([rec(), rec()], UID) is None,
      "two own finished clips cannot join")
check(app.assembly_pick_refusal(
    [rec(), rec(), rec(kind="short"), rec(kind="assembly")], UID) is None,
    "shorts and movies cannot join a movie")
r = app.assembly_pick_refusal([rec()], UID)
check(r is not None and r[0] == 422
      and "at least two" in r[1] and "already a finished video" in r[1],
      "one clip alone is not refused with the plain message")
r = app.assembly_pick_refusal([rec(user_id="someone-else"), rec()], UID)
check(r is not None and r[0] == 404,
      "another account's clip is not refused as unknown")
r = app.assembly_pick_refusal([None, rec()], UID)
check(r is not None and r[0] == 404,
      "a missing clip is not refused as unknown")
r = app.assembly_pick_refusal([rec(status="running"), rec()], UID)
check(r is not None and r[0] == 422 and "not finished" in r[1],
      "an unfinished clip is not refused")
for kind in app.ASSEMBLY_EXCLUDED_KINDS:
    r = app.assembly_pick_refusal([rec(kind=kind), rec()], UID)
    check(r is not None and r[0] == 422,
          f"a {kind} pick is not refused")
r = app.assembly_pick_refusal([rec() for _ in range(13)], UID)
check(r is not None and r[0] == 422,
      "more than the clip cap is not refused")

# -- assembly: creation, order, no charge, the normal finish ---------------

check('"/api/assemblies"' in SRC, "the assembly endpoint is missing")
acore = func_src("create_assembly_core")
check("assembly_pick_refusal" in acore,
      "assembly creation does not run the pick rules")
check("ensure_processed" in acore,
      "assembly creation does not resolve the sources' finished files")
check("enforce_prompt_gate" in acore,
      "assembly creation skips the illegal-content gate")
check('"assembly_sources": ids' in acore,
      "the picks are not stored in the order given")
check('"charged_cents": 0' in acore,
      "the assembly job does not stamp a zero charge")
check("balance_cents" not in acore,
      "assembly creation touches a balance")
check("count_generation" not in acore
      and "reserve_free_slot" not in acore,
      "assembly creation counts against the free tier")
check('"free_tier": False' in acore,
      "the assembly job is marked as a free generation")
check("process_assembly_job.spawn" in acore,
      "assembly creation does not dispatch its worker")
check("_assembly_source_hidden" in acore
      and '"assembly_from_hidden"' in acore,
      "a hidden source's privacy is not stamped onto the movie")

worker = func_src("process_assembly_job")
check("clipping.render_assembly" in worker,
      "the worker does not render through the assembly render")
check("job.get(\"assembly_sources\")" in worker,
      "the worker does not read the picks in their stored order")
check("stamp_content_hash" in worker
      and "classify_job_output" in worker
      and "make_poster" in worker,
      "the finished movie skips the poster, hash or classifier")
check("balance_cents" not in worker and "charged_cents" not in worker,
      "the assembly worker charges")

hidden = func_src("_assembly_source_hidden")
for field in ("removed", "output_flagged", "input_flagged",
              "short_parent", "drama_parent", "gallery_public"):
    check(field in hidden,
          f"the hidden-source read ignores {field}")
check("legal_hold_active" in hidden and "job_is_nsfw" in hidden
      and "prompt_is_nsfw" in hidden,
      "the hidden-source read skips a hold or a prompt verdict")
visible = func_src("gallery_visible")
check('job.get("assembly_from_hidden")' in visible,
      "gallery visibility ignores the assembly stamp")

pub = func_src("public_job")
check('"Movie" if job.get("kind") == "assembly"' in pub,
      "public_job has no Movie label for assemblies")
check('"assembly_sources": job.get("assembly_sources") or []' in pub,
      "public_job does not carry the movie's picks")
ensure = func_src("ensure_processed")
check('"assembly"' in ensure,
      "the file route would run a movie through the finishing chain")
mirror = func_src("mirror_status")
check('"assembly"' in mirror and "ASSEMBLY_POLL_LIMIT_SECONDS" in mirror,
      "a stuck assembly job has no mirror bound")
retry = func_src("retry_job")
check('kind == "assembly"' in retry
      and "create_assembly_core" in retry,
      "retry does not rebuild a failed movie from its picks")
check('lane.get("references")' in retry
      and "the pictures this one was made from were not kept" in retry,
      "retry would rebuild a start frame without its pictures")

# -- the render itself ------------------------------------------------------

check(callable(clipping.render_assembly)
      and callable(clipping.assembly_target_size),
      "the assembly render is missing from clipping.py")
check(clipping.assembly_target_size(
    [{"width": 720, "height": 1280}]) == (720, 1280),
    "a portrait first clip does not set the portrait raster")
check(clipping.assembly_target_size(
    [{"width": 1280, "height": 720}]) == (1280, 720),
    "a landscape first clip does not set the landscape raster")
render_src = Path(ROOT / "platform" / "clipping.py").read_text()
check("force_original_aspect_ratio=decrease" in render_src
      and "setsar=1" in render_src and "fps=" in render_src,
      "the render does not fit whole frames at one frame rate")
try:
    clipping.render_assembly(
        [Path("/tmp/only-one.mp4")], Path("/tmp/out.mp4"),
        Path("/tmp/assembly-check-work"))
    check(False, "the render accepted one clip")
except ValueError:
    check(True, "the render refuses one clip")

if failures:
    print("FAIL check_movie_flow")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_movie_flow")
