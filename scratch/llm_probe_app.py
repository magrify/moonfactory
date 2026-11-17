"""Uncensored-LLM wiring probe (2026-11-17): read-only checks for
the LLM provider pass. Returns (a) the key NAMES present in the
moonfactory-providers secret (never values), and (b) free
OpenRouter account-read calls with the stored key: the public
models list (does it carry the Venice Uncensored Dolphin?) and the
auth/key usage read. No generation, no paid calls.
"""

import os

import modal

app = modal.App("moonfactory-llm-probe")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=300,
)
def probe() -> dict:
    import requests

    skip_prefixes = ("MODAL", "PATH", "HOME", "LANG", "LC_", "PWD",
                     "SHLVL", "PYTHON", "HOSTNAME", "_")
    names = sorted(
        k for k in os.environ
        if k.isupper() and not k.startswith(skip_prefixes)
    )
    out = {"secret_key_names": names}

    key = os.environ.get("OPENROUTER_API_KEY", "")
    out["openrouter_key_present"] = bool(key)
    if key:
        h = {"Authorization": f"Bearer {key}"}
        try:
            r = requests.get("https://openrouter.ai/api/v1/models",
                             headers=h, timeout=30)
            ids = [m.get("id") for m in r.json().get("data", [])]
            out["openrouter_models_status"] = r.status_code
            out["openrouter_model_count"] = len(ids)
            out["dolphin_venice_present"] = (
                "cognitivecomputations/dolphin-mistral-24b-venice-edition"
                in ids
            )
        except Exception as exc:  # noqa: BLE001
            out["openrouter_models_error"] = f"{type(exc).__name__}: {exc}"
        try:
            r = requests.get("https://openrouter.ai/api/v1/auth/key",
                             headers=h, timeout=30)
            d = (r.json() or {}).get("data") or {}
            out["openrouter_auth_key"] = {
                "status": r.status_code,
                "usage": d.get("usage"),
                "limit": d.get("limit"),
                "is_free_tier": d.get("is_free_tier"),
            }
        except Exception as exc:  # noqa: BLE001
            out["openrouter_auth_error"] = f"{type(exc).__name__}: {exc}"
        try:
            r = requests.get("https://openrouter.ai/api/v1/credits",
                             headers=h, timeout=30)
            d = (r.json() or {}).get("data") or {}
            out["openrouter_credits"] = {
                "status": r.status_code,
                "total_credits": d.get("total_credits"),
                "total_usage": d.get("total_usage"),
            }
        except Exception as exc:  # noqa: BLE001
            out["openrouter_credits_error"] = f"{type(exc).__name__}: {exc}"
    return out


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(probe.remote(), indent=2))
