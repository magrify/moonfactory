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
check(draft_ids[-7:-4] == list(SILK_IDS),
      "the silk picks sit in order just before the Numes "
      "uncensored picks that now close the registry")
check([m["label"] for m in llm_providers.DRAFT_MODELS[-7:-4]]
      == list(SILK_LABELS), "the silk pick labels")
check(llm_providers.DRAFT_MODEL_DEFAULT
      == "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
      "the default pick is Nemotron 3 Ultra 550B, a free "
      "model (Jack, 2026-11-21)")
check(draft_ids[:3] == [
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter:google/gemma-4-31b-it:free",
    "openrouter:nvidia/nemotron-3.5-lightning:free",
], "the free models lead the registry")
check(draft_ids[0] == llm_providers.DRAFT_MODEL_DEFAULT,
      "the default pick leads the picker as the first "
      "free model")
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
first_regular = next(o for o in opts if o["group"] == "regular")
check(first_regular["label"] == "Gemini 3.8 Flash",
      "Gemini 3.8 Flash leads the payload's regular group")

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
    os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
    real_provider_chat = llm_providers._provider_chat
    llm_providers._provider_chat = \
        lambda prompt, provider_id, api_key, model: \
        "nemotron wrote this"
    out = shorts.script_from_topic("the deep sea", 30, "house")
    check(out == "nemotron wrote this",
          "a legacy house pick drafts on the free default itself")
    out = shorts.script_from_topic("the deep sea", 30)
    check(out == "nemotron wrote this",
          "the default pick is the free Nemotron itself")
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
    llm_providers._provider_chat = \
        lambda prompt, provider_id, api_key, model: \
        '"nemotron helped this"'
    out = shorts.improve_prompt("a cat", None, "video")
    check(out == "nemotron helped this",
          "the default pick helps through the free Nemotron "
          "and cleans")
    # Script help (the shorts form's Improve my script pick,
    # 2026-11-20): the instruction keeps the line breaks and
    # the helped script comes back with its lines intact, at
    # the script cap rather than the prompt cap. The script
    # below runs over the short script expansion threshold
    # (2026-11-21), so it takes the polish only instruction.
    polish_script = (
        "First line of a longer script that talks about the "
        "morning rush at the diner and the people who come "
        "in early for coffee and pie.\n"
        "Second line keeps going with more of the story so "
        "the whole script is long enough to count as a real "
        "script for polishing.")
    check("keep the line breaks"
          in shorts.prompt_help_instruction(polish_script, "script"),
          "script help asks for the line breaks kept")
    llm_providers._provider_chat = \
        lambda prompt, provider_id, api_key, model: \
        '"First line, clearer now.\nSecond line follows it."'
    out = shorts.improve_prompt(
        "First line here.\nSecond line follows.", None, "script")
    check(out == "First line, clearer now.\nSecond line follows it.",
          "script help returns the script with its lines intact")
    long_script = "\n".join(
        f"Line number {i} speaks at some length here."
        for i in range(30))
    out = shorts.improve_prompt(long_script, None, "script")
    check(isinstance(out, str) and out,
          "a full length script can be helped, over the prompt cap")
    try:
        shorts.improve_prompt("   ", None, "video")
        check(False, "an empty prompt must refuse help")
    except ValueError:
        pass
finally:
    llm_providers._silk_chat = real_chat
    llm_providers._gemini_chat = real_gemini_chat
    llm_providers._provider_chat = real_provider_chat
    os.environ.pop("SILK_API_KEY", None)
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("OPENROUTER_API_KEY", None)

# --- source wiring -----------------------------------------------------------
check('id="short-draft-model"' in SRC, "the shorts picker exists")
check('id="drama-draft-model"' in SRC, "the drama picker exists")
for pid in ("prompt-draft-model",
            "image-draft-model", "image-edit-draft-model"):
    check(f'id="{pid}"' in SRC, f"the {pid} picker exists")
check("House draft" not in SRC,
      "the composite house label is gone from the studio")
for entry in llm_providers.DRAFT_MODELS[-3:]:
    text = entry["label"]
    check(SRC.count(f">{text}</option>") == 5,
          f"all five forms offer {text}")
for mid in SILK_IDS:
    check(SRC.count(f'value="{mid}"') == 5,
          f"all five forms carry the {mid} pick")
check(SRC.count('<optgroup label="Free Models">') == 5,
      "all five forms group the free models")
check(SRC.count('<optgroup label="Regular Models">') == 5,
      "all five forms group the regular models")
check(SRC.count('<optgroup label="Uncensored Models">') == 5,
      "all five forms group the uncensored models")
check(SRC.count('<option disabled data-tier-sep="1">') == 35,
      "all five forms carry the $ tier separators, seven "
      "per select, like the generation pickers")
check(SRC.count(
    '<option disabled data-tier-sep="1">$$$$</option>') == 10,
    "the $$$$ separator heads the priciest tier in both "
    "the Regular and Uncensored groups of every select")
check("promptHelp('short-script', 'short-draft-model', 'script', "
      "'short-help-msg'" in SRC,
      "the shorts form sends the pick with the script to "
      "prompt help (the Improve my script wiring, 2026-11-20)")
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
check("run_in_threadpool(\n                    shorts.improve_prompt, text, draft_model,\n                    kind, language," in SRC,
      "the endpoint passes the pick to the help writer, "
      "off the event loop under its time budget (2026-11-28)")
check("async function promptHelp(" in SRC, "the help action exists")
check(SRC.count("promptHelp('") == 4,
      "all three prompt fields and the shorts script wire "
      "the help action")
check(SRC.count("$('prompt-help-wrap')") == 5,
      "the video help block rides the prompt show and hide lists")
check('id="prompt-help-btn"' in SRC, "the video help button exists")
check('id="image-help-btn"' in SRC, "the image help button exists")
check('id="start-help-btn"' not in SRC,
      "the first frame help button went with its form")
check('id="image-edit-help-btn"' in SRC,
      "the image edit help button exists")
check('id="short-help-btn"' in SRC,
      "the shorts script help button exists")
check(">Improve my script</button>" in SRC,
      "the shorts help button carries its own name")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_silk_drafts")
