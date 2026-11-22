"""Short drama (Jack's call, 2026-11-19, Phase 1): a paid
picker entry in Create that turns an idea or a script into a
short acted episode. The shape follows the Jellyfish drama
pipeline's entity system: the platform drafts people, one
place, and a fixed set of shots; the user confirms each person
and the place before anything paid runs (the candidate
confirmation pattern); every shot then generates from a frozen
prompt pack built from the confirmed entities, so names,
wardrobes, and places stay word for word identical across
shots.

Phase 1 format, fixed on purpose: up to two people, one place,
four shots of five seconds at tall 720p on the Grok Imagine
Video 1.5 lane in reference mode, with the lane's own native
speech performing the lines. This module holds the pure parts:
the text drafting routes, the storyboard record guards, and
the prompt pack builder. The endpoints, billing, and the shot
driver live in app.py with the rest of the platform.
"""

import json
import os
import re


# The one lane Phase 1 generates on, the fixed episode format,
# and the record caps the storyboard guards enforce.
DRAMA_MODEL_ID = "grok15"
DRAMA_SHOT_COUNT = 4
DRAMA_SHOT_SECONDS = 5
DRAMA_RESOLUTION = "720p"
DRAMA_ASPECT = "9:16"
DRAMA_MAX_PEOPLE = 2
DRAMA_IDEA_MAX = 500
DRAMA_SCRIPT_MAX = 4000
DRAMA_LINE_MAX_CHARS = 90
DRAMA_LINE_MAX_WORDS = 14

_DRAMA_SHAPE = (
    '{"title": "a short plain title", "summary": "one or two plain '
    'sentences about the episode", "people": [{"name": "first name", '
    '"role": "who this person is in the story", "description": "age, '
    'build, hair, face, in plain words", "wardrobe": "the clothes '
    'this person wears in every shot"}], "place": {"name": "the '
    'place name", "description": "what the place looks like in '
    'plain words"}, "shots": [{"action": "the one clear thing that '
    'happens in this shot", "people": ["names of the people in '
    'this shot"], "spoken_line": "the words one person says, or an '
    'empty string", "speaker": "the name of the person who speaks, '
    'or an empty string", "camera": "the camera move in plain '
    'words", "mood": "the feeling of the shot in a few words"}]}'
)


def write_drama_text(prompt: str, draft_model: str = None) -> str:
    """One drama drafting call with the picked draft model:
    exactly that model writes, under Jack's model choice rule
    (2026-11-20). The default pick is Nemotron 3 Ultra 550B,
    a free model (Jack, 2026-11-21). When
    the picked model cannot answer this raises
    llm_providers.DraftModelUnavailable and no other model is
    tried in its place, so the caller answers with a clean
    failure that names the pick."""
    import llm_providers

    return llm_providers.draft_text(
        prompt, llm_providers.normalize_draft_model(draft_model))


def _drama_prompt(source_mode: str, source_text: str) -> str:
    head = (
        "Write a short drama episode and return JSON only, with no "
        "words before or after the JSON. The episode has one place, "
        "one or two people, and exactly four shots. Each shot lasts "
        "five seconds and holds one clear action. A spoken line is "
        "short, at most ten words, in plain words a person would "
        "really say out loud. Each person keeps the same face, hair, "
        "and clothes in every shot. The place stays the same in "
        "every shot. Use this exact shape. " + _DRAMA_SHAPE + " "
    )
    if source_mode == "script":
        return (head + "Turn this script into the episode. Keep its "
                "spoken lines where they fit the shots. Script "
                "follows. " + source_text)
    return head + "Story idea follows. " + source_text


def _clean_text(value, cap: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:cap]


