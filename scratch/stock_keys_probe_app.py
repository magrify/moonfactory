"""Stock source key probe (2026-11-20): presence of the Pixabay and
Unsplash keys in the moonfactory-providers secret, and, when a key is
present, a live check of that source's API from inside Modal so the
key value never leaves the function. Returns only booleans, HTTP
statuses, and result counts. No generation, no renders.

Run:  modal run scratch/stock_keys_probe_app.py
"""

import json
import os
import urllib.parse
import urllib.request

import modal

app = modal.App("moonfactory-stock-keys-probe")
image = modal.Image.debian_slim(python_version="3.12")


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return -1, {}


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=120,
)
def probe() -> dict:
    out = {}
    pix = os.environ.get("PIXABAY_API_KEY") or ""
    uns = os.environ.get("UNSPLASH_ACCESS_KEY") or ""
    out["PIXABAY_API_KEY_present"] = bool(pix)
    out["UNSPLASH_ACCESS_KEY_present"] = bool(uns)
    if pix:
        q = urllib.parse.urlencode(
            {"key": pix, "q": "lighthouse sunset",
             "image_type": "photo", "per_page": 3})
        status, data = _get("https://pixabay.com/api/?" + q)
        out["pixabay_status"] = status
        out["pixabay_total_hits"] = data.get("totalHits")
        hits = data.get("hits") or []
        out["pixabay_first_hit"] = bool(hits)
    if uns:
        q = urllib.parse.urlencode(
            {"query": "lighthouse sunset", "per_page": 3})
        status, data = _get(
            "https://api.unsplash.com/search/photos?" + q,
            headers={"Authorization": "Client-ID " + uns,
                     "Accept-Version": "v1"})
        out["unsplash_status"] = status
        out["unsplash_total"] = data.get("total")
        results = data.get("results") or []
        out["unsplash_first_hit"] = bool(results)
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(probe.remote(), indent=2))
