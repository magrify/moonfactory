"""Read one job record's diagnostic fields from the jobs Dict."""
import json
import modal

app = modal.App("moonfactory-jobread")
image = modal.Image.debian_slim(python_version="3.12")
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)


@app.function(image=image, timeout=60)
def read(job_id: str) -> dict:
    j = jobs.get(job_id) or {}
    return {k: j.get(k) for k in (
        "status", "stage", "pick_mode", "pick_note", "transcribe_error",
        "internal_error", "error", "charged_cents", "clip_ids",
        "duration_seconds", "source_title")}


@app.local_entrypoint()
def main(job_id: str):
    print(json.dumps(read.remote(job_id), indent=1, default=str))
