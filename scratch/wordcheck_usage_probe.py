"""Read-only probe (2026-11-26): how much word check /
transcribe work actually ran in the last 30 days, to size
the Modal saving from moving those stages to the Kaggle
audio worker. Counts job records in the moonfactory-jobs
Dict; writes nothing.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/wordcheck_usage_probe.py
"""

import time

import modal


def main() -> None:
    jobs = modal.Dict.from_name("moonfactory-jobs")
    now = time.time()
    cutoff = now - 30 * 86400
    counts = {"jobs_30d": 0, "word_checked": 0, "captioned": 0,
              "spoken": 0}
    statuses = {}
    for key in list(jobs.keys()):
        try:
            job = jobs.get(key)
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        created = float(job.get("created_at") or 0)
        if created < cutoff:
            continue
        if job.get("kind") in ("audio_stage", "comfy_stage",
                               "template_stage"):
            continue
        counts["jobs_30d"] += 1
        if job.get("speech_script"):
            counts["spoken"] += 1
        wc = job.get("word_check_status")
        if wc:
            counts["word_checked"] += 1
            statuses[wc] = statuses.get(wc, 0) + 1
        if job.get("captions_status"):
            counts["captioned"] += 1
    print(counts)
    print("word_check_status values:", statuses)


if __name__ == "__main__":
    main()
