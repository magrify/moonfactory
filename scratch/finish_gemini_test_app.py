"""Finish-run Gemini diag: one minimal generateContent call with
the stored GEMINI_API_KEY, reporting HTTP status and the error
body only (never the key). CPU-only."""

import json
import os
import urllib.parse
import urllib.request

import modal

app = modal.App("moonfactory-gemini-test")
image = modal.Image.debian_slim(python_version="3.12")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=180,
)
def test() -> dict:
    key = os.environ.get("GEMINI_API_KEY", "")
    out = {"key_present": bool(key)}
    for model in ("gemini-3.8-flash", "gemini-3.5-flash-lite"):
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key=" + urllib.parse.quote(key))
        body = {"contents": [{"parts": [{"text": "Reply with the word ok."}]}]}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            out[model] = f"OK: {text.strip()[:40]!r}"
        except urllib.error.HTTPError as exc:
            out[model] = f"HTTP {exc.code}: {exc.read().decode()[:300]}"
        except Exception as exc:
            out[model] = f"ERR {type(exc).__name__}: {str(exc)[:200]}"
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(test.remote(), indent=1))
