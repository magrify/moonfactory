"""Read the full record of the Klein proof job."""
import json

import modal

app = modal.App("moonfactory-klein-record")

jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)


@app.function(timeout=120)
def readback() -> dict:
    rec = jobs.get("9b4781aa2e04459b") or {}
    out = {"record": rec}
    out["comfy_note"] = meta.get("comfy:note")
    return out


@app.local_entrypoint()
def main():
    print(json.dumps(readback.remote(), indent=2, default=str))
