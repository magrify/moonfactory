"""Uncensored LLM providers (Jack, 2026-11-16: "put them all on
moonfactory").

A registry SEPARATE from the video shelf in app.py's MODELS: these
are text models, they never appear in the video model picker. They
are exposed on /api/config under "llm_providers".

Picking-fallback role (narrowed 2026-11-17, Jack's ruling): the
Clip AI picking brain may use a fallback only if it adds ZERO cost
to clipping, so the chain (PICK_CHAIN below) holds free OpenRouter
models only, tried after every Gemini entry has failed for a
window and before the loudness fallback. The paid uncensored APIs
in this registry (Venice, Refuseless) are stored-key records only:
their keys stay in the secret, but nothing in clipping calls them.
Morph was dropped entirely on 2026-11-17 and its entry removed.
Gemini stays the primary brain; default behavior is unchanged.

Every model here is uncensored by its provider's own listing
(abliterated weights or an uncensored fine-tune) and carries the
same "Uncensored" flag the studio shows on the self-hosted Wan 2.2
video lane.

Dark-safe wiring, same as the video providers: a provider is only
ever called when its key env var is present in the platform's
environment (moonfactory-providers secret). No key, no call, no
error surfaced to users. Keys are never printed or exposed; the
public view below carries names, ids, and a configured boolean
only.

Provider state at wiring time (2026-11-17):
- openrouter: key stored (OPENROUTER_API_KEY). The first stored
  key returned 401 on both free account reads (/api/v1/auth/key,
  /api/v1/credits) and was replaced 2026-11-17; the replacement
  verifies live (GET /api/v1/auth/key 200, usage 0, no key limit)
  and the model is confirmed listed in OpenRouter's own catalog
  (435 models, read 2026-11-17). The picking fallback uses
  OpenRouter FREE models only (the :free ids in PICK_CHAIN,
  verified $0 prompt + $0 completion against OpenRouter's live
  catalog, 470 models, read 2026-11-17), so clipping carries
  zero fallback cost. Recorded in BUILD-LOG.
- venice: models enumerated from Venice's public models endpoint
  (https://api.venice.ai/api/v1/models), filtered to the entries
  Venice itself flags "uncensored": true. Key stored
  (VENICE_API_KEY): the browser signup was Turnstile-walled, so
  Jack created the account and key himself and supplied the key
  in chat 2026-11-17; stored via SDK Secret.update() per-key
  merge (key names 18 -> 19, nothing lost). Verified live the
  same day (free read): GET /api/v1/models 200, 143 models, all
  15 registered uncensored ids present. NOT in the picking
  chain: Venice is a paid API, and the 2026-11-17 free-only
  ruling keeps it out of clipping. Key stays stored; this entry
  is a record only. Recorded in BUILD-LOG.
- refuseless: pay-as-you-go per-token (their pricing page: "no
  subscription and no monthly minimum"). Model ids from their own
  model pages. Key stored (REFUSELESS_API_KEY): the browser signup
  could not fill the password fields, so Jack completed the signup
  himself and supplied the key in chat 2026-11-17; stored via SDK
  Secret.update() per-key merge (key names 19 -> 20, nothing
  lost). Verified live the same day (free read):
  GET /v1/models 200, 7 models, all 4 registered ids present.
  NOT in the picking chain: Refuseless is a paid API, and the
  2026-11-17 free-only ruling keeps it out of clipping. Key
  stays stored; this entry is a record only. Recorded in
  BUILD-LOG.
- morph: DROPPED 2026-11-17 (Jack). The pursuit ended and the
  registry entry was removed; no key ever existed.
- silk: Silk Compute (2026-11-20, Jack: "use for drafts").
  OpenAI compatible chat at https://api.silkcompute.com, key
  env SILK_API_KEY in the same moonfactory-providers secret.
  The signup is finishing separately, so NO key exists yet:
  every Silk call below is dark-safe (no key, no call, no
  error surfaced) and the drafting flows fall back to the
  house chain exactly like other unfunded lanes fail clean.
  Silk is wired for DRAFTING ONLY (the Short drama and
  Faceless shorts topic writers, through the draft model
  picker in Create): it is deliberately NOT in PICK_CHAIN,
  so clipping keeps its free-only fallback ruling.
"""

import json
import os
import urllib.error
import urllib.request

