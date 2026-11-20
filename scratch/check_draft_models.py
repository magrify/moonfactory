"""Local checks for the full draft model picker (2026-11-20).

Jack's model choice rule: every model Moonfactory can offer
for drafting and prompt help is its own individually
selectable picker option under its own plain name, a pick
calls exactly that model, and a failed pick is never
silently substituted. This suite pins the whole registry in
the picker payload and in all six studio selects, the
per-provider exact routing with the provider boundary
stubbed, and the no-substitution failure shape. No Modal
calls, no network, no paid generation.

Run:  modal-venv python scratch/check_draft_models.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

KEYS = ("SILK_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
        "VENICE_API_KEY", "REFUSELESS_API_KEY", "MORPH_API_KEY")
for _key in KEYS:
    os.environ.pop(_key, None)

import llm_providers  # noqa: E402
import shorts  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
MODELS = llm_providers.DRAFT_MODELS
BY_ID = {m["id"]: m for m in MODELS}

# --- the registry shape ----------------------------------------------------
check(len(MODELS) == 32, f"expected 32 draft models, got {len(MODELS)}")
check(len(BY_ID) == len(MODELS), "draft model ids are unique")
labels = [m["label"] for m in MODELS]
check(len(set(labels)) == len(labels), "draft model labels are unique")
groups = {}
for m in MODELS:
    groups[m["group"]] = groups.get(m["group"], 0) + 1
check(groups == {"regular": 8, "uncensored": 24},
      f"group sizes drifted: {groups}")
check(MODELS[0]["id"] == "gemini:gemini-3.8-flash"
      and MODELS[0]["label"] == "Gemini 3.8 Flash",
      "the default pick is Gemini 3.8 Flash, first and its own model")
check(BY_ID["gemini:gemini-3.5-flash-lite"]["model"]
      == "gemini-3.5-flash-lite",
      "Gemini 3.5 Flash Lite is a separate model, not a fold-in")
uncensored_ids = [m["id"] for m in MODELS if m["group"] == "uncensored"]
check(uncensored_ids[:4] == [
    "venice:qwen-3-8-27b",
    "venice:deepseek-v4-1-flash-uncensored",
    "venice:gemma-4-uncensored",
    "venice:qwen-3-6-plus",
], "Jack's tier picks lead the uncensored group in his order")

# Every uncensored pick's model is registered on its provider
# record (the registry is the offer list; no invented ids).
for m in MODELS:
    if m["group"] != "uncensored":
        continue
    provider = llm_providers._provider(m["provider"])
    registered = [x["id"] for x in provider["models"]]
    check((m.get("model") or m["id"]) in registered,
          f"{m['id']} names a model its provider does not register")

# Morph: restored record, endpoint and model from the
# registry's own history, dark until its key is stored.
morph = llm_providers._provider("morph")
check(morph["base_url"] == "https://api.morphllm.com/v1",
      "morph base url is the live API host")
check(morph["key_env"] == "MORPH_API_KEY", "morph key env name")
check(morph["access"] == "key_pending",
      "morph access records the key is not in the secret yet")
check([x["id"] for x in morph["models"]]
      == ["morph-dsv41flash-abliterated"], "morph model id")

# --- the picker payload ------------------------------------------------------
opts = llm_providers.draft_model_options()
check([o["id"] for o in opts] == [m["id"] for m in MODELS],
      "the payload lists every registered model individually, in order")
check(all(o["label"] == BY_ID[o["id"]]["label"] for o in opts),
      "the payload carries each model's own plain name")
check(all(o["group"] == BY_ID[o["id"]]["group"] for o in opts),
      "the payload carries the groups")
check(all(o["configured"] is False for o in opts),
      "nothing reports configured with every key absent")

# --- every model is in every studio select, individually ---------------------
for m in MODELS:
    check(SRC.count(f'value="{m["id"]}"') == 6,
          f"all six forms offer {m['id']} individually")
    check(SRC.count(f'>{m["label"]}</option>') == 6,
          f"all six forms name {m['label']}")
check(SRC.count('value="gemini:gemini-3.8-flash" selected') == 6,
      "Gemini 3.8 Flash is the selected default in all six forms")
for host in ("api.venice.ai", "api.refuseless.com",
             "api.morphllm.com",
             "generativelanguage.googleapis.com"):
    check(host not in SRC, f"no hosting detail in the studio: {host}")
# The bare openrouter.ai host exists server side as a video
# provider constant (OPENROUTER_BASE); the pin that matters
# is that no host of any kind appears inside a picker block.
import re as _re
for block in _re.findall(
        r'<select id="(?:start|prompt|image|image-edit|short|drama)'
        r'-draft-model">(.*?)</select>', SRC, _re.S):
    check("http" not in block and ".ai" not in block
          and ".com" not in block,
          "a picker block carries no hosting detail")

# --- configured flags follow the keys -----------------------------------------
os.environ["VENICE_API_KEY"] = "test-venice-key"
try:
    opts = {o["id"]: o for o in llm_providers.draft_model_options()}
    check(opts["venice:qwen-3-8-27b"]["configured"] is True,
          "venice picks report configured with the venice key")
    check(opts["refuseless:glm-5.3-abliterated"]["configured"] is False,
          "refuseless picks stay dark without their key")
    check(opts["morph:morph-dsv41flash-abliterated"]["configured"]
          is False, "morph stays dark without its key")
finally:
    os.environ.pop("VENICE_API_KEY", None)

# --- a pick calls exactly that model ------------------------------------------
real_provider_chat = llm_providers._provider_chat
real_gemini_chat = llm_providers._gemini_chat
seen = {}


def fake_provider_chat(prompt, provider_id, api_key, model):
    seen.update(provider=provider_id, api_key=api_key, model=model)
    return "provider wrote this"


def fake_gemini_chat(prompt, api_key, model):
    seen.update(provider="gemini", api_key=api_key, model=model)
    return "gemini wrote this"


llm_providers._provider_chat = fake_provider_chat
llm_providers._gemini_chat = fake_gemini_chat
try:
    cases = [
        ("gemini:gemini-3.8-flash", "GEMINI_API_KEY",
         "gemini", "gemini-3.8-flash"),
        ("gemini:gemini-3.5-flash-lite", "GEMINI_API_KEY",
         "gemini", "gemini-3.5-flash-lite"),
        ("openrouter:anthropic/claude-sonnet-5",
         "OPENROUTER_API_KEY", "openrouter",
         "anthropic/claude-sonnet-5"),
        ("openrouter:cognitivecomputations/"
         "dolphin-mistral-24b-venice-edition",
         "OPENROUTER_API_KEY", "openrouter",
         "cognitivecomputations/dolphin-mistral-24b-venice-edition"),
        ("venice:qwen-3-8-27b", "VENICE_API_KEY",
         "venice", "qwen-3-8-27b"),
        ("venice:llama-3.3-70b", "VENICE_API_KEY",
         "venice", "llama-3.3-70b"),
        ("refuseless:glm-5.3-abliterated", "REFUSELESS_API_KEY",
         "refuseless", "glm-5.3-abliterated"),
        ("morph:morph-dsv41flash-abliterated", "MORPH_API_KEY",
         "morph", "morph-dsv41flash-abliterated"),
    ]
    for pick, key_env, provider_id, model_id in cases:
        os.environ[key_env] = f"test-{provider_id}-key"
        try:
            seen.clear()
            out = llm_providers.draft_text("draft this", pick)
            check(seen.get("provider") == provider_id
                  and seen.get("model") == model_id,
                  f"{pick} calls exactly {provider_id}/{model_id}")
            check(out in ("provider wrote this", "gemini wrote this"),
                  f"{pick} returns its own model's text")
        finally:
            os.environ.pop(key_env, None)

    # A failed pick raises and names the pick; nothing else
    # is called in its place.
    def broken_provider_chat(prompt, provider_id, api_key, model):
        raise RuntimeError("provider down")

    llm_providers._provider_chat = broken_provider_chat
    os.environ["REFUSELESS_API_KEY"] = "test-refuseless-key"
    try:
        try:
            llm_providers.draft_text(
                "draft this", "refuseless:glm-5.3-abliterated")
            check(False, "a failed pick must raise")
        except llm_providers.DraftModelUnavailable as exc:
            check(exc.label == "GLM 5.3 Abliterated",
                  "the failure names the failed pick")
    finally:
        os.environ.pop("REFUSELESS_API_KEY", None)
    llm_providers._provider_chat = fake_provider_chat

    # Through the prompt help writer: a failed Venice pick
    # raises even though the Gemini route is stubbed ready.
    os.environ["VENICE_API_KEY"] = "test-venice-key"
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    llm_providers._provider_chat = broken_provider_chat
    try:
        try:
            shorts.improve_prompt("a cat", "venice:qwen-3-8-27b",
                                  "video")
            check(False, "prompt help must raise on a failed pick")
        except llm_providers.DraftModelUnavailable as exc:
            check(exc.label == "Qwen 3.8 27B",
                  "prompt help names the failed pick, no substitute")
    finally:
        os.environ.pop("VENICE_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
    llm_providers._provider_chat = fake_provider_chat

    # A pick whose key is absent refuses cleanly.
    try:
        llm_providers.draft_text(
            "draft this", "morph:morph-dsv41flash-abliterated")
        check(False, "a keyless pick must refuse")
    except llm_providers.DraftModelUnavailable as exc:
        check(exc.label == "DeepSeek V4.1 Flash Abliterated",
              "the keyless morph pick refuses by name")
finally:
    llm_providers._provider_chat = real_provider_chat
    llm_providers._gemini_chat = real_gemini_chat

# --- the Clip picking chain is untouched ---------------------------------------
check(llm_providers.PICK_CHAIN == (
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("openrouter", "google/gemma-4-31b-it:free"),
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
), "PICK_CHAIN keeps the free only picking fallback, unchanged")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_draft_models")
