"""Route audit follow-up (2026-11-26): FrameHush estimate reads
for the models its catalog lists that Moonfactory lanes could
ride (Wan 3.0, MiniMax H3, Seedance 2.0, Seedance 2.5 Flash).
Estimates only: no task is created, no credits move.

Run:  modal run scratch/route_audit_framehush_est_app.py
"""

import json
import os
from pathlib import Path

import modal

app = modal.App("moonfactory-route-audit-framehush")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")
UA = {"User-Agent": "Mozilla/5.0 (Moonfactory route audit)"}
OUT = Path(__file__).parent / "route-audit-2026-11-26" / "framehush_estimates.json"

CASES = []
for model, ress in (
    ("wan-3.0", ("480p", "720p", "1080p")),
    ("MiniMax-H3", ("720p", "1080p", "2K")),
    ("ME-MiniMax-H3-2K", ("1080p", "2K")),
    ("mtapi-minimax-h3", ("720p", "1080p")),
    ("seedance_2.0", ("480p", "720p", "1080p", "4K")),
    ("XG-Seedance-2.0-720", ("720p",)),
    ("sd2.5-flash", ("480p", "720p")),
    ("doubao-seedance-2-5-flash-261115", ("480p", "720p")),
):
    for res in ress:
        CASES.append((model, res))


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=900,
)
def probe() -> dict:
    import requests

    key = os.environ.get("FRAMEHUSH_API_KEY", "")
    headers = {**UA, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    out = {}
    for model, res in CASES:
        label = f"{model}|{res}"
        try:
            r = requests.post(
                "https://www.framehush.com/v1/video-studio/estimate",
                headers=headers,
                json={"model": model, "resolution": res, "duration": 5},
                timeout=40)
            out[label] = f"{r.status_code} {r.text[:400]}"
        except Exception as exc:
            out[label] = f"ERR {type(exc).__name__}: {exc}"[:160]
    return out


@app.local_entrypoint()
def main():
    result = probe.remote()
    OUT.write_text(json.dumps(result, indent=1))
    for k, v in result.items():
        print(k, "->", v[:220])