LLM_PROVIDERS = [
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "access": "key_stored",
        "models": [
            {
                "id": "cognitivecomputations/dolphin-mistral-24b-venice-edition",
                "label": "Venice Uncensored (Dolphin Mistral 24B)",
            },
        ],
    },
    {
        "id": "venice",
        "label": "Venice",
        "base_url": "https://api.venice.ai/api/v1",
        "key_env": "VENICE_API_KEY",
        "access": "key_stored",
        "models": [
            {"id": "venice-uncensored-1-2", "label": "Venice Uncensored 1.2"},
            {"id": "venice-uncensored-role-play",
             "label": "Venice Role Play Uncensored"},
            {"id": "deepseek-v4-1-flash-uncensored",
             "label": "DeepSeek V4.1 Flash Uncensored"},
            {"id": "deepseek-v4-1-flash-1017-uncensored",
             "label": "DeepSeek V4.1 Flash 1017 Uncensored"},
            {"id": "e2ee-deepseek-v4-1-flash-1017-abliterated",
             "label": "DeepSeek V4.1 Flash 1017 Abliterated (E2EE)"},
            {"id": "glm-5-3-flash-uncensored",
             "label": "GLM 5.3 Flash Uncensored"},
            {"id": "olafangensan-glm-4.7-flash-heretic",
             "label": "GLM 4.7 Flash Heretic"},
            # Venice deprecates this one 2026-11-18 with an
            # auto-remap to its Gemma 4 26B successor; it was live
            # and flagged uncensored when enumerated.
            {"id": "gemma-4-uncensored", "label": "Gemma 4 Uncensored"},
            {"id": "qwen-3-6-plus", "label": "Qwen 3.6 Plus Uncensored"},
            {"id": "qwen-3-8-27b", "label": "Qwen 3.8 27B"},
            {"id": "abliteration-abliterated-model-large-v2",
             "label": "Abliterated Large V2"},
            {"id": "abliteration-abliterated-model-large-flash",
             "label": "Abliterated Large Flash"},
            {"id": "aion-labs-aion-3-0-mini", "label": "Aion 3.0 Mini"},
            {"id": "aion-labs-aion-3-5", "label": "Aion 3.5"},
            {"id": "aion-labs-aion-3-5-mini", "label": "Aion 3.5 Mini"},
        ],
    },
    {
        "id": "refuseless",
        "label": "Refuseless",
        "base_url": "https://api.refuseless.com/v1",
        "key_env": "REFUSELESS_API_KEY",
        "access": "key_stored",
        "models": [
            {"id": "glm-5.3-abliterated", "label": "GLM 5.3 Abliterated"},
            {"id": "glm-5.3-flash-abliterated",
             "label": "GLM 5.3 Flash Abliterated"},
            {"id": "deepseek-v4.1-flash-abliterated",
             "label": "DeepSeek V4.1 Flash 1017 Abliterated"},
            {"id": "deepseek-v4.1-flash-abliterated-fast",
             "label": "DeepSeek V4.1 Flash 1017 Fast Abliterated"},
        ],
    },
    {
        "id": "silk",
        "label": "Silk Compute",
        "base_url": "https://api.silkcompute.com",
        "key_env": "SILK_API_KEY",
        "access": "key_pending",
        "models": [
            {"id": "deepseek-v4.1-flash-1017-uncensored",
             "label": "DeepSeek V4.1 Flash"},
            {"id": "glm-5.3-flash-uncensored",
             "label": "GLM-5.3 Flash"},
            {"id": "kimi-k3.1-pro-uncensored",
             "label": "Kimi K3.1 Pro"},
        ],
    },
]

# The draft model picker (2026-11-20, Jack: "use for drafts.
# offer a model picker for drafts"). The Create flows that
# draft text (Short drama, and the Faceless shorts topic
# writer) offer these plain names only, never hosting or API
# details. "house" is the incumbent chain the platform already
# drafts with (Gemini first, then the free OpenRouter chain)
# and stays the default; the other three are Silk Compute's
# uncensored builds, tried first when picked and fallen back
# from to the house chain whenever Silk is unavailable (no
# key yet, an error, an empty answer), so a Silk pick can
# never break a draft.
DRAFT_MODEL_HOUSE = "house"
DRAFT_MODELS = [
    {"id": DRAFT_MODEL_HOUSE, "label": "House draft",
     "provider": None},
    {"id": "deepseek-v4.1-flash-1017-uncensored",
     "label": "DeepSeek V4.1 Flash", "provider": "silk"},
    {"id": "glm-5.3-flash-uncensored",
     "label": "GLM-5.3 Flash", "provider": "silk"},
    {"id": "kimi-k3.1-pro-uncensored",
     "label": "Kimi K3.1 Pro", "provider": "silk"},
]


def draft_model_options() -> list:
    """The picker view: plain names, ids, and whether picking
    the entry could actually reach its provider right now.
    Never key material."""
    return [
        {
            "id": m["id"],
            "label": m["label"],
            "configured": (
                True if m["provider"] is None
                else silk_configured()),
        }
        for m in DRAFT_MODELS
    ]


