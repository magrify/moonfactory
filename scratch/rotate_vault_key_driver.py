"""Vault key rotation driver (2026-11-27).

Closes audit run-1 needs_validation record 4: the legal-hold
vault private key leaves the shared moonfactory-providers
bundle for a dedicated secret mounted by no workload, under
a fresh keypair, gated on the vault bucket being empty.

Phases:
  check   - print the providers key NAMES and the vault
            bucket object names/count. Read-only.
  rotate  - harvest the bundle's values sealed under a
            one-time key (values exist in this process's
            memory only, never printed, logged, or written
            to disk), create the moonfactory-legal-hold
            secret holding a freshly generated private key,
            then overwrite moonfactory-providers with the
            harvested bundle minus LEGAL_HOLD_PRIVATE_KEY in
            a single call, and verify both key-name lists.
  proof   - seal a dummy payload under the public pin read
            from platform/app.py, upload it, unseal it with
            the new private key, compare SHA-256, delete the
            object, verify the 404 and the empty bucket.

Run with the modal venv python. Prints names, counts,
statuses, and hashes only - never a secret value. The one
exception is the new vault PUBLIC key, which is public by
design (it is pinned in platform/app.py) and is written to
/tmp/vault_new_public_key.txt for the source edit.
"""

import asyncio
import base64
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import modal  # noqa: E402
from nacl.public import PrivateKey, SealedBox  # noqa: E402

import vault_rotation_probe_app as probe  # noqa: E402

PROVIDERS = "moonfactory-providers"
HOLD_SECRET = "moonfactory-legal-hold"
KEY_NAME = "LEGAL_HOLD_PRIVATE_KEY"
PROOF_KEY = "proof/rotation-proof-2026-11-27.bin"
PUBKEY_OUT = "/tmp/vault_new_public_key.txt"


async def _names(secret_name: str) -> set:
    from modal.secret import _Secret

    sec = _Secret.from_name(secret_name)
    return await sec._get_keys(refresh=True)


def names(secret_name: str) -> set:
    return asyncio.run(_names(secret_name))


async def _create(secret_name: str, env_dict: dict,
                  overwrite: bool) -> None:
    from modal.secret import _Secret

    await _Secret._create_deployed(
        secret_name, env_dict, overwrite=overwrite)


def create(secret_name: str, env_dict: dict,
           overwrite: bool) -> None:
    asyncio.run(_create(secret_name, env_dict, overwrite))


def bucket() -> dict:
    with probe.app.run():
        return probe.bucket_list.remote()


def phase_check() -> None:
    ns = names(PROVIDERS)
    print(f"providers key count: {len(ns)}")
    print(f"providers contains {KEY_NAME}: {KEY_NAME in ns}")
    print("providers key names (sorted):")
    for n in sorted(ns):
        print(f"  - {n}")
    b = bucket()
    print(f"bucket list status: {b.get('list_status')}")
    print(f"bucket object count: {b.get('count')}")
    for n in b.get("names") or []:
        print(f"  object: {n}")


def phase_rotate() -> None:
    before = names(PROVIDERS)
    if KEY_NAME not in before:
        print(f"ABORT: {KEY_NAME} not in the bundle; nothing to do")
        return
    b = bucket()
    if b.get("count") != 0:
        print(f"ABORT: bucket holds {b.get('count')} object(s); "
              "rotation is gated on an empty vault")
        return
    print(f"gate passed: bundle has {len(before)} keys, "
          "bucket is empty")

    # Harvest: values sealed in-container under a one-time key.
    one_time = PrivateKey.generate()
    pub_b64 = base64.b64encode(bytes(one_time.public_key)).decode()
    with probe.app.run():
        sealed = probe.harvest.remote(pub_b64, sorted(before))
    missing = [n for n, v in sealed.items() if v is None]
    if missing:
        print(f"ABORT: values missing in container for: {missing}")
        return
    box = SealedBox(one_time)
    bundle = {n: box.decrypt(base64.b64decode(v)).decode()
              for n, v in sealed.items()}
    assert set(bundle) == before
    print(f"harvest complete: {len(bundle)} values recovered "
          "in memory")

    # Fresh vault keypair; private key goes only into the new
    # dedicated secret.
    vault_priv = PrivateKey.generate()
    vault_priv_b64 = base64.b64encode(bytes(vault_priv)).decode()
    vault_pub_b64 = base64.b64encode(
        bytes(vault_priv.public_key)).decode()
    create(HOLD_SECRET, {KEY_NAME: vault_priv_b64},
           overwrite=False)
    hold_names = names(HOLD_SECRET)
    assert hold_names == {KEY_NAME}, hold_names
    print(f"created secret {HOLD_SECRET} with exactly "
          f"{sorted(hold_names)}")
    with open(PUBKEY_OUT, "w") as fh:
        fh.write(vault_pub_b64 + "\n")
    print(f"new vault public key written to {PUBKEY_OUT}")

    # Rewrite the providers bundle minus the private key, in
    # one overwrite call.
    reduced = {k: v for k, v in bundle.items() if k != KEY_NAME}
    create(PROVIDERS, reduced, overwrite=True)
    after = names(PROVIDERS)
    expected = before - {KEY_NAME}
    assert after == expected, (
        f"name mismatch: missing={expected - after} "
        f"extra={after - expected}")
    print(f"providers rewritten: {len(before)} -> {len(after)} "
          f"keys; {KEY_NAME} gone, every other key intact")


