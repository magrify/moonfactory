"""Faceless shorts lane: the MoneyPrinterTurbo shape (harry0703/
MoneyPrinterTurbo, MIT) rebuilt from Moonfactory's own pieces
instead of ported. A topic or a pasted script goes in; a vertical
narrated short comes out.

Pipeline, one pass per line of the script:

1. The script splits into lines (sentences, wrapped to a spoken
   length). A topic becomes a script first, through the text
   routes the platform already has wired for the Clip lane's
   picking brain (the platform Gemini key, then the free
   OpenRouter models), behind the same dark-safe gates: no key,
   no call, and the caller falls back to asking for a pasted
   script. No new provider is wired for this lane.
2. Each line gets stock search terms drawn from its own words.
3. One stock clip per line: Pexels when its key is configured
   (PEXELS_API_KEY in the platform secret; the Pexels API terms
   ask for a prominent link back and photographer credit where
   possible, both recorded per scene on the job record), and
   openly licensed Wikimedia Commons clips otherwise, so the
   lane works before the key lands and the credit trail exists
   either way.
4. Narration per line from the platform's Kokoro stage (called
   by the processor in app.py, not here: this module never talks
   to Modal). The measured length of each line's wav is that
   scene's length, the MoneyPrinterTurbo timing rule.
5. Captions timed from those same measured lengths: each line's
   words are placed across its own audio by character share and
   grouped into short chunks, burned in as an ASS track. The
   finished short's audio still goes through the platform's
   word check stage in the processor, which holds the real
   faster-whisper word timings and the same-voice fix pass.
6. A music bed under the narration and one ffmpeg assembly at
   1080 x 1920.

This module is pure pipeline (stdlib + ffmpeg subprocesses), the
way clipping.py is: app.py's process_short_job drives it and owns
the job record, the quotas, and the audio stage calls.
"""

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WIDTH = 1080
HEIGHT = 1920
FPS = 30

# Script shape: a short is a handful of spoken lines. The caps
# fit a full two minutes of narration (about 300 spoken words);
# the per line cap keeps one scene's stock clip and caption
# chunks honest (a line is one breath, one scene).
SHORTS_MAX_LINES = 20
SHORTS_MAX_SCRIPT_CHARS = 2200
SHORTS_MAX_LINE_CHARS = 300
SHORTS_MIN_LINES = 2
# A script under this many words is a fragment, not a script:
# Improve my script expands it into full narration lines
# about the same subject instead of polishing it, because a
# three word line has nothing to polish and comes back word
# for word (Jack, 2026-11-21). Scripts at or over the
# threshold keep the polish only instruction exactly.
SHORT_SCRIPT_EXPAND_WORDS = 40
# Spoken pace the length math plans on, about 150 words a
# minute. Topic mode writes to a chosen target length; script
# mode's estimate reads the same pace back to the customer.
SPOKEN_WORDS_PER_SECOND = 2.5
# The lengths a topic short can aim at, in seconds, and the
# plan each one writes to: (spoken words, lines). Lines run
# about 8 to 10 seconds each at the planning pace.
SHORTS_LENGTH_TARGETS = (30, 60, 90, 120)
SHORTS_DEFAULT_LENGTH_SECONDS = 60
SHORTS_LENGTH_PLANS = {
    30: (75, 4),
    60: (150, 7),
    90: (225, 10),
    120: (300, 14),
}
# The pause the narration track leaves between lines, and the
# tail after the last one. Scene lengths include the pause, so
# picture and voice change together.
LINE_GAP_SECONDS = 0.35
TAIL_SECONDS = 0.4
CAPTION_MAX_WORDS = 4
CAPTION_MIN_SECONDS = 0.35

PEXELS_API_BASE = "https://api.pexels.com/v1"
UNSPLASH_API_BASE = "https://api.unsplash.com"
PIXABAY_API_BASE = "https://pixabay.com/api"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia's robot policy asks automated clients for a
# descriptive agent string with a contact, and throttles
# anonymous bursts; the retry below rides out its 429s.
USER_AGENT = ("Moonfactory/1.0 (https://magrify.com; "
              "support@magrify.com) faceless shorts lane")

# The platform's Gemini chain, mirrored from clipping.py's
# GEMINI_PICK_MODELS (same key, same models, same order): the
# Clip lane's picking brain is the route this lane borrows for
# topic writing.
GEMINI_SCRIPT_MODELS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")

_STOPWORDS = frozenset("""
a an the and or but if then else when while of at by for with
about into through after over under again further once here there
all any both each few more most other some such no nor not only
own same so than too very can will just is are was were be been
being have has had having do does did doing would should could
ought i you he she it we they them his her its our their this
that these those am an as from on in to out up down
""".split())


# ---------------------------------------------------------------------------
# Script shape
# ---------------------------------------------------------------------------

def _wrap_long_line(line: str) -> list:
    """Split one overlong sentence into spoken length pieces at
    word boundaries, preferring clause punctuation."""
    pieces = []
    rest = line.strip()
    while len(rest) > SHORTS_MAX_LINE_CHARS:
        cut = rest.rfind(" ", 0, SHORTS_MAX_LINE_CHARS)
        if cut < SHORTS_MAX_LINE_CHARS // 2:
            cut = SHORTS_MAX_LINE_CHARS
            # Never split a word in half if a boundary exists
            # anywhere reasonable; a hard cut is the last resort.
            boundary = rest.rfind(" ", 0, SHORTS_MAX_LINE_CHARS + 40)
            if boundary > 0:
                cut = boundary
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


