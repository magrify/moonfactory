"""Local checks for the creation-time illegal-content gate
(Jack, 2026-11-24).

Rule: every creation path runs its user text through the
gate before any quote, hold, charge, free count, or
dispatch, and an illegal request is refused outright with
one plain message that never names what matched. No job is
created, nothing is charged, no free clip is consumed. The
gate blocks ILLEGAL content in three categories, mirroring
Terms section 4: "hard" (sexual content involving minors),
"sexual-violence" (rape, non-consent, incest, bestiality,
snuff), and "real-person" (sexual content naming a real
person, NCII and deepfakes). It is not the gallery NSFW
scan: lawful adult prompts pass it exactly as before, on
every lane including the uncensored ones.

Every refusal writes a server-side record (account id,
timestamp, lane, category, the prompt as typed, the
creating IP) that no user-facing or public payload carries.
The ladder: a hard refusal suspends the account at once;
any other category suspends at the third refusal on the
account; a suspended account is refused on every creation
path, whatever it asks for.

No Modal calls, no network: the scanner is exercised
against plain strings, the refusal recorder and the gate
helper are extracted and run against fake Dict stores, and
the wiring into every creation path is pinned in source.

Run:  modal-venv python scratch/check_illegal_gate.py
"""
import ast
import secrets as pysecrets
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


scan = app.prompt_is_illegal

# -- hard: sexual content involving minors ---------------------------

for text in (
    "loli fan art",
    "an underage model in a swimsuit",
    "jailbait pics",
    "child porn",
    "a preteen in a bikini",
    # context: a minor word next to a sexual signal
    "a sexy teenager at the beach",
    "an erotic story about a schoolgirl",
    "kids having sex",
    "a sexy little girl costume",
    # stated ages, digits and spelled out
    "a nude 15 year old",
    "sixteen year old in a sex scene",
    "a 17 year old girl in a bikini, erotic",
    # evasions: aging up, leet, letter spacing
    "an aged up schoolgirl, nude",
    "l0li art",
    "c h i l d p o r n",
):
    check(scan(text) == "hard",
          f"hard prompt not blocked as hard: {text!r} "
          f"(got {scan(text)!r})")

# -- sexual violence and non-consent ---------------------------------

for text in (
    "a rape scene",
    "a woman being raped in an alley",
    "non-consensual sex tape",
    "a non consensual encounter",
    "sex without consent",
    "bestiality video",
    "incest porn",
    "a snuff film recreation",
    # context rules: family or helpless next to an act
    "sex with his sister",
    "having sex with her stepbrother",
    "sex with a sleeping woman",
    "a blowjob from an unconscious woman",
):
    check(scan(text) == "sexual-violence",
          f"violence prompt not blocked: {text!r} "
          f"(got {scan(text)!r})")

# -- real-person sexual content --------------------------------------

for text in (
    "a nude photo of Taylor Swift",
    "an erotic video of Emma Watson",
    "a sex tape of Jennifer Lawrence",
    "TAYLOR SWIFT nude edit",
    "deepfake porn of a celebrity",
    "a deepfake of my ex, nude",
):
    check(scan(text) == "real-person",
          f"real-person prompt not blocked: {text!r} "
          f"(got {scan(text)!r})")

# -- lawful adult content passes, on any lane -------------------------

for text in (
    "a nude woman on a beach",
    "an erotic boudoir portrait of two adults",
    "two adults having consensual sex in a hotel room",
    "a sexy milf in lingerie",
    "a bdsm scene between two consenting adults",
    "an adult film star portrait, glamorous",
    "a woman in a sexy dress at a party",
    "topless sunbather, golden hour",
):
    check(scan(text) is None,
          f"lawful adult prompt blocked: {text!r} "
          f"(got {scan(text)!r})")

# -- ordinary prompts stay clean --------------------------------------

