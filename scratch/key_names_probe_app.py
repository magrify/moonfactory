"""Key-names probe (2026-11-20): which provider key NAMES are
present in the moonfactory-providers secret. Names and
presence booleans only; values are never read out. Used to
verify the draft picker's configured flags and the restored
Morph record. No generation, no external calls.
"""

import os

import modal

app = modal.App("moonfactory-key-names-probe")
image = modal.Image.debian_slim(python_version="3.12")

WANTED = (
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "VENICE_API_KEY",
    "REFUSELESS_API_KEY",
    "MORPH_API_KEY",
    "SILK_API_KEY",
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=120,
)
def probe() -> dict:
    return {name: bool(os.environ.get(name)) for name in WANTED}


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(probe.remote(), indent=2))
