"""Store the VidLab API key (2026-11-21).

Reads the key from stdin (one line) and merges it into the
moonfactory-providers Modal secret as VIDLAB_API_KEY with the
SDK's per-key Secret.update(), preserving every existing key.
Prints only the key NAME stored, never a value.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/store_vidlab_key.py
"""

import asyncio
import sys

import modal


def main() -> None:
    key = sys.stdin.read().strip()
    if not key:
        print("nothing to store: stdin was empty")
        return
    secret = modal.Secret.from_name("moonfactory-providers")
    try:
        secret.update({"VIDLAB_API_KEY": key})
    except TypeError:
        asyncio.run(secret.update({"VIDLAB_API_KEY": key}))
    print("stored: VIDLAB_API_KEY")


if __name__ == "__main__":
    main()
