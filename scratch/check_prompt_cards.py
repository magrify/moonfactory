"""Local checks for full prompts on job cards (Jack, 2026-11-27).

The owner screenshotted his library with a Wan 2.2 card cut
off mid-prompt ("a red hot air balloon rising over green
hills,") and no way to see or copy the rest. Pins:

- every card renderer that shows prompt text keeps the
  compact 120 character snippet by default and carries a
  Copy prompt control for any prompt, plus a Full prompt
  expand control whenever the snippet is truncated;
- the full text the card expands and copies is the job's
  real stored prompt: the owner's job payload serves it
  whole, the card element carries it (never the snippet),
  and the gallery payload no longer cuts it at 200;
- the expand fills a bounded, scrollable box as text, so a
  15,000 character MAGI-2 prompt cannot stretch the page;
- copying tries the clipboard API and falls back to the
  plain textarea path where the API refuses;
- the library page, which slices its card code out of the
  studio page, carries the same controls;
- the public gallery tiles show the snippet with the same
  expand and copy controls.

No Modal calls, no network, no generation.

Run:  modal-venv python scratch/check_prompt_cards.py
"""
import ast
import sys
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


html = app.APP_HTML

# -- the card: snippet by default, expand and copy on top -----------------

card = func_src("jobCardEl") or ""
# jobCardEl lives in the served page script, not a Python def;
# read it out of the page when the AST walk finds nothing.
if not card:
    a = html.index("function jobCardEl(j) {")
    b = html.index("\nfunction renderClipResults", a)
    card = html[a:b]
check("j.prompt.slice(0, 120)" in card,
      "the card no longer keeps its compact snippet by default")
check('data-prompt-copy="' in card and "Copy prompt" in card,
      "the card carries no Copy prompt control")
check('data-prompt-toggle="' in card and "Full prompt" in card,
      "the card carries no Full prompt expand control")
check("j.prompt.length > 120" in card,
      "the expand control is not held back to truncated prompts")
check('class="fullprompt" hidden' in card,
      "the card has no hidden full-prompt box")
check("div._prompt = j.prompt || '';" in card,
      "the card element does not carry the job's full prompt")

# -- the click handler: fill as text, copy with a fallback ----------------

click = ""
a = html.index("async function jobBoxClick(e) {")
b = html.index("\n$('jobs').addEventListener('click', jobBoxClick);", a)
click = html[a:b]
check("data-prompt-toggle" in click and "box.textContent" in click,
      "the expand control does not fill the box with the full prompt")
check("card._prompt" in click,
      "the handler does not read the card's full prompt")
check("data-prompt-copy" in click
      and "navigator.clipboard.writeText" in click,
      "the copy control does not use the clipboard API")
check("document.execCommand('copy')" in click,
      "the copy control has no fallback where the API refuses")

# -- the box is bounded ----------------------------------------------------

css = app._CSS
check(".fullprompt{" in css and "max-height:220px" in css
      and "overflow:auto" in css,
      "the full-prompt box is not bounded and scrollable")
check("white-space:pre-wrap" in css,
      "the full-prompt box does not wrap the prompt text")

# -- the payloads serve the real stored prompt -----------------------------

check('"prompt": job["prompt"],' in SRC,
      "the owner's job payload no longer serves the full prompt")
check('(job.get("prompt") or "")[:200]' not in SRC,
      "the gallery payload still truncates the prompt at 200")
check('"prompt": job.get("prompt") or "",' in SRC,
      "the gallery payload does not serve the full stored prompt")

# -- the library page slices the same card code ----------------------------

lib = app.LIBRARY_HTML
check("data-prompt-copy" in lib and "data-prompt-toggle" in lib,
      "the library page's cards lost the prompt controls")
check("navigator.clipboard.writeText" in lib
      and "document.execCommand('copy')" in lib,
      "the library page's copy control lost the API or the fallback")
check("div._prompt = j.prompt || '';" in lib,
      "the library page's cards do not carry the full prompt")

# -- the gallery tiles ------------------------------------------------------

gal = app.GALLERY_HTML
check("item.prompt.slice(0, 120)" in gal,
      "the gallery tile does not show the compact snippet")
check("full.textContent = item.prompt;" in gal,
      "the gallery tile's expand does not carry the full prompt")
check("Copy prompt" in gal and "Full prompt" in gal,
      "the gallery tile is missing its expand or copy control")
check("navigator.clipboard.writeText(item.prompt)" in gal
      and "document.execCommand('copy')" in gal,
      "the gallery copy control lost the API or the fallback")

if failures:
    print("FAIL check_prompt_cards")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_prompt_cards")