def phase_finish() -> None:
    """Recovery path for a rotate run interrupted after the
    dedicated secret was created but before the bundle was
    rewritten: derive the new public key from the stored
    private key, re-harvest, and rewrite the bundle."""
    import vault_proof_unseal_app as unseal_app

    hold_names = names(HOLD_SECRET)
    assert hold_names == {KEY_NAME}, hold_names
    with unseal_app.app.run():
        pub_b64 = unseal_app.derive_public.remote()
    with open(PUBKEY_OUT, "w") as fh:
        fh.write(pub_b64 + "\n")
    print(f"derived new vault public key into {PUBKEY_OUT}")

    before = names(PROVIDERS)
    assert KEY_NAME in before
    b = bucket()
    assert b.get("count") == 0, b
    one_time = PrivateKey.generate()
    pub1_b64 = base64.b64encode(bytes(one_time.public_key)).decode()
    with probe.app.run():
        sealed = probe.harvest.remote(pub1_b64, sorted(before))
    missing = [n for n, v in sealed.items() if v is None]
    assert not missing, missing
    box = SealedBox(one_time)
    bundle = {n: box.decrypt(base64.b64decode(v)).decode()
              for n, v in sealed.items()}
    assert set(bundle) == before
    print(f"harvest complete: {len(bundle)} values recovered "
          "in memory")
    reduced = {k: v for k, v in bundle.items() if k != KEY_NAME}
    create(PROVIDERS, reduced, overwrite=True)
    after = names(PROVIDERS)
    expected = before - {KEY_NAME}
    assert after == expected, (
        f"name mismatch: missing={expected - after} "
        f"extra={after - expected}")
    print(f"providers rewritten: {len(before)} -> {len(after)} "
          f"keys; {KEY_NAME} gone, every other key intact")


def phase_proof() -> None:
    src = open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir,
        "platform", "app.py")).read()
    pin = re.search(
        r'LEGAL_HOLD_VAULT_PUBLIC_KEY\s*=\\\s*"([^"]+)"',
        src).group(1)
    payload = os.urandom(4096)
    want = hashlib.sha256(payload).hexdigest()
    import vault_proof_unseal_app as unseal_app

    with probe.app.run():
        sealed = probe.proof_seal.remote(
            pin, base64.b64encode(payload).decode(), PROOF_KEY)
        print(f"seal/upload: {sealed}")
        assert sealed.get("put_status") == 200
        assert sealed.get("readback_size") == sealed.get(
            "sealed_size")
    with unseal_app.app.run():
        unsealed = unseal_app.proof_unseal.remote(PROOF_KEY)
    print(f"unseal: get={unsealed.get('get_status')} "
          f"size={unsealed.get('size')} "
          f"sha_match={unsealed.get('sha256') == want}")
    assert unsealed.get("sha256") == want
    with probe.app.run():
        deleted = probe.proof_delete.remote(PROOF_KEY)
        print(f"delete: {deleted}")
        assert deleted.get("get_after_delete_status") == 404
        b = probe.bucket_list.remote()
        print(f"bucket after proof: count={b.get('count')}")
        assert b.get("count") == 0
    print("PROOF PASSED: seal -> bucket -> unseal -> sha256 "
          "match -> delete -> 404 -> bucket empty")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "check"
    {"check": phase_check, "rotate": phase_rotate,
     "finish": phase_finish, "proof": phase_proof}[phase]()
