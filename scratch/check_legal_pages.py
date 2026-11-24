"""Local checks for the legal protection pack (Jack, 2026-11-23).

Pins the load-bearing clauses of the finalized Terms of Service
and the plain-words Acceptable Use Policy on the pages the app
serves, exactly as Jack decided them:

- the blanket rule: anything and everything illegal is not
  permitted on Moonfactory, categories are examples only;
- indemnification covering Moonfactory, Magrify LLC, and each of
  its owners, members, managers, and officers personally;
- child safety naming NCMEC and law enforcement reporting;
- the DMCA notice procedure naming Magrify LLC at
  support@magrify.com as the designated agent contact, with NO
  claim anywhere that an agent is registered with the U.S.
  Copyright Office (Jack deferred that filing until Moonfactory
  has 5+ customers, so claiming it would be false);
- binding individual arbitration and the class action waiver
  (Jack: in);
- governing law Texas plus applicable United States federal law,
  venue Dallas County;
- age floors: 13 for the service, 18+ for Uncensored models;
- the liability cap: the greater of what the user paid in the
  3 months before the claim or $100;
- the Acceptable Use page lives at /acceptable-use, is linked
  wherever Terms and Privacy are linked (info footer, studio
  footer, gallery footer, sitemap), and its visible text follows
  Jack's plain-words copy rule: no colons, no semicolons, no
  hyphens or dashes;
- the Privacy page carries the matching gallery exception (flagged
  uncensored prompts and image-built uncensored clips never appear
  in the gallery) and the law-enforcement preservation line, so it
  does not contradict the Terms.

No Modal calls, no network: reads the page constants off the
imported app and the route/link wiring off the source.

Run:  modal-venv python scratch/check_legal_pages.py
"""
import re
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


def visible(html):
    t = re.sub(r"<[^>]+>", " ", html)
    t = t.replace("&quot;", '"').replace("&amp;", "&")
    t = t.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t)


TERMS = visible(app.TERMS_HTML)
PRIVACY = visible(app.PRIVACY_HTML)
AUP = visible(app.AUP_HTML)

# -- Terms: the clauses Jack approved --
check("Anything and everything illegal is not permitted on "
      "Moonfactory" in TERMS,
      "terms lost the blanket illegal ban")
check("including, but not limited to" in TERMS,
      "terms lost the not-limited-to framing over the categories")
check("indemnify, and hold harmless Moonfactory, Magrify LLC, and "
      "each of its owners, members, managers, officers" in TERMS,
      "terms lost the indemnity covering the owners personally")
check("National Center for Missing & Exploited Children (NCMEC) "
      "and to law enforcement" in TERMS,
      "terms no longer name NCMEC and law enforcement reporting")
check("Designated agent" in TERMS and "support@magrify.com" in TERMS,
      "terms lost the DMCA designated agent contact")
check("Copyright Office" not in TERMS,
      "terms mention the Copyright Office; the agent registration "
      "is deferred, so no registration may be claimed")
check("binding, individual arbitration" in TERMS
      and "class action, class arbitration" in TERMS,
      "terms lost the arbitration and class action waiver")
check("greater of what you paid us in the 3 months before the "
      "claim or $100" in TERMS,
      "terms lost the $100 floor liability cap")
check("at least 13 years old" in TERMS,
      "terms lost the 13 year service age floor")
check("at least 18 years old to use any model marked Uncensored"
      in TERMS,
      "terms lost the 18+ Uncensored gate")
check("laws of the State of Texas and applicable United States "
      "federal law" in TERMS and "Dallas County, Texas" in TERMS,
      "terms lost Texas plus federal governing law or Dallas venue")
check("never appear in the public gallery" in TERMS,
      "terms lost the gallery exclusion for flagged and image "
      "built uncensored generations")

# -- Acceptable Use: reachable, linked, plain words --
check('@web.get("/acceptable-use"' in SRC
      and "return HTMLResponse(AUP_HTML)" in SRC,
      "the /acceptable-use route is not wired to AUP_HTML")
check(SRC.count('href="/acceptable-use"') >= 3,
      "acceptable use is not linked from the footers")
check('"/acceptable-use"' in SRC.split("def sitemap_xml", 1)[1]
      .split("def ", 1)[0],
      "acceptable use is missing from the sitemap")
check("Nothing illegal is allowed on Moonfactory" in AUP,
      "the acceptable use page lost its blanket lead line")
check("National Center for Missing and Exploited Children" in AUP,
      "the acceptable use page no longer names NCMEC")

aup_body = AUP.split("What is not allowed on Moonfactory", 1)[1]
aup_body = aup_body.split("Moonfactory Help Terms Privacy", 1)[0]
for ch in (":", ";", "-", "‐", "‑", "–", "—"):
    check(ch not in aup_body,
          f"acceptable use body text contains a forbidden {ch!r}")

# -- Privacy: aligned with the Terms, not contradicting them --
check("never appear in the gallery" in PRIVACY,
      "privacy lost the uncensored gallery exception")
check("shared with law enforcement" in PRIVACY,
      "privacy lost the law enforcement preservation line")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_legal_pages")
