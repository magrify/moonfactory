"""Local checks for the worker plane fixes (2026-11-27).

Audit findings pinned here: worker-endpoints-no-claim-
binding (HIGH), worker-key-readable-by-in-session-third-
party-code (HIGH, platform side: per-family keys with a
rotation window), comfy-heartbeat-session-spoof-requeue,
worker-result-body-buffered-before-cap, worker-upload-
verification-fail-open, audio-result-arbitrary-json-trusted
(MEDIUMs), worker-claim-full-dict-scan (LOW).

The pure helpers are extracted from app.py and exec'd
against fakes; the endpoints are pinned by source. No Modal
calls, no network.

Run:  modal-venv python scratch/check_worker_plane.py
"""
import ast
import hashlib
import hmac
import os
import sys
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


class HTTPException(Exception):
    def __init__(self, status_code, detail=""):
        self.status_code = status_code
        self.detail = detail


# --- per-family keys ---------------------------------------------------------
os.environ["TEMPLATE_WORKER_KEY"] = "shared-key"
os.environ["VOICE_WORKER_KEY"] = "voice-key"
os.environ["TEMPLATE_WORKER_KEY_PREV"] = "old-shared-key"
ns = {"os": os, "hmac": hmac}
exec(func_src("_family_worker_ok"), ns)
ok = ns["_family_worker_ok"]
check(ok("VOICE", "voice-key"), "the voice family key works")
check(ok("VOICE", "shared-key"),
      "the shared key still works during the transition")
check(not ok("VOICE", "wrong"), "a wrong key fails")
check(ok("AUDIO", "old-shared-key"),
      "the previous shared key works inside the rotation window")
check(not ok("COMFY", ""), "an empty key never works")

# --- claim tokens --------------------------------------------------------------
import secrets as pysecrets

ns2 = {"pysecrets": pysecrets, "hashlib": hashlib,
       "hmac": hmac, "HTTPException": HTTPException}
exec(func_src("_issue_claim_token"), ns2)
exec(func_src("_require_claim"), ns2)
job = {}
token = ns2["_issue_claim_token"](job, "1")
check(token and job.get("claim_token_hash")
      and token not in str(job),
      "a bound claim stores only the token hash")
check(ns2["_issue_claim_token"]({}, "") is None,
      "an unbound (legacy) claim issues no token")
try:
    ns2["_require_claim"](job, token)
    passed = True
except HTTPException:
    passed = False
check(passed, "the claiming session's token passes")
for bad in ("", "someone-elses-token"):
    try:
        ns2["_require_claim"](job, bad)
        passed = False
    except HTTPException as exc:
        passed = exc.status_code == 403
    check(passed, f"a foreign token ({bad!r}) is refused")
try:
    ns2["_require_claim"]({}, "anything")
    passed = True
except HTTPException:
    passed = False
check(passed, "a legacy claim (no token issued) stays key-only")

# --- audio result validation -----------------------------------------------------
ns3 = {"HTTPException": HTTPException}
exec(func_src("_validate_audio_result"), ns3)
validate = ns3["_validate_audio_result"]
wc = {"audio_stage": "wordcheck"}
out = validate(wc, {"status": "passed", "wrong": [], "words": [],
                    "listen_at": [1.5], "wrong_ratio": 0.0,
                    "evil": "x", "status2": "forged"})
check(out == {"status": "passed", "wrong": [], "words": [],
              "listen_at": [1.5], "wrong_ratio": 0.0},
      "the verdict is rebuilt from validated fields only")
for bad_body, why in (
        ({"status": "forged"}, "an invented status is refused"),
        ({"status": "passed", "wrong_ratio": 7}, "ratios clamp"),
        ("not-a-dict", "a non-dict body is refused")):
    try:
        out = validate(wc, bad_body)
        passed = why == "ratios clamp" and out["wrong_ratio"] == 1.0
    except HTTPException:
        passed = why != "ratios clamp"
    check(passed, why)
try:
    validate({"audio_stage": "bed"}, {"status": "passed"})
    passed = False
except HTTPException:
    passed = True
check(passed, "a stage that takes no result is refused")

# --- source pins: every per-job endpoint is claim bound -------------------------
ENDPOINTS = (
    "hf_job_source", "hf_job_voice", "hf_job_file", "hf_job_gif",
    "hf_job_fail", "voice_job_reference", "voice_job_file",
    "voice_job_fail", "audio_job_file", "audio_job_source",
    "audio_job_result", "audio_job_fail", "comfy_job_file",
    "comfy_job_fail")
for name in ENDPOINTS:
    check("_require_claim(job" in func_src(name),
          f"{name} requires the claim token")
for name in ("hf_next_job", "voice_next_job", "audio_next_job",
             "comfy_job_next"):
    check("_issue_claim_token" in func_src(name),
          f"{name} issues claim tokens")
for name in ("hf_job_file", "hf_job_gif", "voice_job_file",
             "audio_job_file", "comfy_job_file"):
    check("_check_declared_size" in func_src(name),
          f"{name} checks the declared size before buffering")
for name in ("hf_job_fail", "voice_job_fail", "audio_job_fail",
             "comfy_job_fail"):
    check('job["status"] != "running"' in func_src(name),
          f"{name} refuses never-claimed jobs")
for name in ("hf_job_file", "voice_job_file", "audio_job_file"):
    check("upload probe failed" in func_src(name),
          f"{name} fails the job when the upload cannot be probed")
check("claimq:" in SRC and "_note_claimable" in SRC
      and "_claim_candidates" in SRC,
      "claims read a per-family queue before scanning")
check("comfy:claim_sessions" in func_src("comfy_heartbeat")
      and "_now() - 600" in func_src("comfy_heartbeat"),
      "the comfy heartbeat requeue needs a known session and "
      "a stale claim")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_worker_plane: all checks passed")
