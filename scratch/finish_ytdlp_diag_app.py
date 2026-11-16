"""Diagnostic 2: can yt-dlp extract from Modal egress with any
player_client, and do any public Cobalt instances answer?
extract_info only (no download). CPU-only."""

import json

import modal

app = modal.App("moonfactory-ytdlp-diag")
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "requests", "yt-dlp")

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
UA = {"User-Agent": "Mozilla/5.0 (Moonfactory clip fetch)"}


@app.function(image=image, timeout=900)
def diag() -> dict:
    import requests
    import yt_dlp

    out = {}
    clients = [
        ["android_vr"], ["tv"], ["web_safari"], ["ios"], ["mweb"],
        ["android_vr", "tv", "web_safari"], None,
    ]
    for client in clients:
        key = f"ytdlp_{'+'.join(client) if client else 'default'}"
        opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
        if client:
            opts["extractor_args"] = {"youtube": {"player_client": client}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(URL, download=False)
            fmts = info.get("formats") or []
            out[key] = (f"OK title={info.get('title')!r} "
                        f"dur={info.get('duration')} formats={len(fmts)}")
        except Exception as exc:
            out[key] = f"ERR {str(exc)[:160]}"
    for inst in ("https://api.cobalt.tools", "https://cobalt.synzr.top",
                 "https://api.cobalt.kwiatekmiki.com"):
        try:
            r = requests.post(
                inst + "/", headers={**UA, "Accept": "application/json",
                                     "Content-Type": "application/json"},
                json={"url": URL}, timeout=15)
            out[f"cobalt {inst}"] = f"{r.status_code} {r.text[:140]}"
        except Exception as exc:
            out[f"cobalt {inst}"] = f"ERR {type(exc).__name__}: {str(exc)[:100]}"
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(diag.remote(), indent=1))
