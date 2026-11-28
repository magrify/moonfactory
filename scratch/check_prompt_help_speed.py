"""Local checks for the prompt help time budget (Jack,
2026-11-28): one click gets one provider call with a short
socket budget, run off the web container's event loop under
a wall clock cap, and the page never shows a raw browser
error. Measured live before the fix: the picked free route
ran the full 120 second socket timeout without answering, a
second free route took 103 seconds, and a plain /api/me
took 117.69 seconds while one improve was in flight because
the async handler called the blocking provider call inline.

No Modal calls, no network: imports the platform modules,
stubs the urlopen boundary, and pins the endpoint and page
wiring in the source.

Run:  modal-venv python scratch/check_prompt_help_speed.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

for _key in ("SILK_API_KEY", "GEMINI_API_KEY",
             "OPENROUTER_API_KEY", "VENICE_API_KEY",
             "REFUSELESS_API_KEY", "MORPH_API_KEY",
             "NUMES_API_KEY"):
    os.environ.pop(_key, None)

import llm_providers  # noqa: E402
import shorts  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
LLM_SRC = (ROOT / "platform" / "llm_providers.py").read_text()

# --- the timeout reaches the socket, and only when named ---------------------

captured = {}


class FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(
            {"choices": [{"message": {
                "content": "better words"}}]}).encode()


def fake_urlopen(req, timeout=None):
    captured["timeout"] = timeout
    return FakeResp()


real_urlopen = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
os.environ["VENICE_API_KEY"] = "test-venice-key"
try:
    out = llm_providers.draft_text(
        "draft this", "venice:qwen-3-8-27b", timeout=7)
    check(out == "better words",
          "a timed draft call returns the model's text")
    check(captured.get("timeout") == 7,
          "the named timeout reaches the provider socket")
    captured.clear()
    out = llm_providers.draft_text(
        "draft this", "venice:qwen-3-8-27b")
    check(captured.get("timeout")
          == llm_providers.CHAT_TIMEOUT_SECONDS == 120,
          "an unnamed timeout keeps the old 120 second budget")
finally:
    urllib.request.urlopen = real_urlopen
    os.environ.pop("VENICE_API_KEY", None)

# --- a call that dies on time says so -----------------------------------------


def raising_urlopen(exc):
    def _open(req, timeout=None):
        raise exc
    return _open


os.environ["VENICE_API_KEY"] = "test-venice-key"
try:
    for exc, want in (
            (TimeoutError("timed out"), True),
            (urllib.error.URLError(
                TimeoutError("timed out")), True),
            (RuntimeError("provider down"), False)):
        urllib.request.urlopen = raising_urlopen(exc)
        try:
            llm_providers.draft_text(
                "draft this", "venice:qwen-3-8-27b", timeout=5)
            check(False, f"{exc!r} must raise DraftModelUnavailable")
        except llm_providers.DraftModelUnavailable as du:
            check(du.label == "Qwen 3.8 27B",
                  "the timed failure still names the pick")
            check(du.timed_out is want,
                  f"{exc!r} reads timed_out={want}")
finally:
    urllib.request.urlopen = real_urlopen
    os.environ.pop("VENICE_API_KEY", None)

# --- improve_prompt passes its budget through, and only then -------------------

real_provider_chat = llm_providers._provider_chat
seen = {}


def recorder_chat(prompt, provider_id, api_key, model,
                  timeout="ABSENT"):
    seen["timeout"] = timeout
    return "helped words"


llm_providers._provider_chat = recorder_chat
os.environ["VENICE_API_KEY"] = "test-venice-key"
try:
    out = shorts.improve_prompt(
        "a cat", "venice:qwen-3-8-27b", "video", timeout=9)
    check(out == "helped words",
          "improve returns the helped text on a timed call")
    check(seen.get("timeout") == 9,
          "improve passes its budget to the provider call")
    seen.clear()
    out = shorts.improve_prompt(
        "a cat", "venice:qwen-3-8-27b", "video")
    check(seen.get("timeout") == "ABSENT",
          "improve without a budget keeps the old call shape")
finally:
    llm_providers._provider_chat = real_provider_chat
    os.environ.pop("VENICE_API_KEY", None)

# --- the endpoint wiring --------------------------------------------------------

seg = SRC.split("async def prompt_help(", 1)[1]
seg = seg.split("@web.post(", 1)[0]
check("PROMPT_HELP_CALL_TIMEOUT = 20" in SRC,
      "the provider socket budget is 20 seconds")
check("PROMPT_HELP_BUDGET_SECONDS = 25" in SRC,
      "the whole request budget is 25 seconds")
check("run_in_threadpool(" in seg,
      "the provider call runs off the event loop")
check("asyncio.wait_for(" in seg,
      "the request carries a wall clock cap")
check("timeout=PROMPT_HELP_CALL_TIMEOUT" in seg,
      "the endpoint names the socket budget")
check("timeout=PROMPT_HELP_BUDGET_SECONDS" in seg,
      "the endpoint names the wall clock cap")
check("exc.timed_out" in seg,
      "the endpoint branches on a timed out call")
check(seg.count("took too long to answer") == 2,
      "both timeout paths fail with the plain too long line")
check("did not answer, so your prompt was not changed" in seg,
      "the plain did not answer failure is unchanged")

# --- the page wiring --------------------------------------------------------------
# Failures print through the studio's one error voice
# (errText, the site-wide fix that landed the same morning):
# it maps the browser's raw words to plain sentences. These
# pins cover what this fix adds on top of it, the client
# side deadline, and that both help actions use that voice.

page = SRC.split("async function promptHelpMagi2(", 1)[1]
page = page.split("$('prompt-help-btn')", 1)[0]
check("function promptHelpCall(body)" in SRC,
      "the help call runs through one deadline wrapper")
check("AbortController" in SRC,
      "the page call carries a client side deadline")
check("ctl.abort(), 40000" in SRC,
      "the client side deadline is 40 seconds")
check("msg('prompt-help-msg', errText(e), 'err')" in page,
      "the magi2 help action prints through errText")
check("msg(msgId, errText(e), 'err')" in page,
      "the help action prints through errText")
check("e.message" not in page,
      "no raw error text prints from either help action")
check("took too long to answer, so your prompt was not "
      "changed. Try again, or pick a different model." in SRC,
      "the server failure names the too long reason plainly")
for frag in ("took too long to answer, so your prompt was "
             "not changed. Try again, or pick a different "
             "model.",):
    check(":" not in frag and ";" not in frag,
          "the plain line follows the plain copy rules")
check("ta.value = r.text" in page,
      "the field is still only replaced on success")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_prompt_help_speed")