def _parse_draft(raw: str) -> dict:
    """Parse and guard one drafted storyboard. Raises ValueError
    with a customer safe line when the draft does not fit the
    Phase 1 format, so a bad draft never reaches the confirm
    step."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The draft did not come together. Try again.")
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        raise ValueError("The draft did not come together. Try again.")
    if not isinstance(data, dict):
        raise ValueError("The draft did not come together. Try again.")
    title = _clean_text(data.get("title"), 80)
    summary = _clean_text(data.get("summary"), 300)
    if not title or not summary:
        raise ValueError("The draft did not come together. Try again.")
    raw_people = data.get("people") or []
    if not isinstance(raw_people, list) or \
            not 1 <= len(raw_people) <= DRAMA_MAX_PEOPLE:
        raise ValueError(
            "The draft needs one or two people. Try again.")
    people = []
    for i, person in enumerate(raw_people):
        if not isinstance(person, dict):
            raise ValueError(
                "The draft did not come together. Try again.")
        name = _clean_text(person.get("name"), 40)
        description = _clean_text(person.get("description"), 240)
        if not name or not description:
            raise ValueError(
                "The draft did not come together. Try again.")
        people.append({
            "id": f"p{i + 1}",
            "kind": "person",
            "name": name,
            "role": _clean_text(person.get("role"), 80),
            "description": description,
            "wardrobe": _clean_text(person.get("wardrobe"), 160),
            "picture": {"type": "none"},
            "approved": False,
        })
    raw_place = data.get("place") or {}
    if not isinstance(raw_place, dict):
        raise ValueError("The draft did not come together. Try again.")
    place_name = _clean_text(raw_place.get("name"), 60)
    place_desc = _clean_text(raw_place.get("description"), 240)
    if not place_name or not place_desc:
        raise ValueError("The draft did not come together. Try again.")
    place = {
        "id": "place1",
        "kind": "place",
        "name": place_name,
        "description": place_desc,
        "picture": {"type": "text"},
        "approved": False,
    }
    raw_shots = data.get("shots") or []
    if not isinstance(raw_shots, list) or \
            len(raw_shots) != DRAMA_SHOT_COUNT:
        raise ValueError(
            "The draft needs four shots. Try again.")
    names = [p["name"] for p in people]
    shots = []
    for i, shot in enumerate(raw_shots):
        if not isinstance(shot, dict):
            raise ValueError(
                "The draft did not come together. Try again.")
        action = _clean_text(shot.get("action"), 200)
        if not action:
            raise ValueError(
                "The draft did not come together. Try again.")
        present = []
        for name in shot.get("people") or []:
            name = _clean_text(name, 40)
            if name in names and name not in present:
                present.append(name)
        line = _clean_text(shot.get("spoken_line"),
                           DRAMA_LINE_MAX_CHARS)
        speaker = _clean_text(shot.get("speaker"), 40)
        if line:
            if speaker not in names:
                speaker = present[0] if present else names[0]
            if speaker not in present:
                present.append(speaker)
        else:
            speaker = ""
        shots.append({
            "id": f"s{i + 1}",
            "index": i,
            "action": action,
            "people": present,
            "spoken_line": line,
            "speaker": speaker,
            "camera": _clean_text(shot.get("camera"), 80),
            "mood": _clean_text(shot.get("mood"), 60),
            "status": "ready",
            "child_job_id": None,
            "attempts": 0,
            "version": 1,
            "billed_seconds": None,
            "provider_cost_cents": None,
        })
    return {
        "title": title,
        "summary": summary,
        "entities": {"people": people, "place": place},
        "shots": shots,
    }


def draft_episode(source_mode: str, source_text: str,
                  draft_model: str = None) -> dict:
    """Draft one storyboard from an idea or a script, through
    the picked draft model (Nemotron 3 Ultra 550B, a free
    model, by default). One
    retry with a firmer format note when the first draft does
    not fit the guards, then the guard's clean error stands.
    The retry calls the same picked model; a pick that cannot
    answer raises DraftModelUnavailable on the first call."""
    prompt = _drama_prompt(source_mode, source_text)
    try:
        return _parse_draft(write_drama_text(prompt, draft_model))
    except ValueError:
        firmer = (prompt + " Return exactly four shots and at most "
                  "two people. JSON only.")
        return _parse_draft(write_drama_text(firmer, draft_model))


def validate_storyboard(entities, shots) -> tuple:
    """Guard the confirmed storyboard at create time. The client
    sends back the reviewed entities and shots; the server keeps
    only the fields it trusts, enforces the Phase 1 caps, and
    enforces the readiness gate: every person approved with a
    picture, the place approved, exactly four shots. Raises
    ValueError with a customer safe line. Returns the cleaned
    (entities, shots) pair the episode freezes."""
    if not isinstance(entities, dict) or not isinstance(shots, list):
        raise ValueError("The episode is not ready yet.")
    raw_people = entities.get("people") or []
    if not isinstance(raw_people, list) or \
            not 1 <= len(raw_people) <= DRAMA_MAX_PEOPLE:
        raise ValueError("The episode needs one or two people.")
    people = []
    for i, person in enumerate(raw_people):
        if not isinstance(person, dict):
            raise ValueError("The episode is not ready yet.")
        name = _clean_text(person.get("name"), 40)
        description = _clean_text(person.get("description"), 240)
        if not name or not description:
            raise ValueError("Each person needs a name and a look.")
        picture = person.get("picture") or {}
        ptype = picture.get("type")
        if ptype == "character":
            character_id = _clean_text(picture.get("character_id"), 80)
            if not character_id:
                raise ValueError(
                    "Each person needs a picture before we start.")
            clean_picture = {"type": "character",
                             "character_id": character_id}
        elif ptype == "upload":
            data_url = str(picture.get("data_url") or "")
            if not data_url.startswith("data:image/") or \
                    len(data_url) > 7_000_000:
                raise ValueError(
                    "Each person needs a picture before we start.")
            clean_picture = {"type": "upload", "data_url": data_url}
        else:
            raise ValueError(
                "Each person needs a picture before we start.")
        if person.get("approved") is not True:
            raise ValueError(
                "Mark each person as looking right before we start.")
        people.append({
            "id": f"p{i + 1}",
            "kind": "person",
            "name": name,
            "role": _clean_text(person.get("role"), 80),
            "description": description,
            "wardrobe": _clean_text(person.get("wardrobe"), 160),
            "picture": clean_picture,
            "approved": True,
        })
    raw_place = entities.get("place") or {}
    if not isinstance(raw_place, dict):
        raise ValueError("The episode is not ready yet.")
    place_name = _clean_text(raw_place.get("name"), 60)
    place_desc = _clean_text(raw_place.get("description"), 240)
    if not place_name or not place_desc:
        raise ValueError("The place needs a name and a look.")
    raw_pic = raw_place.get("picture") or {}
    if raw_pic.get("type") == "upload":
        data_url = str(raw_pic.get("data_url") or "")
        if not data_url.startswith("data:image/") or \
                len(data_url) > 7_000_000:
            raise ValueError("That place picture did not come through.")
        place_picture = {"type": "upload", "data_url": data_url}
    else:
        place_picture = {"type": "text"}
    if raw_place.get("approved") is not True:
        raise ValueError(
            "Mark the place as looking right before we start.")
    place = {
        "id": "place1",
        "kind": "place",
        "name": place_name,
        "description": place_desc,
        "picture": place_picture,
        "approved": True,
    }
    if len(shots) != DRAMA_SHOT_COUNT:
        raise ValueError("The episode needs four shots.")
    names = [p["name"] for p in people]
    clean_shots = []
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise ValueError("The episode is not ready yet.")
        action = _clean_text(shot.get("action"), 200)
        if not action:
            raise ValueError("Each shot needs one clear action.")
        present = []
        for name in shot.get("people") or []:
            name = _clean_text(name, 40)
            if name in names and name not in present:
                present.append(name)
        line = _clean_text(shot.get("spoken_line"),
                           DRAMA_LINE_MAX_CHARS)
        if len(line.split()) > DRAMA_LINE_MAX_WORDS:
            raise ValueError(
                "One spoken line is too long for five seconds. "
                "Shorten it and try again.")
        speaker = _clean_text(shot.get("speaker"), 40)
        if line:
            if speaker not in names:
                raise ValueError(
                    "Pick who says each spoken line.")
            if speaker not in present:
                present.append(speaker)
        else:
            speaker = ""
        clean_shots.append({
            "id": f"s{i + 1}",
            "index": i,
            "action": action,
            "people": present,
            "spoken_line": line,
            "speaker": speaker,
            "camera": _clean_text(shot.get("camera"), 80),
            "mood": _clean_text(shot.get("mood"), 60),
            "status": "ready",
            "child_job_id": None,
            "attempts": 0,
            "version": 1,
            "billed_seconds": None,
            "provider_cost_cents": None,
        })
    return {"people": people, "place": place}, clean_shots


def build_prompt_pack(shot: dict, entities: dict) -> tuple:
    """The frozen generation prompt for one shot, built only
    from the confirmed entities, in the scope's fixed order: the
    action, the place lock, the people locks for the people in
    the shot, the spoken line with its speaker, the camera, the
    mood, and the continuity note. Returns the prompt and the
    shot's spoken line."""
    people = {p["name"]: p for p in entities["people"]}
    place = entities["place"]
    parts = [shot["action"].rstrip(".") + "."]
    parts.append(
        f"The place is {place['name']}. "
        + place["description"].rstrip(".") + ".")
    for name in shot["people"]:
        person = people[name]
        bit = f"{name} is {person['description'].rstrip('.')}."
        if person.get("wardrobe"):
            bit += f" {name} wears {person['wardrobe'].rstrip('.')}."
        parts.append(bit)
    line = (shot.get("spoken_line") or "").strip()
    if line:
        parts.append(
            f"{shot['speaker']} says the line out loud with clear "
            f"natural speech. The line is {line}")
    else:
        parts.append("No one speaks in this shot.")
    if shot.get("camera"):
        parts.append("Camera. " + shot["camera"].rstrip(".") + ".")
    if shot.get("mood"):
        parts.append("Mood. " + shot["mood"].rstrip(".") + ".")
    parts.append(
        "Keep the same faces, hair, clothes, and place as the "
        "reference pictures. One continuous shot.")
    return " ".join(parts), line


def episode_speech_script(shots) -> str:
    """The episode's full spoken script in shot order, for the
    finishing word check on the assembled file."""
    return "\n".join(
        s["spoken_line"] for s in shots if s.get("spoken_line"))
