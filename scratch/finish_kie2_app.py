"""Finish-run Kie probe 2: dump the catalog entries (all fields)
for the models Moonfactory routes through Kie, from Kie's own
authenticated models API. Read-only."""

import json
import os

import modal

app = modal.App("moonfactory-finish-kie2")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

WANT = ("kling-4.0", "kling-3.0", "seedance", "bytedance", "hailuo", "minimax",
        "seedance-2.0-mini", "gemini-omni", "grok-imagine",
        "hailuo-2.3")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=600,
)
def run_kie2() -> dict:
    import requests

    key = os.environ.get("KIE_API_KEY", "")
    r = requests.get("https://api.kie.ai/api/v1/models",
                     headers={"Authorization": f"Bearer {key}"},
                     timeout=60)
    data = r.json()["data"]
    models = data["models"]
    out = {"total": len(models), "entries": {}}
    for m in models:
        slug = (m.get("model") or "").lower()
        if any(w in slug for w in WANT):
            keep = {}
            for k, v in m.items():
                lk = k.lower()
                if any(w in lk for w in ("price", "pricing", "credit",
                                         "cost", "charge", "rate",
                                         "slug", "tasktype")):
                    keep[k] = v
            out["entries"][m.get("model")] = keep
    return out


@app.local_entrypoint()
def main():
    result = run_kie2.remote()
    with open("/tmp/kie_entries.json", "w") as fh:
        json.dump(result, fh, indent=1)
    print("wrote /tmp/kie_entries.json, matched:",
          len(result["entries"]), "of", result["total"])
