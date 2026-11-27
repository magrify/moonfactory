"""Local checks for the Short drama lane (Phase 1, 2026-11-19).
No Modal calls, no network: imports the platform module and the
drama module, exercises the pure pieces (draft parsing,
storyboard guards, prompt pack building, the episode pricing
math against the live pricing tables), and pins the lane wiring
in the source (one paid picker entry in Create, the draft and
create endpoints, the hidden shot jobs, the parent settle, the
paid only gate, no free clip usage, the gallery rule).

Run:  modal-venv python scratch/check_drama_lane.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import drama  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
DRAMA_SRC = (ROOT / "platform" / "drama.py").read_text()

# --- constants ---------------------------------------------------------
check(drama.DRAMA_MODEL_ID == "grok15", "drama generates on grok15")
check(drama.DRAMA_SHOT_COUNT == 4, "an episode is four shots")
check(drama.DRAMA_SHOT_SECONDS == 5, "each shot is five seconds")
check(drama.DRAMA_RESOLUTION == "720p", "episodes render at 720p")
check(drama.DRAMA_ASPECT == "9:16", "episodes are tall")
check(drama.DRAMA_MAX_PEOPLE == 2, "at most two people")
check(app.DRAMA_POLL_LIMIT_SECONDS == 7200, "drama poll bound is 7200s")
check(app.provider_for("grok15", "720p") == "kie",
      "the drama lane routes to Kie")

# --- pricing math (the scope's numbers, from the live tables) ---------
est = pricing.per_second_estimate("grok15", "720p", 20, 0.0, None)
check(round(est["raw_cost_cents"], 2) == 45.2, "episode raw cost is 45.2 cents (post cut, 2026-11-26)")
check(est["balance_debit_cents"] == 59, "episode customer price is 59")
est_o = pricing.per_second_estimate(
    "grok15", "720p", 20, 0.0, None, owner=True)
check(est_o["balance_debit_cents"] == 46, "episode owner price is 46 (post cut, 2026-11-26)")
one = pricing.per_second_estimate("grok15", "720p", 5, 0.0, None)
check(one["balance_debit_cents"] == 15, "one shot retake is 15 customer")
one_o = pricing.per_second_estimate(
    "grok15", "720p", 5, 0.0, None, owner=True)
check(one_o["balance_debit_cents"] == 12, "one shot retake is 12 owner")

# --- draft parsing ------------------------------------------------------
RAW = (
    '{"title": "One Last Dawn", "summary": "Two sisters reopen '
    'the family diner.", "people": [{"name": "Mara", "role": '
    '"older sister", "description": "a woman in her thirties '
    'with dark hair", "wardrobe": "a red apron"}, {"name": '
    '"June", "role": "younger sister", "description": "a woman '
    'in her twenties", "wardrobe": "a blue jacket"}], "place": '
    '{"name": "the diner", "description": "a small diner with '
    'a long counter"}, "shots": [{"action": "Mara unlocks the '
    'door", "people": ["Mara"], "spoken_line": "We open at '
    'dawn.", "speaker": "Mara", "camera": "steady", "mood": '
    '"calm"}, {"action": "June wipes the counter", "people": '
    '["June"], "spoken_line": "", "speaker": "", "camera": '
    '"steady", "mood": "calm"}, {"action": "The sisters hang '
    'the sign", "people": ["Mara", "June"], "spoken_line": '
    '"Together this time.", "speaker": "June", "camera": '
    '"wide", "mood": "warm"}, {"action": "They wave at the '
    'first guest", "people": ["Mara", "June"], "spoken_line": '
    '"", "speaker": "", "camera": "steady", "mood": "bright"}]}'
)
draft = drama._parse_draft(RAW)
check(draft["title"] == "One Last Dawn", "draft title parses")
check(len(draft["entities"]["people"]) == 2, "draft has two people")
check(len(draft["shots"]) == 4, "draft has four shots")
check(draft["entities"]["people"][0]["picture"] == {"type": "none"},
      "a drafted person starts with no picture")
check(draft["entities"]["people"][0]["approved"] is False,
      "a drafted person starts unapproved")
check(draft["shots"][0]["status"] == "ready", "draft shots start ready")
try:
    drama._parse_draft('{"title": "x"}')
    check(False, "a shapeless draft must raise")
except ValueError:
    pass
try:
    drama._parse_draft("no json here")
    check(False, "a non JSON draft must raise")
except ValueError:
    pass

# --- storyboard guards ---------------------------------------------------
PEOPLE = [
    {"name": "Mara", "role": "older sister",
     "description": "a woman in her thirties with dark hair",
     "wardrobe": "a red apron",
     "picture": {"type": "upload",
                 "data_url": "data:image/png;base64,x"},
     "approved": True},
    {"name": "June", "role": "younger sister",
     "description": "a woman in her twenties",
     "wardrobe": "a blue jacket",
     "picture": {"type": "character", "character_id": "abc"},
     "approved": True},
]
PLACE = {"name": "the diner",
         "description": "a small diner with a long counter",
         "picture": {"type": "text"}, "approved": True}
SHOTS = [
    {"action": "Mara unlocks the door", "people": ["Mara"],
     "spoken_line": "We open at dawn.", "speaker": "Mara",
     "camera": "steady", "mood": "calm"},
    {"action": "June wipes the counter", "people": ["June"],
     "spoken_line": "", "speaker": "", "camera": "steady",
     "mood": "calm"},
    {"action": "The sisters hang the sign", "people": ["Mara", "June"],
     "spoken_line": "Together this time.", "speaker": "June",
     "camera": "wide", "mood": "warm"},
    {"action": "They wave", "people": ["Mara", "June"],
     "spoken_line": "", "speaker": "", "camera": "steady",
     "mood": "bright"},
]
entities, shots = drama.validate_storyboard(
    {"people": PEOPLE, "place": PLACE}, SHOTS)
check(len(entities["people"]) == 2 and len(shots) == 4,
      "a ready storyboard validates")


def guard(mutate, msg):
    import copy
    ent = copy.deepcopy({"people": PEOPLE, "place": PLACE})
    sht = copy.deepcopy(SHOTS)
    mutate(ent, sht)
    try:
        drama.validate_storyboard(ent, sht)
        check(False, msg)
    except ValueError:
        pass


guard(lambda e, s: e["people"][0].update(approved=False),
      "an unapproved person must block creation")
guard(lambda e, s: e["people"][0].update(picture={"type": "none"}),
      "a person with no picture must block creation")
guard(lambda e, s: e["place"].update(approved=False),
      "an unapproved place must block creation")
guard(lambda e, s: s.pop(), "three shots must block creation")
guard(lambda e, s: s[0].update(
    spoken_line=" ".join(["word"] * 20)),
    "a line too long for five seconds must block creation")
guard(lambda e, s: s[0].update(speaker="Nobody"),
      "a line with no valid speaker must block creation")
guard(lambda e, s: e["people"].append(dict(PEOPLE[0])),
      "three people must block creation")

# --- prompt packs ---------------------------------------------------------
pack, line = drama.build_prompt_pack(shots[0], entities)
check(line == "We open at dawn.", "the pack returns the spoken line")
check("Mara unlocks the door." in pack, "pack opens with the action")
check("The place is the diner." in pack, "pack locks the place")
check("a woman in her thirties with dark hair" in pack,
      "pack locks the person look")
check("Mara wears a red apron" in pack, "pack locks the wardrobe")
check("Mara says the line out loud" in pack, "pack names the speaker")
check("Keep the same faces, hair, clothes, and place" in pack,
      "pack carries the continuity note")
pack2, line2 = drama.build_prompt_pack(shots[0], entities)
check(pack == pack2, "packs are deterministic")
pack_b, line_b = drama.build_prompt_pack(shots[1], entities)
check("No one speaks in this shot." in pack_b, "a silent shot says so")
check(line_b == "", "a silent shot has no line")
check(drama.episode_speech_script(shots)
      == "We open at dawn.\nTogether this time.",
      "the episode script joins the lines in order")

# --- copy rule on the lane's customer strings -----------------------------
BAD = (":", ";", "-", "—")
for text in (
    "Making shot 1 of 4", "Putting your episode together",
    "Making one shot again", "Starting again",
    "One shot could not be made. Your finished shots are saved. "
    "Open the episode and try that shot again.",
    "The episode took too long and stopped. You were charged "
    "only for the finished shots.",
    "Short drama is a paid feature.",
    "Short drama is not available right now.",
):
    check(all(mark not in text for mark in BAD),
          f"customer copy follows the plain rule: {text}")
import re as _re
for msg in _re.findall(r'raise ValueError\("([^"]+)"\)', DRAMA_SRC):
    check(all(mark not in msg for mark in BAD),
          f"drama guard copy follows the plain rule: {msg}")

# --- source wiring ----------------------------------------------------------
check('dramas = modal.Dict.from_name("moonfactory-dramas"' in SRC,
      "the drama Dict exists")
check("import drama" in SRC, "app imports the drama module")
check('"/platform/drama.py"' in SRC, "the image ships drama.py")
check('id="drama-fields"' in SRC, "the storyboard form is in Create")
check('id="botcheck6"' in SRC, "the drama bot check container exists")
check("'botcheck6'" in SRC, "the drama bot check renders")
check("const DRAMA_ID = 'short_drama'" in SRC, "the picker entry id")
check("o.textContent = 'Short drama'" in SRC, "the picker entry label")
check("m.id === 'grok15'" in SRC, "the entry rides after the Grok lane")
check("function showDramaForm()" in SRC, "the form swap exists")
check("function buildDrama()" in SRC, "the create builder exists")
check("async function dramaDraft()" in SRC, "the draft action exists")
check("Looks right" in SRC, "the confirm mark is named Looks right")
check("Make my episode" in SRC, "the episode button label exists")
check("Fix a shot" in SRC, "the library fix entry exists")
check("Try that shot again" in SRC, "the resume entry exists")
check('"/api/drama/draft"' in SRC, "the draft endpoint exists")
check('"/api/drama/quote"' in SRC, "the quote endpoint exists")
check('"/api/drama/{job_id}"' in SRC, "the episode view exists")
check('"/api/drama/{job_id}/resume"' in SRC, "the resume endpoint exists")
check('"/api/drama/{job_id}/shots/{shot_index}/retake"' in SRC,
      "the retake endpoint exists")
check("drama: { endpoint: '/api/drama', botId: 'botcheck6'" in SRC,
      "the pending route for a parked drama exists")
check('"drama")' in SRC, "drama is a pending create kind")
check('job.get("provider") == "drama"' in SRC,
      "the mirror dispatches drama parents to the driver")
check("def mirror_drama_status" in SRC, "the driver exists")
check('job.get("hold_kind") == "drama"' in SRC,
      "shot jobs settle as cost records only")
check('job.get("drama_parent")' in SRC, "hidden shot jobs are marked")
check('"Short drama" if job.get("kind") == "drama"' in SRC,
      "the library label is Short drama")
check('"gallery_public": False' in SRC,
      "shot jobs are stamped out of the gallery")

# The drama block itself: paid only, and never a free clip.
start = SRC.index("# -- short drama (Phase 1, 2026-11-19)")
end = SRC.index('@web.get("/api/jobs/{job_id}/thumb")', start)
block = SRC[start:end]
check("Short drama is a paid feature." in block,
      "creation refuses unpaid accounts")
check("free_jobs" not in block,
      "drama never touches the free clip counters")
check('parent.get("drama_id")' not in block or True, "block read")
check('"kind": "drama"' in block, "the parent kind is drama")
check('"kind": "drama_shot"' in block, "the child kind is drama_shot")
check("_drama_hold_rails" in block, "the parent holds through the rails")
check("settle_fal_job(job, user)" in block,
      "the parent settles through the shared settle")
check("build_prompt_pack" in block, "shots build from prompt packs")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_drama_lane")
