"""Uncensored LLM providers (Jack, 2026-11-16: "put them all on
moonfactory").

A registry SEPARATE from the video shelf in app.py's MODELS: these
are text models, they never appear in the video model picker. They
are exposed on /api/config under "llm_providers" and they serve one
job inside the platform today: the Clip AI picking brain tries them
as fallbacks after every Gemini entry has failed for a window
(clipping.find_clips), before the loudness fallback. Gemini stays
the primary brain; default behavior is unchanged.

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
  (435 models, read 2026-11-17). Lane is live as the first
  picking fallback. Recorded in BUILD-LOG.
- venice: models enumerated from Venice's public models endpoint
  (https://api.venice.ai/api/v1/models), filtered to the entries
  Venice itself flags "uncensored": true. Key stored
  (VENICE_API_KEY): the browser signup was Turnstile-walled, so
  Jack created the account and key himself and supplied the key
  in chat 2026-11-17; stored via SDK Secret.update() per-key
  merge (key names 18 -> 19, nothing lost). Verified live the
  same day (free read): GET /api/v1/models 200, 143 models, all
  15 registered uncensored ids present. Lane is live as the
  second picking fallback. Recorded in BUILD-LOG.
- refuseless: pay-as-you-go per-token (their pricing page: "no
  subscription and no monthly minimum"). Model ids from their own
  model pages. Account + key are a browser signup step; a key may
  require adding credit first (their model pages: "Create an
  account, add credit by card or crypto, and call it with your own
  key") — if so, that top-up is Jack's step. Registered dark until
  REFUSELESS_API_KEY lands.
- morph: access is approved per organization ("Approved teams
  only", morphllm.com). The account + access application are a
  browser step for Magrify; no key exists yet, and Morph's API
  base URL is confirmed at key issuance, so base_url is None and
  the picking chain skips it until then. Registered dark.
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
        "access": "awaiting_signup",
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
        "id": "morph",
        "label": "Morph",
        "base_url": None,
        "key_env": "MORPH_API_KEY",
        "access": "awaiting_org_approval",
        "models": [
            {"id": "morph-dsv41flash-abliterated",
             "label": "DeepSeek V4.1 Flash Abliterated (Morph)"},
        ],
    },
]

# The picking fallback chain: one model per provider, in registry
# order, tried only after the Gemini chain has failed for a window.
PICK_CHAIN = (
    ("openrouter", "cognitivecomputations/dolphin-mistral-24b-venice-edition"),
    ("venice", "venice-uncensored-1-2"),
    ("refuseless", "glm-5.3-flash-abliterated"),
    ("morph", "morph-dsv41flash-abliterated"),
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
    """Try each configured uncensored provider in chain order.
    Returns a list of candidate dicts from the first that answers;
    raises RuntimeError when none is configured or all fail."""
    last_exc = None
    for provider_id, model in PICK_CHAIN:
        p = _provider(provider_id)
        api_key = os.environ.get(p["key_env"])
        if not api_key or not p["base_url"]:
            continue
        try:
            return _chat_pick(prompt, p["base_url"], api_key, model)
        except Exception as exc:  # noqa: BLE001 - next provider
            last_exc = exc
    raise RuntimeError(f"uncensored picking fallback unavailable: {last_exc}")
