"""Local checks for the 2026-11-25 Gemini 4 Neon wiring.

Jack: "Moon factory needs Gemini neon too." Identification
first, from Google's own sources: Gemini 4 Neon is a TEXT
reasoning model (blog.google launch post, 2026-11-18;
Google Cloud model page: model id gemini-4-neon, text
output only, image/audio/video input only, 1,048,576-token
window). It is NOT an image or video model, so it does not
take a video/image shelf lane: Moonfactory's text models
live in the draft picker (llm_providers.DRAFT_MODELS), and
Neon is wired there, as its own Regular pick on the
platform Gemini key, exactly like the other Gemini picks.

Pins:

- the pick exists individually under its own plain name
  "Gemini 4 Neon", provider "gemini", model gemini-4-neon,
  group "regular" — no composite label, no fold-in;
- its cost record is Google's launch price, $3.00 in /
  $15.00 out per 1M tokens, held in
  DRAFT_MODEL_BANDING_FIGURES (Neon has no free tier, so
  these are its only prices), banding it at tier 4 ($$$$),
  and no per-token figure renders in any label or payload;
- a pick calls exactly gemini-4-neon through the Gemini
  route (stubbed at the provider boundary), and with no
  GEMINI_API_KEY it refuses clean, naming the pick, with
  no substitution;
- all five studio selects offer it once, inside the
  Regular $$$$ tier, cost ordered between Gemini 3.1 Pro
  ($14.00 combined) and GPT-5.5 ($35.00 combined);
- it is not in PICK_CHAIN (clipping keeps its free-only
  ruling) and not in the uncensored LLM registry (Neon is
  a regular Google model, not an uncensored build).

No Modal calls, no network, no paid generation.

Run:  modal-venv python scratch/check_gemini_neon_lane.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

for _key in ("GEMINI_API_KEY", "OPENROUTER_API_KEY",
             "VENICE_API_KEY", "REFUSELESS_API_KEY",
             "MORPH_API_KEY", "SILK_API_KEY",
             "NUMES_API_KEY"):
    os.environ.pop(_key, None)

import llm_providers  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


PICK = "gemini:gemini-4-neon"
SRC = (ROOT / "platform" / "app.py").read_text()

# --- the pick itself ---------------------------------------------------------
entry = llm_providers.draft_model_entry(PICK)
check(entry is not None, "the Gemini 4 Neon pick is registered")
check(entry["label"] == "Gemini 4 Neon",
      "the pick carries its own plain name")
check(entry["provider"] == "gemini",
      "the pick routes on the platform Gemini key")
check(entry["model"] == "gemini-4-neon",
      "the pick calls Google's model id gemini-4-neon")
check(entry["group"] == "regular",
      "the pick sits in the Regular Models group")
check(llm_providers.is_draft_model(PICK),
      "the pick is a valid draft model value")
check(llm_providers.draft_model_label(PICK) == "Gemini 4 Neon",
      "the label lookup returns the plain name")

# --- the cost record -----------------------------------------------------------
check(llm_providers.DRAFT_MODEL_BANDING_FIGURES[PICK]
      == ("3.00", "15.00"),
      "the banding figures are Google's launch prices, "
      "$3.00 in / $15.00 out per 1M tokens")
check(PICK not in llm_providers.DRAFT_MODEL_PRICES,
      "Neon carries no second price record to drift against")
check(llm_providers.draft_model_cost(PICK) == 18.00,
      "the audited route cost reads $18.00 per 1M combined")
check(llm_providers.draft_model_price_tier(PICK) == 4,
      "the pick bands at tier 4 ($$$$)")
opts = {o["id"]: o for o in llm_providers.draft_model_options()}
check(opts[PICK]["label"] == "Gemini 4 Neon"
      and opts[PICK]["group"] == "regular"
      and opts[PICK]["price_tier"] == 4,
      "the picker payload carries the pick with its name, "
      "group and tier")
check(opts[PICK]["configured"] is False,
      "the pick reports dark with the Gemini key absent")
check("3.00" not in opts[PICK]["label"]
      and "15.00" not in opts[PICK]["label"],
      "no price figure rides the pick's label")

# --- the studio selects ---------------------------------------------------------
check(SRC.count(f'value="{PICK}"') == 5,
      "all five forms offer Gemini 4 Neon individually")
check(SRC.count(">Gemini 4 Neon</option>") == 5,
      "all five forms name Gemini 4 Neon plainly")
import re as _re
for block in _re.findall(
        r'<optgroup label="Regular Models">(.*?)</optgroup>',
        SRC, _re.S):
    values = _re.findall(r'<option value="([^"]+)"', block)
    check(values.index(PICK)
          == values.index(
              "openrouter:google/gemini-3.1-pro-preview") + 1
          and values.index(
              "openrouter:openai/gpt-5.5")
          == values.index(PICK) + 1,
          "inside Regular, Neon sits cost ordered between "
          "Gemini 3.1 Pro and GPT-5.5 in the $$$$ tier")

# --- a pick calls exactly that model --------------------------------------------
seen = {}
real_gemini_chat = llm_providers._gemini_chat


def fake_gemini_chat(prompt, api_key, model):
    seen.update(api_key=api_key, model=model)
    return "neon wrote this"


llm_providers._gemini_chat = fake_gemini_chat
try:
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    try:
        out = llm_providers.draft_text("draft this", PICK)
        check(seen.get("model") == "gemini-4-neon",
              "the pick calls exactly gemini-4-neon")
        check(seen.get("api_key") == "test-gemini-key",
              "the pick uses the platform Gemini key")
        check(out == "neon wrote this",
              "the pick returns its own model's text")
    finally:
        os.environ.pop("GEMINI_API_KEY", None)

    # No key: the pick refuses clean and names itself.
    try:
        llm_providers.draft_text("draft this", PICK)
        check(False, "a keyless Neon pick must refuse")
    except llm_providers.DraftModelUnavailable as exc:
        check(exc.label == "Gemini 4 Neon",
              "the keyless refusal names Gemini 4 Neon")

    # A failed call also refuses by name, never substitutes.
    def broken_gemini_chat(prompt, api_key, model):
        raise RuntimeError("google down")

    llm_providers._gemini_chat = broken_gemini_chat
    os.environ["GEMINI_API_KEY"] = "test-gemini-key"
    try:
        try:
            llm_providers.draft_text("draft this", PICK)
            check(False, "a failed Neon pick must raise")
        except llm_providers.DraftModelUnavailable as exc:
            check(exc.label == "Gemini 4 Neon",
                  "the failed pick names Gemini 4 Neon")
    finally:
        os.environ.pop("GEMINI_API_KEY", None)
finally:
    llm_providers._gemini_chat = real_gemini_chat

# --- scope guards -----------------------------------------------------------------
check(all(model != "gemini-4-neon"
          for _provider_id, model in llm_providers.PICK_CHAIN),
      "Neon is not in the Clip picking chain")
for p in llm_providers.LLM_PROVIDERS:
    check(all(m["id"] != "gemini-4-neon" for m in p["models"]),
          f"Neon is not folded into the {p['id']} registry")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_gemini_neon_lane")