for text in (
    "a red hot air balloon rising over green hills",
    "a kid riding a bike",
    "teenage mutant ninja turtles",
    "a baby elephant at the zoo",
    "mother and child at the park",
    "my dad grilling burgers",
    "a sleeping dragon in a cave",
    "a portrait of a 12 year old girl smiling",
    "a portrait of Barack Obama speaking",
    "a nude beach in New York at sunset",
    "a Victoria Secret style lingerie catalog shoot",
    "my brother nailed the guitar solo",
    # the education shield: weak sexual words, no act
    "a mom talks about sex education",
    "sexual abuse prevention education for kids",
    "sex education for kids",
    # the gallery scan's Scunthorpe set
    "a cock crowing at dawn on a farm",
    "a sperm whale diving under a boat",
    "chicken breast on a plate with grapes",
    "a documentary about the Essex coast",
    "the therapist's office on main street",
    "a happy class of film students",
    "a peacock in a cucumber garden",
    "the sexton keeps the document archive",
    "",
    None,
):
    check(scan(text) is None,
          f"ordinary prompt blocked: {text!r} "
          f"(got {scan(text)!r})")

# -- the refusal record and the ladder --------------------------------

fake_refusals = {}
fake_users = {}
record = None
ns = {"refusals": fake_refusals, "users": fake_users,
      "pysecrets": pysecrets, "_now": app._now,
      "ILLEGAL_SUSPEND_AFTER": app.ILLEGAL_SUSPEND_AFTER}
exec(func_src("record_illegal_refusal"), ns)
record = ns["record_illegal_refusal"]


def fresh_user(uid="u1"):
    return {"id": uid, "email": f"{uid}@example.com",
            "plan": "free", "verified": True}


# A soft refusal writes the full record and does not suspend.
u = fresh_user()
suspended = record(u, "wan3", "real-person",
                   "a nude photo of Taylor Swift", "203.0.113.9")
check(suspended is False, "first soft refusal suspended")
check(u.get("illegal_refusals") == 1,
      "soft refusal did not count on the account")
check(len(fake_refusals) == 1, "soft refusal wrote no record")
rec = next(iter(fake_refusals.values()))
check(rec["user_id"] == "u1" and rec["lane"] == "wan3"
      and rec["category"] == "real-person"
      and rec["prompt"] == "a nude photo of Taylor Swift"
      and rec["created_ip"] == "203.0.113.9"
      and rec["created_at"] and rec["id"],
      f"refusal record fields wrong: {rec!r}")
check(fake_users["id:u1"]["illegal_refusals"] == 1,
      "the refusal count was not saved on the user record")

# The third soft refusal suspends.
record(u, "image", "sexual-violence", "a rape scene", "203.0.113.9")
check(u.get("suspended") is not True,
      "second soft refusal suspended early")
suspended = record(u, "short", "real-person",
                   "an erotic video of Emma Watson", "203.0.113.9")
check(suspended is True and u.get("suspended") is True,
      "third soft refusal did not suspend")
check(len(fake_refusals) == 3, "soft refusals wrote wrong count")

# A hard refusal suspends on the first one.
u2 = fresh_user("u2")
suspended = record(u2, "wan22_uncut", "hard",
                   "loli fan art", "198.51.100.2")
check(suspended is True and u2.get("suspended") is True,
      "hard refusal did not suspend immediately")
check(u2.get("illegal_refusals") == 1,
      "hard refusal did not count on the account")

# -- the gate helper: refusal, message, suspension --------------------


class FakeHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


gns = {"HTTPException": FakeHTTPException,
       "ILLEGAL_REFUSAL_MESSAGE": app.ILLEGAL_REFUSAL_MESSAGE,
       "prompt_is_illegal": app.prompt_is_illegal,
       "record_illegal_refusal": record}
exec(func_src("enforce_prompt_gate"), gns)
gate = gns["enforce_prompt_gate"]

# Clean text passes and writes nothing.
before = len(fake_refusals)
u3 = fresh_user("u3")
gate(u3, "wan3", "192.0.2.1", "a lighthouse at dusk", None, "")
check(len(fake_refusals) == before,
      "a clean request wrote a refusal record")

