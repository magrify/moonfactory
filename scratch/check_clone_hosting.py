"""Clone hosting checks (2026-11-28).

The failure this pins against: Spec A's proof (source job
291512b2e52ef891) died before any provider submit. Preparing
a clone on any source-sending lane first hosts the source
video at a public URL, and the platform's only hosting route
was fal storage — whose account is locked at $0 ("User is
locked. Reason: Exhausted balance", initiate → 403). The
ingest-time upload failed silently (hosted_url null) and the
create-time retry got the 403, so every motion/"both" Clone
lane (21 of 89) 503'd at quote/create for any source not
already hosted. The full evidence is
~/workspace/sinclip/clone-spec-a-2026-11-28.md.

The fix pinned here: the clone preparation path hosts on
the platform itself. A file on the data volume is
registered under an unguessable token (meta Dict) and
served by the /hosted/{token} route at the app's own
address; fal storage remains only as the fallback for a
self-host mint that fails outright.

Pinned:

- the module helpers exist and _hosted_url_for, driven for
  real below against a fixture meta Dict, mints a URL at
  the app's own base carrying a 48-hex token and the file's
  suffix, and records token -> volume-relative name;
- _worker_host_source, driven for real, returns the
  self-hosted URL without touching fal when the mint works,
  and falls back to fal only when the mint raises;
- _editclone_hosted_source, driven for real against a
  fixture volume with fal simulated as the locked account
  (returns None), still produces the hosted source URL from
  the self-host route and stamps the record; an already
  hosted source is returned untouched; and when the file is
  missing AND fal refuses, the refusal is the same 503 the
  endpoint has always given;
- in the source of each preparation call site, the
  self-host call precedes the fal call: the ingest source
  stamp (_worker_host_source), the ingest character frames
  (_worker_host_image), the create-time character image
  (_host_image_url before _fal_upload_bytes) and the
  create-time source frames (_hosted_url_for before
  _fal_upload_bytes);
- the /hosted/{token} route exists and resolves its token
  through the meta Dict.

Run:  modal-venv python scratch/check_clone_hosting.py
"""
import ast
import secrets as real_secrets
import sys
import tempfile
import textwrap
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
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"{name} not found in app.py")


class _HTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# -- the helpers exist ------------------------------------------------------

for name in ("_hosted_url_for", "_hosted_image_rel",
             "_worker_host_source", "_worker_host_image",
             "_public_base_url"):
    check(hasattr(app, name),
          f"app.{name} is missing: the self-host route the clone "
          "preparation path depends on is gone")

# -- _hosted_url_for, driven for real ---------------------------------------

fake_meta = {}
ns = {
    "pysecrets": real_secrets,
    "meta": fake_meta,
    "Path": Path,
    "_public_base_url": lambda: "https://base.example",
}
exec(textwrap.dedent(func_src("_hosted_url_for")), ns)
url = ns["_hosted_url_for"]("pinsrc.mp4")
check(url.startswith("https://base.example/hosted/")
      and url.endswith(".mp4"),
      f"_hosted_url_for minted an unexpected URL shape: {url}")
token = url.rsplit("/hosted/", 1)[1][: -len(".mp4")]
check(len(token) == 48
      and all(c in "0123456789abcdef" for c in token),
      f"the hosted token is not 48 hex chars: {token!r}")
check(fake_meta.get(f"hosted:{token}") == "pinsrc.mp4",
      "the token -> file mapping was not recorded in the meta Dict")

# -- _worker_host_source, driven for real -----------------------------------
# Self-host first: when the mint works, fal is never called.
# When the mint raises, fal is the fallback it always was.

fal_calls = []


def _fal_stub(data, content_type, file_name):
    fal_calls.append(file_name)
    return "https://fal.example/hosted.mp4"


ns = {
    "_hosted_url_for": lambda rel: "https://base.example/hosted/tok.mp4",
    "_worker_fal_upload": _fal_stub,
}
exec(textwrap.dedent(func_src("_worker_host_source")), ns)
got = ns["_worker_host_source"]("pinjob", b"bytes")
check(got == "https://base.example/hosted/tok.mp4"
      and fal_calls == [],
      "the ingest source stamp did not take the self-host route "
      f"first (got {got!r}, fal calls {fal_calls})")


