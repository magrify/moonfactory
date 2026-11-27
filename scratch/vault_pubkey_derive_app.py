"""Derive the vault public key (2026-11-27).

The rotation driver was killed after it created the
moonfactory-legal-hold secret but before it recorded the
new public key. A SealedBox public key is derivable from
its private key, so this probe mounts the dedicated secret,
derives the public half in-container, and returns only
that. A public key is safe to print: it is the value pinned
in platform/app.py. The private key never leaves the
container.
"""

import base64
import os

import modal

app = modal.App("moonfactory-vault-pubkey-derive")
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "pynacl")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-legal-hold")],
    timeout=120,
)
def derive_public_key() -> dict:
    from nacl.public import PrivateKey

    raw = os.environ.get("LEGAL_HOLD_PRIVATE_KEY", "")
    if not raw:
        return {"present": False}
    priv = PrivateKey(base64.b64decode(raw))
    return {
        "present": True,
        "public_key_b64": base64.b64encode(
            bytes(priv.public_key)).decode(),
    }


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(derive_public_key.remote()))