# The split choices a pasted script offers, Pixelle's set: the
# author decides what a scene is. Auto keeps the lane's original
# reading (author line breaks honored, prose split into
# sentences) and is what topic mode's generated scripts ride.
SPLIT_MODES = ("auto", "lines", "sentences", "paragraphs")
SPLIT_MODE_LABELS = {
    "auto": "Auto",
    "lines": "One scene per line",
    "sentences": "One scene per sentence",
    "paragraphs": "One scene per paragraph",
}


def _sentences(block: str) -> list:
    """One block of prose as spoken lines: sentences first, then
    wrap whatever is still over the per line cap."""
    out = []
    for m in re.finditer(r"[^.!?]+[.!?]*", block):
        piece = m.group(0).strip()
        if piece:
            out.extend(_wrap_long_line(piece))
    return out


def split_script(text: str, mode: str = "auto") -> list:
    """Split a pasted or generated script into narration lines.

    The mode says what a scene is. Auto (the default, and the
    only mode generated scripts use): newlines are the author's
    own scene breaks and are honored first; a block of prose
    splits into sentences. Lines: every pasted line is a scene,
    whatever punctuation it holds. Sentences: line breaks mean
    nothing, the whole text reads as prose and splits into
    sentences. Paragraphs: every blank line separated block is
    one scene, split into sentences only when it runs over the
    per line cap. Every line is wrapped to the per line cap.
    Raises ValueError on anything the lane cannot speak: empty,
    too few lines, too many lines, over the total cap, or a
    split choice the lane does not offer."""
    text = (text or "").strip()
    if not text:
        raise ValueError("write or paste a script first")
    if len(text) > SHORTS_MAX_SCRIPT_CHARS:
        raise ValueError(
            f"that script is too long, keep it under "
            f"{SHORTS_MAX_SCRIPT_CHARS} characters")
    if mode not in SPLIT_MODES:
        raise ValueError("pick one of the split choices on the list")
    blocks = [b.strip() for b in text.splitlines() if b.strip()]
    lines = []
    if mode == "lines":
        for block in blocks:
            lines.extend(_wrap_long_line(block))
    elif mode == "sentences":
        lines.extend(_sentences(" ".join(blocks)))
    elif mode == "paragraphs":
        for para in re.split(r"\n\s*\n", text):
            para = " ".join(para.split())
            if not para:
                continue
            if len(para) <= SHORTS_MAX_LINE_CHARS:
                lines.append(para)
            else:
                lines.extend(_sentences(para))
    else:
        for block in blocks:
            if len(block) <= SHORTS_MAX_LINE_CHARS and "\n" in text:
                lines.append(block)
                continue
            # Prose block (or an overlong pasted line): sentences
            # first, then wrap whatever is still too long.
            lines.extend(_sentences(block))
    lines = [ln.strip() for ln in lines if ln.strip()]
    if len(lines) < SHORTS_MIN_LINES:
        raise ValueError("a short needs at least two lines of script")
    if len(lines) > SHORTS_MAX_LINES:
        raise ValueError(
            f"that script makes {len(lines)} scenes, keep it to "
            f"{SHORTS_MAX_LINES} lines or fewer")
    return lines


def length_plan(target_seconds: int) -> tuple:
    """The writing plan for one chosen topic length: how many
    spoken words and how many lines the script writer aims at.
    Raises ValueError for a length the lane does not offer."""
    try:
        return SHORTS_LENGTH_PLANS[int(target_seconds)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "pick one of the lengths on the list: "
            + ", ".join(str(t) for t in SHORTS_LENGTH_TARGETS))


def estimate_spoken_seconds(text: str) -> float:
    """How long a script takes to say out loud at the planning
    pace. The studio shows the same estimate live while a
    customer pastes, so what they read is what the job record
    keeps."""
    words = len((text or "").split())
    return words / SPOKEN_WORDS_PER_SECOND


def search_terms_for_line(line: str, topic: str = "") -> str:
    """Stock search terms for one line: its strongest content
    words, in order, with the topic's words as the fallback and
    a plain nature query as the last resort. Deterministic, so a
    scene's footage choice is reproducible from the job record."""
    def content_words(text):
        out = []
        for word in re.findall(r"[a-z0-9']+", (text or "").lower()):
            word = word.strip("'")
            if len(word) >= 4 and word not in _STOPWORDS \
                    and word not in out:
                out.append(word)
        return out

    words = content_words(line)[:4]
    if len(words) < 2:
        words = (words + content_words(topic))[:4]
    if not words:
        return "nature"
    return " ".join(words)


# ---------------------------------------------------------------------------
# Topic to script: the platform's already wired text routes
# ---------------------------------------------------------------------------

def script_prompt(topic: str, target_seconds: int) -> str:
    """The topic writing prompt for one chosen length. The word
    and line counts come from the length plan, so a 30 second
    short and a 2 minute short are written to different shapes
    instead of trimmed or padded after the fact."""
    words, lines = length_plan(target_seconds)
    return (
        "Write a narration script for a vertical short video "
        f"about: {topic}\n"
        f"Write {lines} lines, about {words} words in total. "
        "Each line is one short spoken sentence of at most 25 "
        "words. Put one line on each line of the reply. "
        "No title, no numbering, no stage directions, no "
        "quotation marks. Plain spoken English only."
    )


