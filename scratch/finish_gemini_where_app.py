"""Which Modal secret holds GEMINI_API_KEY? Presence only, never
the value. One function per secret."""
import modal

app = modal.App("moonfactory-gemini-where")
image = modal.Image.debian_slim(python_version="3.12")


@app.function(image=image, secrets=[modal.Secret.from_name("moonfactory-secrets")], timeout=60)
def in_secrets() -> dict:
    import os
    return {"moonfactory-secrets": bool(os.environ.get("GEMINI_API_KEY"))}


@app.function(image=image, secrets=[modal.Secret.from_name("moonfactory-integrations")], timeout=60)
def in_integrations() -> dict:
    import os
    return {"moonfactory-integrations": bool(os.environ.get("GEMINI_API_KEY"))}


@app.function(image=image, secrets=[modal.Secret.from_name("moonfactory-providers")], timeout=60)
def in_providers() -> dict:
    import os
    return {"moonfactory-providers": bool(os.environ.get("GEMINI_API_KEY"))}


@app.local_entrypoint()
def main():
    import json
    out = {}
    out.update(in_secrets.remote())
    out.update(in_integrations.remote())
    out.update(in_providers.remote())
    print(json.dumps(out))
