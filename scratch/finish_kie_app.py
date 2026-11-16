"""Finish-run Kie probe (2026-11-16): read Kie's own pricing for
the flagged lanes from every API/catalog surface reachable with
the stored key (no console login; the browser route is
hCaptcha-blocked). Read-only: no task is created, no credits move.

Surfaces tried:
  - api.kie.ai model/credit endpoints with the Bearer key
  - kie.ai public model pages (server-rendered pricing tables)
Focus: Kling 3.0 Omni sound-on cost (the one seed still resting
on third-party estimates), plus a spot re-check of the Kling
3.0 / 4.0 with-audio rates corrected in pricing round 2.
"""

import json
import os
import re

import modal

app = modal.App("moonfactory-finish-kie")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

PAGES = {
    "kling3_omni": "https://kie.ai/kling-3-0-omni",
    "kling3": "https://kie.ai/kling-3-0",
    "kling4": "https://kie.ai/kling-4-0",
}


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=600,
)
def run_kie() -> dict:
    import requests

    key = os.environ.get("KIE_API_KEY", "")
    out = {"key_present": bool(key)}
    H = {"Authorization": f"Bearer {key}"} if key else {}
    UA = {"User-Agent": "Mozilla/5.0 (Moonfactory pricing check)"}

    for name, url in (
        ("api_models", "https://api.kie.ai/api/v1/models"),
        ("api_user_info", "https://api.kie.ai/api/v1/common/user-info"),
        ("api_credits", "https://api.kie.ai/api/v1/users/credits"),
    ):
        try:
            r = requests.get(url, headers={**H, **UA}, timeout=20)
            out[name] = f"{r.status_code} {r.text[:400]}"
        except Exception as exc:
            out[name] = f"ERR {type(exc).__name__}: {str(exc)[:120]}"

    # Public model pages: pull the pricing-table text around any
    # per-second figures.
    for name, url in PAGES.items():
        try:
            r = requests.get(url, headers=UA, timeout=30)
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text)
            hits = []
            for m in re.finditer(
                    r"[^.]{0,90}(\$0\.\d+|per second|/s\b|credits)[^.]{0,90}",
                    text):
                frag = m.group(0).strip()
                if frag not in hits:
                    hits.append(frag)
                if len(hits) >= 14:
                    break
            out[f"page_{name}"] = {"status": r.status_code, "hits": hits}
        except Exception as exc:
            out[f"page_{name}"] = f"ERR {type(exc).__name__}: {str(exc)[:120]}"
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(run_kie.remote(), indent=1))