def _gemini_text(prompt: str, api_key: str) -> str:
    """One text generation call on the platform's Gemini key,
    shaped like clipping._gemini_pick but returning the raw
    text: the script writer wants prose, not the picker's JSON."""
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7},
    }
    last_exc = None
    for model in GEMINI_SCRIPT_MODELS:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key=" + urllib.parse.quote(api_key)
        )
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            if text and text.strip():
                return text
        except Exception as exc:  # noqa: BLE001 - next model
            last_exc = exc
    raise RuntimeError(f"gemini script writing unavailable: {last_exc}")


def _env_value(name):
    """One environment value by name, or an empty string.
    Written as a scan so call sites stay plain."""
    for key, value in os.environ.items():
        if key == name:
            return value
    return ""


def _openrouter_text(prompt: str) -> str:
    """One text generation call on the free OpenRouter chain the
    Clip lane's picking fallback uses (llm_providers.PICK_CHAIN,
    free models only, so this lane adds no text cost either).
    Raises when the key is absent or every free model fails."""
    import llm_providers

    last_exc = None
    for provider_id, model in llm_providers.PICK_CHAIN:
        provider = llm_providers._provider(provider_id)
        key_name = provider["key_env"]
        bearer = _env_value(key_name)
        if not bearer or not provider["base_url"]:
            continue
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        body = {
            "model": model,
            "temperature": 0.7,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + bearer,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"] or ""
            if text.strip():
                return text
        except Exception as exc:  # noqa: BLE001 - next free model
            last_exc = exc
    raise RuntimeError(f"free script writing unavailable: {last_exc}")


def _picked_draft_text(prompt: str, draft_model: str) -> str:
    """The picked draft model's text, from exactly that model
    (the draft picker lives in llm_providers). Under Jack's
    model choice rule (2026-11-20) a pick is never silently
    substituted: when the picked model cannot answer, this
    raises llm_providers.DraftModelUnavailable and the caller
    turns it into a clean failure that names the pick."""
    import llm_providers

    return llm_providers.draft_text(
        prompt, llm_providers.normalize_draft_model(draft_model))


def script_routes_configured() -> bool:
    """Whether any wired route could write a script from a
    topic: the platform Gemini key, the free picking fallback
    chain, or any configured draft model route. The studio
    reads this to offer topic mode only when it can work."""
    if os.environ.get("GEMINI_API_KEY"):
        return True
    try:
        import llm_providers

        return llm_providers.any_configured() \
            or llm_providers.any_draft_configured()
    except Exception:  # noqa: BLE001 - module not shipped, no route
        return False


def script_from_topic(topic: str,
                      target_seconds: int =
                      SHORTS_DEFAULT_LENGTH_SECONDS,
                      draft_model: str = None) -> str:
    """Write a script for a topic with the picked draft model:
    exactly that model writes, under the model choice rule.
    The default pick is Gemini 3.8 Flash. When the picked
    model cannot answer, this raises
    llm_providers.DraftModelUnavailable and no other model is
    tried in its place; the create route turns that into a
    clean failure that names the pick and asks for a pasted
    script instead. Returns script text the caller validates
    with split_script."""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("give the short a topic first")
    prompt = script_prompt(topic, target_seconds)
    return _picked_draft_text(prompt, draft_model)


# ---------------------------------------------------------------------------
# Prompt help: the draft picker on the generative prompt fields
# ---------------------------------------------------------------------------

def prompt_help_instruction(text: str, kind: str) -> str:
    """The instruction for one prompt help call: rewrite the
    user's draft into one clear and detailed generation prompt,
    same subject, same intent, nothing but the prompt back.
    The script kind (the Faceless shorts form's Improve my
    script pick, Jack, 2026-11-20) rewrites a narration script
    instead: same facts, same order, one line for each scene,
    and the line breaks kept, because one line is one scene.
    A very short script (under SHORT_SCRIPT_EXPAND_WORDS
    words) has nothing to polish, so its instruction instead
    expands it into full narration lines about the same
    subject (Jack, 2026-11-21)."""
    if kind == "script":
        if len(text.split()) < SHORT_SCRIPT_EXPAND_WORDS:
            return (
                "Turn the short script below into a full "
                "narration script about the same subject. "
                "Write full spoken lines, one line for each "
                "scene, keeping the subject and any facts "
                "given in it. Use plain language that reads "
                "well out loud. Keep the whole script under "
                "1500 characters. Return only the script, "
                "with no words before or after it and no "
                "quotation marks. Script follows. " + text)
        return (
            "Rewrite the narration script below so it reads "
            "better out loud. Keep the same facts in the same "
            "order, keep one line for each scene, and keep the "
            "line breaks. Make each line clear and easy to say. "
            "Return only the rewritten script, with no words "
            "before or after it and no quotation marks. "
            "Script follows. " + text)
    if kind == "image":
        shape = "an image generation prompt"
        craft = ("Add concrete detail about the subject, the "
                 "setting, the light, the colors, and the "
                 "composition.")
    else:
        shape = "a video generation prompt"
        craft = ("Add concrete detail about the subject, the "
                 "setting, the light, and how the camera moves.")
    return (
        "Rewrite the draft below into one clear and detailed "
        + shape + ". Keep the same subject and the same intent. "
        + craft + " Return only the improved prompt, with no "
        "words before or after it and no quotation marks. "
        "Draft follows. " + text
    )


def _clean_helped_prompt(text: str) -> str:
    """One helped prompt as the field should hold it: a single
    block of plain text, quotes the model wrapped it in gone."""
    cleaned = re.sub(r"\s+", " ", text or "").strip().strip('"').strip()
    if not cleaned:
        raise RuntimeError("prompt help came back empty")
    return cleaned[:2000]


def _clean_helped_script(text: str) -> str:
    """One helped script as the field should hold it: the line
    breaks kept (one line is one scene), quotes the model
    wrapped the whole script in gone, blank lines dropped."""
    cleaned = (text or "").strip().strip('"').strip()
    kept = [line.strip() for line in cleaned.splitlines()]
    kept = [line for line in kept if line]
    if not kept:
        raise RuntimeError("prompt help came back empty")
    return "\n".join(kept)[:SHORTS_MAX_SCRIPT_CHARS]


def improve_prompt(text: str, draft_model: str = None,
                   kind: str = "video") -> str:
    """Improve one generative prompt with the picked draft
    model (2026-11-20 expansion: every text to image and text
    to video prompt field in Create carries the draft picker).
    Exactly the picked model helps, under the model choice
    rule; when it cannot answer this raises
    llm_providers.DraftModelUnavailable and the field keeps
    the user's own words, with the failure named in the UI.
    The script kind improves a whole pasted short script (up
    to the script box's own cap) and keeps its line breaks."""
    text = (text or "").strip()
    if not text:
        raise ValueError("write a prompt first")
    if kind == "script":
        if len(text) > SHORTS_MAX_SCRIPT_CHARS:
            raise ValueError(
                "that script is too long to help with, keep it "
                f"under {SHORTS_MAX_SCRIPT_CHARS} characters")
        prompt = prompt_help_instruction(text, kind)
        return _clean_helped_script(
            _picked_draft_text(prompt, draft_model))
    if len(text) > 1000:
        raise ValueError(
            "that prompt is too long to help with, keep it "
            "under 1000 characters")
    prompt = prompt_help_instruction(text, kind)
    return _clean_helped_prompt(
        _picked_draft_text(prompt, draft_model))


def help_unchanged(text: str, improved: str, kind: str) -> bool:
    """True when a prompt help answer is the submitted text
    itself, compared the way the answer was cleaned: the
    picked model sent the words back untouched, so the page
    must say so instead of calling it an update (Jack,
    2026-11-21). Scripts compare line by line with blank
    lines dropped; prompts compare with whitespace folded,
    matching _clean_helped_script and _clean_helped_prompt."""
    if kind == "script":
        submitted = "\n".join(
            line.strip() for line in (text or "").splitlines()
            if line.strip())
    else:
        submitted = re.sub(r"\s+", " ", text or "").strip()
    return bool(submitted) and submitted == (improved or "")


# ---------------------------------------------------------------------------
# AI pictures: one generated still per line, in one picked style
# ---------------------------------------------------------------------------

# The picture styles the AI visuals mode offers. One style rides
# every line of a short, so the pictures feel like one set: the
# prefix is the style, the line (or a planned description of it)
# is the subject. The set is short on purpose, the way Pixelle's
# prompt prefix examples are short: a style is a look, not a
# settings page.
VISUAL_STYLES = {
    "photo": {
        "label": "Photo",
        "prefix": "photorealistic photograph, natural light, "
                  "sharp detail",
    },
    "illustration": {
        "label": "Illustration",
        "prefix": "warm flat illustration, soft colors, clean "
                  "simple shapes",
    },
    "anime": {
        "label": "Anime",
        "prefix": "anime style illustration, clean line art, "
                  "bright colors",
    },
    "painting": {
        "label": "Painting",
        "prefix": "oil painting, rich texture, visible brush "
                  "strokes",
    },
    "sketch": {
        "label": "Simple sketch",
        "prefix": "minimalist black and white line sketch, clean "
                  "lines, lots of white space",
    },
}
VISUAL_STYLE_DEFAULT = "illustration"


def image_prompt_for_line(line: str, style_id: str) -> str:
    """One line's picture prompt in the picked style: the style
    prefix, then the line itself as the subject. Deterministic,
    so a scene's picture is reproducible from the job record."""
    style = VISUAL_STYLES.get(style_id) or \
        VISUAL_STYLES[VISUAL_STYLE_DEFAULT]
    subject = (line or "").strip().rstrip(".")
    return f"{style['prefix']}: {subject}"


def _planned_image_prompts(lines: list, style_id: str):
    """Ask the wired text routes for one picture description per
    line, Pixelle's image planning step: narration lines are
    often abstract ('nobody really knows why'), and a planner
    turns each into something a picture model can draw. Best
    effort by design: returns a list of styled prompts, or None
    when no route answers or the answer does not line up with
    the script, and the caller falls back to the lines
    themselves. Never raises."""
    style = VISUAL_STYLES.get(style_id) or \
        VISUAL_STYLES[VISUAL_STYLE_DEFAULT]
    numbered = "\n".join(
        f"{i + 1}. {line}" for i, line in enumerate(lines))
    prompt = (
        "For each numbered narration line below, write one "
        "image generation prompt describing a single picture "
        "that shows what the line is talking about. Concrete "
        "visual subjects only, no text in the picture. Reply "
        "with ONLY a JSON array of strings, one prompt per "
        f"line, in order. There are {len(lines)} lines.\n"
        + numbered
    )
    text = None
    errors = []
    if os.environ.get("GEMINI_API_KEY"):
        try:
            text = _gemini_text(prompt, os.environ["GEMINI_API_KEY"])
        except Exception as exc:  # noqa: BLE001 - free chain next
            errors.append(str(exc))
    if text is None:
        try:
            import llm_providers

            if llm_providers.any_configured():
                text = _openrouter_text(prompt)
        except Exception as exc:  # noqa: BLE001 - planning is optional
            errors.append(str(exc))
    if not text:
        return None
    try:
        start = text.index("[")
        end = text.rindex("]")
        planned = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001 - not the shape we asked for
        return None
    if not isinstance(planned, list) or len(planned) != len(lines):
        return None
    out = []
    for item in planned:
        item = str(item or "").strip()
        if not item or len(item) > 400:
            return None
        out.append(f"{style['prefix']}: {item}")
    return out


def image_prompts_for_lines(lines: list, style_id: str) -> list:
    """The picture prompt for every line, in order: planned
    descriptions when a text route answers, the lines themselves
    otherwise. Always returns one prompt per line."""
    planned = _planned_image_prompts(lines, style_id)
    if planned is not None:
        return planned
    return [image_prompt_for_line(line, style_id) for line in lines]


# ---------------------------------------------------------------------------
# Stock footage
# ---------------------------------------------------------------------------

def _open_with_retry(req, timeout: int):
    """Open one request, riding out the transient 429 and 503
    answers both stock sources give anonymous bursts."""
    import time

    last_exc = None
    for attempt, wait in enumerate((0, 5, 15)):
        if wait:
            time.sleep(wait)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in (429, 503):
                raise
    raise last_exc


def _get_json(url: str, headers: dict = None) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with _open_with_retry(req, timeout=60) as resp:
        return json.loads(resp.read())


def _download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _open_with_retry(req, timeout=300) as resp, \
            dest.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    if dest.stat().st_size < 10_000:
        dest.unlink(missing_ok=True)
        raise RuntimeError("stock download came back empty")
    return dest


def pexels_search_videos(query: str, api_key: str, per_page: int = 6) -> list:
    """Search Pexels videos (portrait first, this lane is
    vertical). Returns scene candidates with the download link,
    the photographer's name, and the Pexels page, the credit
    trail the API terms ask us to keep."""
    url = PEXELS_API_BASE + "/videos/search?" + urllib.parse.urlencode({
        "query": query, "orientation": "portrait",
        "per_page": per_page,
    })
    data = _get_json(url, headers={"Authorization": api_key})
    out = []
    for video in data.get("videos") or []:
        files = [
            f for f in video.get("video_files") or []
            if f.get("file_type") == "video/mp4" and f.get("link")
        ]
        if not files:
            continue
        usable = [f for f in files if (f.get("height") or 0) >= 720]
        pool = usable or files
        chosen = min(
            pool,
            key=lambda f: abs((f.get("width") or 0) - WIDTH))
        out.append({
            "provider": "pexels",
            "download_url": chosen["link"],
            "width": chosen.get("width"),
            "height": chosen.get("height"),
            "duration": video.get("duration"),
            "page_url": video.get("url"),
            "credit": (video.get("user") or {}).get("name") or "",
        })
    return out


def pexels_search_photos(query: str, api_key: str, per_page: int = 6) -> list:
    """Search Pexels photos for one description (the image
    picker's Stock image lane). Returns candidates in the
    same shape pexels_search_videos returns: the download
    link (the large2x rendition, falling back to large and
    then the original), the photographer's name, and the
    Pexels page, the credit trail the API terms ask us to
    keep. An empty list means no photo matched."""
    url = PEXELS_API_BASE + "/search?" + urllib.parse.urlencode({
        "query": query, "per_page": per_page,
    })
    data = _get_json(url, headers={"Authorization": api_key})
    out = []
    for photo in data.get("photos") or []:
        src = photo.get("src") or {}
        link = (src.get("large2x") or src.get("large")
                or src.get("original"))
        if not link:
            continue
        out.append({
            "provider": "pexels",
            "download_url": link,
            "width": photo.get("width"),
            "height": photo.get("height"),
            "page_url": photo.get("url"),
            "credit": photo.get("photographer") or "",
            "alt": photo.get("alt") or "",
        })
    return out


def unsplash_search_photos(query: str, api_key: str, per_page: int = 6) -> list:
    """Search Unsplash photos for one description (the
    image picker's Stock image lane, its second source).
    Returns candidates in the same shape
    pexels_search_photos returns, plus the two fields
    Unsplash's API terms ride on: credit_url, the
    photographer's profile page, so the credit stored on
    the job record names the photographer and links their
    profile the way the terms ask; and track_url, the
    photo's download location, which the terms require the
    platform to call when a photo is actually used (the
    stock submit fires it at delivery). The download link
    is the photo's raw rendition. An empty list means no
    photo matched."""
    url = UNSPLASH_API_BASE + "/search/photos?" + urllib.parse.urlencode({
        "query": query, "per_page": per_page,
    })
    data = _get_json(url, headers={
        "Authorization": f"Client-ID {api_key}",
        "Accept-Version": "v1",
    })
    out = []
    for photo in data.get("results") or []:
        urls = photo.get("urls") or {}
        link = urls.get("raw") or urls.get("full")
        if not link:
            continue
        user = photo.get("user") or {}
        links = photo.get("links") or {}
        out.append({
            "provider": "unsplash",
            "download_url": link,
            "width": photo.get("width"),
            "height": photo.get("height"),
            "page_url": links.get("html"),
            "credit": user.get("name") or "",
            "credit_url": (user.get("links") or {}).get("html"),
            "track_url": links.get("download_location"),
            "alt": photo.get("alt_description") or "",
        })
    return out


def pixabay_search_photos(query: str, api_key: str, per_page: int = 6) -> list:
    """Search Pixabay photos for one description (the
    image picker's Stock image lane, its third source).
    Returns candidates in the same shape
    pexels_search_photos returns. Pixabay's terms ask for
    no attribution, but the photographer's name rides the
    credit field anyway, the same trail the other sources
    keep. The download link is the large rendition. An
    empty list means no photo matched."""
    url = PIXABAY_API_BASE + "/?" + urllib.parse.urlencode({
        "key": api_key, "q": query, "image_type": "photo",
        "per_page": per_page, "safesearch": "true",
    })
    data = _get_json(url)
    out = []
    for hit in data.get("hits") or []:
        link = hit.get("largeImageURL") or hit.get("webformatURL")
        if not link:
            continue
        out.append({
            "provider": "pixabay",
            "download_url": link,
            "width": hit.get("imageWidth"),
            "height": hit.get("imageHeight"),
            "page_url": hit.get("pageURL"),
            "credit": hit.get("user") or "",
            "alt": hit.get("tags") or "",
        })
    return out


def commons_search_videos(query: str, per_page: int = 8) -> list:
    """Search Wikimedia Commons for openly licensed video. Uses
    the videoinfo derivatives (the transcoded webm and mp4
    versions Commons itself publishes) and carries the artist
    and license names from the file's metadata as the credit."""
    params = {
        "action": "query", "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:video {query}",
        "gsrnamespace": 6, "gsrlimit": per_page,
        "prop": "videoinfo|imageinfo",
        "viprop": "url|size|mime|derivatives",
        "iiprop": "url|extmetadata",
    }
    url = COMMONS_API + "?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    pages = (data.get("query") or {}).get("pages") or {}
    out = []
    for page in pages.values():
        info = (page.get("videoinfo") or [{}])[0]
        derivatives = [
            d for d in info.get("derivatives") or [] if d.get("src")
        ]
        if not derivatives:
            continue
        mp4s = [d for d in derivatives
                if (d.get("type") or "").startswith("video/mp4")]
        pool = mp4s or derivatives
        chosen = min(pool, key=lambda d: abs((d.get("width") or 0) - WIDTH))
        meta = ((page.get("imageinfo") or [{}])[0]
                .get("extmetadata") or {})
        artist = re.sub(
            r"<[^>]+>", "",
            (meta.get("Artist") or {}).get("value") or "").strip()
        license_name = (meta.get("LicenseShortName") or {}).get(
            "value") or ""
        out.append({
            "provider": "commons",
            "download_url": chosen["src"],
            "width": chosen.get("width"),
            "height": chosen.get("height"),
            "duration": None,
            "page_url": (page.get("canonicalurl")
                         or info.get("descriptionurl") or ""),
            "credit": artist,
            "license": license_name,
        })
    return out


def _query_ladder(terms: str) -> list:
    """Search queries from most specific to broadest. Pexels
    ranks a full phrase well; Commons search matches loosely,
    so a scene whose phrase finds nothing there retries with
    its first two words, then its first word alone."""
    words = terms.split()
    ladder = [terms]
    if len(words) > 2:
        ladder.append(" ".join(words[:2]))
    if len(words) > 1:
        ladder.append(words[0])
    return ladder


def find_stock_clip(terms: str, dest_dir: Path, index: int,
                    api_key: str = "") -> dict:
    """One stock clip for one scene: Pexels when its key is
    configured, Wikimedia Commons otherwise and as the fallback
    when Pexels has nothing for the terms. Downloads the file
    into dest_dir and returns the scene record (provider, terms,
    page, credit) the job record keeps. Raises RuntimeError when
    no source yields a usable clip."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    candidates = []
    if api_key:
        try:
            candidates = pexels_search_videos(terms, api_key)
        except Exception as exc:  # noqa: BLE001 - fall to Commons
            errors.append(f"pexels: {exc}")
    used_query = terms
    if not candidates:
        # Commons search matches loosely and its video corpus
        # is small, so the ladder works harder there and a
        # relevance gate keeps the loose matches honest: a
        # candidate counts as a match only when one of the
        # scene's content words appears in its file title
        # (Commons titles describe their footage). The ladder
        # runs the full phrase, then title searches for the
        # scene's longest (rarest) words first, then the plain
        # broadening ladder. If nothing passes the gate, the
        # first answer set still serves: a loose match beats
        # no short, and the job record keeps the terms either
        # way. Within a set, usable widths go first.
        content = [w for w in terms.split() if len(w) >= 4]

        def relevant(cand):
            title = (cand.get("page_url") or "").lower()
            variants = set()
            for w in content:
                variants.add(w)
                if w.endswith("s") and len(w) > 4:
                    variants.add(w[:-1])
            return any(
                re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])",
                          title)
                for w in variants)

        def by_width(found):
            return sorted(
                found,
                key=lambda c: 0 if (c.get("width") or 0) >= 480
                else 1)

        queries = [terms]
        for word in sorted(content, key=len, reverse=True):
            queries.append(f"intitle:{word}")
        queries += _query_ladder(terms)[1:]
        first_found = None
        seen = set()
        for query in queries:
            if query in seen:
                continue
            seen.add(query)
            try:
                found = commons_search_videos(query)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"commons: {exc}")
                continue
            if not found:
                continue
            if first_found is None:
                first_found = (query, found)
            gated = [c for c in found if relevant(c)]
            if gated:
                used_query = query
                candidates = by_width(gated)
                break
        if not candidates and first_found is not None:
            used_query, found = first_found
            candidates = by_width(found)
    for rank, cand in enumerate(candidates):
        suffix = ".mp4"
        if ".webm" in (cand["download_url"] or ""):
            suffix = ".webm"
        dest = dest_dir / f"scene_{index}_{rank}{suffix}"
        try:
            _download(cand["download_url"], dest)
            probe(dest)
        except Exception as exc:  # noqa: BLE001 - next candidate
            errors.append(f"download: {exc}")
            continue
        return {**cand, "terms": used_query, "path": str(dest)}
    raise RuntimeError(
        f"no stock clip found for '{terms}': " + "; ".join(errors)[:200])


# ---------------------------------------------------------------------------
# Media helpers and captions
# ---------------------------------------------------------------------------

def probe(path) -> dict:
    """ffprobe facts for one media file: duration, video width
    and height, whether an audio stream exists."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: "
                           f"{proc.stderr[-200:]}")
    data = json.loads(proc.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0)
    width = height = 0
    has_audio = False
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        elif stream.get("codec_type") == "audio":
            has_audio = True
    return {"duration": duration, "width": width, "height": height,
            "has_audio": has_audio}


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return (text.replace("\\", " ").replace("{", "(")
            .replace("}", ")").replace("\n", " "))


