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
- morph: DROPPED from the picking chain 2026-11-17 and its
  registry entry removed then; RESTORED 2026-11-20 as a
  draft picker record only (Jack's model choice rule: every
  model is listed individually). The entry is the original
  2026-11-16 registration (key env MORPH_API_KEY, model
  morph-dsv41flash-abliterated) with the base URL Morph's
  API answers on today (https://api.morphllm.com/v1; its
  models route verified live 2026-11-20, 401 key required).
  Morph granted access to this model on the account's key
  (their mail, 2026-11-17), but a key-names probe of the
  moonfactory-providers secret on 2026-11-20 found NO
  MORPH_API_KEY in it, so the record is dark (configured
  False) until the key is stored; a pick of it fails clean
  under the no-substitution draft rule below.
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
- draft picker expansion (2026-11-20, Jack: "just do the
  other uncensored llms ive given u then" + "add regular
  llms too"), corrected the same day by Jack's model
  choice rule: EVERY model is listed individually under
  its own plain name and a pick calls exactly that model;
  no composite labels, no silent substitution when a pick
  fails. The old "House draft" entry is gone: Gemini 3.8
  Flash was the default pick as its own option until
  2026-11-21, when Jack moved the default to a free model
  (Nemotron 3 Ultra 550B, the first Free Models entry);
  Gemini 3.5 Flash Lite is a separate
  option. DRAFT_MODELS below offers the two Gemini models
  (platform key), regular models through the stored
  OpenRouter and Venice keys, the registry's uncensored
  models (Jack's 2026-11-18 tier picks first), the Morph
  model, and the three Silk picks. The regular ids were
  verified against OpenRouter's and Venice's live model
  lists on 2026-11-20 (free reads), and every registered
  Venice uncensored id was confirmed still listed.
  PICK_CHAIN and clipping are untouched.
- gemini 4 neon (2026-11-25, Jack: "Moon factory needs
  Gemini neon too"): Google's Gemini 4 Neon is a TEXT
  reasoning model, not an image or video model (Google's
  launch post, blog.google, 2026-11-18; model id
  gemini-4-neon; text output only, image/audio/video
  input only, 1M-token window). It is wired as its own
  Regular draft pick on the platform Gemini key, exactly
  like the other two Gemini picks. Google's price is
  $3.00 in / $15.00 out per 1M tokens and Neon has NO
  free tier (paid Gemini API tier only), so the pick
  bands at those figures in DRAFT_MODEL_BANDING_FIGURES;
  on a free-tier platform key the pick fails clean by
  name under the no-substitution rule until the key sits
  on the paid tier. NOT in PICK_CHAIN: clipping keeps
  its free-only ruling.
"""

import json
import os
import urllib.error
import urllib.parse
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
        "id": "morph",
        "label": "Morph",
        "base_url": "https://api.morphllm.com/v1",
        "key_env": "MORPH_API_KEY",
        "access": "key_pending",
        "models": [
            {"id": "morph-dsv41flash-abliterated",
             "label": "DeepSeek V4.1 Flash Abliterated"},
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
    {
        # Numes (2026-11-21, Jack's tap): an OpenAI-shaped
        # inference marketplace. The moonfactory key is
        # stored (NUMES_API_KEY). Its launch promo serves a
        # set of chat models at $0 (verified live against
        # GET /models and one $0 chat call); the draft
        # entries below carry no DRAFT_MODEL_PRICES record,
        # so they read as $0 like the OpenRouter :free
        # picks. If the promo ends and Numes starts
        # charging for any of them, that model must get a
        # price record or leave the picker the same day:
        # nothing that costs the platform money may sit at
        # $0. This models list is the uncensored registry
        # view, so it holds only the uncensored Numes
        # builds; the clean free builds are draft picks
        # only.
        "id": "numes",
        "label": "Numes",
        "base_url": "https://api.numes.ai/api/v1",
        "key_env": "NUMES_API_KEY",
        "access": "key_stored",
        "models": [
            {"id": "dealignai/deepseek-v4.1-flash-uncensored-fp8:free",
             "label": "DeepSeek V4.1 Flash Uncensored FP8"},
            {"id": "dealignai/glm-5.3-uncensored-fp8:free",
             "label": "GLM 5.3 Uncensored FP8"},
            {"id": "dealignai/glm-5.3-flash-uncensored-fp8:free",
             "label": "GLM 5.3 Flash Uncensored FP8"},
            {"id": "uniboshi/kimi-k3-abliterated-v1:free",
             "label": "Kimi K3 Abliterated"},
        ],
    },
]

# The draft model picker (2026-11-20, Jack: "use for drafts.
# offer a model picker for drafts"), under Jack's model
# choice rule (2026-11-20): every model Moonfactory can
# offer for drafting and prompt help is its own individually
# selectable option under its own plain name, a pick calls
# exactly that model, and a pick that errors or answers
# empty fails clean (DraftModelUnavailable) with NO silent
# substitution. There is no composite entry: the old "House
# draft" label folded the Gemini chain into one name, which
# the rule kills. The default pick was Gemini 3.8 Flash
# until 2026-11-21, when Jack ordered the default moved to
# a free model: the default pick is now Nemotron 3 Ultra
# 550B, the first Free Models entry, as its own option;
# Gemini 3.8 Flash and Gemini 3.5 Flash Lite stay separate
# regular options. "house" survives only as a
# legacy value from parked creations and older clients; it
# normalizes to the default pick. The internal chains in
# shorts and clipping are NOT user picks and keep their own
# behavior; this rule governs the picker only.
#
# Entry fields: id (the picker value), label (the plain
# name shown), provider (the route the call takes: "gemini"
# is the platform Google key, the others are LLM_PROVIDERS
# records), model (the provider-side model id; defaults to
# the id), group ("free", "regular" or "uncensored" in
# the select). The route-cost record lives in
# DRAFT_MODEL_PRICES below, keyed by id; it is internal
# only and never renders in a label.
DRAFT_MODEL_HOUSE = "house"
# Jack, 2026-11-21: the default pick is a free model, not
# Gemini 3.8 Flash. The default is Nemotron 3 Ultra 550B,
# the first Free Models entry (verified $0 in and $0 out).
DRAFT_MODEL_DEFAULT = \
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
DRAFT_MODELS = [
    # Free Models: the three OpenRouter :free models the
    # internal chains already fall back to, verified $0 in
    # and $0 out in the 2026-11-20 cost audit and offered
    # as picks in their own right (Jack, 2026-11-20). Same
    # ids the chains call; a pick calls exactly that model.
    {"id": "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
     "label": "Nemotron 3 Ultra 550B", "provider": "openrouter",
     "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
     "group": "free"},
    {"id": "openrouter:google/gemma-4-31b-it:free",
     "label": "Gemma 4 31B", "provider": "openrouter",
     "model": "google/gemma-4-31b-it:free", "group": "free"},
    {"id": "openrouter:nvidia/nemotron-3.5-lightning:free",
     "label": "Nemotron 3.5 Lightning", "provider": "openrouter",
     "model": "nvidia/nemotron-3.5-lightning:free",
     "group": "free"},
    # Free Models, Numes route (2026-11-21): the clean
    # builds Numes serves at $0 under its launch promo,
    # verified live with the platform key. Same $0
    # treatment as the OpenRouter :free picks above (no
    # DRAFT_MODEL_PRICES record). Nemotron 3 Ultra is also
    # free on Numes but is NOT duplicated here: the
    # OpenRouter entry above already offers that model.
    {"id": "numes:moonshotai/kimi-k3.1-pro:free",
     "label": "Kimi K3.1 Pro", "provider": "numes",
     "model": "moonshotai/kimi-k3.1-pro:free",
     "group": "free"},
    {"id": "numes:z-ai/glm-5.3:free",
     "label": "GLM 5.3", "provider": "numes",
     "model": "z-ai/glm-5.3:free", "group": "free"},
    {"id": "numes:z-ai/glm-5.3-flash:free",
     "label": "GLM 5.3 Flash", "provider": "numes",
     "model": "z-ai/glm-5.3-flash:free", "group": "free"},
    {"id": "numes:deepseek/deepseek-v4.1-flash:free",
     "label": "DeepSeek V4.1 Flash", "provider": "numes",
     "model": "deepseek/deepseek-v4.1-flash:free",
     "group": "free"},
    # Regular: the platform Gemini models first, then the
    # regular catalogs reachable
    # through the stored OpenRouter and Venice keys.
    {"id": "gemini:gemini-3.8-flash", "label": "Gemini 3.8 Flash",
     "provider": "gemini", "model": "gemini-3.8-flash",
     "group": "regular"},
    {"id": "gemini:gemini-3.5-flash-lite",
     "label": "Gemini 3.5 Flash Lite",
     "provider": "gemini", "model": "gemini-3.5-flash-lite",
     "group": "regular"},
    # Gemini 4 Neon (2026-11-25): Google's text reasoning
    # model on the same platform Gemini key. Paid tier
    # only at Google (no free tier); see the header note.
    {"id": "gemini:gemini-4-neon", "label": "Gemini 4 Neon",
     "provider": "gemini", "model": "gemini-4-neon",
     "group": "regular"},
    {"id": "openrouter:anthropic/claude-sonnet-5",
     "label": "Claude Sonnet 5", "provider": "openrouter",
     "model": "anthropic/claude-sonnet-5", "group": "regular"},
    {"id": "openrouter:openai/gpt-5.5",
     "label": "GPT-5.5", "provider": "openrouter",
     "model": "openai/gpt-5.5", "group": "regular"},
    {"id": "openrouter:google/gemini-3.1-pro-preview",
     "label": "Gemini 3.1 Pro", "provider": "openrouter",
     "model": "google/gemini-3.1-pro-preview",
     "group": "regular"},
    {"id": "openrouter:x-ai/grok-4.7",
     "label": "Grok 4.7", "provider": "openrouter",
     "model": "x-ai/grok-4.7", "group": "regular"},
    {"id": "venice:llama-3.3-70b",
     "label": "Llama 3.3 70B", "provider": "venice",
     "model": "llama-3.3-70b", "group": "regular"},
    {"id": "venice:z-ai-glm-5-3",
     "label": "GLM 5.3", "provider": "venice",
     "model": "z-ai-glm-5-3", "group": "regular"},
    # Uncensored: Jack's 2026-11-18 tier picks first (best
    # overall, hosted, writing, fast), then the rest of the
    # Venice registry, Refuseless, the OpenRouter Dolphin,
    # Morph, and the Silk picks (dark until SILK_API_KEY
    # lands). The tier list's small pick (a Gemma 4 12B) has
    # no registered hosted entry, so it is not listed.
    {"id": "venice:qwen-3-8-27b",
     "label": "Qwen 3.8 27B", "provider": "venice",
     "model": "qwen-3-8-27b", "group": "uncensored"},
    {"id": "venice:deepseek-v4-1-flash-uncensored",
     "label": "DeepSeek V4.1 Flash Uncensored",
     "provider": "venice",
     "model": "deepseek-v4-1-flash-uncensored",
     "group": "uncensored"},
    {"id": "venice:gemma-4-uncensored",
     "label": "Gemma 4 Uncensored", "provider": "venice",
     "model": "gemma-4-uncensored", "group": "uncensored"},
    {"id": "venice:qwen-3-6-plus",
     "label": "Qwen 3.6 Plus Uncensored", "provider": "venice",
     "model": "qwen-3-6-plus", "group": "uncensored"},
    {"id": "venice:venice-uncensored-1-2",
     "label": "Venice Uncensored 1.2", "provider": "venice",
     "model": "venice-uncensored-1-2", "group": "uncensored"},
    {"id": "venice:venice-uncensored-role-play",
     "label": "Venice Role Play Uncensored",
     "provider": "venice",
     "model": "venice-uncensored-role-play",
     "group": "uncensored"},
    {"id": "venice:deepseek-v4-1-flash-1017-uncensored",
     "label": "DeepSeek V4.1 Flash 1017 Uncensored",
     "provider": "venice",
     "model": "deepseek-v4-1-flash-1017-uncensored",
     "group": "uncensored"},
    {"id": "venice:e2ee-deepseek-v4-1-flash-1017-abliterated",
     "label": "DeepSeek V4.1 Flash 1017 Abliterated (E2EE)",
     "provider": "venice",
     "model": "e2ee-deepseek-v4-1-flash-1017-abliterated",
     "group": "uncensored"},
    {"id": "venice:glm-5-3-flash-uncensored",
     "label": "GLM 5.3 Flash Uncensored", "provider": "venice",
     "model": "glm-5-3-flash-uncensored",
     "group": "uncensored"},
    {"id": "venice:olafangensan-glm-4.7-flash-heretic",
     "label": "GLM 4.7 Flash Heretic", "provider": "venice",
     "model": "olafangensan-glm-4.7-flash-heretic",
     "group": "uncensored"},
    {"id": "venice:abliteration-abliterated-model-large-v2",
     "label": "Abliterated Large V2", "provider": "venice",
     "model": "abliteration-abliterated-model-large-v2",
     "group": "uncensored"},
    {"id": "venice:abliteration-abliterated-model-large-flash",
     "label": "Abliterated Large Flash", "provider": "venice",
     "model": "abliteration-abliterated-model-large-flash",
     "group": "uncensored"},
    {"id": "venice:aion-labs-aion-3-5",
     "label": "Aion 3.5", "provider": "venice",
     "model": "aion-labs-aion-3-5", "group": "uncensored"},
    {"id": "venice:aion-labs-aion-3-5-mini",
     "label": "Aion 3.5 Mini", "provider": "venice",
     "model": "aion-labs-aion-3-5-mini", "group": "uncensored"},
    {"id": "venice:aion-labs-aion-3-0-mini",
     "label": "Aion 3.0 Mini", "provider": "venice",
     "model": "aion-labs-aion-3-0-mini", "group": "uncensored"},
    {"id": "refuseless:glm-5.3-abliterated",
     "label": "GLM 5.3 Abliterated", "provider": "refuseless",
     "model": "glm-5.3-abliterated", "group": "uncensored"},
    {"id": "refuseless:glm-5.3-flash-abliterated",
     "label": "GLM 5.3 Flash Abliterated",
     "provider": "refuseless",
     "model": "glm-5.3-flash-abliterated",
     "group": "uncensored"},
    {"id": "refuseless:deepseek-v4.1-flash-abliterated",
     "label": "DeepSeek V4.1 Flash 1017 Abliterated",
     "provider": "refuseless",
     "model": "deepseek-v4.1-flash-abliterated",
     "group": "uncensored"},
    {"id": "refuseless:deepseek-v4.1-flash-abliterated-fast",
     "label": "DeepSeek V4.1 Flash 1017 Fast Abliterated",
     "provider": "refuseless",
     "model": "deepseek-v4.1-flash-abliterated-fast",
     "group": "uncensored"},
    {"id": "openrouter:cognitivecomputations/"
           "dolphin-mistral-24b-venice-edition",
     "label": "Dolphin Mistral 24B", "provider": "openrouter",
     "model": "cognitivecomputations/"
              "dolphin-mistral-24b-venice-edition",
     "group": "uncensored"},
    {"id": "morph:morph-dsv41flash-abliterated",
     "label": "DeepSeek V4.1 Flash Abliterated",
     "provider": "morph",
     "model": "morph-dsv41flash-abliterated",
     "group": "uncensored"},
    {"id": "deepseek-v4.1-flash-1017-uncensored",
     "label": "DeepSeek V4.1 Flash", "provider": "silk",
     "group": "uncensored"},
    {"id": "glm-5.3-flash-uncensored",
     "label": "GLM-5.3 Flash", "provider": "silk",
     "group": "uncensored"},
    {"id": "kimi-k3.1-pro-uncensored",
     "label": "Kimi K3.1 Pro", "provider": "silk",
     "group": "uncensored"},
    # Uncensored builds, Numes route (2026-11-21), filed
    # under Free Models (2026-11-28): the uncensored
    # builds Numes serves at $0 under the same launch
    # promo. They used to sit in the Uncensored group
    # (their nature) with no price record, banding at
    # tier 0 above that group's $ separator, which left
    # the writer pickers grouped unlike the Create
    # pickers. Jack, 2026-11-28: every picker groups the
    # same way, free specified as free, so a $0 route
    # files under Free Models whatever its nature, and
    # the $ separators head paid tiers only. The entries
    # stay at the registry tail beside the Numes provider
    # record they ride; only the group field moved. The
    # promo caveat on the provider record above applies
    # to them in full: if the promo ends, a price record
    # or a group change lands the same day.
    {"id": "numes:dealignai/deepseek-v4.1-flash-uncensored-fp8:free",
     "label": "DeepSeek V4.1 Flash Uncensored FP8",
     "provider": "numes",
     "model": "dealignai/deepseek-v4.1-flash-uncensored-fp8:free",
     "group": "free"},
    {"id": "numes:dealignai/glm-5.3-uncensored-fp8:free",
     "label": "GLM 5.3 Uncensored FP8", "provider": "numes",
     "model": "dealignai/glm-5.3-uncensored-fp8:free",
     "group": "free"},
    {"id": "numes:dealignai/glm-5.3-flash-uncensored-fp8:free",
     "label": "GLM 5.3 Flash Uncensored FP8",
     "provider": "numes",
     "model": "dealignai/glm-5.3-flash-uncensored-fp8:free",
     "group": "free"},
    {"id": "numes:uniboshi/kimi-k3-abliterated-v1:free",
     "label": "Kimi K3 Abliterated", "provider": "numes",
     "model": "uniboshi/kimi-k3-abliterated-v1:free",
     "group": "free"},
]


# INTERNAL COST RECORD ONLY. Per-token prices for the
# picker routes, USD per 1M tokens, input / output,
# exactly as read from each provider's own price data in
# the 2026-11-20 LLM cost audit
# (~/workspace/sinclip/llm-cost-audit-2026-11-20.md).
# This table documents what the routes cost for pricing
# work; it is NEVER rendered in any user-facing label.
# Jack's picker system (restored 2026-11-20 after the
# same-day price strings were pulled on his order):
# picker options are the plain model names only, cost in
# the pickers shows as simple $ tier signs, and an exact
# estimated cost appears only at the Generate button.
# The $ tier signs are the one place this record reaches
# the picker: the draft picker's Regular and Uncensored
# groups carry the same disabled $ separator lines as
# the video and image pickers, banded from these figures
# by draft_model_price_tier below (Jack, 2026-11-20:
# the LLM picker matches the other pickers in full).
# The three OpenRouter :free models
# cost $0 (their Free Models group heading states it) and
# the two Gemini picks depend on which Google tier the
# platform key sits on (free tier $0, paid tier priced),
# still unsettled; neither case changes this rule,
# because no entry in this table ever reaches a label.
DRAFT_MODEL_PRICES = {
    "openrouter:anthropic/claude-sonnet-5": ("2.00", "10.00"),
    "openrouter:openai/gpt-5.5": ("5.00", "30.00"),
    "openrouter:google/gemini-3.1-pro-preview": ("2.00", "12.00"),
    "openrouter:x-ai/grok-4.7": ("2.00", "6.00"),
    "venice:llama-3.3-70b": ("0.70", "2.80"),
    "venice:z-ai-glm-5-3": ("1.75", "5.50"),
    "venice:qwen-3-8-27b": ("0.45", "3.20"),
    "venice:deepseek-v4-1-flash-uncensored": ("0.33", "1.32"),
    "venice:gemma-4-uncensored": ("0.1625", "0.50"),
    "venice:qwen-3-6-plus": ("0.625", "3.75"),
    "venice:venice-uncensored-1-2": ("0.20", "0.90"),
    "venice:venice-uncensored-role-play": ("0.50", "2.00"),
    "venice:deepseek-v4-1-flash-1017-uncensored":
        ("0.33", "1.32"),
    "venice:e2ee-deepseek-v4-1-flash-1017-abliterated":
        ("0.375", "1.50"),
    "venice:glm-5-3-flash-uncensored": ("0.165", "0.55"),
    "venice:olafangensan-glm-4.7-flash-heretic":
        ("0.07", "0.40"),
    "venice:abliteration-abliterated-model-large-v2":
        ("3.00", "5.00"),
    "venice:abliteration-abliterated-model-large-flash":
        ("1.25", "2.50"),
    "venice:aion-labs-aion-3-5": ("3.75", "7.50"),
    "venice:aion-labs-aion-3-5-mini": ("0.875", "1.75"),
    "venice:aion-labs-aion-3-0-mini": ("0.875", "1.75"),
    "refuseless:glm-5.3-abliterated": ("1.00", "2.00"),
    "refuseless:glm-5.3-flash-abliterated": ("0.12", "0.50"),
    "refuseless:deepseek-v4.1-flash-abliterated":
        ("0.12", "0.45"),
    "refuseless:deepseek-v4.1-flash-abliterated-fast":
        ("0.12", "0.45"),
    "openrouter:cognitivecomputations/"
    "dolphin-mistral-24b-venice-edition": ("0.20", "0.90"),
    "morph:morph-dsv41flash-abliterated": ("0.30", "1.20"),
    "deepseek-v4.1-flash-1017-uncensored": ("0.24", "0.90"),
    "glm-5.3-flash-uncensored": ("0.24", "1.00"),
    "kimi-k3.1-pro-uncensored": ("3.50", "8.00"),
}


# $ TIER BANDING for the draft picker (Jack, 2026-11-20).
# The video picker bands its models by a fixed rate table
# ($ under 5c/s, $$ under 12c/s, $$$ under 25c/s, $$$$
# above) and the image picker does the same per image;
# the draft picker bands by the audited route cost: the
# input plus output price per 1M tokens from
# DRAFT_MODEL_PRICES, on fixed bands in the same style.
# Tier 0 means no separator: the Free Models group (all
# $0, the heading states it), exactly as the generation
# pickers give their Free group no separators.
DRAFT_MODEL_TIER_BANDS = (1.50, 4.00, 10.00)

# The Gemini picks are banded by their paid-tier list
# prices (the worst case), because which Google tier the
# platform key sits on is unsettled: Gemini 3.8 Flash at
# $0.75 in / $3.75 out and Gemini 3.5 Flash Lite at $0.30
# in / $2.50 out per 1M, from the same 2026-11-20 audit.
# Gemini 4 Neon's figures are Google's launch prices
# (2026-11-18) and are its ONLY prices: Neon has no free
# tier, so there is no cheaper case to band against.
# Banding only; these figures render nowhere either.
DRAFT_MODEL_BANDING_FIGURES = {
    "gemini:gemini-3.8-flash": ("0.75", "3.75"),
    "gemini:gemini-3.5-flash-lite": ("0.30", "2.50"),
    "gemini:gemini-4-neon": ("3.00", "15.00"),
}


def draft_model_cost(model_id: str):
    """The audited route cost of a draft pick as one
    number: input plus output USD per 1M tokens. The two
    Gemini picks read at their paid-tier list prices (see
    DRAFT_MODEL_BANDING_FIGURES). None for the $0 free
    models and for any id with no audited figure."""
    figures = DRAFT_MODEL_PRICES.get(
        model_id, DRAFT_MODEL_BANDING_FIGURES.get(model_id))
    if figures is None:
        return None
    return float(figures[0]) + float(figures[1])


def draft_model_price_tier(model_id: str) -> int:
    """The $ tier of a draft pick, 1 to 4, from its
    audited route cost on DRAFT_MODEL_TIER_BANDS; 0 for
    the Free Models group, whose members carry no tier
    separator, same as the generation pickers."""
    entry = draft_model_entry(model_id)
    if entry is None or entry["group"] == "free":
        return 0
    cost = draft_model_cost(model_id)
    if cost is None:
        return 0
    tier = 1
    for band in DRAFT_MODEL_TIER_BANDS:
        if cost >= band:
            tier += 1
    return tier


def draft_model_entry(value) -> dict:
    """The registry entry for a picker value, or None."""
    return next(
        (m for m in DRAFT_MODELS if m["id"] == value), None)


def draft_model_label(value) -> str:
    """The plain name for a picker value (the default pick's
    name for an unknown value)."""
    entry = draft_model_entry(value)
    if entry is not None:
        return entry["label"]
    return draft_model_entry(DRAFT_MODEL_DEFAULT)["label"]


def provider_configured(provider_id: str) -> bool:
    """True when a provider route could actually be called
    right now (base URL and key env present). The "gemini"
    route is the platform Google key, not a registry record."""
    if provider_id == "gemini":
        return bool(os.environ.get("GEMINI_API_KEY"))
    p = _provider(provider_id)
    return bool(p["base_url"] and os.environ.get(p["key_env"]))


def any_draft_configured() -> bool:
    """True when at least one picker entry's route is live."""
    return any(
        provider_configured(m["provider"]) for m in DRAFT_MODELS)


def draft_model_options() -> list:
    """The picker view: each model's plain name (option
    labels are the plain names only; no price text ever
    rides this payload), its id, its group, its $ tier
    (the band its separator heads in the picker, from
    draft_model_price_tier), and whether picking the
    entry could actually reach its model right now.
    Never key material."""
    return [
        {
            "id": m["id"],
            "label": m["label"],
            "group": m["group"],
            "price_tier": draft_model_price_tier(m["id"]),
            "configured": provider_configured(m["provider"]),
        }
        for m in DRAFT_MODELS
    ]


def is_draft_model(value) -> bool:
    return draft_model_entry(value) is not None


def normalize_draft_model(value) -> str:
    """A missing, unknown, or legacy "house" pick reads as
    the default pick (Nemotron 3 Ultra 550B, the first free
    model), so older clients and parked creations keep
    drafting."""
    if value and value != DRAFT_MODEL_HOUSE \
            and is_draft_model(value):
        return value
    return DRAFT_MODEL_DEFAULT


def silk_configured() -> bool:
    """True when the Silk key is present in the environment
    (the moonfactory-providers secret). False today by design:
    the signup is finishing separately."""
    return provider_configured("silk")


class DraftModelUnavailable(RuntimeError):
    """The picked draft model did not answer: its route is
    not configured, the call failed, or the answer came back
    empty. Under the model choice rule a pick is never
    silently replaced by another model, so the drafting
    flows surface this as a clean failure that names the
    pick. Carries the pick's plain label."""

    def __init__(self, label: str):
        super().__init__(f"{label} did not answer")
        self.label = label


def _provider_chat(prompt: str, provider_id: str,
                   api_key: str, model: str) -> str:
    """One OpenAI compatible chat call to a registry
    provider, returning the raw text (drafting wants prose
    or the drama JSON, not a parsed shape). Raises on any
    failure or an empty answer."""
    p = _provider(provider_id)
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
        raise RuntimeError("draft came back empty")
    return text


def _silk_chat(prompt: str, api_key: str, model: str) -> str:
    """One chat call to Silk Compute (kept as the named seam
    the first draft build's checks stub)."""
    return _provider_chat(prompt, "silk", api_key, model)


def _gemini_chat(prompt: str, api_key: str, model: str) -> str:
    """One Google Gemini call for exactly the named model,
    in the same request shape shorts._gemini_text uses.
    Raises on any failure or an empty answer."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key="
        + urllib.parse.quote(api_key)
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7},
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    if not text or not text.strip():
        raise RuntimeError("draft came back empty")
    return text


def draft_text(prompt: str, draft_model: str) -> str:
    """The picked draft model's text, from exactly that
    model. Raises DraftModelUnavailable when the pick is
    unknown, its route is not configured, the call fails,
    or the answer is empty: under the model choice rule a
    user pick is never silently substituted with another
    model, so the caller turns this into a clean failure
    that names the pick."""
    entry = draft_model_entry(draft_model)
    if entry is None:
        raise DraftModelUnavailable(
            draft_model_label(draft_model))
    label = entry["label"]
    model = entry.get("model") or entry["id"]
    try:
        if entry["provider"] == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise DraftModelUnavailable(label)
            return _gemini_chat(prompt, api_key, model)
        p = _provider(entry["provider"])
        api_key = os.environ.get(p["key_env"])
        if not api_key or not p["base_url"]:
            raise DraftModelUnavailable(label)
        if entry["provider"] == "silk":
            return _silk_chat(prompt, api_key, model)
        return _provider_chat(
            prompt, entry["provider"], api_key, model)
    except DraftModelUnavailable:
        raise
    except Exception:  # noqa: BLE001 - the pick failed, cleanly
        raise DraftModelUnavailable(label)


def silk_draft_text(prompt: str, draft_model: str):
    """Compatibility view from the first draft build: the
    picked Silk model's text, or None for a non-Silk pick,
    a missing key, or any failure. The drafting flows no
    longer use it (they call draft_text, which never
    substitutes a pick); it stays for the original checks."""
    entry = draft_model_entry(draft_model)
    if entry is None or entry["provider"] != "silk":
        return None
    p = _provider("silk")
    api_key = os.environ.get(p["key_env"])
    if not api_key or not p["base_url"]:
        return None
    try:
        return _silk_chat(prompt, api_key, entry["id"])
    except Exception:  # noqa: BLE001
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
