"""Kling route hunt probe (2026-11-27): read-only live catalog
reads across every provider Moonfactory holds a key with,
returning the FULL catalog entries for every Kling 3.0 / 4.0
video endpoint plus price fragments from public model pages
where the API catalog carries no price (fal, Eachlabs,
CometAPI). No task is created, no credits move, no generation
runs. Key values never leave the function.

Run:  modal run scratch/kling_hunt_probe_app.py
Out:  /tmp/kling_hunt_probe.json
"""

import json
import os
import re

import modal

app = modal.App("moonfactory-kling-hunt-probe")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

UA = {"User-Agent": "Mozilla/5.0 (Moonfactory catalog check)"}


def _kling_entries(node, path=""):
    """Collect dicts that look like catalog entries mentioning
    kling in an id/slug/model/name field."""
    out = []
    if isinstance(node, dict):
        ident = " ".join(str(node.get(k, "")) for k in
                         ("id", "slug", "model", "name", "endpoint_id"))
        if "kling" in ident.lower():
            out.append(node)
        else:
            for k, v in node.items():
                out.extend(_kling_entries(v, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_kling_entries(v, f"{path}[{i}]"))
    return out


def _price_frags(text, limit=14):
    frags = []
    for m in re.finditer(r"\$", text):
        frag = text[max(0, m.start() - 160): m.start() + 200]
        frag = re.sub(r"\s+", " ", frag)
        if frag not in frags:
            frags.append(frag)
        if len(frags) >= limit:
            break
    return frags


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=1200,
)
def probe() -> dict:
    import requests

    out = {}

    def bearer(env):
        k = os.environ.get(env, "")
        return {"Authorization": f"Bearer {k}"} if k else {}

    def get_json(name, url, headers=None, timeout=60):
        try:
            r = requests.get(url, headers={**UA, **(headers or {})},
                             timeout=timeout)
            entry = {"status": r.status_code, "bytes": len(r.text)}
            try:
                data = r.json()
                entry["json"] = True
                entries = _kling_entries(data)
                entry["kling_entries"] = entries[:60]
                entry["kling_count"] = len(entries)
            except Exception:
                entry["json"] = False
                entry["kling_fragments"] = [
                    f for f in _price_frags(r.text) if "kling" in f.lower()]
            out[name] = entry
            return r
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            return None

    def get_page(name, url, headers=None, timeout=40):
        try:
            r = requests.get(url, headers={**UA, **(headers or {})},
                             timeout=timeout)
            out[name] = {"status": r.status_code, "bytes": len(r.text),
                         "price_fragments": _price_frags(r.text)}
            return r
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            return None

    for label, env in (
        ("kie", "KIE_API_KEY"), ("fal", "FAL_KEY"),
        ("beatapi", "BEATAPI_KEY"), ("vidlab", "VIDLAB_API_KEY"),
        ("cometapi", "COMETAPI_API_KEY"),
        ("eachlabs", "EACHLABS_API_KEY"),
        ("spicyapi", "SPICYAPI_API_KEY"),
        ("framehush", "FRAMEHUSH_API_KEY"),
        ("numes", "NUMES_API_KEY"),
    ):
        out[f"key_{label}"] = bool(os.environ.get(env, ""))

    # --- Kie ------------------------------------------------------
    get_json("kie_models", "https://api.kie.ai/api/v1/models",
             bearer("KIE_API_KEY"), timeout=90)

    # --- BeatAPI --------------------------------------------------
    get_json("beatapi_models", "https://api.beatapi.io/v1/models",
             bearer("BEATAPI_KEY"), timeout=90)

    # --- VidLab ---------------------------------------------------
    get_json("vidlab_models", "https://vidlab.ai/v1/models",
             timeout=90)

    # --- SpicyAPI -------------------------------------------------
    get_json("spicyapi_models", "https://api.spicyapi.ai/api/v1/models",
             bearer("SPICYAPI_API_KEY"), timeout=90)

    # --- Numes ----------------------------------------------------
    get_json("numes_models", "https://api.numes.ai/api/v1/models",
             bearer("NUMES_API_KEY"), timeout=90)

    # --- FrameHush ------------------------------------------------
    get_json("framehush_models", "https://www.framehush.com/v1/models",
             bearer("FRAMEHUSH_API_KEY"))

    # --- CometAPI -------------------------------------------------
    get_json("cometapi_models", "https://api.cometapi.com/v1/models",
             bearer("COMETAPI_API_KEY"), timeout=90)
    get_json("cometapi_pricing_api", "https://api.cometapi.com/api/pricing",
             bearer("COMETAPI_API_KEY"), timeout=90)
    get_page("cometapi_pricing_page", "https://www.cometapi.com/pricing/")

    # --- Eachlabs -------------------------------------------------
    el = os.environ.get("EACHLABS_API_KEY", "")
    get_json("eachlabs_models", "https://api.eachlabs.ai/v1/models",
             {"X-API-Key": el} if el else {}, timeout=90)
    for slug in ("kling-video-4-0-text-to-video",
                 "kling-video-4-0-image-to-video",
                 "kling-video-4-0-reference-to-video",
                 "kling-video-4-0-flash-text-to-video",
                 "kling-video-4-0-flash-reference-to-video"):
        get_page(f"eachlabs_page_{slug}",
                 f"https://eachlabs.ai/models/{slug}")

    # --- fal public pages (API catalog carries no prices) ---------
    for label, url in (
        ("v3_std_t2v", "https://fal.ai/models/fal-ai/kling-video/v3/standard/text-to-video"),
        ("v3_pro_t2v", "https://fal.ai/models/fal-ai/kling-video/v3/pro/text-to-video"),
        ("v3_std_i2v", "https://fal.ai/models/fal-ai/kling-video/v3/standard/image-to-video"),
        ("v3_pro_r2v", "https://fal.ai/models/fal-ai/kling-video/v3/pro/reference-to-video"),
        ("v4_t2v", "https://fal.ai/models/kling/v4/text-to-video"),
        ("v4_i2v", "https://fal.ai/models/kling/v4/image-to-video"),
        ("v4_r2v", "https://fal.ai/models/kling/v4/reference-to-video"),
        ("v4_flash_t2v", "https://fal.ai/models/kling/v4/flash/text-to-video"),
        ("v4_flash_r2v", "https://fal.ai/models/kling/v4/flash/reference-to-video"),
    ):
        get_page(f"fal_page_{label}", url)

    return out


@app.local_entrypoint()
def main():
    result = probe.remote()
    with open("/tmp/kling_hunt_probe.json", "w") as fh:
        json.dump(result, fh, indent=1, default=str)
    print("wrote /tmp/kling_hunt_probe.json")
    for k, v in result.items():
        if isinstance(v, dict):
            print(k, "status:", v.get("status"),
                  "kling:", v.get("kling_count"),
                  "frags:", len(v.get("price_fragments", [])),
                  v.get("error", ""))
        else:
            print(k, v)
