"""Route audit probe (2026-11-26): read-only full-catalog pulls
from every provider Moonfactory holds a key with, for the
cheapest-route audit. No task is created, no credits move, no
generation runs. Key values never leave the function; only
public catalog data comes back.

Surfaces:
  - Kie:       GET api.kie.ai/api/v1/models (Bearer)
  - fal:       GET api.fal.ai/v1/models (Key) for the id list,
               plus fal.ai model pages for price lines on a
               targeted endpoint list
  - BeatAPI:   beatapi.io sitemap -> every model page's price
               lines (its authenticated /v1/models is not the
               video catalog)
  - VidLab:    GET vidlab.ai/v1/models (public + Bearer)
  - CometAPI:  GET api.cometapi.com/v1/models (Bearer)
  - Eachlabs:  GET api.eachlabs.ai/v1/models (X-API-Key)
  - SpicyAPI:  GET api.spicyapi.ai/api/v1/models (Bearer)
  - FrameHush: GET framehush.com/v1/models (Bearer)
  - Numes:     GET api.numes.ai/api/v1/models (Bearer)

Run:  modal run scratch/route_audit_probe_app.py
Writes scratch/route-audit-2026-11-26/<provider>.json locally.
"""

import json
import os
import re
from pathlib import Path

import modal

app = modal.App("moonfactory-route-audit-probe")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

UA = {"User-Agent": "Mozilla/5.0 (Moonfactory route audit)"}
OUT = Path(__file__).parent / "route-audit-2026-11-26"

FAL_PAGE_ENDPOINTS = [
    "lightricks/ltx-2.5/text-to-video",
    "lightricks/ltx-2.5/image-to-video",
    "lightricks/ltx-2.5/text-to-video/fast",
    "fal-ai/luma-dream-machine/ray-3.2/text-to-video",
    "fal-ai/pika/v2.2/text-to-video",
    "fal-ai/wan-motion",
    "fal-ai/vidu/q2/text-to-video",
    "fal-ai/vidu/q3/text-to-video",
    "xai/grok-imagine-video/text-to-video",
    "fal-ai/veo3.1",
    "fal-ai/veo3.1/fast",
    "fal-ai/kling-video/v3/pro/text-to-video",
    "fal-ai/kling-video/v2.6/pro/text-to-video",
    "fal-ai/minimax/hailuo-2.3/text-to-video",
    "fal-ai/wan/v2.6/text-to-video",
    "fal-ai/wan-3/text-to-video",
    "bytedance/seedance-2.0/text-to-video",
    "bytedance/seedance-2.5/text-to-video",
]


def _trim(node, depth=0):
    """Drop long prose fields so the return payload stays sane;
    ids, names and every price-shaped field survive."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if isinstance(v, str) and len(v) > 400 and "pric" not in k.lower():
                out[k] = v[:200] + "..."
            else:
                out[k] = _trim(v, depth + 1)
        return out
    if isinstance(node, list):
        return [_trim(v, depth + 1) for v in node]
    return node


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=1500,
)
def probe() -> dict:
    import requests

    out = {}

    def get(name, url, headers=None, timeout=45):
        try:
            r = requests.get(url, headers={**UA, **(headers or {})},
                             timeout=timeout)
            entry = {"status": r.status_code, "bytes": len(r.text)}
            try:
                entry["data"] = _trim(r.json())
            except Exception:
                entry["text"] = r.text[:200000]
            out[name] = entry
            return r
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            return None

    key = os.environ.get("KIE_API_KEY")
    if key:
        get("kie", "https://api.kie.ai/api/v1/models",
            {"Authorization": f"Bearer {key}"})
    key = os.environ.get("FAL_KEY")
    if key:
        get("fal_catalog", "https://api.fal.ai/v1/models?limit=1000",
            {"Authorization": f"Key {key}"})
        pages = {}
        for ep in FAL_PAGE_ENDPOINTS:
            try:
                r = requests.get(f"https://fal.ai/models/{ep}",
                                 headers=UA, timeout=30)
                text = r.text.replace("\\", "")
                pairs = {}
                for price, res in re.findall(
                        r"\$(\d+\.\d+)\*\*\s*\*\*(480p|720p|1080p|4K)\*\*",
                        text):
                    pairs[res] = float(price)
                if not pairs:
                    for price, res in re.findall(
                            r"\$(\d+\.\d+)\s+at\s+(480p|720p|1080p|4K)",
                            text):
                        pairs[res] = float(price)
                generics = [float(x) for x in re.findall(
                    r"\$(\d+\.\d+)\s*(?:per|/)\s*(?:second|video|generation|image)",
                    text)]
                pages[ep] = {"status": r.status_code, "pairs": pairs,
                             "generic_prices": generics[:6]}
            except Exception as exc:
                pages[ep] = {"error": str(exc)[:120]}
        out["fal_pages"] = pages
    # BeatAPI: sitemap -> model pages, price lines only.
    try:
        r = requests.get("https://beatapi.io/sitemap.xml",
                         headers=UA, timeout=30)
        urls = re.findall(r"<loc>(https://beatapi\.io/[^<]+)</loc>",
                          r.text)
        model_urls = [u for u in urls if u.rstrip("/").count("/") >= 3
                      or "-api" in u]
        pages = {}
        for u in model_urls[:90]:
            try:
                pr = requests.get(u, headers=UA, timeout=25)
                text = pr.text
                prices = []
                for m in re.finditer(
                        r"\$(\d+\.\d+)\s*(?:per|/)\s*(second|video|generation|image|clip)?",
                        text):
                    prices.append(
                        {"usd": float(m.group(1)),
                         "unit": m.group(2) or "",
                         "ctx": text[max(0, m.start() - 90):
                                     m.start() + 60].replace("\n", " ")})
                pages[u] = {"status": pr.status_code,
                            "prices": prices[:14]}
            except Exception as exc:
                pages[u] = {"error": str(exc)[:120]}
        out["beatapi_pages"] = {"sitemap_urls": len(urls), "pages": pages}
    except Exception as exc:
        out["beatapi_pages"] = {"error": str(exc)[:200]}
    get("vidlab", "https://vidlab.ai/v1/models")
    key = os.environ.get("COMETAPI_API_KEY")
    if key:
        get("cometapi", "https://api.cometapi.com/v1/models",
            {"Authorization": f"Bearer {key}"})
    key = os.environ.get("EACHLABS_API_KEY")
    if key:
        get("eachlabs", "https://api.eachlabs.ai/v1/models?limit=1000",
            {"X-API-Key": key})
    key = os.environ.get("SPICYAPI_API_KEY")
    if key:
        get("spicyapi", "https://api.spicyapi.ai/api/v1/models",
            {"Authorization": f"Bearer {key}"})
    key = os.environ.get("FRAMEHUSH_API_KEY")
    if key:
        get("framehush", "https://www.framehush.com/v1/models",
            {"Authorization": f"Bearer {key}"})
    return out


@app.local_entrypoint()
def main():
    result = probe.remote()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, entry in result.items():
        (OUT / f"{name}.json").write_text(
            json.dumps(entry, indent=1)[:60_000_000])
        status = entry.get("status") or entry.get("error") or "ok"
        print(f"{name}: {status}")
    print(f"wrote {OUT}")
