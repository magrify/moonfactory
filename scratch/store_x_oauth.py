"""Store the X OAuth 2.0 client credentials (2026-11-25).

Reads two lines from stdin (client ID, then client secret) and
merges them into the moonfactory-providers Modal secret as
X_CLIENT_ID / X_CLIENT_SECRET with the SDK's per-key
Secret.update(), preserving every existing key.
Prints only the key NAMES stored, never a value.
"""

import asyncio
import sys

import modal


def main() -> None:
    lines = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if len(lines) != 2:
        print("nothing stored: expected exactly two lines on stdin")
        return
    client_id, client_secret = lines
    secret = modal.Secret.from_name("moonfactory-providers")
    updates = {
        "X_CLIENT_ID": client_id,
        "X_CLIENT_SECRET": client_secret,
    }
    try:
        secret.update(updates)
    except TypeError:
        asyncio.run(secret.update(updates))
    print("stored: X_CLIENT_ID, X_CLIENT_SECRET")


if __name__ == "__main__":
    main()
