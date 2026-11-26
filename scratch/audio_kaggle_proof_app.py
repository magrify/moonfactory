"""Live proof for the 2026-11-24 audio move: one music bed
child and one voiceover child, queued in the platform's own
jobs Dict in the exact shape _queue_audio_child writes,
the Kaggle audio kernel woken with the platform's own push
payload, and the results read back from the platform's
records and the data Volume.

MODAL SPEND: NONE AUTHORIZED. Both stages execute in a
free Kaggle session through the platform's audio worker
route; nothing here calls a Modal generation callable. The
harness's own functions only write and read records, push
the kernel with the platform's Kaggle token (the token
never leaves the Modal function), and clean the proof
children up afterwards.

Usage:
  modal run scratch/audio_kaggle_proof_app.py --action submit
  modal run scratch/audio_kaggle_proof_app.py --action status
  modal run scratch/audio_kaggle_proof_app.py --action cleanup
"""

import hashlib
import os
import time
from pathlib import Path

import modal

app = modal.App("moonfactory-audio-kaggle-proof")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("requests")
    .add_local_file(
        str(Path(__file__).parent.parent
            / "worker-kaggle" / "audio_worker_kernel.py"),
        "/assets/audio_worker_kernel.py")
)

jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
data_volume = modal.Volume.from_name("moonfactory-data",
                                     create_if_missing=False)

KERNEL_PATH = Path("/assets/audio_worker_kernel.py")
SLUG = "magrify/moonfactory-audio-worker"
KEY_DATASET = "magrify/moonfactory-hf-key"

BED_PAYLOAD = {"caption": "gentle uplifting instrumental "
                          "background music, soft piano melody, "
                          "warm strings, light airy pads, calm "
                          "and dreamy, instrumental, no vocals",
               "seed": 4242}
VOICE_PAYLOAD = {"text": "The audio worker is speaking from "
                         "a Kaggle session now.",
                 "voice": "af_heart"}


def _key(stage, payload):
    if stage == "bed":
        return hashlib.sha256(
            f"{payload['caption']}|{payload['seed']}".encode()
        ).hexdigest()[:16]
    return hashlib.sha256(
        f"{payload['voice']}|{payload['text']}".encode()
    ).hexdigest()[:16]


def _child_id(stage, payload):
    return f"aud-{stage}-{_key(stage, payload)}"


def _kernel_rev():
    """The revision of the kernel file this proof pushes:
    the platform only hands children to a session whose
    script hashes to the same value."""
    return hashlib.sha256(KERNEL_PATH.read_bytes()).hexdigest()[:12]


def _child(stage, payload):
    return {
        "id": _child_id(stage, payload),
        "user_id": "proof",
        "worker_job_id": None,
        "provider": "kaggle-audio",
        "kind": "audio_stage",
        "status": "queued",
        "error": None,
        "bytes": None,
        "created_at": time.time(),
        "charged_cents": 0,
        "audio_stage": stage,
        "audio_key": _key(stage, payload),
        "audio_rev": _kernel_rev(),
        "audio_parent": "proof",
        **payload,
    }


@app.function(image=image,
              secrets=[modal.Secret.from_name("moonfactory-kaggle")],
              timeout=300)
def submit():
    out = {}
    for stage, payload in (("bed", BED_PAYLOAD),
                           ("voiceover", VOICE_PAYLOAD)):
        child = _child(stage, payload)
        existing = jobs.get(child["id"]) or {}
        if existing.get("status") in ("queued", "running", "done"):
            out[stage] = f"already {existing['status']}: {child['id']}"
            continue
        jobs[child["id"]] = child
        out[stage] = f"queued: {child['id']}"
    import requests

    token = os.environ.get("KAGGLE_API_TOKEN") or ""
    if not token:
        out["push"] = "NO TOKEN in moonfactory-kaggle secret"
        return out
    payload = {
        "slug": SLUG,
        "newTitle": "Moonfactory Audio Worker",
        "text": KERNEL_PATH.read_text(),
        "language": "python",
        "kernelType": "script",
        "isPrivate": True,
        "enableGpu": True,
        "enableTpu": False,
        "enableInternet": True,
        "datasetDataSources": [KEY_DATASET],
        "competitionDataSources": [],
        "kernelDataSources": [],
        "modelDataSources": [],
        "categoryIds": [],
    }
    resp = requests.post(
        "https://www.kaggle.com/api/v1/kernels/push",
        headers={"Authorization": f"Bearer {token}"},
        json=payload, timeout=60)
    out["push"] = f"kaggle answered {resp.status_code}"
    meta["audio:last_trigger"] = time.time()
    return out


@app.function(image=image, volumes={"/data": data_volume}, timeout=120)
def status():
    out = {}
    out["heartbeat_age_seconds"] = (
        round(time.time() - float(meta.get("audio:heartbeat") or 0), 1)
        if meta.get("audio:heartbeat") else None)
    files = {}
    try:
        data_volume.reload()
        base = Path("/data/files/audio_worker")
        if base.exists():
            files = {p.name: p.stat().st_size for p in base.iterdir()}
    except Exception as exc:
        files = {"error": str(exc)[:200]}
    out["audio_worker_files"] = files
    for stage, payload in (("bed", BED_PAYLOAD),
                           ("voiceover", VOICE_PAYLOAD)):
        cid = _child_id(stage, payload)
        rec = jobs.get(cid) or {}
        out[stage] = {
            "id": cid,
            "audio_rev": rec.get("audio_rev"),
            "status": rec.get("status"),
            "bytes": rec.get("bytes"),
            "error": rec.get("error"),
            "internal_error": (rec.get("internal_error") or "")[:300],
            "claimed_at": rec.get("claimed_at"),
        }
    return out


@app.function(image=image, volumes={"/data": data_volume}, timeout=120)
def cleanup():
    out = {}
    for stage, payload in (("bed", BED_PAYLOAD),
                           ("voiceover", VOICE_PAYLOAD)):
        cid = _child_id(stage, payload)
        try:
            del jobs[cid]
            out[stage] = "record deleted"
        except Exception:
            out[stage] = "no record"
        path = Path("/data/files/audio_worker") / f"{cid}.wav"
        try:
            path.unlink(missing_ok=True)
            out[stage] += ", file removed"
        except Exception as exc:
            out[stage] += f", file: {exc}"[:200]
    data_volume.commit()
    return out


@app.local_entrypoint()
def main(action: str = "status"):
    if action == "submit":
        print(submit.remote())
    elif action == "cleanup":
        print(cleanup.remote())
    else:
        print(status.remote())
