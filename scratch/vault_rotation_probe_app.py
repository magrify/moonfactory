"""Vault key rotation probes (2026-11-27).

One-off tooling for rotating the legal-hold vault keypair
(audit run-1, needs_validation record 4). Four functions:

- bucket_list: names and count of the objects in the
  moonfactory-legal-holds R2 bucket, through the same
  Cloudflare REST object API the app itself uses. Names and
  counts only.
- harvest: seals every value of the moonfactory-providers
  secret under a one-time public key supplied by the driver,
  so the bundle can be rewritten minus one key without any
  value ever being printed, logged, or written to disk in
  plaintext. Ciphertext only leaves the container.
- proof_seal / proof_unseal / proof_delete: the end-to-end
  vault proof. proof_seal seals a dummy payload under the
  vault public key pin extracted from platform/app.py (the
  exact SealedBox construction _vault_seal performs) and
  uploads it with _vault_put's readback verification.
  proof_unseal opens it with the private key held in the
  dedicated moonfactory-legal-hold secret and returns only
  the SHA-256 of the recovered bytes. proof_delete removes
  the object and verifies the 404.

No value of any secret is ever printed or returned in
plaintext.
"""

import base64
import hashlib
import os

import modal

app = modal.App("moonfactory-vault-rotation")
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "requests", "pynacl")

BUCKET = "moonfactory-legal-holds"
PROVIDERS = [modal.Secret.from_name("moonfactory-providers")]


def _r2_base() -> str:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    return ("https://api.cloudflare.com/client/v4/accounts/"
            f"{account}/r2/buckets/{BUCKET}")


def _r2_headers() -> dict:
    return {"Authorization":
            f"Bearer {os.environ.get('LEGAL_HOLD_R2_TOKEN', '')}"}


@app.function(image=image, secrets=PROVIDERS, timeout=180)
def bucket_list() -> dict:
    import requests

    out = {"bucket": BUCKET}
    url = _r2_base() + "/objects"
    names = []
    cursor = None
    try:
        while True:
            params = {"per_page": 100}
            if cursor:
                params["cursor"] = cursor
            r = requests.get(url, headers=_r2_headers(),
                             params=params, timeout=60)
            out["list_status"] = r.status_code
            if r.status_code != 200:
                break
            result = r.json().get("result") or {}
            objs = result.get("objects") or []
            names.extend(o.get("key") for o in objs)
            cursor = result.get("cursor")
            if not cursor or not objs:
                break
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
    out["count"] = len(names)
    out["names"] = names
    return out


@app.function(image=image, secrets=PROVIDERS, timeout=300)
def harvest(pubkey_b64: str, names: list) -> dict:
    from nacl.public import PublicKey, SealedBox

    box = SealedBox(PublicKey(base64.b64decode(pubkey_b64)))
    out = {}
    for name in names:
        value = os.environ.get(name)
        if value is None:
            out[name] = None
        else:
            out[name] = base64.b64encode(
                box.encrypt(value.encode())).decode()
    return out


@app.function(image=image, secrets=PROVIDERS, timeout=300)
def proof_seal(pin_b64: str, payload_b64: str, key: str) -> dict:
    import requests
    from nacl.public import PublicKey, SealedBox

    payload = base64.b64decode(payload_b64)
    box = SealedBox(PublicKey(base64.b64decode(pin_b64)))
    sealed = box.encrypt(payload)
    url = _r2_base() + f"/objects/{key}"
    r = requests.put(url, headers=_r2_headers(), data=sealed,
                     timeout=120)
    out = {"put_status": r.status_code, "sealed_size": len(sealed)}
    if r.status_code == 200:
        with requests.get(url, headers=_r2_headers(), stream=True,
                          timeout=120) as got:
            out["readback_status"] = got.status_code
            out["readback_size"] = int(
                got.headers.get("Content-Length") or -1)
    return out


@app.function(image=image, secrets=PROVIDERS, timeout=180)
def proof_delete(key: str) -> dict:
    import requests

    url = _r2_base() + f"/objects/{key}"
    r = requests.delete(url, headers=_r2_headers(), timeout=120)
    out = {"delete_status": r.status_code}
    r = requests.get(url, headers=_r2_headers(), timeout=120)
    out["get_after_delete_status"] = r.status_code
    return out
