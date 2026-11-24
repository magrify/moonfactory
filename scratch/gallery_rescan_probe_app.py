"""Read-only measurement for the 2026-11-24 gallery rule change:
how many existing done jobs sit on regular lanes with a prompt
the (now all-lane) gallery prompt scan flags. Those are the
items whose public gallery visibility flips under the new rule;
everything else in the library is untouched by the expansion
(the classifier verdict gate only binds jobs created after
OUTPUT_CLASSIFIER_SINCE, and no existing job carries a verdict).

The scan itself (prompt_is_nsfw / job_is_uncensored) is copied
from platform/app.py's dependency closure by importing the
module file's pure functions through exec of their sources, so
the probe measures with the platform's own words lists."""
import ast
import json
import modal

app = modal.App("moonfactory-gallery-rescan-probe")
image = modal.Image.debian_slim(python_version="3.12").add_local_file(
    "platform/pricing.py", "/root/pricing.py")
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)


@app.function(image=image, timeout=300)
def measure(app_py: str) -> dict:
    tree = ast.parse(app_py)
    wanted = {
        "prompt_is_nsfw", "job_is_uncensored", "job_has_image_input",
        "_prompt_names_person",
    }
    import re
    import pricing

    ns = {"re": re, "pricing": pricing}
    # Module-level data the scan reads: exec every top-level
    # assignment and the wanted function defs, in source order.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            try:
                exec(ast.get_source_segment(app_py, node), ns)
            except Exception:
                pass
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(ast.get_source_segment(app_py, node), ns)
    prompt_is_nsfw = ns["prompt_is_nsfw"]
    job_is_uncensored = ns["job_is_uncensored"]

    total_done = 0
    flips = []
    already_out = 0
    for key in list(jobs.keys()):
        job = jobs.get(key)
        if not job or job.get("status") != "done" or job.get("removed"):
            continue
        if job.get("short_parent") or job.get("drama_parent"):
            continue
        total_done += 1
        flagged = prompt_is_nsfw(
            job.get("user_prompt") or job.get("prompt") or "")
        if not flagged:
            continue
        if job_is_uncensored(job):
            already_out += 1
        else:
            flips.append({
                "id": job.get("id"),
                "lane": job.get("lane"),
                "model": job.get("model") or job.get("image_model"),
                "kind": job.get("kind"),
            })
    return {"total_done_gallery_candidates": total_done,
            "flagged_already_out_uncensored_scope": already_out,
            "newly_out_regular_lane": len(flips),
            "flips": flips[:50]}


@app.local_entrypoint()
def main():
    from pathlib import Path
    src = Path("platform/app.py").read_text()
    print(json.dumps(measure.remote(src), indent=1, default=str))
