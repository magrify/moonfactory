"""List the recent FLUX.2 Klein jobs on the Kaggle route and
the platform's current comfy heartbeat/note state."""
import json
import time

import modal

app = modal.App("moonfactory-klein-jobs")

jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)


@app.function(timeout=120)
def readback() -> dict:
    out = {"now": time.time()}
    rows = []
    for key in list(jobs.keys()):
        rec = jobs.get(key) or {}
        if rec.get("image_model") == "flux2_klein4b" \
                and rec.get("provider") == "kaggle-comfy":
            rows.append({k: rec.get(k) for k in (
                "id", "status", "image_size", "charged_cents",
                "cost_cents", "free_tier", "gpu_seconds", "bytes",
                "error", "internal_error", "worker_session",
                "created_at", "prompt")})
    rows.sort(key=lambda r: r.get("created_at") or 0)
    out["klein_kaggle_jobs"] = rows[-4:]
    out["comfy_note"] = meta.get("comfy:note")
    out["heartbeat_age_s"] = round(
        time.time() - float(meta.get("comfy:heartbeat") or 0), 1)
    cf_key = "cfneurons:" + time.strftime("%Y-%m-%d", time.gmtime())
    out["cf_ledger_today"] = meta.get(cf_key)
    for key in list(users.keys()):
        rec = users.get(key)
        if rec and rec.get("is_owner"):
            out["owner"] = {
                "balance_cents": rec.get("balance_cents"),
                "free_jobs_total": rec.get("free_jobs_total"),
            }
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(readback.remote(), indent=2, default=str))