def caption_events(lines: list, timings: list) -> list:
    """Word timed caption chunks for the whole short.

    timings holds one (start, duration) per line, the measured
    narration placement the processor computed. Inside a line,
    words are placed by character share of the line's length
    (the line is one breath of one voice, so share tracks speech
    closely at sentence length) and grouped into chunks of at
    most CAPTION_MAX_WORDS words. Returns (start, end, text)
    events in order."""
    events = []
    for line, (start, duration) in zip(lines, timings):
        words = line.split()
        if not words:
            continue
        total_chars = sum(len(w) for w in words) or 1
        placed = []
        cursor = float(start)
        for word in words:
            span = duration * (len(word) + 1) / (total_chars + len(words))
            placed.append((cursor, cursor + span, word))
            cursor += span
        line_end = float(start) + float(duration)
        for i in range(0, len(placed), CAPTION_MAX_WORDS):
            chunk = placed[i:i + CAPTION_MAX_WORDS]
            c_start = chunk[0][0]
            c_end = min(line_end, chunk[-1][1])
            if c_end - c_start < CAPTION_MIN_SECONDS:
                c_end = min(line_end, c_start + CAPTION_MIN_SECONDS)
            events.append((c_start, c_end,
                           " ".join(w for _s, _e, w in chunk)))
    return events


