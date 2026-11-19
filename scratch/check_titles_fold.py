"""Local checks for the Titles fold into Create (2026-11-18).
No Modal calls, no network: imports the platform module and pins
the studio surface at source level. The Titles tab and its pane
are gone; title cards are one entry in the Create model picker,
marked free; picking the entry swaps the composer to the template
form; submission still goes through /api/templates, so the job
kind, the Kaggle dispatch, and the pricing rules (zero credits,
paid 20 a day, unpaid against the five free lifetime clips) are
the lane's own, unchanged.

Run:  modal-venv python scratch/check_titles_fold.py
"""
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
HTML = app.APP_HTML

# --- the tab and the pane are gone --------------------------------------
check('id="tab-titles"' not in HTML, "studio tab bar has no Titles button")
check('id="titles-pane"' not in HTML, "studio has no titles pane")
check('id="make-title"' not in HTML, "the pane's own button is gone")
check("which === 'titles'" not in SRC, "showTab no longer maps a titles pane")
check("showTab('titles')" not in SRC, "nothing routes to a titles tab")
check("$('tab-titles')" not in SRC, "no tab wiring for titles remains")
check("$('titles-pane')" not in SRC, "no pane wiring for titles remains")

# --- the picker entry -----------------------------------------------------
check("const TITLE_CARDS_ID = 'title_cards'" in SRC,
      "the picker entry has a stable id")
catalog = SRC.split("function buildCatalog()", 1)[1].split(
    "function applyModelGates()", 1)[0]
check("tc.value = TITLE_CARDS_ID" in catalog,
      "buildCatalog appends the title cards entry")
check("tc.textContent = 'Title cards · Free'" in catalog,
      "the entry is labeled plainly and marked free")
check("sel.appendChild(tc)" in catalog,
      "the entry lands in the model picker")
check("o.value !== TITLE_CARDS_ID" in SRC,
      "the paid gate never disables the free entry")
check("sel.value !== TITLE_CARDS_ID" in SRC,
      "the paid gate never resets a free account off the entry")

# --- the form swap ---------------------------------------------------------
check('id="titlecard-fields"' in HTML, "the create card carries the title form")
check(HTML.count('id="tpl-template"') == 1,
      "exactly one template style picker exists")
check(HTML.index('id="titlecard-fields"') < HTML.index('id="tpl-template"')
      < HTML.index('<details class="more" id="more-opts">'),
      "the style picker lives inside the title card block in Create")
for name in ("title", "captions", "stat", "promo"):
    check(f'id="tpl-fields-{name}"' in HTML,
          f"the {name} field block moved into Create")
check('id="tpl-title-headline" type="text" maxlength="90"' in HTML,
      "headline keeps its 90 character limit")
check('id="tpl-title-subline" type="text" maxlength="120"' in HTML,
      "subline keeps its 120 character limit")
check('<optgroup label="Titles">' in HTML,
      "the style picker keeps its Titles group")
check("if (m === TITLE_CARDS_ID) { showTitleCardForm(); return; }" in SRC,
      "picking the entry swaps the form in updateModelUI")
swap = SRC.split("function showTitleCardForm()", 1)[1].split(
    "function updateModelUI()", 1)[0]
check("$('titlecard-fields').style.display = ''" in swap,
      "the swap shows the title form")
for el in ("$('dropzone')", "$('start-source')", "$('prompt')",
           "$('length-house')", "$('length-api')", "details.more",
           "$('api-opts')", "$('house-price')"):
    check(el in swap, f"the swap hides the video only field {el}")
check("$('create').textContent = 'Make it for free'" in swap,
      "the shared button becomes the free lane's button")
check("for (const name of ['title', 'captions', 'stat', 'promo'])"
      in SRC, "the style switcher still swaps the four field blocks")
check("if ($('model').value === TITLE_CARDS_ID) { await makeTitleCard(); return; }"
      in SRC, "the Create button routes title cards to the template submit")

# --- submission still dispatches the template lane -------------------------
check("async function makeTitleCard()" in SRC,
      "the template submit survives as a named function")
check("await api('/api/templates', { method: 'POST'" in SRC,
      "submission still posts to /api/templates")
check("const payload = { template: t, color: $('tpl-color').value };" in SRC,
      "the payload still carries the picked style and color")
check("await fetch('/api/templates/upload'" in SRC,
      "captions uploads still use the template upload route")
lane = SRC.split("# -- template lane", 1)[1].split(
    '@web.get("/api/jobs/{job_id}/thumb")', 1)[0]
check('"kind": "template"' in lane, "jobs keep the template kind")
check('"lane": "template"' in lane, "jobs keep the template lane")
check('"charged_cents": 0' in lane, "template jobs still charge zero")
check("TEMPLATE_DAILY_CAP_PAID" in lane,
      "paid accounts still hit the daily cap")
check("FREE_CLIPS_TOTAL" in lane,
      "unpaid renders still count against the free lifetime clips")
check("_hf_maybe_trigger()" in lane,
      "creation still wakes the Kaggle worker")
check(app.TEMPLATE_DAILY_CAP_PAID == 20, "the paid daily cap is still 20")
check(set(app.HF_TEMPLATES) == {"title", "captions", "stat", "promo",
                                 "story", "sting"},
      "all six templates still exist server side")
check(app.HF_TEMPLATES["title"]["fields"] == ("headline", "subline"),
      "the title card still takes a headline and a subline")

# --- the library label is untouched ----------------------------------------
check('(job.get("template_name") or "Title card")' in SRC,
      "library rows still label template jobs by their template name")

if failures:
    print("TITLES FOLD CHECK FAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL TITLES FOLD CHECKS PASSED")
