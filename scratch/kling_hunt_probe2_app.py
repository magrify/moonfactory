"""Kling hunt probe 2 (2026-11-27): follow-up reads where the
first probe's shapes missed prices — BeatAPI (loose matcher on
the raw catalog), Eachlabs model detail + pricing surfaces,
CometAPI pricing surfaces. Read-only; no tasks, no credits.

Run:  modal run scratch/kling_hunt_probe2_app.py
Out:  /tmp/kling_hunt_probe2.json
"""

import json
import os
import re

import modal

app = modal.App("moonfactory-kling-hunt-probe2")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

UA = {"User-Agent": "Mozilla/5.0 (Moonfactory catalog check)"}


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=900,
)
def probe() -> dict:
    import requests

    out = {}

    def bearer(env):
        k = os.environ.get(env, "")
        return {"Authorization": f"Bearer {k}"} if k else {}

    def raw(name, url, headers=None, timeout=60, keep=()):
        try:
            r = requests.get(url, headers={**UA, **(headers or {})},
                             timeout=timeout)
            entry = {"status": r.status_code, "bytes": len(r.text)}
            text = r.text
            frags = []
            low = text.lower()
            for needle in keep:
                for m in re.finditer(re.escape(needle), low):
                    frag = re.sub(r"\s+", " ",
                                  text[max(0, m.start() - 300):
                                       m.start() + 700])
                    if frag not in frags:
                        frags.append(frag)
                    if len(frags) >= 12:
                        break
            entry["fragments"] = frags
            try:
                data = r.json()
                entry["json"] = True
                entry["data"] = data
            except Exception:
                entry["json"] = False
            out[name] = entry
            return r
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            return None

    # --- BeatAPI: dump every top-level entry whose JSON mentions
    # kling, whatever field carries the name.
    try:
        r = requests.get("https://api.beatapi.io/v1/models",
                         headers={**UA, **bearer("BEATAPI_KEY")},
                         timeout=90)
        data = r.json()
        items = data if isinstance(data, list) else (
            data.get("data") or data.get("models") or [])
        hits = [e for e in items
                if "kling" in json.dumps(e).lower()]
        out["beatapi_kling"] = {"status": r.status_code,
                                "total": len(items), "hits": hits[:40]}
    except Exception as exc:
        out["beatapi_kling"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    el = os.environ.get("EACHLABS_API_KEY", "")
    el_headers = {"X-API-Key": el} if el else {}
    raw("eachlabs_detail_t2v",
        "https://api.eachlabs.ai/v1/models/kling-video-4-0-text-to-video",
        el_headers, keep=("kling", "price", "cost", "credit"))
    raw("eachlabs_pricing", "https://api.eachlabs.ai/v1/pricing",
        el_headers, keep=("kling",))
    raw("eachlabs_pricing_page", "https://eachlabs.ai/pricing",
        keep=("kling",))

    raw("cometapi_pricing_v1", "https://api.cometapi.com/v1/pricing",
        bearer("COMETAPI_API_KEY"), keep=("kling",))
    raw("cometapi_kling_page", "https://www.cometapi.com/models/kling-video/",
        keep=("kling", "$"))
    return out


@app.local_entrypoint()
def main():
    result = probe.remote()
    with open("/tmp/kling_hunt_probe2.json", "w") as fh:
        json.dump(result, fh, indent=1, default=str)
    print("wrote /tmp/kling_hunt_probe2.json")
    for k, v in result.items():
        if isinstance(v, dict):
            print(k, "status:", v.get("status"), "json:", v.get("json"),
                  "frags:", len(v.get("fragments", [])),
                  "hits:", len(v.get("hits", [])) if "hits" in v else "",
                  v.get("error", ""))
