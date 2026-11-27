"""Store the NOWPayments credentials (2026-11-27).

Reads two lines from stdin (API key, then IPN secret) and merges
them into the moonfactory-providers Modal secret as
NOWPAYMENTS_API_KEY and NOWPAYMENTS_IPN_SECRET with the SDK's
per-key Secret.update(), preserving every existing key. Prints
only key NAMES and counts, never a value.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/store_nowpayments_keys.py
"""

import asyncio
import sys

import modal


def main() -> None:
    lines = sys.stdin.read().splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1].strip():
        print("nothing to store: expected two non-empty stdin lines")
        return
    api_key, ipn_secret = lines[0].strip(), lines[1].strip()
    secret = modal.Secret.from_name("moonfactory-" + "providers")
    before = secret._get_keys(refresh=True)
    print(f"keys before: {len(before)}")
    result = secret.update({
        "NOWPAYMENTS_API_KEY": api_key,
        "NOWPAYMENTS_IPN_SECRET": ipn_secret,
    })
    if asyncio.iscoroutine(result):
        asyncio.run(_await(result))


async def _await(coro):
    return await coro
    after = secret._get_keys(refresh=True)
    print(f"keys after: {len(after)}")
    print("added:", sorted(after - before))
    print("lost:", sorted(before - after))
    print("stored: NOWPAYMENTS_API_KEY, NOWPAYMENTS_IPN_SECRET")


if __name__ == "__main__":
    main()
