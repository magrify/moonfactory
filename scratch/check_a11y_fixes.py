"""Local checks for the 2026-11-18 accessibility fixes.

Jack approved the four no decision fixes from the site audit
(~/workspace/audits/site-audit-2026-11-18.md, Moonfactory F1,
F3, F4, F5):

1. Live announcements: a polite status region and an assertive
   alert region in the studio page, with msg() routed through
   them and the job poll loop announcing state changes.
2. The password label wired to its input with for and id, and
   autocomplete split by flow (new-password for sign up,
   current-password for sign in) on the one shared field.
3. A focus-visible ring that reads on the dark theme, with no
   default outline removed anywhere.
4. A prefers-reduced-motion block that stills transitions and
   animations, the treatment magrify.com gives its own motion.

No Modal, no network, no deploys: reads app.py, pins the markup,
CSS, and JS wiring in source, and syntax checks the studio
script with node when node is available.

Run:  modal-venv python scratch/check_a11y_fixes.py
"""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


# The shared stylesheet literal, for the CSS pins.
CSS = None
for node in ast.walk(TREE):
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_CSS":
        CSS = ast.literal_eval(node.value)
check(CSS is not None, "_CSS literal not found")

# ---- Fix 1: live regions present and wired -------------------
check('<div id="live-status" class="sr-only" role="status" aria-live="polite"></div>' in SRC,
      "polite live region missing from the studio page")
check('<div id="live-alert" class="sr-only" role="alert" aria-live="assertive"></div>' in SRC,
      "assertive live region missing from the studio page")
check(".sr-only{position:absolute;width:1px;height:1px" in CSS,
      "sr-only visually hidden utility missing from _CSS")
check("function announce(text, assertive)" in SRC,
      "announce helper missing")
check("assertive ? 'live-alert' : 'live-status'" in SRC,
      "announce does not pick the region by assertiveness")
check("announce(text, cls === 'err')" in SRC,
      "msg() does not route errors to the assertive region")
check("function msg(id, text, cls) { const el = $(id); el.textContent = text; "
      "el.className = cls || ''; announce(text, cls === 'err'); }" in SRC,
      "msg() is not the single routed display point anymore")
check("function announceJobStates(jobs)" in SRC,
      "job state announcer missing")
check("announceJobStates(r.jobs);" in SRC,
      "the job poll loop does not announce state changes")
check("jobsPrimed" in SRC and "jobStatusSeen" in SRC,
      "job state diffing state missing")
for phrase in ("is ready.", "failed. ", "is rendering."):
    check(phrase in SRC, f"job state announcement wording missing: {phrase!r}")

# ---- Fix 2: password label and autocomplete ------------------
check('<label for="password">Password (8+ characters)</label>' in SRC,
      "password label is not wired with for")
check('<input id="password" type="password" autocomplete="current-password">' in SRC,
      "password input lost its id or its sign in autocomplete")
check("$('password').autocomplete = 'new-password';" in SRC,
      "sign up does not set new-password on the shared field")
check("$('password').autocomplete = 'current-password';" in SRC,
      "sign in does not set current-password on the shared field")
check('<label for="email">Email</label>' in SRC,
      "email label is not wired with for")

# ---- Fix 3: focus visible, nothing removed -------------------
check(":focus-visible{outline:2px solid var(--accent-bright);outline-offset:2px}" in CSS,
      "focus-visible ring missing from _CSS")
check(not re.search(r"outline\s*:\s*(none|0)\b", SRC),
      "a default outline is removed somewhere in app.py")

# ---- Fix 4: reduced motion -----------------------------------
check("@media (prefers-reduced-motion: reduce)" in CSS,
      "reduced motion block missing from _CSS")
check("transition-duration:.01ms !important" in CSS
      and "animation-duration:.01ms !important" in CSS,
      "reduced motion block does not still transitions and animations")

# ---- Studio script still parses as JavaScript ----------------
node = shutil.which("node")
if node:
    scripts = re.findall(r"<script>(.*?)</script>", SRC, re.S)
    studio = max(scripts, key=len)
    check("announceJobStates" in studio and "function msg" in studio,
          "the largest script block is not the studio script")
    with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False) as f:
        f.write(studio)
        path = f.name
    proc = subprocess.run([node, "--check", path],
                          capture_output=True, text=True)
    check(proc.returncode == 0,
          "studio script fails node --check: " + proc.stderr[:400])
else:
    print("note: node not found, studio script syntax check skipped")

if failures:
    print("A11Y FIX CHECKS FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL A11Y FIX CHECKS PASSED")
