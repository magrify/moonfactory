"""Error surfacing checks (2026-11-28, Jack's site-wide fix).

The failures this pins against, both from Jack's phone on
2026-11-28:

- "Make image" on the Grok Imagine Image 2.0 lanes answered
  a raw "HTTP 500" printed under the button. The server half
  of that failure: commit 99edadc (the atomic free-slot
  reservation pass, 2026-11-27) wrapped the image shelf
  path's post-submit free counter in `if not free_reserved:`
  and added a `free_reserved` read to the submit failure
  handler, but bound the variable only inside the free lane
  branch, so every paid shelf image create died with an
  UnboundLocalError AFTER the balance debit and the provider
  submit (Modal log, POST /api/images -> 500, app.py line
  18541 in create_image_core). The client half: api() threw
  `body.detail || ('HTTP ' + r.status)`, and a 500's body
  carries no detail, so the page printed the bare status.
- "Improve my prompt" printed the browser's own "Load
  failed" the same way: a rejected fetch's TypeError message
  went straight into the page through `msg(..., e.message)`.

Pinned here:

- the image shelf branch binds free_reserved at branch
  level (beside served_free), before the free lane branch,
  so both post-submit reads are bound for every lane;
- an unhandled server error answers with the plain-words
  JSON detail, never the framework's bare text;
- the studio client carries the one shared error voice:
  api() throws only plain sentences (offline on a rejected
  fetch, httpErrText on a bad status), every raw fetch site
  builds its error through httpErrText, no 'HTTP ' + status
  construction survives anywhere, and no caught error's
  .message is printed anywhere except inside errText itself;
- errText and httpErrText, extracted from the source and
  run in node, map the exact raw shapes Jack saw to the
  plain sentences and pass the app's own server messages
  through untouched.
"""

import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "platform" / "app.py"
TEXT = SRC.read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# -- the server half: free_reserved is bound for every lane --

core_at = TEXT.index("def create_image_core")
core = TEXT[core_at:TEXT.index("def ", core_at + 100)]
shelf = core[core.index('free_lane = bool(lane.get("free"))'):]
shelf = shelf[:shelf.index("if free_lane:")]
check("free_reserved = False" in shelf,
      "the image shelf branch no longer binds free_reserved "
      "before the free lane branch: a paid lane's create "
      "reaches the post-submit reads unbound and 500s after "
      "the charge (the 2026-11-28 Make image failure)")
check("if not free_reserved:" in core
      and "count_generation(user, free_job=served_free)" in core,
      "the shelf path's post-submit free counter changed "
      "shape; re-pin what guards it")

# -- the server half: unhandled errors answer in plain words --

check("@web.exception_handler(Exception)" in TEXT
      and "something went wrong on our side." in TEXT
      and "Try again in a moment." in TEXT,
      "the plain-words 500 handler is gone: an unhandled "
      "error would answer with the framework's bare "
      "'Internal Server Error' text again")

# -- the client half: one shared error voice --

check("'HTTP ' +" not in TEXT and '"HTTP " +' not in TEXT,
      "a raw 'HTTP ' + status construction survives in the "
      "client: a bare status can print into the page again")
check("throw new Error(ERR_OFFLINE)" in TEXT,
      "api() no longer turns a rejected fetch into the "
      "plain offline sentence")
check("throw new Error(httpErrText(r.status, body))" in TEXT,
      "api() no longer builds its bad-status error through "
      "httpErrText")
check(TEXT.count("httpErrText(r.status, q)") >= 6,
      "a raw fetch site (photo, clip, editclone, or "
      "template upload) no longer builds its error through "
      "httpErrText")

# No caught error's .message prints outside errText itself.
errtext_at = TEXT.index("function errText(e) {")
errtext_end = TEXT.index("\n}", errtext_at)
outside = TEXT[:errtext_at] + TEXT[errtext_end:]
check("e.message" not in outside and "err.message" not in outside,
      "a caught error's .message is printed outside errText "
      "somewhere: a raw browser error (Load failed) can "
      "reach the page again")

# -- behavior: run the real helpers in node --

helpers = TEXT[TEXT.index("const ERR_OFFLINE"):errtext_end + 2]
driver = helpers + """
const cases = [
  [errText(new TypeError('Load failed')),
   'That did not go through. Check your connection and try again.'],
  [errText(new TypeError('Failed to fetch')),
   'That did not go through. Check your connection and try again.'],
  [errText(new Error('HTTP 500')),
   'Something went wrong on our side. Try again in a moment.'],
  [errText(new Error('your balance is too low for this image. Top up first')),
   'your balance is too low for this image. Top up first'],
  [errText(new Error('')), 'That did not go through. Try again.'],
  [httpErrText(500, {detail: 'Internal Server Error'}),
   'Something went wrong on our side. Try again in a moment.'],
  [httpErrText(402, {detail: 'top up first'}), 'top up first'],
  [httpErrText(422, {detail: [{loc: ['body']}]}),
   'That did not go through. Try again.'],
  [httpErrText(404, {}), 'That did not go through. Try again.'],
];
let bad = 0;
for (const [got, want] of cases) {
  if (got !== want) { bad++; console.log('MISMATCH', JSON.stringify(got), '!=', JSON.stringify(want)); }
}
process.exit(bad ? 1 : 0);
"""
proc = subprocess.run(["node", "-e", driver],
                      capture_output=True, text=True, timeout=60)
check(proc.returncode == 0,
      "errText/httpErrText behavior drifted from the plain "
      f"sentences: {proc.stdout.strip()} {proc.stderr.strip()}")

if failures:
    print("check_error_surfacing: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("check_error_surfacing: PASS")
