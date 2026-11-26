"""Store the Stock image source keys (2026-11-20).

Reads up to two lines from stdin:
  line 1: the Pixabay API key        (blank line skips Pixabay)
  line 2: the Unsplash Access Key    (blank line skips Unsplash)

Merges each into the moonfactory-providers Modal secret with the
SDK's per-key Secret.update(), so every existing key is preserved.
Prints only which key NAMES were stored, never a value.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/store_stock_keys.py
"""

import asyncio
import sys

import modal


def main() -> None:
    lines = sys.stdin.read().splitlines()
    pix = (lines[0].strip() if len(lines) > 0 else "")
    uns = (lines[1].strip() if len(lines) > 1 else "")
    updates = {}
    if pix:
        updates["PIXABAY_API_KEY"] = pix
    if uns:
        updates["UNSPLASH_ACCESS_KEY"] = uns
    if not updates:
        print("nothing to store: both lines blank")
        return
    secret = modal.Secret.from_name("moonfactory-providers")
    try:
        secret.update(updates)
    except TypeError:
        asyncio.run(secret.update(updates))
    print("stored:", ", ".join(sorted(updates)))


if __name__ == "__main__":
    main()
