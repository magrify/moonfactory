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
- age floor: 18 for the service (raised from 13 by Jack,
  2026-11-27), 18+ for Uncensored models;
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
check("at least 18 years old" in TERMS,
      "terms lost the 18 year service age floor")
check("at least 13 years old" not in TERMS,
      "terms still carry the old 13 year service age floor")
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

# -- Terms v2 hardening (Jack, 2026-11-23): the strongest lawful
# clause set, layered so Texas law cannot void it --
check("Section 230 of the Communications Decency Act" in TERMS,
      "terms lost the Section 230 reservation of rights")
check("512(m)" in TERMS and "shall be construed to condition" in TERMS,
      "terms lost the DMCA 512(m) no duty to monitor statement")
check("does not limit any duty that applies once we obtain actual "
      "knowledge" in TERMS,
      "terms lost the actual knowledge carve out on no monitoring")
check("Time limit for bringing claims" in TERMS
      and "within one year after the claim accrues" in TERMS
      and "permanently barred" in TERMS and "16.070" in TERMS,
      "terms lost the layered one year / Texas 16.070 claim limit")
check("WAIVE ANY RIGHT TO A TRIAL BY JURY" in TERMS,
      "terms lost the express conspicuous jury trial waiver")
check("American Arbitration Association" in TERMS
      and "responsible for our reasonable attorneys' fees" in TERMS
      and "batches of up to 25 claimants" in TERMS,
      "terms lost the AAA, fee shifting, or mass filing terms")
check("Office of Foreign Assets Control" in TERMS,
      "terms lost the sanctions and export control clause")
check("grounds for immediate termination" in TERMS,
      "terms lost circumvention as a terminating violation")
check("you bear all risk of any use you make of Output" in TERMS,
      "terms lost the output risk allocation")
check("consent of every real person depicted, named, or imitated"
      in TERMS,
      "terms lost the warranty covering all inputs and uploads")
check("EXTENDS TO CLAIMS ARISING IN WHOLE OR IN PART FROM THE "
      "NEGLIGENCE" in TERMS,
      "terms lost the express negligence indemnity extension")
check("costs of responding to subpoenas" in TERMS,
      "terms lost indemnity coverage of legal process costs")
check("fails of its essential purpose" in TERMS,
      "terms lost the essential purpose limitation language")
check("reformed to the minimum extent necessary" in TERMS
      and "any attempt to do so is void" in TERMS
      and "beyond our reasonable control" in TERMS
      and "intended third-party beneficiaries of those sections"
      in TERMS and "without posting a bond" in TERMS
      and "Notices to us under these Terms must be sent to Magrify "
      "LLC" in TERMS,
      "terms lost a v2 general provision (reformation, assignment, "
      "force majeure, beneficiaries, injunctions, notices)")
check("full trade ban" in AUP and "We do not check every prompt" in AUP,
      "acceptable use lost the v2 sanctions or no check lines")

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