def is_draft_model(value) -> bool:
    return any(m["id"] == value for m in DRAFT_MODELS)


def normalize_draft_model(value) -> str:
    """An unknown or missing pick reads as the house default,
    so older clients and parked creations keep drafting."""
    return value if is_draft_model(value) else DRAFT_MODEL_HOUSE


def silk_configured() -> bool:
    """True when the Silk key is present in the environment
    (the moonfactory-providers secret). False today by design:
    the signup is finishing separately."""
    p = _provider("silk")
    return bool(p["base_url"] and os.environ.get(p["key_env"]))


def _silk_chat(prompt: str, api_key: str, model: str) -> str:
    """One OpenAI compatible chat call to Silk Compute,
    returning the raw text (drafting wants prose or the drama
    JSON, not a parsed shape). Raises on any failure so the
    caller falls back to the house chain."""
    p = _provider("silk")
    url = p["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "temperature": 0.7,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data["choices"][0]["message"]["content"] or ""
    if not text.strip():
        raise RuntimeError("silk draft came back empty")
    return text


def silk_draft_text(prompt: str, draft_model: str):
    """The picked Silk model's draft, or None when the pick is
    the house default, the key is absent, or the call fails
    for any reason. Never raises: None is the caller's signal
    to run the house chain, exactly the clean failure the
    unfunded lanes give."""
    entry = next(
        (m for m in DRAFT_MODELS if m["id"] == draft_model), None)
    if entry is None or entry["provider"] != "silk":
        return None
    p = _provider("silk")
    api_key = os.environ.get(p["key_env"])
    if not api_key or not p["base_url"]:
        return None
    try:
        return _silk_chat(prompt, api_key, entry["id"])
    except Exception:  # noqa: BLE001 - house chain is the fallback
        return None

# The picking fallback chain (2026-11-17, Jack's free-only
# ruling): OpenRouter FREE models only, tried only after the
# Gemini chain has failed for a window. Every id below was
# verified against OpenRouter's live catalog on 2026-11-17 with
# pricing prompt = 0 and completion = 0, so the fallback adds
# zero cost to clipping. The paid providers in the registry
# above (Venice, Refuseless) are deliberately NOT in this chain;
# their keys stay stored but clipping never calls them.
PICK_CHAIN = (
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("openrouter", "google/gemma-4-31b-it:free"),
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
)


def _provider(provider_id: str) -> dict:
    for p in LLM_PROVIDERS:
        if p["id"] == provider_id:
            return p
    raise KeyError(provider_id)


def public_llm_providers() -> list:
    """The /api/config view: names, ids, the Uncensored flag, and
    whether a key is configured. Never key material."""
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "configured": bool(os.environ.get(p["key_env"])),
            "access": p["access"],
            "models": [
                {"id": m["id"], "label": m["label"], "uncensored": True}
                for m in p["models"]
            ],
        }
        for p in LLM_PROVIDERS
    ]


def any_configured() -> bool:
    """True when at least one picking fallback could actually be
    called (key present and a base URL to call)."""
    for provider_id, _model in PICK_CHAIN:
        p = _provider(provider_id)
        if p["base_url"] and os.environ.get(p["key_env"]):
            return True
    return False


def _chat_pick(prompt: str, base_url: str, api_key: str, model: str):
    """One OpenAI-compatible chat call asking for the same JSON the
    Gemini picker returns. Returns a list of candidate dicts."""
    url = base_url.rstrip("/") + "/chat/completions"
    for with_format in (True, False):
        body = {
            "model": model,
            "temperature": 0.4,
            "messages": [{"role": "user", "content": prompt}],
        }
        if with_format:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"] or ""
            text = text.strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parsed = parsed.get("clips") or parsed.get("moments") or []
            return parsed if isinstance(parsed, list) else []
        except urllib.error.HTTPError as exc:
            if with_format and exc.code in (400, 422):
                continue  # provider rejects response_format; retry bare
            raise
    raise RuntimeError("unreachable")


def pick_fallback(prompt: str):
    """Try each free picker in chain order (OpenRouter free
    models only). Returns a list of candidate dicts from the
    first that answers; raises RuntimeError when the OpenRouter
    key is absent or all free models fail."""
    last_exc = None
    for provider_id, model in PICK_CHAIN:
        p = _provider(provider_id)
        api_key = os.environ.get(p["key_env"])
        if not api_key or not p["base_url"]:
            continue
        try:
            return _chat_pick(prompt, p["base_url"], api_key, model)
        except Exception as exc:  # noqa: BLE001 - next picker
            last_exc = exc
    raise RuntimeError(f"free picking fallback unavailable: {last_exc}")