# The caption looks a short can wear. Classic is the house
# style the lane shipped with (bold white words, heavy black
# outline, low on the frame); Big runs larger and sits higher,
# Quiet runs smaller and thinner. No captions (Jack,
# 2026-11-20) is a look with no words at all: it carries no
# geometry because nothing is ever drawn for it. The set is
# deliberately small: this is a caption choice, not a template
# system (the full template gallery stays a named gap, see
# the run record).
CAPTION_STYLES = {
    "classic": {"label": "Classic", "fontsize": 72,
                "outline": 5, "margin_v": 300},
    "big": {"label": "Big", "fontsize": 96,
            "outline": 7, "margin_v": 430},
    "quiet": {"label": "Quiet", "fontsize": 58,
              "outline": 3, "margin_v": 220},
    "none": {"label": "No captions"},
}
CAPTION_STYLE_DEFAULT = "classic"
CAPTION_STYLE_NONE = "none"


def build_captions_ass(lines: list, timings: list,
                       style: str = CAPTION_STYLE_DEFAULT) -> str:
    """The ASS subtitle file for one short: bold white words
    with a black outline, low on the frame, in the picked
    caption look (classic is the house shorts style). Burned in
    by the assembly's subtitles filter. The no captions look
    returns the document with no caption events in it, so a
    short made with it carries no captions even if a captions
    file is still written and burned."""
    if style == CAPTION_STYLE_NONE:
        look = CAPTION_STYLES[CAPTION_STYLE_DEFAULT]
    else:
        look = CAPTION_STYLES.get(style) or \
            CAPTION_STYLES[CAPTION_STYLE_DEFAULT]
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\n"
        f"PlayResY: {HEIGHT}\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        f"Style: Shorts,DejaVu Sans,{look['fontsize']},"
        "&H00FFFFFF,&H00FFFFFF,"
        "&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{look['outline']},0,2,60,60,{look['margin_v']},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    if style == CAPTION_STYLE_NONE:
        return header
    body = "".join(
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Shorts,,"
        f"0,0,0,,{_ass_escape(text)}\n"
        for start, end, text in caption_events(lines, timings)
    )
    return header + body


