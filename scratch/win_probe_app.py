"""No-expiry wins probe (2026-11-25): read-only account checks
for the two approved provider moves. No task is created, no
credits move, no generation runs.

1. Kie: the stored key's own catalog API (api.kie.ai/api/v1/
   models) filtered to the Veo family, so the Veo 3.1 Lite
   lane's model id and pricingDesc come from Kie itself, plus
   the account user-info surface.
2. APIMart: whether any APIMart key exists in the
   moonfactory-providers secret at all, and, if one does,
   what the account's own API surfaces return for model /
   pricing reads (the Ext-channel Nano Banana rates are
   Gold-member prices on the public page; only the account's
   own read can say what this account actually pays).

Returns only statuses, model ids, pricing text and truncated
bodies. Key values never leave the function.

Run:  modal run scratch/win_probe_app.py
"""

import json
import os

import modal

app = modal.App("moonfactory-win-probe")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=300,
)
def probe() -> dict:
    import requests

    out = {}
    UA = {"User-Agent": "Mozilla/5.0 (Moonfactory pricing check)"}

    kie = os.environ.get("KIE_API_KEY", "")
    out["kie_key_present"] = bool(kie)
    if kie:
        H = {"Authorization": f"Bearer {kie}", **UA}
        try:
            import re as _re

            r = requests.get("https://api.kie.ai/api/v1/models",
                             headers=H, timeout=30)
            out["kie_models_status"] = r.status_code
            if r.status_code == 200:
                text = r.text
                out["kie_models_bytes"] = len(text)
                try:
                    data = r.json()
                    out["kie_models_top_type"] = type(data).__name__
                    if isinstance(data, dict):
                        out["kie_models_top_keys"] = sorted(data)[:20]
                        inner = data.get("data")
                        out["kie_models_data_type"] = \
                            type(inner).__name__
                        if isinstance(inner, dict):
                            out["kie_models_data_keys"] = \
                                sorted(inner)[:20]
                        elif isinstance(inner, list):
                            out["kie_models_data_count"] = len(inner)
                except Exception:
                    pass
                frags = []
                for m in _re.finditer(r"veo", text, _re.IGNORECASE):
                    frag = text[max(0, m.start() - 160):
                                m.start() + 340]
                    if frag not in frags:
                        frags.append(frag)
                    if len(frags) >= 12:
                        break
                out["kie_veo_fragments"] = frags
        except Exception as exc:
            out["kie_models"] = f"ERR {type(exc).__name__}: {exc}"[:200]
        try:
            r = requests.get(
                "https://api.kie.ai/api/v1/common/user-info",
                headers=H, timeout=20)
            out["kie_user_info"] = f"{r.status_code} {r.text[:300]}"
        except Exception as exc:
            out["kie_user_info"] = f"ERR {type(exc).__name__}: {exc}"[:200]

    mart = os.environ.get("APIMART_API_KEY", "") or \
        os.environ.get("APIMART_KEY", "")
    out["apimart_key_present"] = bool(mart)
    out["apimart_env_names_present"] = sorted(
        name for name in os.environ
        if "APIMART" in name or "MART" in name)
    if mart:
        H = {"Authorization": f"Bearer {mart}", **UA}
        for name, url in (
            ("models", "https://api.apimart.ai/v1/models"),
            ("user_info", "https://api.apimart.ai/v1/user/info"),
            ("balance", "https://api.apimart.ai/v1/user/balance"),
        ):
            try:
                r = requests.get(url, headers=H, timeout=20)
                out[f"apimart_{name}"] = \
                    f"{r.status_code} {r.text[:1200]}"
            except Exception as exc:
                out[f"apimart_{name}"] = \
                    f"ERR {type(exc).__name__}: {exc}"[:200]
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(probe.remote(), indent=1))
