"""R2 vault probe (2026-11-24): can the app's stored Cloudflare
credential reach the dedicated moonfactory-legal-holds bucket
through the Cloudflare REST object API? Tries a bucket list, a
tiny probe-object PUT, a HEAD readback, and a DELETE, printing
HTTP status codes and presence booleans only. No credential
value is ever printed. No hold, no generation.
"""

import os

import modal

app = modal.App("moonfactory-r2-vault-probe")
image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

BUCKET = "moonfactory-legal-holds"
PROBE_KEY = "probe/probe.txt"


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    timeout=180,
)
def probe() -> dict:
    import requests

    out = {
        "has_vault_token": bool(os.environ.get("LEGAL_HOLD_R2_TOKEN")),
        "has_cf_account": bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID")),
    }
    token = os.environ.get("LEGAL_HOLD_R2_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not token or not account:
        out["error"] = "cloudflare credential not configured"
        return out
    base = ("https://api.cloudflare.com/client/v4/accounts/"
            f"{account}/r2/buckets")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(base, headers=headers, timeout=30)
        out["list_buckets_status"] = r.status_code
        if r.status_code == 200:
            names = [b.get("name") for b in
                     (r.json().get("result") or {}).get("buckets", [])]
            out["vault_bucket_visible"] = BUCKET in names
    except Exception as exc:  # noqa: BLE001
        out["list_error"] = type(exc).__name__
    obj = f"{base}/{BUCKET}/objects/{PROBE_KEY}"
    try:
        r = requests.put(obj, headers=headers, data=b"probe",
                         timeout=30)
        out["put_status"] = r.status_code
        out["put_result_size"] = None
        try:
            result = r.json().get("result") or {}
            out["put_result_size"] = result.get("size")
        except Exception:  # noqa: BLE001
            pass
        r = requests.get(obj, headers=headers, timeout=30)
        out["get_status"] = r.status_code
        out["get_size"] = len(r.content) if r.status_code == 200 else None
        r = requests.delete(obj, headers=headers, timeout=30)
        out["delete_status"] = r.status_code
        r = requests.get(obj, headers=headers, timeout=30)
        out["get_after_delete_status"] = r.status_code
    except Exception as exc:  # noqa: BLE001
        out["object_error"] = type(exc).__name__
    return out


@app.local_entrypoint()
def main():
    import json

    print(json.dumps(probe.remote(), indent=2))