# ---------------------------------------------------------------------------
# Assembly (ffmpeg): the same steps the platform processor runs,
# and the local proof runs unchanged.
# ---------------------------------------------------------------------------

def _run(cmd: list, timeout: int = 1200, cwd: Path = None):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {' '.join(cmd[:3])}... "
            f"{proc.stderr[-400:]}")
    return proc


def build_narration_track(line_wavs: list, line_durations: list,
                          work_dir: Path) -> Path:
    """Join the per line narration wavs into one track, each
    line padded with silence out to its scene length (line
    duration plus the gap; the last line gets the tail)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for i, (wav, duration) in enumerate(zip(line_wavs, line_durations)):
        pad = TAIL_SECONDS if i == len(line_wavs) - 1 \
            else LINE_GAP_SECONDS
        seg = work_dir / f"vox_{i}.wav"
        _run(["ffmpeg", "-y", "-i", str(wav),
              "-af", f"apad,atrim=0:{duration + pad:.3f}",
              "-ar", "24000", "-ac", "1", str(seg)], timeout=300)
        segments.append(seg)
    list_path = work_dir / "vox_list.txt"
    list_path.write_text(
        "".join(f"file '{p.name}'\n" for p in segments),
        encoding="utf-8")
    out = work_dir / "narration.wav"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
          "-i", str(list_path), "-c:a", "pcm_s16le", str(out)],
         timeout=300, cwd=work_dir)
    return out


def animate_image_scene(image_path, duration: float,
                        out_path: Path, index: int = 0) -> Path:
    """Turn one AI still into one scene clip at the frame size
    and rate the assembly expects, exactly the scene's length:
    a slow zoom in, a slow zoom out, or a slow pan across a
    slightly zoomed frame, cycling by scene index so a run of
    stills does not all drift the same way (the Ken Burns
    shape). The still is upscaled first so the zoom crops from
    a larger canvas and stays smooth."""
    frames = max(1, int(round(duration * FPS)))
    motion = index % 3
    if motion == 0:
        zoom = f"z='1+0.12*on/{frames}'"
        x = "x='iw/2-(iw/zoom/2)'"
        y = "y='ih/2-(ih/zoom/2)'"
    elif motion == 1:
        zoom = f"z='1.13-0.12*on/{frames}'"
        x = "x='iw/2-(iw/zoom/2)'"
        y = "y='ih/2-(ih/zoom/2)'"
    else:
        zoom = "z='1.15'"
        x = f"x='(iw-iw/zoom)*on/{frames}'"
        y = "y='ih/2-(ih/zoom/2)'"
    vf = (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:"
        "force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan={zoom}:{x}:{y}:d={frames}:"
        f"s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p")
    _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-vf", vf, "-t", f"{duration:.3f}",
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", str(out_path),
    ], timeout=600)
    return out_path


def assemble_short(scene_paths: list, scene_durations: list,
                   narration_path, captions_ass: Path,
                   out_path: Path, work_dir: Path,
                   bed_path=None, watermark: bool = False,
                   font_path: str = "") -> dict:
    """Assemble one finished short at 1080 x 1920.

    Each stock clip is scaled to cover the frame, cropped,
    looped or trimmed to its scene's length, and stripped of
    its own sound. The scenes join, the narration and the
    (looped, ducked) music bed mix to the house loudness, the
    captions burn in when a captions file is given (None means
    the short was made with the No captions pick, so no
    subtitles filter runs at all), and a free job's file
    carries the same Moonfactory watermark the finishing stage
    burns into free generation files. Returns the finished
    file's probe."""
    work_dir.mkdir(parents=True, exist_ok=True)
    scenes = []
    for i, (clip, duration) in enumerate(
            zip(scene_paths, scene_durations)):
        scene = work_dir / f"scene_{i}.mp4"
        _run([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip),
            "-t", f"{duration:.3f}",
            "-vf", (f"scale={WIDTH}:{HEIGHT}:"
                    "force_original_aspect_ratio=increase,"
                    f"crop={WIDTH}:{HEIGHT},fps={FPS},"
                    "format=yuv420p"),
            "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", str(scene),
        ], timeout=900)
        scenes.append(scene)
    list_path = work_dir / "scene_list.txt"
    list_path.write_text(
        "".join(f"file '{p.name}'\n" for p in scenes),
        encoding="utf-8")
    joined = work_dir / "joined.mp4"
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
          "-i", str(list_path), "-c", "copy", str(joined)],
         timeout=600, cwd=work_dir)

    total = sum(scene_durations)
    inputs = ["-i", str(joined), "-i", str(narration_path)]
    filters = []
    mix = ["[vox]"]
    filters.append("[1:a]volume=1.0[vox]")
    if bed_path and Path(bed_path).exists():
        inputs += ["-stream_loop", "-1", "-i", str(bed_path)]
        filters.append("[2:a]volume=0.22[bed]")
        mix.append("[bed]")
    filters.append(
        "".join(mix)
        + f"amix=inputs={len(mix)}:duration=first:"
          "dropout_transition=0,"
          "loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    vf_parts = []
    if captions_ass is not None:
        vf_parts.append(f"subtitles={captions_ass.name}")
    if watermark:
        if not font_path:
            font_path = ("/usr/share/fonts/truetype/dejavu/"
                         "DejaVuSans-Bold.ttf")
        vf_parts.append(
            f"drawtext=fontfile={font_path}:text='Moonfactory':"
            "fontcolor=white@0.55:fontsize=h/26:borderw=2:"
            "bordercolor=black@0.3:x=w-tw-24:y=h-th-24")
    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", ";".join(filters)]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
    cmd += [
           "-map", "0:v:0", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart",
           "-t", f"{total:.3f}", str(out_path)]
    _run(cmd, timeout=1200, cwd=work_dir)
    return probe(out_path)
