"""Local checks for the Silk Compute draft wiring (2026-11-20),
updated the same day for Jack's model choice rule: every
draft model is its own picker option, a pick calls exactly
that model, and a failed pick is never silently substituted.
No Modal calls, no network: imports the platform modules,
exercises the Silk provider record, the dark-safe Silk route
(no key, no call), the picked-model-drives-the-call behavior
with the provider boundary stubbed, the no-substitution
failure shape through the shorts and drama writers and
prompt help, and pins the picker wiring in the source.

Run:  modal-venv python scratch/check_silk_drafts.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

for _key in ("SILK_API_KEY", "GEMINI_API_KEY",
             "OPENROUTER_API_KEY", "VENICE_API_KEY",
             "REFUSELESS_API_KEY", "MORPH_API_KEY"):
    os.environ.pop(_key, None)

import app  # noqa: E402
import drama  # noqa: E402
import llm_providers  # noqa: E402
import shorts  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()

SILK_IDS = (
    "deepseek-v4.1-flash-1017-uncensored",
    "glm-5.3-flash-uncensored",
    "kimi-k3.1-pro-uncensored",
)
SILK_LABELS = ("DeepSeek V4.1 Flash", "GLM-5.3 Flash", "Kimi K3.1 Pro")

# --- the provider record -------------------------------------------------
silk = llm_providers._provider("silk")
check(silk["base_url"] == "https://api.silkcompute.com",
      "silk base url is the OpenAI compatible host")
check(silk["key_env"] == "SILK_API_KEY", "silk key env name")
check([m["id"] for m in silk["models"]] == list(SILK_IDS),
      "silk serves exactly the three picker models")
check("silk" not in [pid for pid, _m in llm_providers.PICK_CHAIN],
      "silk stays out of the free only picking chain")

# --- the draft registry ----------------------------------------------------
draft_ids = [m["id"] for m in llm_providers.DRAFT_MODELS]
check(draft_ids[-3:] == list(SILK_IDS),
      "the silk picks close the draft registry in order")
check([m["label"] for m in llm_providers.DRAFT_MODELS[-3:]]
      == list(SILK_LABELS), "the silk pick labels")
check(llm_providers.DRAFT_MODEL_DEFAULT == "gemini:gemini-3.8-flash",
      "the default pick is Gemini 3.8 Flash as its own model")
check(draft_ids[:3] == [
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter:google/gemma-4-31b-it:free",
    "openrouter:nvidia/nemotron-3.5-lightning:free",
], "the free models lead the registry")
check(draft_ids[3] == llm_providers.DRAFT_MODEL_DEFAULT,
      "the default pick leads the regular group")
check("house" not in draft_ids,
      "there is no composite house entry in the registry")
check(not llm_providers.is_draft_model("house"),
      "house is a legacy value, not a draft model")
check(llm_providers.is_draft_model(SILK_IDS[0]), "silk id is a model")
check(not llm_providers.is_draft_model("nope"), "unknown id refused")
check(llm_providers.normalize_draft_model("nope")
      == llm_providers.DRAFT_MODEL_DEFAULT,
      "an unknown pick normalizes to the default pick")
check(llm_providers.normalize_draft_model(None)
      == llm_providers.DRAFT_MODEL_DEFAULT,
      "a missing pick normalizes to the default pick")
check(llm_providers.normalize_draft_model("house")
      == llm_providers.DRAFT_MODEL_DEFAULT,
      "a legacy house pick normalizes to the default pick")
opts = llm_providers.draft_model_options()
check(all(o["configured"] is False for o in opts),
      "no pick reports configured while every key is absent")
check(opts[0]["group"] == "free"
      and opts[0]["label"] == "Nemotron 3 Ultra 550B",
      "the picker payload leads with the Free Models group")
check(opts[3]["group"] == "regular"
      and opts[3]["label"] == "Gemini 3.8 Flash",
      "the default pick leads the payload's regular group")

# --- dark-safe: no key, no call, clean fallback signal ---------------------
check(llm_providers.silk_configured() is False,
      "silk is unconfigured without its key")
check(llm_providers.silk_draft_text("prompt", "house") is None,
      "a non silk value never touches silk")
check(llm_providers.silk_draft_text(
    "prompt", "venice:qwen-3-8-27b") is None,
    "a venice pick never touches silk")
check(llm_providers.silk_draft_text("prompt", SILK_IDS[0]) is None,
      "a silk pick without a key gives the fallback signal")
check(llm_providers.silk_draft_text("prompt", "nope") is None,
      "an unknown pick gives the fallback signal")
try:
    llm_providers.draft_text("prompt", SILK_IDS[0])
    check(False, "a silk pick without a key must refuse")
except llm_providers.DraftModelUnavailable as exc:
    check(exc.label == "DeepSeek V4.1 Flash",
          "the refusal names the silk pick")

# --- the pick drives the call, and failure never substitutes ---------------
real_chat = llm_providers._silk_chat
real_gemini_chat = llm_providers._gemini_chat
os.environ["SILK_API_KEY"] = "test-key-not-real"
try:
    seen = {}

    def fake_chat(prompt, api_key, model):
        seen.update(prompt=prompt, api_key=api_key, model=model)
        return "silk wrote this"

    llm_providers._silk_chat = fake_chat
    out = llm_providers.silk_draft_text("draft this", SILK_IDS[2])
    check(out == "silk wrote this", "a silk pick returns silk text")
    check(seen.get("model") == SILK_IDS[2],
          "the picked model id is the model called")
    check(seen.get("api_key") == "test-key-not-real",
          "the env key is the key sent")
    out = llm_providers.draft_text("draft this", SILK_IDS[0])
    check(out == "silk wrote this",
          "draft_text routes a silk pick to silk")
    check(seen.get("model") == SILK_IDS[0],
          "draft_text sends the picked silk model id")

    def broken_chat(prompt, api_key, model):
        raise RuntimeError("provider down")

    llm_providers._silk_chat = broken_chat
    check(llm_providers.silk_draft_text("draft this", SILK_IDS[0])
          is None, "a silk failure gives the fallback signal")

    # The writers: the pick writes, and a failed pick raises
    # DraftModelUnavailable with NO other model tried. The
    # house chain seams are rigged to prove they stay unused.
    house_calls = []
    real_gemini = shorts._gemini_text
    real_openrouter = shorts._openrouter_text
    shorts._gemini_text = lambda *a, **k: house_calls.append(1) or \
        "house wrote this"
    shorts._openrouter_text = lambda *a, **k: house_calls.append(1) or \
        "house wrote this"
    llm_providers._silk_chat = fake_chat
    out = shorts.script_from_topic("the deep sea", 30, SILK_IDS[0])
    check(out == "silk wrote this",
          "shorts topic writing uses the picked silk model")
    llm_providers._silk_chat = broken_chat
    try:
        shorts.script_from_topic("the deep sea", 30, SILK_IDS[0])
        check(False, "a failed silk pick must raise, not substitute")
    except llm_providers.DraftModelUnavailable:
        check(True, "a failed silk pick raises DraftModelUnavailable")
    check(house_calls == [],
          "no house chain model is tried after a failed pick")
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    llm_providers._gemini_chat = \
        lambda prompt, api_key, model: "gemini wrote this"
    out = shorts.script_from_topic("the deep sea", 30, "house")
    check(out == "gemini wrote this",
          "a legacy house pick drafts on Gemini 3.8 Flash itself")
    out = shorts.script_from_topic("the deep sea", 30)
    check(out == "gemini wrote this",
          "the default pick is Gemini 3.8 Flash itself")
    shorts._gemini_text = real_gemini
    shorts._openrouter_text = real_openrouter

    # Drama drafting: same shape through the drama writer.
    llm_providers._silk_chat = fake_chat
    out = drama.write_drama_text("draft this", SILK_IDS[1])
    check(out == "silk wrote this",
          "drama drafting uses the picked silk model")
    check(seen.get("model") == SILK_IDS[1],
          "drama sends the picked model id")
    llm_providers._silk_chat = broken_chat
    try:
        drama.write_drama_text("draft this", SILK_IDS[1])
        check(False, "a failed silk pick must raise in drama too")
    except llm_providers.DraftModelUnavailable:
        check(True, "drama raises DraftModelUnavailable, no substitute")

    # Prompt help: the pick drives the call, the kinds shape
    # the instruction, and a failed pick raises.
    check("video generation prompt"
          in shorts.prompt_help_instruction("a cat", "video"),
          "video help asks for a video prompt")
    check("image generation prompt"
          in shorts.prompt_help_instruction("a cat", "image"),
          "image help asks for an image prompt")
    llm_providers._silk_chat = fake_chat
    out = shorts.improve_prompt("a cat", SILK_IDS[0], "video")
    check(out == "silk wrote this",
          "prompt help uses the picked silk model")
    llm_providers._silk_chat = broken_chat
    try:
        shorts.improve_prompt("a cat", SILK_IDS[0], "image")
        check(False, "a failed silk pick must raise in prompt help")
    except llm_providers.DraftModelUnavailable:
        check(True, "prompt help raises DraftModelUnavailable")
    llm_providers._gemini_chat = \
        lambda prompt, api_key, model: '"gemini helped this"'
    out = shorts.improve_prompt("a cat", None, "video")
    check(out == "gemini helped this",
          "the default pick helps through Gemini 3.8 Flash and cleans")
    try:
        shorts.improve_prompt("   ", None, "video")
        check(False, "an empty prompt must refuse help")
    except ValueError:
        pass
finally:
    llm_providers._silk_chat = real_chat
    llm_providers._gemini_chat = real_gemini_chat
    os.environ.pop("SILK_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)

# --- source wiring -----------------------------------------------------------
check('id="short-draft-model"' in SRC, "the shorts picker exists")
check('id="drama-draft-model"' in SRC, "the drama picker exists")
for pid in ("prompt-draft-model", "start-draft-model",
            "image-draft-model", "image-edit-draft-model"):
    check(f'id="{pid}"' in SRC, f"the {pid} picker exists")
check("House draft" not in SRC,
      "the composite house label is gone from the studio")
for entry in llm_providers.DRAFT_MODELS[-3:]:
    text = llm_providers.draft_model_option_label(entry)
    check(SRC.count(f">{text}</option>") == 6,
          f"all six forms offer {text}")
for mid in SILK_IDS:
    check(SRC.count(f'value="{mid}"') == 6,
          f"all six forms carry the {mid} pick")
check(SRC.count('<optgroup label="Free Models">') == 6,
      "all six forms group the free models")
check(SRC.count('<optgroup label="Regular Models">') == 6,
      "all six forms group the regular models")
check(SRC.count('<optgroup label="Uncensored Models">') == 6,
      "all six forms group the uncensored models")
check("payload.draft_model = $('short-draft-model').value" in SRC,
      "the shorts builder sends the pick")
check("draft_model: $('drama-draft-model').value" in SRC,
      "the drama draft call sends the pick")
check('"draft_models": llm_providers.draft_model_options()' in SRC,
      "the shorts config serves the picker view")
check("shorts.script_from_topic(\n                    topic, length_seconds, draft_model)"
      in SRC, "the shorts endpoint passes the pick to the writer")
check('"short_draft_model": draft_model' in SRC,
      "the short job record keeps the pick")
check("drama.draft_episode(\n                source_mode, source_text, draft_model)"
      in SRC, "the drama endpoint passes the pick to the drafter")
check('"draft_model": draft_model,' in SRC,
      "the drama record keeps the pick")
check(SRC.count("pick one of the draft models on the list") == 3,
      "all three endpoints refuse an unknown pick")
check(SRC.count("llm_providers.normalize_draft_model(") == 3,
      "all three endpoints normalize the pick to the default")
check(SRC.count("except llm_providers.DraftModelUnavailable") == 3,
      "all three endpoints surface a failed pick by name")
check("api.silkcompute.com" not in SRC,
      "no hosting detail leaks into the studio source")
check('"/api/prompt-help"' in SRC, "the prompt help endpoint exists")
check("shorts.improve_prompt(text, draft_model, kind)" in SRC,
      "the endpoint passes the pick to the help writer")
check("async function promptHelp(" in SRC, "the help action exists")
check(SRC.count("promptHelp('") == 4,
      "all four prompt fields wire the help action")
check(SRC.count("$('prompt-help-wrap')") == 5,
      "the video help block rides the prompt show and hide lists")
check('id="prompt-help-btn"' in SRC, "the video help button exists")
check('id="image-help-btn"' in SRC, "the image help button exists")
check('id="start-help-btn"' in SRC, "the first frame help button exists")
check('id="image-edit-help-btn"' in SRC,
      "the image edit help button exists")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_silk_drafts")
