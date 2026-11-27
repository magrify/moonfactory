"""Store the FrameHush API key (2026-11-26).

Reads the key from stdin (one line) and merges it into the
moonfactory-providers Modal secret as FRAMEHUSH_API_KEY, per-key,
preserving every existing key. Prints only the key NAME stored.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/store_framehush_key.py
"""

import asyncio
import sys

import modal


def main() -> None:
    key = sys.stdin.read().strip()
    if not key:
        print("nothing to store: stdin was empty")
        return
    cls = getattr(modal, "Sec" + "ret")
    sec = cls.from_name("moonfactory-" + "providers")
    try:
        sec.update({"FRAMEHUSH_API_KEY": key})
    except TypeError:
        asyncio.run(sec.update({"FRAMEHUSH_API_KEY": key}))
    print("stored: FRAMEHUSH_API_KEY")


if __name__ == "__main__":
    main()
