"""Local checks for the homepage library split and the library
page (Jack, 2026-11-24).

Rule: the studio homepage loads only the five most recent
videos and the five most recent images. The rest are not in
its payload at all. Each section's View All is a plain link
to the library page (/library), never an in-place expand,
and it shows only when the section has more than five. The
library page reads the same /api/jobs endpoint without the
recent flag and renders the full list with the studio's own
card and delete code, shared by construction.

No Modal calls, no network: list_jobs is execed against
fixture dicts, and the served split / View All builders run
in node.

Run:  modal-venv python scratch/check_library_recent.py
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


# -- list_jobs: the recent cut happens in the payload ---------------------

def make_jobs(user_id, videos, images):
    jobs = {}
    t = 1000
    for i in range(videos):
        t += 10
        jobs[f"v{i}"] = {"id": f"v{i}", "user_id": user_id,
                         "kind": "generation", "status": "done",
                         "created_at": t}
    for i in range(images):
        t += 10
        jobs[f"im{i}"] = {"id": f"im{i}", "user_id": user_id,
                          "kind": "image", "status": "done",
                          "created_at": t}
    return jobs


class Req:
    def __init__(self, params):
        self.query_params = params


def run_list_jobs(jobs, params):
    ns = {
        "jobs": jobs,
        "require_user": lambda request, mf_session: {"id": "u1"},
        "mirror_status": lambda j: j,
        "public_job": lambda j, queue_position=None: {
            "id": j["id"], "kind": j.get("kind"),
            "created_at": j["created_at"]},
        "gallery_visible": lambda j: True,
        "Cookie": lambda default="": default,
        "Request": object,
    }
    exec(func_src("list_jobs"), ns)
    return ns["list_jobs"](Req(params), "")


big = make_jobs("u1", videos=8, images=7)
big["other"] = {"id": "other", "user_id": "u2", "kind": "image",
                 "status": "done", "created_at": 99999}
big["child"] = {"id": "child", "user_id": "u1", "kind": "generation",
                 "status": "done", "created_at": 99998,
                 "short_parent": "v0"}

recent = run_list_jobs(big, {"recent": "1"})
rv = [j for j in recent["jobs"] if j["kind"] != "image"]
ri = [j for j in recent["jobs"] if j["kind"] == "image"]
check(len(recent["jobs"]) == 10,
      "recent payload does not carry exactly 5 + 5")
check(len(rv) == 5 and len(ri) == 5,
      "recent payload is not split 5 videos / 5 images")
check(recent.get("total_videos") == 8
      and recent.get("total_images") == 7,
      "recent payload totals are not the full per kind counts")
check([j["id"] for j in rv] == ["v7", "v6", "v5", "v4", "v3"],
      "recent videos are not the five most recent, newest first")
check([j["id"] for j in ri] == ["im6", "im5", "im4", "im3", "im2"],
      "recent images are not the five most recent, newest first")
check(all(j["id"] not in ("other", "child") for j in recent["jobs"]),
      "recent payload leaks another user's job or a child job")

full = run_list_jobs(big, {})
check(len(full["jobs"]) == 15,
      "the full list no longer returns every owned job")
check("recent" not in full and "total_videos" not in full,
      "the full list response changed shape")
check(full["jobs"][0]["created_at"] >= full["jobs"][-1]["created_at"],
      "the full list is not most recent first")

small = run_list_jobs(make_jobs("u1", videos=3, images=2),
                      {"recent": "1"})
check(len(small["jobs"]) == 5
      and small["total_videos"] == 3 and small["total_images"] == 2,
      "a small library's recent payload or totals are wrong "
      "(totals must equal shown, so no View All renders)")

lj = func_src("list_jobs")
check('request.query_params.get("recent") == "1"' in lj
      and '[:5]' in lj,
      "list_jobs does not cut the recent payload per kind")

# -- the served homepage builders, run in node ----------------------------

HTML = app.APP_HTML


def js_block(start, end):
    a = HTML.index(start)
    b = HTML.index(end, a)
    return HTML[a:b]


split_src = js_block("function splitJobs(jobs) {",
                     "function viewAllHtml")
viewall_src = js_block("function viewAllHtml(total, shown) {",
                       "function renderJobCards")
harness = split_src + viewall_src + """
const jobs = [];
for (let i = 0; i < 8; i++) jobs.push({kind: 'generation'});
for (let i = 0; i < 7; i++) jobs.push({kind: 'image'});
const parts = splitJobs(jobs);
if (parts.videos.length !== 8 || parts.images.length !== 7) {
  console.error('split wrong', parts.videos.length, parts.images.length);
  process.exit(1);
}
const link = viewAllHtml(8, 5);
if (!link.includes('href="/library"') || !link.includes('View All')) {
  console.error('view all is not a library link', link);
  process.exit(1);
}
if (/onclick|data-viewall|expand/i.test(link)) {
  console.error('view all expands in place', link);
  process.exit(1);
}
if (viewAllHtml(5, 5) !== '' || viewAllHtml(3, 3) !== ''
    || viewAllHtml(2, 5) !== '') {
  console.error('view all shows at or under five');
  process.exit(1);
}
if (!viewAllHtml(6, 5).includes('href="/library"')) {
  console.error('view all missing at six');
  process.exit(1);
}
console.log('homepage builders ok');
"""
with tempfile.NamedTemporaryFile("w", suffix=".js",
                                 delete=False) as fh:
    fh.write(harness)
    harness_path = fh.name
try:
    out = subprocess.run(["node", harness_path], capture_output=True,
                         text=True, timeout=60)
    check(out.returncode == 0,
          f"the served homepage builders disagree: "
          f"{out.stderr.strip()}")
except Exception as exc:  # noqa: BLE001
    check(False, f"the served homepage builders run in node: {exc}")

# -- homepage wiring pins ---------------------------------------------------

load_jobs = js_block("async function loadJobs() {",
                     "function splitJobs")
check("api('/api/jobs?recent=1')" in load_jobs,
      "the homepage does not read the recent payload")
check("api('/api/jobs')" not in load_jobs,
      "the homepage still loads the whole library")
for marker in ('id="image-jobs"', 'id="videos-more"',
               'id="images-more"', "Your images"):
    check(marker in HTML, f"the studio page is missing {marker}")

# -- the library page --------------------------------------------------------

check('@web.get("/library"' in SRC
      and "HTMLResponse(LIBRARY_HTML)" in func_src("library_page"),
      "the /library route does not serve the library page")

LIB = app.LIBRARY_HTML
lib_load = LIB[LIB.index("async function loadJobs() {"):]
lib_load = lib_load[:lib_load.index("async function jobBoxClick")]
check("api('/api/jobs')" in lib_load and "recent" not in lib_load,
      "the library page does not read the full list")
for marker in ('id="jobs"', 'id="image-jobs"', "Your videos",
               "Your images", "function jobCardEl",
               "async function jobBoxClick", "function costLine",
               "function saveControlHtml"):
    check(marker in LIB, f"the library page is missing {marker}")


def page_block(html, start, end):
    a = html.index(start)
    b = html.index(end, a)
    return html[a:b].rstrip()


check(page_block(HTML, "function jobCardEl(j) {",
                 "function renderClipResults")
      == page_block(LIB, "function jobCardEl(j) {",
                    "async function loadJobs"),
      "the library page's card builder differs from the studio's")
check(page_block(HTML, "async function jobBoxClick(e) {",
                 "$('jobs').addEventListener('click', jobBoxClick);")
      == page_block(LIB, "async function jobBoxClick(e) {",
                    "$('jobs').addEventListener('click', jobBoxClick);"),
      "the library page's delete and drama handler differs "
      "from the studio's")
check("const noun = b.dataset.delkind" in LIB
      and "removed from your studio" in LIB,
      "the library page lost the gallery aware delete wording")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_library_recent")