# An illegal text is refused with the one plain message,
# which never echoes the matched words, and the refusal is
# the only thing written (no job exists anywhere to write:
# the gate raises before any creation code runs, pinned by
# the wiring order below).
try:
    gate(u3, "zimage_unc", "192.0.2.1", "loli fan art")
    check(False, "hard prompt passed the gate")
except FakeHTTPException as exc:
    check(exc.status_code == 400, "gate refusal is not a 400")
    check(exc.detail == "This request is not allowed on "
          "Moonfactory.", f"gate message wrong: {exc.detail!r}")
    check("loli" not in exc.detail,
          "gate message echoes the matched term")
check(u3.get("suspended") is True,
      "hard gate hit did not suspend the account")

# A suspended account is refused even for clean text, and
# the refusal writes no further record.
before = len(fake_refusals)
try:
    gate(u3, "wan3", "192.0.2.1", "a lighthouse at dusk")
    check(False, "suspended account passed the gate")
except FakeHTTPException as exc:
    check(exc.status_code == 400
          and exc.detail == app.ILLEGAL_REFUSAL_MESSAGE,
          "suspended refusal is not the plain 400")
check(len(fake_refusals) == before,
      "a suspended account's clean attempt wrote a record")

# -- wiring pins -------------------------------------------------------

# The gate helper checks suspension first and records
# through the one recorder.
gate_src = func_src("enforce_prompt_gate")
check('user.get("suspended")' in gate_src
      and gate_src.index('user.get("suspended")')
      < gate_src.index("prompt_is_illegal"),
      "the gate does not check suspension before scanning")
check("record_illegal_refusal" in gate_src,
      "the gate does not write refusal records")

# Every creation path runs the gate.
for fname, label in (
    ("create_api_job", "API video jobs (and drama retakes)"),
    ("create_job", "house video jobs"),
    ("create_image", "image jobs"),
    ("create_clip", "clips"),
    ("create_template", "templates"),
    ("create_short", "faceless shorts"),
    ("create_drama_draft", "drama draft (idea and script)"),
    ("create_drama", "drama creation"),
):
    check("enforce_prompt_gate" in func_src(fname),
          f"{label} do not run the gate ({fname})")

# In create_api_job the gate runs before the quote and hold.
api_src = func_src("create_api_job")
check(api_src.index("enforce_prompt_gate")
      < api_src.index("per_second_estimate"),
      "create_api_job gates after the quote")
# In create_image the gate runs before any job is stored.
img_src = func_src("create_image")
check(img_src.index("enforce_prompt_gate")
      < img_src.index("jobs[job"),
      "create_image gates after the job is stored")
# In create_short the topic is gated before the writing
# route, and the script before the free count.
short_src = func_src("create_short")
check(short_src.index("enforce_prompt_gate")
      < short_src.index("script_from_topic"),
      "create_short gates the topic after the writing route")
check(short_src.rindex("enforce_prompt_gate")
      < short_src.index("is_free_job = True"),
      "create_short gates the script after the free count")
# In create_drama the storyboard is gated before the hold.
drama_src = func_src("create_drama")
check(drama_src.index("enforce_prompt_gate")
      < drama_src.index("_drama_hold_rails"),
      "create_drama gates after the hold")

# The refusal store exists and stays out of public payloads.
check('modal.Dict.from_name("moonfactory-refusals"' in SRC,
      "the refusals Dict is not declared")
for fname in ("public_job", "public_gallery_item",
              "gallery_list", "list_jobs"):
    src = func_src(fname)
    check("refusals" not in src and "illegal_refusals" not in src
          and "suspended" not in src,
          f"{fname} exposes gate state")
# The gallery scan is untouched by the gate: job_is_nsfw
# still reads only the stored user prompt on uncensored
# lanes, and the gate never calls gallery code.
check("prompt_is_illegal" not in func_src("job_is_nsfw"),
      "job_is_nsfw was changed by the gate")
check("gallery_visible" not in func_src("prompt_is_illegal"),
      "the gate reads gallery state")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_illegal_gate")