def _mint_raises(rel):
    raise RuntimeError("meta Dict unavailable")


ns["_hosted_url_for"] = _mint_raises
exec(textwrap.dedent(func_src("_worker_host_source")), ns)
got = ns["_worker_host_source"]("pinjob", b"bytes")
check(got == "https://fal.example/hosted.mp4"
      and fal_calls == ["pinjob.mp4"],
      "the ingest source stamp lost its fal fallback for a "
      f"failed self-host mint (got {got!r}, fal calls {fal_calls})")

# -- _editclone_hosted_source, driven for real ------------------------------
# The locked-account simulation: fal returns None, exactly as
# the 403'd initiate/PUT flow does. The preparation must
# still produce the hosted URL, from the self-host route.

tmp = Path(tempfile.mkdtemp(prefix="clone-hosting-pin-"))
SRC_ID = "pinsrcjob"
(tmp / f"{SRC_ID}.mp4").write_bytes(b"fake mp4 bytes")
fixture_jobs = {}
fal_upload_calls = []


def _fal_upload_stub(data, content_type, file_name):
    fal_upload_calls.append(file_name)
    return None


class _Volume:
    def reload(self):
        pass

    def commit(self):
        pass


host_ns = {
    "FILES_DIR": tmp,
    "data_volume": _Volume(),
    "HTTPException": _HTTPException,
    "jobs": fixture_jobs,
    "_hosted_url_for": lambda rel: f"https://base.example/hosted/tok{Path(rel).suffix}",
    "_fal_upload_bytes": _fal_upload_stub,
}
exec(textwrap.dedent(func_src("_editclone_hosted_source")), host_ns)
hosted_source = host_ns["_editclone_hosted_source"]

record = {"id": SRC_ID}
got = hosted_source(record)
check(got == "https://base.example/hosted/tok.mp4",
      "with fal locked, the create-time source preparation did "
      f"not produce the self-hosted URL (got {got!r})")
check(fal_upload_calls == [],
      "the create-time source preparation called fal storage "
      f"even though self-hosting worked (calls {fal_upload_calls})")
check(record.get("hosted_url") == got
      and fixture_jobs.get(SRC_ID, {}).get("hosted_url") == got,
      "the self-hosted URL was not stamped back on the source record")

fal_upload_calls.clear()
already = {"id": SRC_ID, "hosted_url": "https://fal.example/old.mp4"}
got = hosted_source(already)
check(got == "https://fal.example/old.mp4"
      and fal_upload_calls == [],
      "an already hosted source was re-hosted instead of "
      "returned untouched")

missing = {"id": "nosuchjob"}
try:
    hosted_source(missing)
    check(False, "a source with no file and a locked fal "
                 "account did not refuse")
except _HTTPException as exc:
    check(exc.status_code == 503,
          "a source with no file and a locked fal account "
          f"refused with {exc.status_code}, not the endpoint's 503")

# -- call-site order: self-host first, fal as the fallback ------------------

def order(fn_name, first, second):
    src = func_src(fn_name)
    i, j = src.find(first), src.find(second)
    check(0 <= i < j,
          f"{fn_name}: {first} does not precede {second}; the "
          "self-host route must be attempted before fal storage")


order("_editclone_hosted_source", "_hosted_url_for", "_fal_upload_bytes")
order("_editclone_char_image_url", "_host_image_url", "_fal_upload_bytes")
order("_editclone_source_frame_urls", "_hosted_url_for", "_fal_upload_bytes")

ingest = func_src("process_editclone_job")
check("job[\"hosted_url\"] = _worker_host_source(" in ingest,
      "the ingest path no longer stamps the source's hosted "
      "URL through the self-host-first worker helper")
check("_worker_host_image(data)" in ingest,
      "the ingest character path no longer hosts its frames "
      "through the self-host-first worker helper")

# -- the hosted route --------------------------------------------------------

check('@web.get("/hosted/{token}")' in SRC,
      "the /hosted/{token} route is gone: minted URLs would 404")
route_src = func_src("hosted_file")
check('meta.get(f"hosted:{token}")' in route_src,
      "the hosted route no longer resolves its token through "
      "the meta Dict the mint writes to")
check("FileResponse" in route_src,
      "the hosted route no longer serves the file itself")

if failures:
    print("check_clone_hosting: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_clone_hosting: PASS")
