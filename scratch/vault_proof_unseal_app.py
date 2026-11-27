"""Vault proof unseal (2026-11-27).

Second half of the rotation proof, kept in its own app
because it mounts the dedicated moonfactory-legal-hold
secret, which only exists after the rotation runs. Fetches
the proof object from the moonfactory-legal-holds bucket,
opens it with the vault private key, and returns only the
SHA-256 and size of the recovered bytes. The private key
and the plaintext never leave the container.
"""

import base64
import hashlib
import os

import modal

app = modal.App("moonfactory-vault-proof-unseal")
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "requests", "pynacl")

BUCKET = "moonfactory-legal-holds"


@app.function(image=image, timeout=120, secrets=[
    modal.Secret.from_name("moonfactory-legal-hold"),
])
def derive_public() -> str:
    """The base64 public key matching the stored private key.
    A public key is safe to return: it is pinned in source."""
    from nacl.public import PrivateKey

    priv = PrivateKey(base64.b64decode(
        os.environ["LEGAL_HOLD_PRIVATE_KEY"]))
    return base64.b64encode(bytes(priv.public_key)).decode()


@app.function(image=image, timeout=300, secrets=[
    modal.Secret.from_name("moonfactory-providers"),
    modal.Secret.from_name("moonfactory-legal-hold"),
])
def proof_unseal(key: str) -> dict:
    import requests
    from nacl.public import PrivateKey, SealedBox

    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    url = ("https://api.cloudflare.com/client/v4/accounts/"
           f"{account}/r2/buckets/{BUCKET}/objects/{key}")
    headers = {"Authorization":
               f"Bearer {os.environ.get('LEGAL_HOLD_R2_TOKEN', '')}"}
    r = requests.get(url, headers=headers, timeout=120)
    out = {"get_status": r.status_code}
    if r.status_code == 200:
        priv = PrivateKey(base64.b64decode(
            os.environ["LEGAL_HOLD_PRIVATE_KEY"]))
        plain = SealedBox(priv).decrypt(r.content)
        out["sha256"] = hashlib.sha256(plain).hexdigest()
        out["size"] = len(plain)
    return out
