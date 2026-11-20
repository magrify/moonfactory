"""Local checks for the Silk Compute draft wiring (2026-11-20).
No Modal calls, no network: imports the platform modules,
exercises the draft model registry, the dark-safe Silk route
(no key, no call, house fallback), the picked-model-drives-
the-call behavior with the provider boundary stubbed, and
pins the picker wiring in the source (both Create forms, the
server side validation and pass through, the job and draft
records).

Run:  modal-venv python scratch/check_silk_drafts.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

os.environ.pop("SILK_API_KEY", None)

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
check([m["id"] for m in llm_providers.DRAFT_MODELS]
      == ["house"] + list(SILK_IDS), "draft model ids and order")
check([m["label"] for m in llm_providers.DRAFT_MODELS]
      == ["House draft"] + list(SILK_LABELS), "draft model labels")
opts = llm_providers.draft_model_options()
check(opts[0]["configured"] is True, "the house draft is configured")
check(all(o["configured"] is False for o in opts[1:]),
      "silk picks report unconfigured while the key is absent")
check(llm_providers.is_draft_model("house"), "house is a draft model")
check(llm_providers.is_draft_model(SILK_IDS[0]), "silk id is a model")
check(not llm_providers.is_draft_model("nope"), "unknown id refused")
check(llm_providers.normalize_draft_model("nope") == "house",
      "an unknown pick normalizes to the house default")
check(llm_providers.normalize_draft_model(None) == "house",
      "a missing pick normalizes to the house default")

# --- dark-safe: no key, no call, clean fallback signal ---------------------
check(llm_providers.silk_configured() is False,
      "silk is unconfigured without its key")
check(llm_providers.silk_draft_text("prompt", "house") is None,
      "the house pick never touches silk")
check(llm_providers.silk_draft_text("prompt", SILK_IDS[0]) is None,
      "a silk pick without a key gives the fallback signal")
check(llm_providers.silk_draft_text("prompt", "nope") is None,
      "an unknown pick gives the fallback signal")

# --- the pick drives the call, and failure falls back ----------------------
real_chat = llm_providers._silk_chat
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

    def broken_chat(prompt, api_key, model):
        raise RuntimeError("provider down")

    llm_providers._silk_chat = broken_chat
    check(llm_providers.silk_draft_text("draft this", SILK_IDS[0])
          is None, "a silk failure gives the fallback signal")

    # Shorts topic writing: silk first when picked, house after.
    # The house chain engages its Gemini branch only when the
    # platform key is present, so the test sets a dummy one and
    # stubs the call itself.
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    real_gemini = shorts._gemini_text
    real_openrouter = shorts._openrouter_text
    shorts._gemini_text = lambda *a, **k: "house wrote this"
    shorts._openrouter_text = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("unused"))
    llm_providers._silk_chat = fake_chat
    out = shorts.script_from_topic("the deep sea", 30, SILK_IDS[0])
    check(out == "silk wrote this",
          "shorts topic writing uses the picked silk model")
    llm_providers._silk_chat = broken_chat
    out = shorts.script_from_topic("the deep sea", 30, SILK_IDS[0])
    check(out == "house wrote this",
          "shorts topic writing falls back to the house chain")
    out = shorts.script_from_topic("the deep sea", 30, "house")
    check(out == "house wrote this",
          "the house pick keeps the house chain")
    shorts._gemini_text = real_gemini
    shorts._openrouter_text = real_openrouter

    # Drama drafting: same shape through the drama module names.
    real_d_gemini = drama._gemini_text
    real_d_openrouter = drama._openrouter_text
    drama._gemini_text = lambda *a, **k: "house drama"
    drama._openrouter_text = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("unused"))
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    llm_providers._silk_chat = fake_chat
    out = drama.write_drama_text("draft this", SILK_IDS[1])
    check(out == "silk wrote this",
          "drama drafting uses the picked silk model")
    check(seen.get("model") == SILK_IDS[1],
          "drama sends the picked model id")
    llm_providers._silk_chat = broken_chat
    out = drama.write_drama_text("draft this", SILK_IDS[1])
    check(out == "house drama",
          "drama drafting falls back to the house chain")
    drama._gemini_text = real_d_gemini
    drama._openrouter_text = real_d_openrouter
    os.environ.pop("GEMINI_API_KEY", None)

    # Prompt help (the expansion to every generative prompt
    # field): the pick drives the call, the kinds shape the
    # instruction, and a Silk failure falls back to the house.
    check("video generation prompt"
          in shorts.prompt_help_instruction("a cat", "video"),
          "video help asks for a video prompt")
    check("image generation prompt"
          in shorts.prompt_help_instruction("a cat", "image"),
          "image help asks for an image prompt")
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    real_gemini = shorts._gemini_text
    shorts._gemini_text = lambda *a, **k: '"house helped this"'
    llm_providers._silk_chat = fake_chat
    out = shorts.improve_prompt("a cat", SILK_IDS[0], "video")
    check(out == "silk wrote this",
          "prompt help uses the picked silk model")
    llm_providers._silk_chat = broken_chat
    out = shorts.improve_prompt("a cat", SILK_IDS[0], "image")
    check(out == "house helped this",
          "prompt help falls back and cleans the house answer")
    out = shorts.improve_prompt("a cat", "house", "video")
    check(out == "house helped this",
          "the house pick helps through the house chain")
    shorts._gemini_text = real_gemini
    os.environ.pop("GEMINI_API_KEY", None)
    try:
        shorts.improve_prompt("   ", "house", "video")
        check(False, "an empty prompt must refuse help")
    except ValueError:
        pass
finally:
    llm_providers._silk_chat = real_chat
    os.environ.pop("SILK_API_KEY", None)

# --- source wiring -----------------------------------------------------------
check('id="short-draft-model"' in SRC, "the shorts picker exists")
check('id="drama-draft-model"' in SRC, "the drama picker exists")
for pid in ("prompt-draft-model", "start-draft-model",
            "image-draft-model", "image-edit-draft-model"):
    check(f'id="{pid}"' in SRC, f"the {pid} picker exists")
for label in ("House draft",) + SILK_LABELS:
    check(SRC.count(f">{label}</option>") == 6,
          f"all six forms offer {label}")
for mid in SILK_IDS:
    check(SRC.count(f'value="{mid}"') == 6,
          f"all six forms carry the {mid} pick")
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
