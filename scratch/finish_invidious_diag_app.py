"""Diagnostic: which Invidious/Piped instances answer from Modal
egress for video jNQXAC9IVRw? Reports status + first bytes per
candidate. CPU-only, read-only."""

import json

import modal

app = modal.App("moonfactory-invidious-diag")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

VID = "jNQXAC9IVRw"
UA = {"User-Agent": "Mozilla/5.0 (Moonfactory clip fetch)"}

CANDIDATES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
    "https://invidious.f5.si",
    "https://invidious.0011.lt",
    "https://invidious.privacyredirect.com",
    "https://invidious.perennialte.ch",
    "https://iv.melmac.space",
    "https://invidious.einfachzocken.eu",
    "https://invidious.adminforge.de",
    "https://inv.us.projectsegfau.lt",
    "https://invidious.darkness.services",
    "https://inv.zoomerville.com",
    "https://invidious.0011.lt",
    "https://yt.lemnoslife.com",
]


@app.function(image=image, timeout=600)
def diag() -> dict:
    import requests

    out = {}
    for inst in CANDIDATES:
        try:
            r = requests.get(f"{inst}/api/v1/videos/{VID}",
                             headers=UA, timeout=12)
            body = r.text[:120].replace("\n", " ")
            title = ""
            if r.status_code == 200:
                try:
                    d = r.json()
                    title = f"title={d.get('title')!r} len={d.get('lengthSeconds')} streams={len(d.get('formatStreams') or [])}"
                except Exception:
                    title = "unparseable"
            out[inst] = f"{r.status_code} {title} {body[:60]}"
        except Exception as exc:
            out[inst] = f"ERR {type(exc).__name__}: {str(exc)[:80]}"
    # Piped candidates
    for inst in ("https://pipedapi.kavin.rocks", "https://pipedapi.adminforge.de",
                 "https://pipedapi.leptons.xyz", "https://api.piped.yt",
                 "https://pipedapi.dmatik.dev"):
        try:
            r = requests.get(f"{inst}/streams/{VID}", headers=UA, timeout=12)
            title = ""
            if r.status_code == 200:
                try:
                    d = r.json()
                    title = f"title={d.get('title')!r} dur={d.get('duration')} vstreams={len(d.get('videoStreams') or [])}"
                except Exception:
                    title = "unparseable"
            out[inst + " (piped)"] = f"{r.status_code} {title} {r.text[:60]}"
        except Exception as exc:
            out[inst + " (piped)"] = f"ERR {type(exc).__name__}: {str(exc)[:80]}"
    # oEmbed sanity: is youtube.com itself reachable at all?
    try:
        r = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={VID}",
                    "format": "json"},
            headers=UA, timeout=12)
        out["youtube_oembed"] = f"{r.status_code} {r.text[:100]}"
    except Exception as exc:
        out["youtube_oembed"] = f"ERR {type(exc).__name__}: {str(exc)[:80]}"
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(diag.remote(), indent=1))
