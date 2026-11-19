"""Moonfactory platform: the public web app and API.

One FastAPI service on Modal that serves the landing page and studio,
handles accounts, enforces the business rules (pricing.py + config
below), dispatches generation jobs to the private worker endpoint
(Wan 2.2 lanes) and to the per-second model lanes (Wan 3.0,
Wan 3.0 Prime, Seedance 2.5, Veo 3.1, Veo 3.1 Fast, and Kling 3.0,
via the provider routes configured below), and
receives Stripe webhooks. The worker API key lives only in this app's
Modal Secret and is never sent to a browser.

The worker returns silent video. Every delivered file gets a music
bed muxed on here, on CPU: the ACE-Step house bed by default, or a
bed generated from the job's own music prompt for Creator-plan jobs
(custom audio is paid-only, gated at job creation). Free-tier files
also get a Moonfactory watermark burned in. GPU work stays inside
the worker apps; everything here is CPU.

Finished clips list in a public gallery by default (handles only,
never emails; Creator users can opt out; reports and an admin
removal path keep it moderated).

The studio's camera layer comes from the preset library
(workflows/camera-presets/presets-index.json, shipped in the image
as data): users chain up to three camera moves and pick from a
film-look vocabulary; at job creation the platform composes the
final prompt from the preset recipes and the user's prompt, and
the job record keeps both, plus the preset names the gallery and
studio display.

State: Modal Dicts (users, jobs, meta, outbox). Finished files are
cached on the moonfactory-data Volume.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets as pysecrets
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import modal

sys.path.insert(0, "/platform")
import pricing  # noqa: E402
import clipping  # noqa: E402
import fun_camera  # noqa: E402
import llm_providers  # noqa: E402
import shorts  # noqa: E402

app = modal.App("moonfactory")

worker_models = modal.Volume.from_name("sinclip-models", create_if_missing=False)
data_volume = modal.Volume.from_name("moonfactory-data", create_if_missing=True)

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)
meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)
outbox = modal.Dict.from_name("moonfactory-outbox", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "fonts-dejavu-core")
    .pip_install("fastapi[standard]", "requests", "stripe")
    .add_local_file(str(Path(__file__).parent / "pricing.py"), "/platform/pricing.py")
    .add_local_file(
        str(Path(__file__).parent.parent
            / "worker-kaggle" / "hf_worker_kernel.py"),
        "/platform/hf_worker_kernel.py")
    .add_local_file(str(Path(__file__).parent / "clipping.py"), "/platform/clipping.py")
    .add_local_file(str(Path(__file__).parent / "fun_camera.py"), "/platform/fun_camera.py")
    .add_local_file(str(Path(__file__).parent / "llm_providers.py"), "/platform/llm_providers.py")
    .add_local_file(str(Path(__file__).parent / "shorts.py"), "/platform/shorts.py")
    .add_local_file(
        str(
            Path(__file__).parent.parent
            / "workflows" / "camera-presets" / "presets-index.json"
        ),
        "/platform/presets-index.json",
    )
    .add_local_file(
        str(Path(__file__).parent / "icons" / "icon-192.png"),
        "/platform/icons/icon-192.png")
    .add_local_file(
        str(Path(__file__).parent / "icons" / "icon-512.png"),
        "/platform/icons/icon-512.png")
    .add_local_file(
        str(Path(__file__).parent / "icons" / "icon-maskable-512.png"),
        "/platform/icons/icon-maskable-512.png")
)


# ---------------------------------------------------------------------------
# Clipping (CLIPPING-DESIGN.md): a CPU-only function in this app, never
# the GPU apps. 4 cores, 8 GiB, short scaledown, ffmpeg and sherpa-onnx
# in the image, the Parakeet model cached on the data Volume at
# /data/clip-models (downloaded once by clipping.ensure_model). The
# pipeline itself is platform/clipping.py, ported from PeakCut (MIT,
# pinned commit b3e403e; see PEAKCUT-LICENSE.txt).
# ---------------------------------------------------------------------------

clip_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg", "fonts-dejavu-core")
    .pip_install("sherpa-onnx", "yt-dlp", "numpy", "requests")
    .add_local_file(str(Path(__file__).parent / "pricing.py"), "/platform/pricing.py")
    .add_local_file(str(Path(__file__).parent / "clipping.py"), "/platform/clipping.py")
    .add_local_file(str(Path(__file__).parent / "fun_camera.py"), "/platform/fun_camera.py")
    .add_local_file(str(Path(__file__).parent / "llm_providers.py"), "/platform/llm_providers.py")
    .add_local_file(str(Path(__file__).parent / "shorts.py"), "/platform/shorts.py")
    .add_local_dir(str(Path(__file__).parent / "clip_fonts"), "/platform/clip_fonts")
)

CLIP_STAGES = (
    "Getting the video",
    "Listening and transcribing",
    "Picking the moments",
    "Cutting your clips",
)


def _clip_usage(user: dict) -> dict:
    period = _period()
    u = user.get("usage") or {}
    if u.get("period") != period:
        u = {"period": period, "free_jobs": 0, "included_seconds": 0.0,
             "included_cost_cents": 0.0}
        user["usage"] = u
    return u


def _clip_release_hold(job: dict):
    """Rule 8 for clip jobs: a failed job restores balance or budget
    in full. Mirrors release_hold inside api() (the worker function
    runs outside that closure)."""
    if job.get("settled") or job.get("hold_released"):
        return
    hold = float(job.get("hold_amount") or 0)
    if hold > 0:
        owner = users.get(f"id:{job['user_id']}")
        if owner:
            if job.get("hold_kind") == "balance":
                owner["balance_cents"] = int(owner.get("balance_cents", 0)) + int(hold)
            elif job.get("hold_kind") == "included":
                u = _clip_usage(owner)
                u["included_cost_cents"] = max(
                    0.0, float(u.get("included_cost_cents") or 0.0) - hold)
                owner["usage"] = u
            users[f"id:{owner['id']}"] = owner
            users[f"email:{owner['email']}"] = owner
    job["hold_released"] = True
    jobs[job["id"]] = job


def _clip_settle(job: dict):
    """Settle a finished clip job at the exact quoted charge (the
    source duration is probed before the hold, so there is no
    estimate drift): included budget at the 0.9c per source minute
    floor rate, or balance at 5c per source minute. The hold was the
    exact charge, so settling only records the Rule 7 numbers and
    the Amendment 2 settled cost (worst-case 0.3c per minute)."""
    if job.get("settled") or job.get("hold_released"):
        return
    minutes = int(job.get("source_minutes") or 0)
    owner = users.get(f"id:{job['user_id']}")
    if owner:
        owner["cost_settled_cents"] = (
            float(owner.get("cost_settled_cents") or 0.0)
            + minutes * pricing.CLIP_COST_CENTS_PER_MINUTE
        )
        users[f"id:{owner['id']}"] = owner
        users[f"email:{owner['email']}"] = owner
    job["charged_cents"] = (
        int(job.get("hold_amount") or 0) if job.get("hold_kind") == "balance"
        else 0
    )
    job["budget_cost_cents"] = (
        round(float(job.get("hold_amount") or 0), 2)
        if job.get("hold_kind") == "included" else 0.0
    )
    job["raw_cost_cents"] = round(
        minutes * pricing.CLIP_COST_CENTS_PER_MINUTE, 2)
    job["settled"] = True
    jobs[job["id"]] = job


@app.function(
    image=clip_image,
    volumes={"/data": data_volume},
    cpu=2.0,
    memory=2048,
    timeout=300,
    scaledown_window=60,
)
def clip_probe(url: str) -> dict:
    """Link metadata (title, channel, duration) through the fetch
    allowlist guard, for the quote step."""
    return clipping.fetch_info(url)


@app.function(
    image=clip_image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    volumes={"/data": data_volume},
    cpu=4.0,
    memory=8192,
    timeout=3600,
    scaledown_window=60,
)
def process_clip_job(job_id: str):
    """Run one clip job end to end on CPU: fetch, transcribe, pick,
    render. Clips land in the library as first-class jobs (lane
    "clip") with their files already in place on the data Volume,
    so the existing file, gallery and delete routes serve them."""
    import secrets as _secrets

    job = jobs.get(job_id)
    if not job:
        return
    # A warm container's Volume view can predate the upload that
    # created this job; reload before touching any file.
    try:
        data_volume.reload()
    except Exception:
        pass
    files_dir = Path("/data/files")
    files_dir.mkdir(parents=True, exist_ok=True)
    work = Path("/data/clip-work") / job_id
    work.mkdir(parents=True, exist_ok=True)

    def set_stage(stage, status="running"):
        job["stage"] = stage
        job["status"] = status
        jobs[job_id] = job

    try:
        set_stage(CLIP_STAGES[0])
        if job.get("upload_path"):
            src = Path(job["upload_path"])
            if not src.exists():
                raise RuntimeError("the uploaded file is missing")
            title = job.get("source_title") or src.name
            uploader = ""
        else:
            info = clipping.fetch_info(job["source_url"])
            title = info.get("title") or ""
            uploader = info.get("uploader") or ""
            src = clipping.download_video(job["source_url"], work)
        info = clipping.probe(src)
        duration = float(info["duration"])
        if duration > clipping.MAX_SOURCE_SECONDS:
            raise RuntimeError("this video is longer than the 3 hour clipping limit")
        job["duration_seconds"] = round(duration, 2)
        job["source_title"] = title
        jobs[job_id] = job

        set_stage(CLIP_STAGES[1])
        wav = work / "audio.wav"
        clipping.extract_audio(src, wav)
        db = clipping.loudness_per_second(wav)
        words = []
        try:
            words = clipping.transcribe(wav, Path("/data/clip-models"))
        except Exception as exc:  # transcription never blocks clipping
            job["transcribe_error"] = str(exc)[:200]
            jobs[job_id] = job

        set_stage(CLIP_STAGES[2])
        picks, pick_info = clipping.find_clips(
            words, db, duration, title=title, uploader=uploader,
            max_clips=int(job.get("clip_count") or 3),
            length=job.get("length") or "medium",
            api_key=os.environ.get("GEMINI_API_KEY", ""),
        )
        job["pick_mode"] = pick_info.get("mode")
        job["pick_note"] = pick_info.get("note") or ""
        jobs[job_id] = job
        if not picks:
            raise RuntimeError("no clips could be picked from this video")

        set_stage(CLIP_STAGES[3])
        clip_ids = []
        for pick in picks:
            child_id = _secrets.token_hex(8)
            spec = dict(pick)
            spec["style"] = job.get("caption_style") or "bounce"
            spec["layout"] = job.get("layout") or "blur"
            clipping.render_clip(
                src, files_dir, child_id, spec, words,
                int(info["width"]), int(info["height"]),
                Path("/platform/clip_fonts"),
            )
            child = {
                "id": child_id,
                "user_id": job["user_id"],
                "worker_job_id": None,
                "provider": "clip",
                "kind": "clip_output",
                "clip_parent": job_id,
                "lane": "clip",
                "model": "clip",
                "prompt": pick.get("hook") or pick.get("title") or "Clip",
                "user_prompt": pick.get("hook") or pick.get("title") or "Clip",
                "camera_presets": [],
                "camera_preset_names": [],
                "camera_look": {},
                "status": "done",
                "error": None,
                "created_at": _now(),
                "charged_cents": 0,
                "hook": pick.get("hook") or "",
                "title": pick.get("title") or "",
                "score": pick.get("score"),
                "duration_seconds": round(pick["end"] - pick["start"], 2),
                "source_start": pick["start"],
                "source_end": pick["end"],
                "has_thumb": (files_dir / f"{child_id}.jpg").exists(),
                "bytes": (files_dir / f"{child_id}.mp4").stat().st_size,
                "plan_at_job": job.get("plan_at_job"),
                "free_tier": False,
                "music_prompt": None,
            }
            jobs[child_id] = child
            clip_ids.append(child_id)
        job["clip_ids"] = clip_ids
        job["stage"] = "Done"
        job["status"] = "done"
        jobs[job_id] = job
        _clip_settle(job)
        data_volume.commit()
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = "clipping failed, nothing was charged"
        if clipping.LINK_DEAD_MESSAGE.split(". ")[0] in str(exc):
            # A dead link is the one failure the user can act on:
            # surface the working alternative, not the generic line.
            job["error"] = (clipping.LINK_DEAD_MESSAGE
                            + ". Nothing was charged")
        job["internal_error"] = str(exc)[:300]
        jobs[job_id] = job
        _clip_release_hold(job)
        data_volume.commit()


def _short_word_check(job: dict, out_path: Path):
    """The word check stage for a finished short: the same stage
    the generation finishing chain runs (see _word_check_for_job
    inside api()), duplicated at top level the way the clip
    helpers mirror their api() counterparts. The delivered audio
    is transcribed and aligned against the spoken script, and a
    same-voice repair is spliced in when the audio app returns
    one. The short was voiced by the platform's own Kokoro
    stage, so the fix pass is always eligible. Outcomes land on
    the job record in the same fields; a stage failure flags
    "unavailable" and never blocks the delivery."""
    script = (job.get("speech_script") or "").strip()
    if not script or job.get("word_check_status"):
        return
    files_dir = Path("/data/files")
    audio_path = files_dir / f"{job['id']}.wordcheck.wav"
    fixed_path = files_dir / f"{job['id']}.wordfix.wav"
    swapped_path = files_dir / f"{job['id']}.wordfix.mp4"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(out_path), "-vn",
             "-acodec", "pcm_s16le", str(audio_path)],
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not audio_path.exists():
            raise RuntimeError("final audio extraction failed")
        voice = ""
        if job.get("voiceover_status") == "ready":
            voice = job.get("speech_voice") or DEFAULT_SPEECH_VOICE
        word_check = modal.Function.from_name(
            "sinclip-audio", "word_check")
        result = word_check.remote(
            audio_path.read_bytes(), script, voice)
        fixed = result.pop("audio", None)
        if fixed:
            fixed_path.write_bytes(fixed)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(out_path),
                 "-i", str(fixed_path),
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                 "-shortest", str(swapped_path)],
                capture_output=True, text=True, timeout=1800)
            if proc.returncode != 0 or not swapped_path.exists():
                raise RuntimeError("fixed audio swap failed")
            swapped_path.replace(out_path)
        job["word_check_status"] = result.get("status") \
            or "unavailable"
        job["word_check_listen_at"] = \
            result.get("listen_at") or []
        job["word_check_wrong"] = result.get("wrong") or []
        jobs[job["id"]] = job
    except Exception:
        job["word_check_status"] = "unavailable"
        jobs[job["id"]] = job
    finally:
        for path in (audio_path, fixed_path, swapped_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


@app.function(
    image=clip_image,
    secrets=[modal.Secret.from_name("moonfactory-providers")],
    volumes={"/data": data_volume, "/worker-models": worker_models},
    cpu=4.0,
    memory=8192,
    timeout=3600,
    scaledown_window=60,
)
def process_short_job(job_id: str):
    """Run one faceless short end to end on CPU: one stock clip
    per script line, Kokoro narration per line through the
    platform's own audio stage, captions timed from the measured
    narration lengths, the house music bed underneath, and one
    ffmpeg assembly. Shorts land in the library as first-class
    jobs (lane "shorts") with the finished file already in
    place, the way clip jobs do, so the file, gallery, poster
    and delete routes serve them unchanged. The lane is free:
    there is no hold, and a failure charges nothing."""
    job = jobs.get(job_id)
    if not job:
        return
    # A warm container's Volume view can predate the job's
    # creation; reload before touching any file.
    try:
        data_volume.reload()
    except Exception:
        pass
    files_dir = Path("/data/files")
    files_dir.mkdir(parents=True, exist_ok=True)
    work = Path("/data/shorts-work") / job_id
    work.mkdir(parents=True, exist_ok=True)

    def set_stage(stage, status="running"):
        job["stage"] = stage
        job["status"] = status
        jobs[job_id] = job

    try:
        lines = list(job.get("short_lines") or [])
        if not lines:
            raise RuntimeError("the job carries no script lines")
        voice = job.get("speech_voice") or DEFAULT_SPEECH_VOICE
        stock_key = shorts._env_value("PEXELS_API_KEY")

        set_stage(SHORTS_STAGES[0])
        scenes = []
        for i, line in enumerate(lines):
            terms = shorts.search_terms_for_line(
                line, job.get("short_topic") or "")
            scenes.append(shorts.find_stock_clip(
                terms, work / "stock", i, stock_key))
        job["short_scenes"] = [
            {key: scene.get(key) for key in (
                "provider", "terms", "page_url", "credit",
                "license")}
            for scene in scenes
        ]
        jobs[job_id] = job

        set_stage(SHORTS_STAGES[1])
        generate_voiceover = modal.Function.from_name(
            "sinclip-audio", "generate_voiceover")
        line_wavs = []
        line_durations = []
        for i, line in enumerate(lines):
            data = generate_voiceover.remote(line, voice)
            wav = work / f"line_{i}.wav"
            wav.write_bytes(data)
            duration = shorts.probe(wav)["duration"]
            if duration <= 0:
                raise RuntimeError("the narration came back empty")
            line_wavs.append(wav)
            line_durations.append(duration)
        job["voiceover_status"] = "ready"
        jobs[job_id] = job
        narration = shorts.build_narration_track(
            line_wavs, line_durations, work)
        scene_durations = []
        timings = []
        cursor = 0.0
        for i, duration in enumerate(line_durations):
            pad = shorts.TAIL_SECONDS if i == len(line_durations) - 1 \
                else shorts.LINE_GAP_SECONDS
            scene_durations.append(duration + pad)
            timings.append((cursor, duration))
            cursor += duration + pad

        set_stage(SHORTS_STAGES[2])
        ass_path = work / "captions.ass"
        ass_path.write_text(
            shorts.build_captions_ass(lines, timings),
            encoding="utf-8")

        set_stage(SHORTS_STAGES[3])
        out_path = files_dir / f"{job_id}.mp4"
        info = shorts.assemble_short(
            [Path(scene["path"]) for scene in scenes],
            scene_durations, narration, ass_path, out_path, work,
            bed_path=BED_PATH if BED_PATH.exists() else None,
            watermark=bool(job.get("free_tier")),
            font_path=FONT_PATH)
        job["duration_seconds"] = round(float(info["duration"]), 2)
        job["bytes"] = out_path.stat().st_size
        jobs[job_id] = job
        # The word check runs on the delivered file here (the
        # processor is the finishing chain for this lane), then
        # the poster is cut from the final file.
        _short_word_check(job, out_path)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "0.5", "-i", str(out_path),
                 "-vframes", "1", "-q:v", "3",
                 str(files_dir / f"{job_id}.jpg")],
                capture_output=True, timeout=120)
        except Exception:
            pass
        job["stage"] = "Done"
        job["status"] = "done"
        jobs[job_id] = job
        data_volume.commit()
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = "the short could not be made, nothing was charged"
        job["internal_error"] = str(exc)[:300]
        job["stage"] = None
        if job.get("voiceover_status") != "ready":
            job["voiceover_status"] = "unavailable"
        jobs[job_id] = job
        data_volume.commit()


# ---------------------------------------------------------------------------
# Config (BUSINESS-RULES.md, Rule 5 and friends). Env can override.
# ---------------------------------------------------------------------------

WORKER_BASE = os.environ.get(
    "WORKER_BASE", "https://magrify--sinclip-comfyui-api.us-east.modal.run"
)
# Free plan (Jack's rule, 2026-11-16): five clips per account, TOTAL.
# Lifetime, no reset, no monthly allowance of any kind. The
# platform-wide ceiling has the same shape: a total free-work budget
# the operator can raise in config, not a monthly refill.
FREE_CLIPS_TOTAL = int(os.environ.get("FREE_CLIPS_TOTAL", "5"))
FREE_PLATFORM_TOTAL_CEILING = int(
    os.environ.get("FREE_PLATFORM_TOTAL_CEILING", "100")
)
MAX_FREE_RETRIES = 2
POW_DIFFICULTY_BITS = 18
SEGMENT_SECONDS = pricing.SEGMENT_SECONDS
# Form finishers (Pass 1): house-lane shape limits. Chaining runs
# 2-12 segments of about 5 seconds each; aspects are rendered
# natively by the worker at the chosen shape, never cropped from
# another shape in the post chain.
HOUSE_ASPECTS = ("9:16", "16:9", "1:1")
MAX_SEGMENTS = 12
# Per-plan in-flight generation limits: free accounts run one job
# at a time, paying customers four. Clip jobs are a separate CPU
# pipeline and are not counted here.
CONCURRENCY_FREE = 1
CONCURRENCY_PAID = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Start-image generation (Pass 3): the image prompt is a frame
# description, sized like the other per-job prompt fields.
IMAGE_PROMPT_MAX_CHARS = 1000


def _validate_image_b64(value, field: str):
    """Shape-check an uploaded frame before anything is dispatched:
    valid base64 carrying a PNG or JPEG of a sane size. Raises
    ValueError; job creation turns it into a clean 422."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be base64 image data")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise ValueError(f"{field} is not valid base64 image data") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f"{field} is larger than 10 MB")
    if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
        raise ValueError(f"{field} must be a PNG or JPEG image")
    return value
# Paid feature gates (Jack, 2026-11-16; Amendment 3): custom audio,
# model/lane switching, and gallery opt-out are open to any paying
# customer (active subscription OR an account that has ever paid
# in), enforced server-side in create_job and the settings endpoints
# below, not just in the UI. See is_paid().
CUSTOM_AUDIO_MAX_CHARS = 300
# Speech script (Jack's correction, 2026-11-16): "audio" means
# voiceover/speech too, not just music. A job may carry a spoken
# script; on lanes that cannot generate speech natively the
# platform's Kokoro voiceover stage speaks it in post. Paid-only,
# like the custom music prompt.
SPEECH_SCRIPT_MAX_CHARS = 1000
# The full English Kokoro voice set (voice picker, Jack,
# 2026-11-18): 11 American women, 9 American men, 4 British
# women, 4 British men. Every id verified against hexgrad's own
# Kokoro-TTS demo choices and the Kokoro-82M VOICES.md list; an
# id that cannot be verified stays out. af_heart stays the
# default everywhere. SPEECH_VOICE_LABELS holds the plain name
# the studio picker shows for each id; raw ids never reach the
# page as visible text.
DEFAULT_SPEECH_VOICE = "af_heart"
SPEECH_VOICES = (
    "af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah",
    "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)
SPEECH_VOICE_LABELS = {
    "af_heart": "Heart, American woman",
    "af_alloy": "Alloy, American woman",
    "af_aoede": "Aoede, American woman",
    "af_bella": "Bella, American woman",
    "af_jessica": "Jessica, American woman",
    "af_kore": "Kore, American woman",
    "af_nicole": "Nicole, American woman",
    "af_nova": "Nova, American woman",
    "af_river": "River, American woman",
    "af_sarah": "Sarah, American woman",
    "af_sky": "Sky, American woman",
    "am_adam": "Adam, American man",
    "am_echo": "Echo, American man",
    "am_eric": "Eric, American man",
    "am_fenrir": "Fenrir, American man",
    "am_liam": "Liam, American man",
    "am_michael": "Michael, American man",
    "am_onyx": "Onyx, American man",
    "am_puck": "Puck, American man",
    "am_santa": "Santa, American man",
    "bf_alice": "Alice, British woman",
    "bf_emma": "Emma, British woman",
    "bf_isabella": "Isabella, British woman",
    "bf_lily": "Lily, British woman",
    "bm_daniel": "Daniel, British man",
    "bm_fable": "Fable, British man",
    "bm_george": "George, British man",
    "bm_lewis": "Lewis, British man",
}
HANDLE_RE = re.compile(r"[A-Za-z0-9_-]{3,20}")

BED_PATH = Path("/worker-models/audio/music_bed_first.wav")
FILES_DIR = Path("/data/files")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ---------------------------------------------------------------------------
# Model picker (Jack's ruling, 2026-11-16; API-LANES-DESIGN.md s.4).
# Users see model names only, on every surface. The internal routes
# below never reach a user-facing string. The default model for every
# account and every job is Wan 2.2, the cheapest (Rule 3, Amendment
# 2): free accounts render Wan 2.2 Draft, paid accounts Wan 2.2
# Production, and nobody lands on a bigger model without actively
# switching. Switching is paid-only, enforced in create_job.
# ---------------------------------------------------------------------------

MODELS = [
    {"id": "wan2.2", "label": "Wan 2.2", "paid_only": False},
    {"id": "wan3", "label": "Wan 3.0", "paid_only": True},
    {"id": "wan3_prime", "label": "Wan 3.0 Prime", "paid_only": True},
    {"id": "wan_motion", "label": "Wan Motion", "paid_only": True},
    {"id": "seedance", "label": "Seedance 2.5", "paid_only": True},
    {"id": "veo3", "label": "Veo 3.1", "paid_only": True},
    {"id": "veo3_fast", "label": "Veo 3.1 Fast", "paid_only": True},
    {"id": "kling3", "label": "Kling 3.0", "paid_only": True},
    # Full shelf (2026-11-16 availability audit): every model the
    # audit confirmed addable through the two wired suppliers.
    # Confirmed-unavailable models (Sora 2, Cinema Studio, DoP,
    # Kling 4.0 Flash) are not listed and were not wired.
    {"id": "kling4", "label": "Kling 4.0", "paid_only": True},
    {"id": "kling3_omni", "label": "Kling 3.0 Omni", "paid_only": True},
    {"id": "seedance2", "label": "Seedance 2.0", "paid_only": True},
    {"id": "seedance2_fast", "label": "Seedance 2.0 Fast", "paid_only": True},
    {"id": "seedance2_mini", "label": "Seedance 2.0 Mini", "paid_only": True},
    {"id": "veo3_lite", "label": "Veo 3.1 Lite", "paid_only": True},
    # The Gemini Omni line is the pinned successor lane for the
    # Veo 3.1 family, whose fal endpoints carry a 2026-11-17
    # deprecation date; the Veo entries above stay as they are.
    {"id": "omni12", "label": "Gemini Omni 1.2 Flash", "paid_only": True},
    {"id": "omni11", "label": "Gemini Omni 1.1 Flash", "paid_only": True},
    {"id": "hailuo23", "label": "Hailuo 2.3", "paid_only": True},
    {"id": "minimax_h3", "label": "MiniMax H3", "paid_only": True},
    {"id": "grok", "label": "Grok Imagine Video", "paid_only": True},
    # Grok Imagine 1.5 family as their own lanes (2026-11-17,
    # Jack's approval): the 1.5 on its documented Kie preview
    # model id, and the 1.5 Lite on fal. Lite sells only its
    # genuine 480p/720p tiers (its 1080p is upscaled; see
    # pricing.py).
    {"id": "grok15", "label": "Grok Imagine Video 1.5", "paid_only": True},
    {"id": "grok15_lite", "label": "Grok Imagine Video 1.5 Lite", "paid_only": True},
    {"id": "luma_ray32", "label": "Luma Ray 3.2", "paid_only": True},
    {"id": "ltx25", "label": "LTX-2.5", "paid_only": True},
    {"id": "ltx25_fast", "label": "LTX-2.5 Fast", "paid_only": True},
    {"id": "happyhorse", "label": "HappyHorse 1.1", "paid_only": True},
    {"id": "pika22", "label": "Pika 2.2", "paid_only": True},
    {"id": "vidu_q2", "label": "Vidu Q2", "paid_only": True},
    # Follow-up wiring pass (2026-11-16): Vidu Q3 opens on its
    # first published per-second route; Runway Gen-4.5 wires
    # direct. Vidu Q4, Seedance 2.5 Flash and PixVerse V6 are
    # wired but price-paused (their supplier publishes only
    # per-run "from" prices, never a per-second rate), exactly
    # like Vidu Q2.
    {"id": "vidu_q3", "label": "Vidu Q3", "paid_only": True},
    {"id": "vidu_q4", "label": "Vidu Q4", "paid_only": True},
    {"id": "seedance25_flash", "label": "Seedance 2.5 Flash", "paid_only": True},
    {"id": "pixverse_v6", "label": "PixVerse V6", "paid_only": True},
    {"id": "runway_gen45", "label": "Runway Gen-4.5", "paid_only": True},
    # Uncensored video lanes (2026-11-17 wiring pass): uncensored
    # builds of shelf models on suppliers that add no refusal
    # layer, flagged in UNCENSORED_VIDEO_MODELS below so the
    # picker shows them with the " · Uncensored" suffix. Labels
    # are the plain model names (Jack's ruling): the suffix is
    # the differentiator from the moderated lane of the same
    # model. Supplier model ids keep their real names in the
    # submit tables only; nothing user-facing names a supplier
    # or a build nickname.
    {"id": "wan22_unc", "label": "Wan 2.2", "paid_only": True},
    {"id": "wan3_unc", "label": "Wan 3.0", "paid_only": True},
    {"id": "minimax_h3_unc", "label": "MiniMax H3", "paid_only": True},
    {"id": "seedance25_unc", "label": "Seedance 2.5", "paid_only": True},
    {"id": "vidu_q3_unc", "label": "Vidu Q3", "paid_only": True},
    {"id": "wan26_unc", "label": "Wan 2.6", "paid_only": True},
    {"id": "wan27_unc", "label": "Wan 2.7", "paid_only": True},
    {"id": "wan3pro_unc", "label": "Wan 3.0 Pro", "paid_only": True},
    # SpicyAPI catalog completion (2026-11-17): every remaining
    # spicy (uncensored) video family on the supplier's own
    # catalog, each wired from its own model page (lengths,
    # resolutions, prices, audio and input fields per page; see
    # ~/workspace/sinclip/spicyapi-video-catalog-2026-11-17.md).
    {"id": "wan3prime_unc", "label": "Wan 3.0 Prime", "paid_only": True},
    {"id": "wan22_lora_unc", "label": "Wan 2.2 LoRA", "paid_only": True},
    # House Uncut lane (2026-11-18, Jack's order): the community
    # Wan 2.2 NSFW LoRA pair staged on our own worker, running
    # the stock A14B sampler path. Not a per-second lane: it
    # settles on the house price table (pricing lane "nsfw"),
    # routed in create_job like the effects lane. The label is
    # the differentiator from the house Wan 2.2 and the hosted
    # Wan 2.2 LoRA build; the Uncensored suffix comes from
    # UNCENSORED_VIDEO_MODELS like the rest.
    {"id": "wan22_uncut", "label": "Wan 2.2 Uncut", "paid_only": True},
    {"id": "seedance2_unc", "label": "Seedance 2.0", "paid_only": True},
    {"id": "seedance2fast_unc", "label": "Seedance 2.0 Fast", "paid_only": True},
    {"id": "seedance2mini_unc", "label": "Seedance 2.0 Mini", "paid_only": True},
    {"id": "seedance15pro_unc", "label": "Seedance 1.5 Pro", "paid_only": True},
    {"id": "ltx23_unc", "label": "LTX 2.3", "paid_only": True},
    {"id": "ltx23_lora_unc", "label": "LTX 2.3 LoRA", "paid_only": True},
]
MODEL_LABELS = {m["id"]: m["label"] for m in MODELS}
DEFAULT_MODEL = "wan2.2"
# The Uncensored label (Jack, 2026-11-16): the self-hosted house
# lane runs open weights on our own worker, so no provider
# moderates its prompts or its outputs. Every other video lane is
# a hosted API whose provider moderates on its own end. Mirrors
# the uncensored flag on the LLM provider group (llm_providers.py).
UNCENSORED_VIDEO_MODELS = (
    "wan2.2",
    # Uncensored builds wired 2026-11-17: hosted lanes whose
    # supplier adds no refusal layer in front of the model.
    "wan22_unc", "wan3_unc", "minimax_h3_unc", "seedance25_unc",
    "vidu_q3_unc", "wan26_unc", "wan27_unc", "wan3pro_unc",
    "wan3prime_unc", "wan22_lora_unc", "seedance2_unc",
    "seedance2fast_unc", "seedance2mini_unc", "seedance15pro_unc",
    "ltx23_unc", "ltx23_lora_unc",
    # The house Uncut lane: our own worker, open weights, no
    # provider moderation layer.
    "wan22_uncut",
)
# House video models: served by our own worker on the per-job
# price table rather than the per-second shelf. Each maps to the
# pricing lane its jobs settle on (the ceiling, the included
# budget, the balance price and the refunds all read the lane).
HOUSE_MODEL_PRICING_LANE = {
    "wan2.2": "production",
    "wan22_uncut": "nsfw",
}


def job_is_nsfw(job: dict) -> bool:
    """Whether a job is NSFW for public gallery purposes (Jack,
    2026-11-18): its lane is the house NSFW lane, or its model is
    one of the uncensored catalog models (the same
    UNCENSORED_VIDEO_MODELS flag /api/config serves and the
    studio picker renders as the Uncensored marker). Either
    signal classifies. Note the set includes the house Wan 2.2
    itself: the self-hosted lane runs open weights with no
    provider moderation layer, so its outputs carry the flag."""
    if not job:
        return False
    if job.get("lane") == "nsfw":
        return True
    model = job.get("model")
    if not model and job.get("lane") in pricing.MEASURED_COST_CENTS:
        # Legacy house records predate the model field. A job on
        # a house lane with no model recorded ran the default
        # house model, the same fallback public_job renders, so
        # classify it by that model instead of letting the
        # missing field slip it past the gallery rule (found by
        # the 2026-11-18 verification bundle: two Nov 16 balloon
        # clips with no model field were still listed).
        model = DEFAULT_MODEL
    return model in UNCENSORED_VIDEO_MODELS
# Price tier signs for the studio dropdown (Jack, 2026-11-16):
# Google-Maps-style $ signs banded on FIXED edges over the model's
# lowest customer sell rate in cents per second (see
# model_price_tier inside the web app). $ under 5c/s, $$ under
# 12c/s, $$$ under 25c/s, $$$$ above. Fixed bands, not quartiles,
# so a model's sign never changes because another model's price
# moved.
PRICE_TIER_BANDS_CENTS_PER_SECOND = (5.0, 12.0, 25.0)
PER_SECOND_MODELS = (
    "wan3", "wan3_prime", "seedance", "veo3", "veo3_fast", "kling3",
    "kling4", "kling3_omni", "seedance2", "seedance2_fast",
    "seedance2_mini", "veo3_lite", "omni12", "omni11", "hailuo23",
    "minimax_h3", "grok", "grok15", "grok15_lite", "luma_ray32",
    "ltx25", "ltx25_fast",
    "happyhorse", "pika22", "vidu_q2", "vidu_q3", "vidu_q4",
    "seedance25_flash", "pixverse_v6", "runway_gen45",
    "wan22_unc", "wan3_unc", "minimax_h3_unc", "seedance25_unc",
    "vidu_q3_unc", "wan26_unc", "wan27_unc", "wan3pro_unc",
    "wan3prime_unc", "wan22_lora_unc", "seedance2_unc",
    "seedance2fast_unc", "seedance2mini_unc", "seedance15pro_unc",
    "ltx23_unc", "ltx23_lora_unc",
    "wan_motion",
)
# Which supplier serves each per-second model. Internal only: the
# picker, the pricing page, and every error show model names, never
# suppliers (Amendment 1). Route rule (Jack, 2026-11-16): where a
# model is sold by both suppliers, the cheaper published route is
# primary and the other stays wired or recorded as the fallback.
# That is why Wan 3.0, Wan 3.0 Prime and Kling 3.0 moved to
# BeatAPI (their fal rates sit in pricing.py's fallback table and
# their fal endpoints stay in FAL_ENDPOINTS below), and why Veo
# stays on fal despite BeatAPI's lower sticker: BeatAPI bills Veo
# as a fixed 8-second clip, which does not fit the per-second
# lane shape. Seedance 2.5's fal endpoint stays in FALLBACK notes
# in pricing.py, not wired, because fal charges over twice the
# rate and offers no 1080p tier for this model.
MODEL_PROVIDER = {
    "wan3": "beatapi",
    "wan3_prime": "cometapi",
    "seedance": "beatapi",
    "kling3": "kie",
    "kling4": "kie",
    "seedance2": "beatapi",
    "seedance2_fast": "kie",
    "seedance2_mini": "kie",
    "omni12": "kie",
    "omni11": "beatapi",
    "grok": "kie",
    "grok15": "kie",
    "grok15_lite": "fal",
    "happyhorse": "openrouter",
    "minimax_h3": "beatapi",
    "veo3": "piapi",
    "veo3_fast": "fal",
    "kling3_omni": "kie",
    "veo3_lite": "eachlabs",
    "hailuo23": "kie",
    "luma_ray32": "fal",
    "ltx25": "fal",
    "ltx25_fast": "fal",
    "pika22": "fal",
    "vidu_q2": "fal",
    "vidu_q3": "novita",
    "vidu_q4": "wavespeed",
    "seedance25_flash": "wavespeed",
    "pixverse_v6": "wavespeed",
    "runway_gen45": "runway",
    # Uncensored lanes (2026-11-17): cheapest route per model
    # across the three uncensored suppliers (route rule).
    "wan22_unc": "spicyapi",
    "wan3_unc": "spicyapi",
    "seedance25_unc": "spicyapi",
    "vidu_q3_unc": "spicyapi",
    "minimax_h3_unc": "hotapi",
    # Wan 2.6 / 2.7 moved from Wiro to SpicyAPI 2026-11-17:
    # the supplier's own pages price both tiers of both models
    # below the Wiro route (2.6: $0.095/$0.1425 vs $0.10/$0.15;
    # 2.7: $0.1235/$0.19 vs $0.13/$0.20). The Wiro rates are
    # recorded in pricing.py's DISPLACED_RATES and the Wiro
    # submit path stays wired as the spare.
    "wan26_unc": "spicyapi",
    "wan27_unc": "spicyapi",
    "wan3pro_unc": "wiro",
    "wan3prime_unc": "spicyapi",
    "wan22_lora_unc": "spicyapi",
    "seedance2_unc": "spicyapi",
    "seedance2fast_unc": "spicyapi",
    "seedance2mini_unc": "spicyapi",
    "seedance15pro_unc": "spicyapi",
    "ltx23_unc": "spicyapi",
    "ltx23_lora_unc": "spicyapi",
    # Motion transfer (2026-11-17): fal's Wan Motion endpoint.
    "wan_motion": "fal",
}
# The secret env var each supplier's key lives under in the
# moonfactory-providers Modal secret. Lane state checks the
# PRIMARY supplier's key; the per-resolution override below is
# checked again at quote and dispatch time.
PROVIDER_KEY_ENV = {
    "fal": "FAL_KEY",
    "beatapi": "BEATAPI_KEY",
    "kie": "KIE_API_KEY",
    "cometapi": "COMETAPI_API_KEY",
    "piapi": "PIAPI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "novita": "NOVITA_API_KEY",
    "wavespeed": "WAVESPEED_API_KEY",
    "eachlabs": "EACHLABS_API_KEY",
    "runway": "RUNWAY_API_KEY",
    "vidu": "VIDU_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "spicyapi": "SPICYAPI_API_KEY",
    "hotapi": "HOTAPI_API_KEY",
    "wiro": "WIRO_API_KEY",
}
# Per-resolution route overrides: Seedance 2.5's cheapest route
# is a different supplier at 480p/720p (CometAPI) than at 1080p
# (BeatAPI), so the provider is a function of (model,
# resolution), not model alone. The 480p tier joined the
# CometAPI route in pricing round 2 (2026-11-16): CometAPI's
# own models API states an actual 480p price of $0.0824/s,
# below BeatAPI's ~$0.0968/s live read.
MODEL_PROVIDER_OVERRIDES = {
    "seedance": {"480p": "cometapi", "720p": "cometapi"},
    # Veo 3.1's 4K tier (2026-11-17, 4K expansion): the primary
    # PiAPI route's own docs cap its resolution enum at
    # 720p/1080p, so 4K rides the wired fal route, priced at
    # fal's with-audio 4K rate in pricing.py.
    "veo3": {"4K": "fal"},
}


def provider_for(model: str, resolution: str = None) -> str:
    """The supplier that serves this model at this resolution."""
    if resolution:
        override = MODEL_PROVIDER_OVERRIDES.get(model, {}).get(resolution)
        if override:
            return override
    return MODEL_PROVIDER.get(model, "fal")


# ---------------------------------------------------------------------------
# Lane audio capability (Jack's correction, 2026-11-16): "audio" is
# not one thing. A lane can generate speech/dialogue natively, can
# generate music/ambient/SFX audio only, or can be silent. The
# record is per LANE (the model on its wired route), because the
# cheapest route can strip a capability the model itself has: Veo
# 3.1 the model speaks, but its wired PiAPI route is the published
# no-audio rate, so the lane records silent. Where a lane has a
# native-audio mode, the submit wiring selects it by default.
# Talking content on a non-speech lane is served by the platform's
# Kokoro voiceover stage (the job's speech_script), never by
# treating a music bed as if it were speech.
# ---------------------------------------------------------------------------
LANE_AUDIO = {
    "wan2.2": {"native_speech": False, "native_audio": False,
               "note": "house model is silent; music bed and the voiceover path run in post"},
    "wan22_uncut": {"native_speech": False, "native_audio": False,
               "note": "house model is silent; music bed and the voiceover path run in post"},
    "wan3": {"native_speech": False, "native_audio": True,
             "note": "native audio is music/SFX-led; speech is not reliable"},
    "wan3_prime": {"native_speech": False, "native_audio": True,
                   "note": "native audio is music/SFX-led; speech is not reliable"},
    "wan_motion": {"native_speech": False, "native_audio": False,
                   "note": "motion transfer: your subject image performs the motion from your reference video; silent, music and voice run in post"},
    "seedance": {"native_speech": True, "native_audio": True,
                 "note": "Seedance native audio incl. dialogue; audio on for the BeatAPI tiers; the CometAPI 720p tier's audio basis is unconfirmed"},
    "veo3": {"native_speech": False, "native_audio": False,
             "note": "the model generates dialogue natively, but the wired route is the published no-audio rate; with-audio routes are recorded as fallbacks in pricing.py"},
    "veo3_fast": {"native_speech": True, "native_audio": True,
                  "note": "wired route is the with-audio rate; dialogue native"},
    "kling3": {"native_speech": True, "native_audio": True,
               "note": "Kling sound on by default on the wired route; dialogue native"},
    "kling4": {"native_speech": True, "native_audio": True,
               "note": "Kling native audio incl. dialogue"},
    "kling3_omni": {"native_speech": True, "native_audio": True,
                    "note": "Kling native audio incl. dialogue"},
    "seedance2": {"native_speech": True, "native_audio": True,
                  "note": "Seedance native audio incl. dialogue; generate_audio on"},
    "seedance2_fast": {"native_speech": True, "native_audio": True,
                       "note": "Seedance native audio incl. dialogue; generate_audio on"},
    "seedance2_mini": {"native_speech": False, "native_audio": False,
                       "note": "silent model on every route"},
    "veo3_lite": {"native_speech": False, "native_audio": False,
                  "note": "the model generates dialogue natively, but the wired route is the published no-audio tier; the with-audio tier is recorded in pricing.py"},
    "omni12": {"native_speech": True, "native_audio": True,
               "note": "Gemini Omni native audio incl. speech; the wired route's audio basis is unconfirmed"},
    "omni11": {"native_speech": True, "native_audio": True,
               "note": "Gemini Omni native audio incl. speech"},
    "hailuo23": {"native_speech": False, "native_audio": False,
                 "note": "silent on every route; no native audio mode exists"},
    "minimax_h3": {"native_speech": True, "native_audio": True,
                   "note": "H3 native audio incl. speech"},
    "grok": {"native_speech": True, "native_audio": True,
             "note": "Grok Imagine native audio incl. dialogue"},
    "grok15": {"native_speech": True, "native_audio": True,
               "note": "Grok Imagine Video 1.5 native audio incl. lip-synced dialogue; generated in the same pass on the wired route, no audio switch"},
    "grok15_lite": {"native_speech": True, "native_audio": True,
                    "note": "Grok Imagine Video 1.5 Lite native synchronized audio incl. lip-synced dialogue; always on, no audio parameter on the wired route"},
    "luma_ray32": {"native_speech": False, "native_audio": False,
                   "note": "no published native-audio mode on the wired route"},
    "ltx25": {"native_speech": False, "native_audio": True,
              "note": "LTX native audio is music/SFX-led; speech is not reliable"},
    "ltx25_fast": {"native_speech": False, "native_audio": True,
                   "note": "LTX native audio is music/SFX-led; speech is not reliable"},
    "happyhorse": {"native_speech": False, "native_audio": False,
                   "note": "no published native-audio mode on the wired route"},
    "pika22": {"native_speech": False, "native_audio": False,
               "note": "silent"},
    "vidu_q2": {"native_speech": False, "native_audio": True,
                "note": "Vidu native audio; speech unconfirmed; lane price-paused"},
    "vidu_q3": {"native_speech": True, "native_audio": True,
                "note": "Vidu Q3 native audio incl. speech; audio on in the wired payload"},
    "vidu_q4": {"native_speech": True, "native_audio": True,
                "note": "Vidu Q4 native audio incl. speech; generate_audio on; lane price-paused"},
    "seedance25_flash": {"native_speech": True, "native_audio": True,
                         "note": "Seedance native audio incl. dialogue; generate_audio on; lane price-paused"},
    "pixverse_v6": {"native_speech": False, "native_audio": True,
                    "note": "PixVerse native audio is music/SFX-led; lane price-paused"},
    "runway_gen45": {"native_speech": False, "native_audio": False,
                     "note": "Gen-4.5 is silent; its maker sells audio as separate models"},
    # Uncensored lanes (2026-11-17): audio capability follows
    # each build's wired route, same rule as the moderated
    # lanes. The audio switches the routes publish are selected
    # by default in the submit wiring.
    "wan22_unc": {"native_speech": False, "native_audio": False,
                  "note": "silent on the wired route, like the base model; music bed and the voiceover path run in post"},
    "wan3_unc": {"native_speech": False, "native_audio": True,
                 "note": "native audio is music/SFX-led; speech is not reliable; generate_audio on"},
    "seedance25_unc": {"native_speech": True, "native_audio": True,
                       "note": "Seedance native audio incl. dialogue; generate_audio on"},
    "vidu_q3_unc": {"native_speech": True, "native_audio": True,
                    "note": "Vidu Q3 native audio incl. speech; generate_audio on in the wired payload"},
    "minimax_h3_unc": {"native_speech": True, "native_audio": True,
                       "note": "H3 native audio incl. speech; audio is always on for this build, no silent mode exists"},
    "wan26_unc": {"native_speech": False, "native_audio": False,
                  "note": "the wired route generates no audio: this build can pace a clip to a supplied audio track, which the platform does not send; music bed and the voiceover path run in post"},
    "wan27_unc": {"native_speech": False, "native_audio": True,
                  "note": "generate_audio on by default on the wired route; speech unconfirmed"},
    "wan3pro_unc": {"native_speech": False, "native_audio": True,
                    "note": "generated audio is a mode on the wired route and is selected by default; speech unconfirmed"},
    # SpicyAPI catalog completion (2026-11-17): audio follows
    # each build's own model page, same rule as above.
    "wan3prime_unc": {"native_speech": False, "native_audio": True,
                      "note": "native audio is music/SFX-led; speech is not reliable; generate_audio on"},
    "wan22_lora_unc": {"native_speech": False, "native_audio": False,
                       "note": "silent on the wired route, like the base model; music bed and the voiceover path run in post"},
    "seedance2_unc": {"native_speech": False, "native_audio": True,
                      "note": "Seedance native audio; generate_audio on; its page publishes sound, not speech"},
    "seedance2fast_unc": {"native_speech": False, "native_audio": True,
                          "note": "Seedance native audio; generate_audio on; its page publishes sound, not speech"},
    "seedance2mini_unc": {"native_speech": False, "native_audio": True,
                          "note": "this build generates sound (unlike the moderated Mini lane); generate_audio on; speech not published"},
    "seedance15pro_unc": {"native_speech": False, "native_audio": True,
                          "note": "native sound; generate_audio on; priced at the audio-on rates its page publishes"},
    "ltx23_unc": {"native_speech": False, "native_audio": False,
                  "note": "silent on the wired route: the endpoint publishes no audio field; music bed and the voiceover path run in post"},
    "ltx23_lora_unc": {"native_speech": False, "native_audio": False,
                       "note": "silent on the wired route: the endpoint publishes no audio field; music bed and the voiceover path run in post"},
}


def lane_audio(model: str) -> dict:
    return LANE_AUDIO.get(model, {
        "native_speech": False, "native_audio": False, "note": "",
    })


def lip_sync_applies(model: str, speech_script) -> bool:
    """Jack's built-in lip sync rule (2026-11-17): every video
    where someone or something is talking must have its mouth
    match the audio. A job whose picture will be voiced in post
    (a spoken line on a lane that does not speak natively) gets
    the automatic lip sync stage in the finishing pipeline, and
    its cost is folded into the quote, the hold and the ceiling.
    Speech-native lanes generate picture and speech in one pass,
    so their mouths already match and no stage runs."""
    if not speech_script:
        return False
    return not lane_audio(model)["native_speech"]


# Suppliers whose finished files arrive as a result URL for the
# platform to download (the house worker is the only other source).
URL_RESULT_PROVIDERS = (
    "fal", "beatapi", "kie", "cometapi", "piapi", "openrouter",
    "novita", "wavespeed", "eachlabs", "runway", "vidu", "minimax",
    "spicyapi", "hotapi", "wiro",
)
# Suppliers polled through mirror_api_status (fal and BeatAPI keep
# their original mirror functions).
API_POLL_PROVIDERS = (
    "kie", "cometapi", "piapi", "openrouter", "novita",
    "wavespeed", "eachlabs", "runway", "vidu", "minimax",
    "spicyapi", "hotapi", "wiro",
)
# Bounded poll lifetimes, in seconds from job creation (the
# universal safety net, 2026-11-17). The SpicyAPI lesson: a
# mirror written against a guessed envelope can read a finished
# job as "still running" forever, and an error-count give-up
# never fires on a provider that keeps answering politely. So
# every job family also carries a wall-clock bound: past it the
# job fails clean, the hold releases, and the charge refunds in
# full (mirror_status enforces it on every read). Video bounds
# are generous because chained lengths submit their segments
# one after another inside one job; no single generation takes
# anywhere near these limits. The clip bound sits just past
# process_clip_job's own 3600 second function timeout: a clip
# job still marked running beyond it is dead by construction.
PROVIDER_POLL_LIMIT_SECONDS = 7200
HOUSE_POLL_LIMIT_SECONDS = 7200
IMAGE_POLL_LIMIT_SECONDS = 1800
CLIP_POLL_LIMIT_SECONDS = 3900
# Short jobs carry the same bound shape as clip jobs: the
# runner's own 3600 second function timeout plus margin.
SHORT_POLL_LIMIT_SECONDS = 3900
TRAINING_POLL_LIMIT_SECONDS = 3600
# Template lane (HyperFrames on Kaggle, 2026-11-17): the bound is
# generous because a cold start is a Kaggle machine starting, the
# toolchain dataset loading, and only then the render itself. An
# hour covers all three with room to spare; past it the job fails
# clean (the lane is free, so there is nothing to refund).
TEMPLATE_POLL_LIMIT_SECONDS = 3600
# ---------------------------------------------------------------------------
# Template lane: HyperFrames compositions rendered on Kaggle
# (Jack, 2026-11-17). HyperFrames (github.com/heygen-com/hyperframes,
# Apache 2.0) turns an HTML composition with seekable GSAP animation
# into a deterministic MP4 on plain CPU. The lane is FREE: zero
# credits, zero charge, ever. Rendering runs on Kaggle free quota
# from a worker kernel that mounts a persistent toolchain dataset
# (Node 22, the HyperFrames CLI, its Chromium, GSAP); Modal never
# renders these jobs and there is no Modal fallback.
#
# The compositions are fixed templates. The user's words ride in as
# escaped text data only; nobody's HTML ever reaches the renderer.
# Vertical 1080 x 1920, matching the platform delivery shape.
# ---------------------------------------------------------------------------

HF_KERNEL_SLUG = "magrify/moonfactory-hyperframes-worker"
HF_TOOLCHAIN_DATASET = "magrify/moonfactory-hf-toolchain"
HF_KEY_DATASET = "magrify/moonfactory-hf-key"
HF_WORKER_SCRIPT_PATH = Path("/platform/hf_worker_kernel.py")
# Paid accounts get a fair daily cap on the free lane (free
# accounts are bounded by the lifetime free cap instead).
TEMPLATE_DAILY_CAP_PAID = 20
# Faceless shorts lane (2026-11-18): the MoneyPrinterTurbo shape
# built from the platform's own pieces (platform/shorts.py). A
# rendered lane with the Titles pricing treatment: zero credits,
# zero charge, paid accounts capped per day, unpaid accounts
# counting against the free lifetime clips. The pipeline runs
# inside process_short_job on CPU; stock comes from Pexels when
# its key is configured and from Wikimedia Commons otherwise.
SHORTS_DAILY_CAP_PAID = 20
SHORTS_STAGES = (
    "Finding footage",
    "Recording the narration",
    "Timing the captions",
    "Putting it together",
)
TEMPLATE_MAX_SOURCE_SECONDS = 60
TEMPLATE_MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# A trigger push is attempted at most once per this many seconds,
# and only while no kernel heartbeat is fresher than the stale
# window: one live kernel serves every queued job.
HF_HEARTBEAT_STALE_SECONDS = 180
HF_TRIGGER_MIN_INTERVAL_SECONDS = 180

HF_TEMPLATES = {
    "title": {
        "name": "Title card",
        "duration": 6.0,
        "fields": ("headline", "subline"),
    },
    "captions": {
        "name": "Captions over a clip",
        "duration": None,  # the source clip sets the length
        "fields": ("caption",),
    },
    "stat": {
        "name": "Stat card",
        "duration": 6.0,
        "fields": ("stat", "label"),
    },
    "promo": {
        "name": "Promo card",
        "duration": 7.0,
        "fields": ("headline", "subline", "cta"),
    },
    "story": {
        "name": "Animated story short",
        "duration": None,  # the story plan sets it, one scene per line
        "fields": ("title", "lines"),
        "motion": True,
    },
    "sting": {
        "name": "Logo sting",
        "duration": 4.0,
        "fields": ("headline", "subline"),
    },
}

_HF_FIELD_LIMITS = {
    "headline": 90,
    "subline": 120,
    "caption": 120,
    "stat": 20,
    "label": 90,
    "cta": 40,
    "title": 90,
    "lines": 1100,
}


def hf_clean_fields(template_id: str, body: dict) -> dict:
    """Validate and trim one template job's words. Raises
    ValueError with a plain message; the create route turns it
    into a clean 422. The accent color must be a plain hex value
    so it can ride into CSS without escaping games."""
    spec = HF_TEMPLATES.get(template_id)
    if spec is None:
        raise ValueError("pick one of the templates on the list")
    out = {}
    for field in spec["fields"]:
        if field == "lines":
            out[field] = _hf_clean_lines(body.get(field))
            continue
        value = str(body.get(field) or "").strip()
        limit = _HF_FIELD_LIMITS[field]
        if len(value) > limit:
            raise ValueError(f"that text is too long, keep it under {limit} characters")
        out[field] = value
    required = {
        "title": ("headline",),
        "captions": ("caption",),
        "stat": ("stat", "label"),
        "promo": ("headline",),
        "story": ("title", "lines"),
        "sting": ("headline",),
    }[template_id]
    for field in required:
        if not out.get(field):
            raise ValueError("one of the boxes is empty, fill it in and try again")
    color = str(body.get("color") or "#6366f1").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", color):
        raise ValueError("that color does not look right, pick one from the color box")
    out["color"] = color
    return out


def _hf_clean_lines(raw) -> str:
    """The story template's lines: one story line per scene,
    three to eight of them, each short enough to read on screen.
    Accepts the studio textarea text or a list. Stored as one
    newline joined string in the job's fields."""
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw or "").split("\n")
    lines = []
    for item in items:
        line = " ".join(str(item).split())
        if not line:
            continue
        if len(line) > 120:
            raise ValueError("keep each story line under 120 characters")
        lines.append(line)
    if len(lines) < 3:
        raise ValueError("write at least three story lines, one on each row")
    if len(lines) > 8:
        raise ValueError("keep it to eight story lines at most")
    return "\n".join(lines)


def hf_story_plan(title: str, lines: list) -> list:
    """The animated story short's scene plan, the single source
    of truth the composition, the scene script and the audio
    generator all read. A title scene, one scene per story line
    timed to a reading pace, and a closing scene in golden
    light. Boundaries land on whole frames at 30 frames a
    second. Camera programs cycle the town framings."""
    fps = 30
    spans = []
    frame = 0

    def add(kind, line, light, program, seconds):
        nonlocal frame
        frames = max(1, round(seconds * fps))
        spans.append({
            "a": round(frame / fps, 4),
            "b": round((frame + frames) / fps, 4),
            "kind": kind, "line": line, "light": light,
            "program": program,
        })
        frame += frames

    add("title", -1, "day", 0, 4.5)
    last = len(lines) - 1
    for i, line in enumerate(lines):
        words = len(str(line).split())
        seconds = min(7.5, max(4.2, 2.4 + 0.38 * words))
        add("line", i, "golden" if i == last else "day", 1 + (i % 5), seconds)
    add("outro", -1, "golden", 6, 3.5)
    return spans


def _hf_page(duration: float, accent: str, clips: str, timeline: str) -> str:
    """The shared composition shell: a vertical stage, the GSAP
    library the worker copies in beside this file, and one
    seekable timeline registered the way the renderer expects."""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #0c0c0f; }}
      #root {{ position: relative; width: 1080px; height: 1920px; background: #0c0c0f;
        font-family: sans-serif; color: #f4f4f1; overflow: hidden; }}
      .bar {{ position: absolute; left: 90px; top: 780px; width: 180px; height: 14px;
        background: {accent}; border-radius: 7px; }}
      .headline {{ position: absolute; left: 90px; top: 840px; width: 900px;
        font-size: 104px; font-weight: 700; line-height: 1.08; letter-spacing: -0.02em; }}
      .subline {{ position: absolute; left: 90px; top: 1240px; width: 860px;
        font-size: 46px; font-weight: 400; line-height: 1.3; color: #b8b8c0; }}
      .statnum {{ position: absolute; left: 90px; top: 800px; width: 900px;
        font-size: 230px; font-weight: 800; line-height: 1.0; color: {accent}; }}
      .statlabel {{ position: absolute; left: 90px; top: 1120px; width: 860px;
        font-size: 54px; font-weight: 500; line-height: 1.3; }}
      .cta {{ position: absolute; left: 90px; top: 1560px; padding: 28px 44px;
        background: {accent}; color: #ffffff; font-size: 44px; font-weight: 600;
        border-radius: 999px; }}
      .bgvideo {{ position: absolute; left: 0; top: 0; width: 1080px; height: 1920px;
        object-fit: cover; }}
      .capwrap {{ position: absolute; left: 60px; top: 1330px; width: 960px;
        text-align: center; }}
      .capbar {{ display: inline-block; width: 150px; height: 12px; border-radius: 6px;
        background: {accent}; margin-bottom: 30px; }}
      .caption {{ font-size: 76px; font-weight: 700; line-height: 1.2;
        text-shadow: 0 4px 30px rgba(0,0,0,0.85); }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0"
      data-duration="{duration:g}" data-width="1080" data-height="1920" data-fps="30">
{clips}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{timeline}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


def build_hf_composition(template_id: str, fields: dict,
                         source_duration: float = 0.0) -> str:
    """Build one job's composition HTML from its fixed template
    and its escaped words. Called at claim time, so the words the
    renderer sees are exactly the words stored on the job."""
    import html as _html

    esc = _html.escape
    accent = fields.get("color") or "#6366f1"
    if template_id == "title":
        clips = f"""      <div class="bar clip" data-start="0" data-duration="6" data-track-index="0"></div>
      <h1 class="headline clip" data-start="0" data-duration="6" data-track-index="1">{esc(fields.get("headline") or "")}</h1>
      <p class="subline clip" data-start="0" data-duration="6" data-track-index="2">{esc(fields.get("subline") or "")}</p>"""
        timeline = """      tl.fromTo(".bar", { scaleX: 0, transformOrigin: "left center" }, { scaleX: 1, duration: 0.5, ease: "power3.out" }, 0.2);
      tl.fromTo(".headline", { opacity: 0, y: 60 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.35);
      tl.fromTo(".subline", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.6);
      tl.to([".bar", ".headline", ".subline"], { opacity: 0, duration: 0.5, ease: "power2.in" }, 5.4);"""
        return _hf_page(6.0, accent, clips, timeline)
    if template_id == "stat":
        clips = f"""      <div class="statnum clip" data-start="0" data-duration="6" data-track-index="0">{esc(fields.get("stat") or "")}</div>
      <p class="statlabel clip" data-start="0" data-duration="6" data-track-index="1">{esc(fields.get("label") or "")}</p>"""
        timeline = """      tl.fromTo(".statnum", { opacity: 0, scale: 0.82, transformOrigin: "left center" }, { opacity: 1, scale: 1, duration: 0.55, ease: "back.out(1.6)" }, 0.25);
      tl.fromTo(".statlabel", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.55, ease: "power3.out" }, 0.55);
      tl.to([".statnum", ".statlabel"], { opacity: 0, duration: 0.5, ease: "power2.in" }, 5.4);"""
        return _hf_page(6.0, accent, clips, timeline)
    if template_id == "promo":
        cta = fields.get("cta") or ""
        cta_clip = (
            f"""      <div class="cta clip" data-start="0" data-duration="7" data-track-index="3">{esc(cta)}</div>"""
            if cta else "")
        clips = f"""      <div class="bar clip" data-start="0" data-duration="7" data-track-index="0"></div>
      <h1 class="headline clip" data-start="0" data-duration="7" data-track-index="1">{esc(fields.get("headline") or "")}</h1>
      <p class="subline clip" data-start="0" data-duration="7" data-track-index="2">{esc(fields.get("subline") or "")}</p>
{cta_clip}"""
        timeline = """      tl.fromTo(".bar", { scaleX: 0, transformOrigin: "left center" }, { scaleX: 1, duration: 0.5, ease: "power3.out" }, 0.2);
      tl.fromTo(".headline", { opacity: 0, y: 60 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.35);
      tl.fromTo(".subline", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.6);
      tl.fromTo(".cta", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.55, ease: "power3.out" }, 0.85);
      tl.to([".bar", ".headline", ".subline", ".cta"], { opacity: 0, duration: 0.5, ease: "power2.in" }, 6.4);"""
        return _hf_page(7.0, accent, clips, timeline)
    if template_id == "captions":
        dur = max(1.0, min(float(source_duration or 0), TEMPLATE_MAX_SOURCE_SECONDS))
        dur = round(dur, 2)
        clips = f"""      <video class="bgvideo clip" data-start="0" data-duration="{dur:g}" data-track-index="0" src="source.mp4" muted playsinline></video>
      <div class="capwrap clip" data-start="0" data-duration="{dur:g}" data-track-index="1">
        <span class="capbar"></span>
        <p class="caption">{esc(fields.get("caption") or "")}</p>
      </div>"""
        timeline = """      tl.fromTo(".capwrap", { opacity: 0, y: 50 }, { opacity: 1, y: 0, duration: 0.45, ease: "back.out(1.8)" }, 0.3);"""
        return _hf_page(dur, accent, clips, timeline)
    if template_id == "sting":
        # Timing follows the motion design tables the wing was
        # built under: a short anticipation hold, a dramatic
        # reveal for the ring, a standard word stagger, and an
        # exit at about seventy percent of the entrance time.
        words = " ".join(
            f'<span class="sting-word" style="display:inline-block;">{esc(w)}</span>'
            for w in (fields.get("headline") or "").split())
        sub = fields.get("subline") or ""
        sub_clip = (
            f"""      <p id="sting-sub" style="position:absolute;left:90px;top:1180px;width:900px;text-align:center;font-size:44px;font-weight:400;color:#b8b8c0;">{esc(sub)}</p>"""
            if sub else "")
        clips = f"""      <div class="clip" data-start="0" data-duration="4" data-track-index="0" style="position:absolute;left:0;top:0;width:1080px;height:1920px;">
        <svg width="1080" height="1920" viewBox="0 0 1080 1920" style="position:absolute;left:0;top:0;">
          <circle id="sting-dot" cx="540" cy="740" r="26" fill="{accent}" style="transform-box:fill-box;transform-origin:center;" />
          <circle id="sting-ring" cx="540" cy="740" r="150" fill="none" stroke="{accent}" stroke-width="10" stroke-linecap="round" stroke-dasharray="943" stroke-dashoffset="943" transform="rotate(-90 540 740)" />
        </svg>
        <h1 id="sting-head" style="position:absolute;left:90px;top:990px;width:900px;text-align:center;font-size:92px;font-weight:800;line-height:1.12;letter-spacing:-0.01em;">{words}</h1>
{sub_clip}
        <div id="sting-sweep" style="position:absolute;top:0;left:0;width:260px;height:1920px;background:linear-gradient(100deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.10) 50%, rgba(255,255,255,0) 100%);"></div>
      </div>"""
        timeline = """      tl.fromTo("#sting-dot", { scale: 0 }, { scale: 1, duration: 0.35, ease: "expo.out" }, 0.1);
      tl.to("#sting-ring", { strokeDashoffset: 0, duration: 0.95, ease: "power2.inOut" }, 0.3);
      tl.fromTo(".sting-word", { opacity: 0, y: 34 }, { opacity: 1, y: 0, duration: 0.5, ease: "expo.out", stagger: 0.08 }, 1.15);
      tl.fromTo("#sting-sub", { opacity: 0, y: 26 }, { opacity: 1, y: 0, duration: 0.45, ease: "expo.out" }, 1.75);
      tl.fromTo("#sting-sweep", { x: -320 }, { x: 1420, duration: 1.1, ease: "sine.inOut" }, 2.1);
      tl.to(["#sting-ring", "#sting-dot", "#sting-head", "#sting-sub"], { opacity: 0, duration: 0.65, ease: "power2.in" }, 3.3);"""
        return _hf_page(4.0, accent, clips, timeline)
    if template_id == "story":
        return _hf_story_page(fields)
    raise ValueError(f"unknown template {template_id}")


def _hf_story_page(fields: dict) -> str:
    """The animated story short's composition: the town scene
    script draws the film on a canvas, the story words ride as
    timed panels over it, and the worker writes the synthesized
    score beside this file as mix.wav before rendering."""
    import html as _html
    import json as _json

    esc = _html.escape
    title = fields.get("title") or ""
    lines = [ln for ln in (fields.get("lines") or "").split("\n") if ln.strip()]
    accent = fields.get("color") or "#6366f1"
    plan = hf_story_plan(title, lines)
    duration = plan[-1]["b"]
    props = {"title": title, "lines": lines, "color": accent,
             "duration": duration, "scenes": plan}
    props_js = _json.dumps(props).replace("</", "<\\/")
    title_span = plan[0]
    outro_span = plan[-1]
    clips = [
        f"""      <canvas id="gl" class="clip" data-start="0" data-duration="{duration:g}" data-track-index="0" width="1080" height="1920" style="position:absolute;left:0;top:0;width:1080px;height:1920px;"></canvas>""",
        f"""      <audio id="mix" class="clip" data-start="0" data-duration="{duration:g}" data-track-index="8" src="mix.wav"></audio>""",
        f"""      <div id="story-title" class="clip" data-start="0" data-duration="{title_span["b"]:g}" data-track-index="2" style="position:absolute;left:0;top:0;width:1080px;height:1920px;opacity:0;">
        <div id="story-bar" style="position:absolute;left:50%;top:796px;width:170px;height:14px;margin-left:-85px;background:{accent};border-radius:7px;"></div>
        <h1 style="position:absolute;left:70px;top:852px;width:940px;text-align:center;font-size:88px;font-weight:800;line-height:1.12;letter-spacing:-0.01em;text-shadow:0 6px 44px rgba(0,0,0,0.55);">{esc(title)}</h1>
      </div>""",
    ]
    timeline = [
        """      tl.fromTo("#story-bar", { scaleX: 0, transformOrigin: "center" }, { scaleX: 1, duration: 0.55, ease: "expo.out" }, 0.3);""",
        """      tl.fromTo("#story-title", { opacity: 0, y: 44 }, { opacity: 1, y: 0, duration: 0.7, ease: "expo.out" }, 0.45);""",
        f"""      tl.to("#story-title", {{ opacity: 0, y: -30, duration: 0.45, ease: "power2.in" }}, {max(0.6, title_span["b"] - 0.5):g});""",
    ]
    for span in plan:
        if span["kind"] != "line":
            continue
        i = span["line"]
        a, b = span["a"], span["b"]
        clips.append(f"""      <div id="story-line-{i}" class="clip" data-start="{a:g}" data-duration="{b - a:g}" data-track-index="3" style="position:absolute;left:64px;bottom:118px;width:952px;opacity:0;background:rgba(10,10,16,0.58);border-radius:30px;padding:38px 44px;">
        <p style="font-size:52px;font-weight:600;line-height:1.32;color:#f7f4ec;text-shadow:0 2px 18px rgba(0,0,0,0.4);">{esc(lines[i])}</p>
      </div>""")
        timeline.append(f"""      tl.fromTo("#story-line-{i}", {{ opacity: 0, y: 46 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: "expo.out" }}, {a + 0.3:g});""")
        timeline.append(f"""      tl.to("#story-line-{i}", {{ opacity: 0, y: -26, duration: 0.45, ease: "power2.in" }}, {b - 0.5:g});""")
    clips.append(f"""      <div id="story-outro" class="clip" data-start="{outro_span["a"]:g}" data-duration="{outro_span["b"] - outro_span["a"]:g}" data-track-index="2" style="position:absolute;left:0;top:0;width:1080px;height:1920px;opacity:0;">
        <h1 style="position:absolute;left:70px;top:826px;width:940px;text-align:center;font-size:64px;font-weight:800;line-height:1.15;text-shadow:0 6px 44px rgba(0,0,0,0.55);">{esc(title)}</h1>
        <p style="position:absolute;left:70px;top:1000px;width:940px;text-align:center;font-size:40px;color:#d8d5cc;">The end</p>
      </div>
      <div id="story-fade" style="position:absolute;left:0;top:0;width:1080px;height:1920px;background:#000000;opacity:0;"></div>""")
    timeline.append(f"""      tl.fromTo("#story-outro", {{ opacity: 0, y: 30 }}, {{ opacity: 1, y: 0, duration: 0.7, ease: "expo.out" }}, {outro_span["a"] + 0.35:g});""")
    timeline.append(f"""      tl.to("#story-fade", {{ opacity: 1, duration: 0.6, ease: "power1.in" }}, {duration - 0.6:g});""")
    clips_js = "\n".join(clips)
    timeline_js = "\n".join(timeline)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #0c0c0f; }}
      #root {{ position: relative; width: 1080px; height: 1920px; background: #0c0c0f;
        font-family: sans-serif; color: #f4f4f1; overflow: hidden; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0"
      data-duration="{duration:g}" data-width="1080" data-height="1920" data-fps="24">
{clips_js}
    </div>
    <script>window.__motion = {props_js};</script>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{timeline_js}
      window.__timelines["main"] = tl;
    </script>
    <script src="motion-scene.js"></script>
  </body>
</html>
"""


def hf_kaggle_push(token: str, script_text: str) -> tuple:
    """Start a worker run: one SaveKernel call against Kaggle's
    public API, pushing a fresh version of the worker kernel,
    which is what makes Kaggle run it. Returns (ok, detail). The
    token rides in the Authorization header only; nothing about
    it is logged here."""
    import requests as _requests

    payload = {
        # The push endpoint's "id" field is the numeric kernel id;
        # a kernel is created and addressed by its string "slug".
        "slug": HF_KERNEL_SLUG,
        "newTitle": "Moonfactory Hyperframes Worker",
        "text": script_text,
        "language": "python",
        "kernelType": "script",
        "isPrivate": True,
        "enableGpu": False,
        "enableTpu": False,
        "enableInternet": True,
        "datasetDataSources": [HF_TOOLCHAIN_DATASET, HF_KEY_DATASET],
        "competitionDataSources": [],
        "kernelDataSources": [],
        "modelDataSources": [],
        "categoryIds": [],
    }
    try:
        resp = _requests.post(
            "https://www.kaggle.com/api/v1/kernels/push",
            headers={"Authorization": f"Bearer {token}"},
            json=payload, timeout=60,
        )
    except Exception as exc:
        return False, f"request failed: {exc}"[:200]
    if resp.status_code != 200:
        return False, f"kaggle answered {resp.status_code}"
    try:
        body = resp.json()
    except Exception:
        body = {}
    if body.get("error"):
        return False, str(body.get("error"))[:200]
    return True, "kernel version pushed"


# Per-model duration bounds in seconds (the global form range is
# 3-30; these narrow it to what each model actually accepts).
MODEL_DURATION_BOUNDS = {
    "wan_motion": (2, 30),
    "seedance": (4, 30),
    "veo3": (4, 8),
    "veo3_fast": (4, 8),
    "kling3": (3, 15),
    "kling4": (3, 30),
    "kling3_omni": (3, 15),
    "seedance2": (4, 15),
    "seedance2_fast": (4, 15),
    "seedance2_mini": (4, 15),
    "veo3_lite": (4, 8),
    "omni12": (3, 15),
    "omni11": (3, 10),
    "minimax_h3": (4, 15),
    # Maker-documented minimums (duration verification,
    # 2026-11-17): Grok Imagine takes 1 to 15 seconds (xAI
    # docs), PixVerse V6 takes 1 to 15, Vidu Q3 takes 1 to 16
    # (Vidu's API model map), Runway Gen-4.5 takes 2 to 10
    # (Runway's guide), Wan 3.0 takes 2 to 30 (Alibaba Model
    # Studio).
    "wan3": (2, 30),
    "grok": (1, 15),
    # Grok Imagine Video 1.5 and 1.5 Lite take 1 to 15 seconds
    # (xAI's docs and both wired routes' pages, read 2026-11-17).
    "grok15": (1, 15),
    "grok15_lite": (1, 15),
    "happyhorse": (3, 15),
    "ltx25": (6, 20),
    "ltx25_fast": (6, 20),
    "vidu_q3": (1, 16),
    "vidu_q4": (3, 16),
    # Vidu Q2 takes 1 to 10 seconds, whole seconds, default 5
    # (Vidu's API model map and Q2 text-to-video reference,
    # read 2026-11-17). It previously fell through to the
    # generic (3, 30) default, which advertised lengths the
    # model cannot make (verification pass, 2026-11-17).
    "vidu_q2": (1, 10),
    "seedance25_flash": (4, 30),
    "pixverse_v6": (1, 15),
    "runway_gen45": (2, 10),
    # Uncensored lanes (2026-11-17), from each route's own
    # published schema or model page.
    "wan22_unc": (5, 5),
    # Wan 3.0 Spicy's own page accepts 2 to 30 whole seconds
    # (drift fix, 2026-11-17 catalog pass: the lane opened
    # at a 3 second minimum its page does not publish).
    "wan3_unc": (2, 30),
    "seedance25_unc": (4, 30),
    # Vidu Q3 Spicy's own page accepts 1 to 16 whole seconds
    # (same drift fix: the lane opened at 3).
    "vidu_q3_unc": (1, 16),
    "minimax_h3_unc": (5, 15),
    "wan26_unc": (5, 15),
    # Wan 2.7 Spicy accepts any whole second from 2 to 15 on
    # the SpicyAPI route it moved to 2026-11-17 (the Wiro
    # route's 5/10/15 steps no longer govern).
    "wan27_unc": (2, 15),
    "wan3pro_unc": (5, 30),
    # SpicyAPI catalog completion (2026-11-17), each from its
    # own model page.
    "wan3prime_unc": (2, 30),
    "wan22_lora_unc": (5, 8),
    "seedance2_unc": (4, 15),
    "seedance2fast_unc": (4, 15),
    "seedance2mini_unc": (4, 15),
    "seedance15pro_unc": (4, 12),
    "ltx23_unc": (3, 20),
    "ltx23_lora_unc": (3, 20),
}
# Veo generates only these clip lengths; anything else is refused
# at validation instead of being silently rounded by the provider.
# Hailuo, Luma and Pika likewise accept only their listed lengths.
MODEL_DURATION_STEPS = {
    "veo3": (4, 6, 8),
    "veo3_fast": (4, 6, 8),
    "veo3_lite": (4, 6, 8),
    "hailuo23": (6, 10),
    "luma_ray32": (5, 10),
    "pika22": (5, 10),
    # LTX takes only the even lengths: fal's LTX API schema
    # enumerates duration as 6, 8, 10, 12, 14, 16, 18, 20
    # (fal's LTX API docs, read 2026-11-17; the maker's facts
    # page caps the family at 20 seconds).
    "ltx25": (6, 8, 10, 12, 14, 16, 18, 20),
    "ltx25_fast": (6, 8, 10, 12, 14, 16, 18, 20),
    # The uncensored builds on the per-run supplier accept only
    # their listed lengths (its published duration options).
    # Wan 2.2 is fixed at 5 seconds in the maker's own
    # documentation (duration verification, 2026-11-17); the
    # supplier build's schema also lists an 8 second option,
    # but the maker cap governs the plan, so longer lengths
    # chain 5 second segments.
    "wan22_unc": (5,),
    "wan26_unc": (5, 10, 15),
    "wan3pro_unc": (5, 10, 15, 20, 25, 30),
    # Wan 2.2 Spicy LoRA accepts exactly 5 or 8 seconds (its
    # own page: anything else is a validation error).
    "wan22_lora_unc": (5, 8),
}
# The length rule (Jack, 2026-11-17, refined the same day):
# the picker lists each model's REAL native lengths, never a
# continuous range. Stepped models list exactly their steps;
# models whose maker accepts any whole second in a range list
# every whole second in it. Nothing is ever trimmed: nothing
# below the model's real minimum, nothing between its steps.
# Past the model's maximum, the universal longer lengths
# (45, 60, 90, 120) are offered on every model, made in native
# segments that sum exactly to the ordered length, each
# starting from the previous segment's last frame, joined into
# one finished video.
# Price follows the generated seconds across all segments (the
# existing per-second rules), and the pre-dispatch ceiling
# check runs on that full cost.
# Past every model's own maximum, these longer lengths are
# offered on every model, made in segments (Jack's length rule,
# refined 2026-11-17).
UNIVERSAL_LONG_LENGTHS = (45, 60, 90, 120)
# Lanes with a usable continuation route for chaining: the
# segment after the first starts from the previous segment's
# last frame, so the lane needs a start-image mode on its wired
# route. Audited 2026-11-17 against every submit function in
# this file: every per-second lane has one (fal image
# endpoints, BeatAPI images, Kie image slugs, CometAPI
# input_reference, PiAPI image_url, OpenRouter image_url,
# Novita i2v, WaveSpeed image paths, Eachlabs image slug,
# Runway image_to_video, the image-only SpicyAPI builds,
# HotAPI image_url, Wiro inputImage).
CHAIN_CONTINUATION_MODELS = PER_SECOND_MODELS

# Per-lane reference caps (images, videos, audios), raised from
# the old flat platform cap to what each model actually supports
# (Jack's reference-caps call, 2026-11-17; no fake caps). Sources,
# read 2026-11-17: Seedance 2.5 takes 30 images / 10 videos / 10
# audios per BytePlus ModelArk's documentation (the 50 Higgsfield
# allows is this model's total); the Seedance 2.0 family takes
# 9 / 3 / 3 on the same documentation; Veo 3.1 and Veo 3.1 Fast
# take up to 3 reference images and no reference video or audio
# (Google's documentation); Kling 4.0 takes 10 images / 5 videos /
# 3 voice clips (Kling's published reference limits); Kling 3.0
# Omni takes 10 / 5 / 5 (Kling's published comparison). Lanes not
# listed keep the platform cap of 10 / 5 / 5: their makers publish
# no higher verified figure for the wired route, so nothing is
# claimed for them. The combined reference-video length cap is
# 15 seconds except on the Seedance 2.5 family, whose docs allow
# 30 seconds combined.
MODEL_REF_CAPS = {
    "seedance": (30, 10, 10),
    "seedance25_flash": (30, 10, 10),
    "seedance2": (9, 3, 3),
    "seedance2_fast": (9, 3, 3),
    "seedance2_mini": (9, 3, 3),
    "veo3": (3, 0, 0),
    "veo3_fast": (3, 0, 0),
    "kling4": (10, 5, 3),
    "kling3_omni": (10, 5, 5),
    # Grok Imagine Video 1.5 reference mode takes up to 7
    # reference images and no reference video or audio (fal's
    # own Grok Imagine page and xAI's docs, read 2026-11-17);
    # the lane is also in IMAGE_REFS_ONLY_MODELS below. Lite
    # takes no references at all (text or one start image only;
    # TEXT_IMAGE_ONLY_MODELS below refuses them first).
    "grok15": (7, 0, 0),
    "grok15_lite": (0, 0, 0),
}
MODEL_REF_VIDEO_SECONDS_CAP = {
    "seedance": 30.0,
    "seedance25_flash": 30.0,
    # Motion transfer's reference video is the product: up to
    # the model's own 30 second driving limit.
    "wan_motion": 30.0,
}


def ref_caps_for(model: str):
    return MODEL_REF_CAPS.get(model, (10, 5, 5))


def _first_image_url(node):
    """First downloadable image URL anywhere in a provider
    payload. The shelf endpoints' response envelopes are
    PAYLOAD-UNVERIFIED, so the scan accepts the common
    shapes: a URL string ending in an image extension, or a
    dict carrying a url with an image content type."""
    if isinstance(node, str):
        if node.startswith("http") and any(
                node.split("?")[0].lower().endswith(ext)
                for ext in (".png", ".jpg", ".jpeg", ".webp")):
            return node
        return None
    if isinstance(node, dict):
        url = node.get("url")
        ctype = str(node.get("content_type") or "")
        if isinstance(url, str) and url.startswith("http") and (
                ctype.startswith("image")
                or any(url.split("?")[0].lower().endswith(ext)
                       for ext in (".png", ".jpg", ".jpeg", ".webp"))):
            return url
        for value in node.values():
            found = _first_image_url(value)
            if found:
                return found
        return None
    if isinstance(node, list):
        for value in node:
            found = _first_image_url(value)
            if found:
                return found
    return None


def _spicyapi_result_url(data):
    """Result URL from a SpicyAPI recordInfo payload. The real
    envelope (verified live 2026-11-17 against real jobs) is
    data.output.assets[]: one presigned URL per asset. The
    Kie-shaped resultJson/resultUrl forms stay as fallbacks.
    The explicit output path matters: an edit job's payload
    also carries the SOURCE image URL under data.input, which
    a blind recursive scan could mistake for the result."""
    output = data.get("output") or {}
    assets = output.get("assets") or []
    for asset in assets:
        if isinstance(asset, dict):
            url = asset.get("url")
            if isinstance(url, str) and url.startswith("http"):
                return url
    raw = data.get("resultJson")
    if raw:
        try:
            urls = (json.loads(raw) or {}).get("resultUrls") or []
            if urls:
                return urls[0]
        except Exception:
            pass
    if isinstance(data.get("resultUrl"), str) and data["resultUrl"]:
        return data["resultUrl"]
    return _first_image_url(output)


def _step_composition(steps: list, total: int):
    """Fewest native steps summing EXACTLY to total, or None.
    Stepped lanes (Veo's 4/6/8, Hailuo's 6/10) may only ever
    deliver lengths their own clips add up to: nothing is
    trimmed, ever (Jack's rule, 2026-11-17)."""
    best = {0: []}
    for amount in range(1, total + 1):
        for step in steps:
            if step <= amount and (amount - step) in best:
                candidate = best[amount - step] + [step]
                if amount not in best or len(candidate) < len(best[amount]):
                    best[amount] = candidate
    return best.get(total)


def native_lengths(model: str) -> list:
    """The model's REAL native lengths, from the maker's own
    documentation (Jack's length rule, refined 2026-11-17):
    stepped models list exactly their steps (Veo makes only 4,
    6 or 8 seconds; Hailuo only 6 or 10; LTX only the even
    lengths to 20); a model whose maker accepts any whole second
    in a range lists every whole second in it (Seedance 2.5:
    4 through 30; Kling 4.0: 3 through 30)."""
    steps = MODEL_DURATION_STEPS.get(model)
    if steps:
        return sorted(steps)
    low, high = MODEL_DURATION_BOUNDS.get(model, (3, 30))
    return list(range(low, high + 1))


def _native_description(model: str) -> str:
    steps = MODEL_DURATION_STEPS.get(model)
    if steps:
        return ("clips of " + ", ".join(f"{s}" for s in sorted(steps))
                + " seconds")
    low, high = MODEL_DURATION_BOUNDS.get(model, (3, 30))
    return f"clips of {low} to {high} seconds"


def duration_plan(model: str, requested: int) -> dict:
    """How a length is produced on a per-second lane: the native
    segment durations to generate, in order. The length must be
    one of the model's real native lengths, or one of the
    universal longer lengths (45, 60, 90, 120) past its maximum,
    which is made in native segments that sum EXACTLY to it and
    joined into one finished video, priced per second across
    the segments. NO TRIMMING, ever: nothing below the model's
    real minimum, nothing between its real steps, and
    generated_seconds always equals the ordered length. Raises
    ValueError for a length the model cannot make."""
    natives = native_lengths(model)
    native_min, native_max = natives[0], natives[-1]
    steps = MODEL_DURATION_STEPS.get(model)
    if requested in natives:
        segments = [requested]
    elif requested in UNIVERSAL_LONG_LENGTHS and requested > native_max:
        if model == "wan_motion":
            # Motion transfer's output follows the driving
            # video's own length (2 to 30 seconds); the lane
            # has no chained lengths (verification pass,
            # 2026-11-17).
            raise ValueError(
                "motion transfer follows the motion video's own "
                "length, up to 30 seconds; it makes no chained "
                "lengths")
        if model not in CHAIN_CONTINUATION_MODELS:
            raise ValueError(
                "this model makes single clips of up to "
                f"{native_max} seconds and has no continuation "
                "route for longer videos")
        if steps:
            segments = _step_composition(natives, requested)
        else:
            import math as _math

            count = _math.ceil(requested / native_max)
            base = requested // count
            extra = requested - base * count
            segments = [base + 1] * extra + [base] * (count - extra)
            if any(s < native_min or s > native_max for s in segments):
                segments = None
        if segments is None:
            raise ValueError(
                f"this model makes {_native_description(model)}; "
                f"{requested} seconds cannot be made from them "
                "exactly")
    elif requested < native_min:
        raise ValueError(
            f"this model's shortest clip is {native_min} seconds")
    elif requested > native_max:
        raise ValueError(
            f"this model makes {_native_description(model)}; "
            "longer videos come in 45, 60, 90 or 120 seconds")
    else:
        raise ValueError(
            f"this model makes {_native_description(model)}; "
            f"{requested} seconds is not one of its lengths")
    generated = sum(segments)
    return {
        "requested": requested,
        "segments": segments,
        "generated_seconds": generated,
        "deliver_seconds": requested,
        "chained": len(segments) > 1,
        "native_min": native_min,
        "native_max": native_max,
    }


def offered_lengths(model: str) -> list:
    """The lengths the picker offers on this model: all of its
    real native lengths, plus the universal longer lengths
    (45, 60, 90, 120) past its maximum that its native segments
    can sum to exactly. On lanes whose steps are all even (the
    Veo family, Hailuo), 45 seconds has no exact composition
    and is not offered: the no-trim rule wins that conflict."""
    out = list(native_lengths(model))
    for length in UNIVERSAL_LONG_LENGTHS:
        try:
            duration_plan(model, length)
            out.append(length)
        except ValueError:
            pass
    # A universal length that is also one of the model's own
    # native lengths appears once.
    return list(dict.fromkeys(out))


def model_display_name(model: dict) -> str:
    """The customer-facing name of one shelf entry: the plain
    model name plus the Uncensored suffix where it applies (the
    $ tier signs ride separately in the config payload)."""
    return model["label"] + (
        " · Uncensored" if model["id"] in UNCENSORED_VIDEO_MODELS
        else "")


def grouped_model_order(models: list, rate_of) -> list:
    """The model menu order (Jack, 2026-11-17): variants of the
    same base model sit together (an uncensored build shares its
    base model's label, so the label is the group key). Groups
    are ordered by their cheapest member's lowest customer sell
    rate, cheapest first; inside a group, alphabetical by
    display name, with the rate as the tiebreak for identical
    names (the house Wan 2.2 and its uncensored API build share
    a display name). Groups with no priced member go last."""
    groups = {}
    for model in models:
        groups.setdefault(model["label"], []).append(model)

    def member_key(model):
        rate = rate_of(model["id"])
        return (
            model_display_name(model),
            rate is None,
            rate if rate is not None else 0.0,
            model["id"],
        )

    def group_key(item):
        label, members = item
        rates = [rate_of(m["id"]) for m in members]
        priced = [r for r in rates if r is not None]
        cheapest = min(priced) if priced else None
        return (cheapest is None,
                cheapest if cheapest is not None else 0.0, label)

    ordered = []
    for _label, members in sorted(groups.items(), key=group_key):
        ordered.extend(sorted(members, key=member_key))
    return ordered
# Models whose supplier endpoint has no reference mode at all:
# they take a prompt or a start image, nothing else. Validation
# refuses reference inputs for them with a clean 422.
TEXT_IMAGE_ONLY_MODELS = (
    "veo3_lite", "hailuo23", "luma_ray32", "ltx25", "ltx25_fast",
    "pika22", "vidu_q2", "vidu_q3", "runway_gen45", "pixverse_v6",
    # Uncensored lanes whose published parameter sets have no
    # reference inputs, only a prompt and a start image (the
    # Wiro Wan 3.0 Pro lane and, since their 2026-11-17 route
    # move, the SpicyAPI Wan 2.6 / 2.7 lanes).
    "wan26_unc", "wan27_unc", "wan3pro_unc",
    # Grok Imagine Video 1.5 Lite: text and image modes only on
    # every route (xAI's docs, read 2026-11-17); no reference
    # mode exists for the model.
    "grok15_lite",
)
# Models that accept references but only still images: their
# supplier schemas have no reference video or audio inputs.
IMAGE_REFS_ONLY_MODELS = ("grok", "grok15", "happyhorse")
FAL_BASE = "https://queue.fal.run"
FAL_ENDPOINTS = {
    ("wan3", "text"): "alibaba/wan-3.0/text-to-video",
    ("wan3", "image"): "alibaba/wan-3.0/image-to-video",
    ("wan3", "reference"): "alibaba/wan-3.0/reference-to-video",
    # Motion transfer (2026-11-17): one endpoint takes the
    # subject image plus the driving video.
    ("wan_motion", "reference"): "fal-ai/wan-motion",
    ("wan_motion", "image"): "fal-ai/wan-motion",
    ("wan3_prime", "text"): "alibaba/wan-3.0-prime/text-to-video",
    ("wan3_prime", "image"): "alibaba/wan-3.0-prime/image-to-video",
    ("wan3_prime", "reference"): "alibaba/wan-3.0-prime/reference-to-video",
    ("veo3", "text"): "fal-ai/veo3.1",
    ("veo3", "image"): "fal-ai/veo3.1/image-to-video",
    ("veo3", "reference"): "fal-ai/veo3.1/reference-to-video",
    ("veo3_fast", "text"): "fal-ai/veo3.1/fast",
    ("veo3_fast", "image"): "fal-ai/veo3.1/fast/image-to-video",
    ("veo3_fast", "reference"): "fal-ai/veo3.1/fast/reference-to-video",
    # Kling 3.0: the resolution picks the tier, Standard at 720p and
    # Pro at 1080p, so these entries are keyed by resolution too.
    ("kling3", "text"): {
        "720p": "fal-ai/kling-video/v3/standard/text-to-video",
        "1080p": "fal-ai/kling-video/v3/pro/text-to-video",
    },
    ("kling3", "image"): {
        "720p": "fal-ai/kling-video/v3/standard/image-to-video",
        "1080p": "fal-ai/kling-video/v3/pro/image-to-video",
    },
    ("kling3", "reference"): {
        "720p": "fal-ai/kling-video/v3/standard/reference-to-video",
        "1080p": "fal-ai/kling-video/v3/pro/reference-to-video",
    },
    # Kling 3.0 Omni: the same resolution-keyed tier shape as
    # Kling 3.0 (Standard at 720p, Pro at 1080p).
    ("kling3_omni", "text"): {
        "720p": "fal-ai/kling-video/o3/standard/text-to-video",
        "1080p": "fal-ai/kling-video/o3/pro/text-to-video",
    },
    ("kling3_omni", "image"): {
        "720p": "fal-ai/kling-video/o3/standard/image-to-video",
        "1080p": "fal-ai/kling-video/o3/pro/image-to-video",
    },
    ("kling3_omni", "reference"): {
        "720p": "fal-ai/kling-video/o3/standard/reference-to-video",
        "1080p": "fal-ai/kling-video/o3/pro/reference-to-video",
    },
    # Veo 3.1 Lite: text and image modes only (validation refuses
    # reference inputs for this model).
    ("veo3_lite", "text"): "fal-ai/veo3.1/lite",
    ("veo3_lite", "image"): "fal-ai/veo3.1/lite/image-to-video",
    # Grok Imagine Video 1.5 Lite (2026-11-17): fal's xAI
    # endpoints; text and image modes only, like Veo Lite above
    # (validation refuses reference inputs for this model).
    ("grok15_lite", "text"): "xai/grok-imagine-video/v1.5/lite/text-to-video",
    ("grok15_lite", "image"): "xai/grok-imagine-video/v1.5/lite/image-to-video",
    # Hailuo 2.3: the tier rides in the path like Kling's; text
    # and image modes only, and the model is silent.
    ("hailuo23", "text"): {
        "720p": "minimax/hailuo-2.3/standard/text-to-video",
        "1080p": "minimax/hailuo-2.3/pro/text-to-video",
    },
    ("hailuo23", "image"): {
        "720p": "minimax/hailuo-2.3/standard/image-to-video",
        "1080p": "minimax/hailuo-2.3/pro/image-to-video",
    },
    ("luma_ray32", "text"): "luma/agent/ray/v3.2/text-to-video",
    ("luma_ray32", "image"): "luma/agent/ray/v3.2/image-to-video",
    ("ltx25", "text"): "lightricks/ltx-2.5/text-to-video",
    ("ltx25", "image"): "lightricks/ltx-2.5/image-to-video",
    ("ltx25_fast", "text"): "lightricks/ltx-2.5/text-to-video/fast",
    ("ltx25_fast", "image"): "lightricks/ltx-2.5/image-to-video/fast",
    ("pika22", "text"): "fal-ai/pika/v2.2/text-to-video",
    ("pika22", "image"): "fal-ai/pika/v2.2/image-to-video",
    # Vidu Q2: wired but price-paused (no published per-second
    # rate in the 2026-11-16 audit, so pricing.py carries no rate
    # for it and the lane stays paused until one is recorded).
    ("vidu_q2", "text"): "fal-ai/vidu/q2/text-to-video",
    ("vidu_q2", "image"): "fal-ai/vidu/q2/image-to-video",
}


def fal_endpoint_for(model: str, mode: str, resolution: str) -> str:
    entry = FAL_ENDPOINTS[(model, mode)]
    if isinstance(entry, dict):
        return entry[resolution]
    return entry


# Second supplier (BeatAPI): an async task API. Create returns
# a task id; the platform polls the task on the same cadence as the
# fal lanes until it succeeds or fails, then downloads the result
# from the task's output. The supplier also offers webhooks, but a
# task stopped by a processing timeout sends no event, so polling
# is the contract's reliable path and the only one wired.
BEATAPI_BASE = "https://api.beatapi.io"
BEATAPI_MODEL_IDS = {
    "seedance": "seedance-2.5",
    "wan3": "wan-3.0",
    "wan3_prime": "wan-3.0-prime",
    "kling3": "kling-v3",
    "kling4": "kling-v4",
    "seedance2": "seedance-2",
    "seedance2_fast": "seedance-2-fast",
    "seedance2_mini": "seedance-2-mini",
    "omni12": "gemini-omni-1.2-flash",
    "omni11": "gemini-omni-1.1-flash",
    "grok": "grok-imagine-video-1.5",
    # The grok15 lane's recorded BeatAPI fallback (2026-11-17):
    # the same supplier model id; Kie stays primary (cheaper).
    "grok15": "grok-imagine-video-1.5",
    "happyhorse": "happyhorse-1.1",
    "minimax_h3": "minimax-h3",
}
# This supplier's model schemas are closed (additionalProperties:
# false): only these models define a generate_audio flag at all.
# Kling 3.0 takes its audio switch under a different name
# ("sound"); every other model on this supplier either always
# includes audio or is silent, and sending an unknown field would
# fail the submit.
BEATAPI_AUDIO_MODELS = ("seedance", "seedance2", "seedance2_fast")
# Resolutions whose value on this supplier differs from the
# platform's resolution name for the same output size.
BEATAPI_RESOLUTION_MAP = {
    "kling3": {"720p": "std", "1080p": "pro"},
    "minimax_h3": {"720p": "768P"},
}
# The one user-facing sentence for any per-second-lane failure:
# provider errors, billing failures, and pauses all surface as this,
# never as a raw provider response.
MODEL_UNAVAILABLE = "this model is temporarily unavailable"

# ---------------------------------------------------------------------------
# Camera presets (workflows/camera-presets/, ported from Freefield's
# Cinema Studio, MIT; see that folder's INDEX.md). The library is
# data: the studio picker is rendered from presets-index.json, never
# hand-built per preset. At job creation the platform composes the
# final prompt as preset recipe text + the user's prompt, in the
# library's composition grammar, and stores both the original prompt
# and the applied preset names on the job record.
#
# Availability: the prompt-recipe presets (mechanism "prompt") run
# today on the Wan 2.2 lanes, and the same composed prompt passes
# through to the per-second lanes. The Fun Camera presets
# (mechanism "workflow_patch") went live 2026-11-16 (Pass 4): the
# chosen pose conditions the worker's Fun Camera render instead of
# adding prompt text. A Fun Camera preset is a single conditioned
# move: it cannot chain with any other move, and it runs on the
# Production lane only (one segment, no end image), enforced in
# validation below. Presets are not a paid feature; free accounts
# may use the prompt recipes on their Draft lane, and Fun Camera
# inherits the Production lane's paid gate.
# ---------------------------------------------------------------------------


def _load_camera_library() -> dict:
    for cand in (
        Path("/platform/presets-index.json"),
        Path(__file__).parent.parent
        / "workflows" / "camera-presets" / "presets-index.json",
    ):
        try:
            data = json.loads(cand.read_text())
            if isinstance(data, dict) and data.get("presets"):
                return data
        except Exception:
            continue
    return {"presets": [], "looks": {}, "composition": {}}


CAMERA_LIBRARY = _load_camera_library()
CAMERA_PRESETS = {p["id"]: p for p in CAMERA_LIBRARY.get("presets") or []}
CAMERA_LOOKS = CAMERA_LIBRARY.get("looks") or {}
CAMERA_MAX_MOVES = int(
    ((CAMERA_LIBRARY.get("composition") or {}).get("moves_join") or {}).get("max")
    or 3
)
LOOK_SLOTS = (
    "tempo", "genre", "era", "camera_body", "lens", "focal",
    "aperture", "lighting", "palette", "emotion",
)


def camera_preset_available(preset: dict) -> bool:
    """Selectable today: any preset whose status is not
    needs_model. The prompt recipes run on both Wan 2.2 lanes; the
    Fun Camera presets (mechanism "workflow_patch") run on the
    Production lane since the model download landed (Pass 4)."""
    return preset.get("status") != "needs_model"


def fun_camera_preset(preset_ids):
    """The single Fun Camera preset in a selection, or None. Fun
    Camera entries carry their pose in patch.value, the exact
    camera_pose string the worker's Fun Camera workflow takes."""
    for pid in preset_ids or []:
        preset = CAMERA_PRESETS.get(pid) or {}
        if preset.get("mechanism") == "workflow_patch":
            return preset
    return None


def validate_camera_selection(body: dict):
    """Validate a job body's camera_presets / camera_look fields.

    Returns (preset_ids, look) with duplicates removed and order
    preserved. Raises ValueError with a user-facing message; job
    creation turns that into a 422 before anything is dispatched.
    """
    raw = body.get("camera_presets") or []
    if not isinstance(raw, list):
        raise ValueError("camera_presets must be a list of preset ids")
    ids = []
    for pid in raw:
        if not isinstance(pid, str) or pid not in CAMERA_PRESETS:
            raise ValueError(f"unknown camera preset: {pid}")
        if not camera_preset_available(CAMERA_PRESETS[pid]):
            raise ValueError(
                f"the {CAMERA_PRESETS[pid]['name']} camera preset is coming soon"
            )
        if pid not in ids:
            ids.append(pid)
    if len(ids) > CAMERA_MAX_MOVES:
        raise ValueError(
            f"at most {CAMERA_MAX_MOVES} camera moves can be chained"
        )
    if fun_camera_preset(ids) is not None and len(ids) > 1:
        # A Fun Camera preset is one conditioned move baked into
        # the render, not prompt text: it cannot chain with prompt
        # recipes or with a second Fun Camera preset.
        raise ValueError(
            "a Fun Camera preset is a single conditioned move and "
            "cannot be combined with other camera moves"
        )
    look_in = body.get("camera_look") or {}
    if not isinstance(look_in, dict):
        raise ValueError("camera_look must be an object of look choices")
    look = {}
    for slot, value in look_in.items():
        if slot not in LOOK_SLOTS or slot not in CAMERA_LOOKS:
            raise ValueError(f"unknown look setting: {slot}")
        if value is None or value == "":
            continue
        if value not in (CAMERA_LOOKS.get(slot) or {}):
            raise ValueError(f"unknown {slot} look: {value}")
        look[slot] = value
    return ids, look


def compose_camera_prompt(scene: str, preset_ids, look: dict) -> str:
    """Compose the final prompt in the library's grammar:

        {moves}{scene}.{tempo} The look of a film{genre}{era}:
        {camera_body}; {lens}; {focal}; {aperture}.
        Lighting: {lighting}. Color: {palette}.

    Every part is optional and contributes nothing when empty. Look
    choices are pills the user actively picked: unlike Freefield's
    studio, nothing is defaulted in silently, so a job with no
    camera selection keeps exactly the prompt the user wrote.
    """
    look = look or {}

    def phrase(slot: str) -> str:
        value = look.get(slot)
        if not value:
            return ""
        return (CAMERA_LOOKS.get(slot) or {}).get(value) or ""

    sentences = []
    fragments = []
    for pid in preset_ids or []:
        frag = ((CAMERA_PRESETS.get(pid) or {}).get("prompt") or {}).get("fragment")
        if frag:
            fragments.append(frag)
    if fragments:
        sentences.append("The camera " + ", then ".join(fragments) + ".")
    scene_clean = (scene or "").strip().rstrip(".").strip()
    scene_sentence = scene_clean + "."
    tempo = phrase("tempo")
    if tempo:
        scene_sentence += tempo
    sentences.append(scene_sentence)
    body_bits = [
        b for b in (
            phrase("camera_body"), phrase("lens"),
            phrase("focal"), phrase("aperture"),
        ) if b
    ]
    if phrase("genre") or phrase("era") or body_bits:
        head = "The look of a film" + phrase("genre") + phrase("era")
        if body_bits:
            head += ": " + "; ".join(body_bits)
        sentences.append(head + ".")
    if phrase("lighting"):
        sentences.append("Lighting: " + phrase("lighting") + ".")
    if phrase("palette"):
        sentences.append("Color: " + phrase("palette") + ".")
    if phrase("emotion"):
        # The performance slot (parity pass, 2026-11-17): the
        # closest equivalent to Cinema Studio's emotion wheel in
        # this prompt grammar, a directed performance note.
        sentences.append(
            "The subject's performance is " + phrase("emotion") + ".")
    return " ".join(sentences).strip()


# ---------------------------------------------------------------------------
# Effects (PARITY-PROGRAM Pass 9, pulled forward): one-tap effects
# on the worker's Wan 2.1 effects lane. The picker is data: this
# table's ids and plain display names, served at /api/effects and
# rendered by the studio, never hand-built per effect in the form.
# The worker owns the per-effect render details; the platform owns
# the gates: effects are paid-only (Production-class pricing, see
# pricing.py), require a start image, take exactly one effect per
# job, and do not combine with chaining, an end image, camera
# presets or looks, or the per-second models. Unknown effects and
# bad combinations are clean 422s before anything is dispatched.
# ---------------------------------------------------------------------------

EFFECTS = {
    "cakeify": "Cakeify",
    "explode": "Explode",
    "inflate": "Inflate",
    "deflate": "Deflate",
    "squish": "Squish",
    "crush": "Hydraulic press",
    "electrify": "Electrify",
    "angry": "Angry face",
    "crying": "Crying",
    "laughing": "Laughing",
    "puppies": "Surrounded by puppies",
    "steam": "Steam from nose and ears",
    "money-rain": "Money rain",
    "rotate": "Rotate 360",
    "orbit-360": "360 orbit",
    "arc-shot": "Arc shot",
    "bullet-time": "Bullet Time",
    "car-chase": "Car chase",
    "crane-down": "Crane down",
    "crane-over-head": "Crane over the head",
    "crane-up": "Crane up",
    "crash-zoom-in": "Crash zoom in",
    "dolly-zoom": "Dolly zoom",
    "hero-run": "Hero run",
}


def _now() -> float:
    return time.time()


def _period() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def _hash_password(password: str) -> str:
    salt = pysecrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"{salt.hex()}${digest.hex()}"


def _check_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=2**14, r=8, p=1, dklen=32,
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def _session_secret() -> str:
    return os.environ.get("SESSION_SECRET", "")


def _make_session(user_id: str) -> str:
    payload = {"uid": user_id, "exp": int(_now()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    import base64

    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(_session_secret().encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


def _read_session(token: str):
    import base64

    try:
        body, sig = token.rsplit(".", 1)
        expect = hmac.new(
            _session_secret().encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("exp", 0) < _now():
            return None
        return payload.get("uid")
    except Exception:
        return None


def _send_email(to: str, subject: str, body: str) -> str:
    """Send through Resend when its key is configured, else SMTP when
    a password is configured, else record the message in the operator
    outbox (visible only with the admin key). Returns the delivery
    mode used."""
    # Primary route: Resend. The API key lives in the deployment
    # secret; the sender is config, so a domain change never touches
    # code. Sending domain: magrify.com, sender noreply@magrify.com
    # (Jack's final pick, 2026-11-16).
    resend_key = os.environ.get("RESEND_API_KEY", "")
    if resend_key:
        try:
            import requests

            resp = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": os.environ.get(
                        "RESEND_FROM", "Moonfactory <noreply@magrify.com>"
                    ),
                    "to": [to],
                    "subject": subject,
                    "text": body,
                },
                timeout=15,
            )
            if resp.status_code < 300:
                return "resend"
        except Exception:
            pass  # fall through to SMTP / outbox
    # Fallback route: PrivateEmail (Namecheap) SMTP. Public server
    # settings are code defaults; the mailbox password is the only
    # value that has to arrive through the deployment secret. Until
    # it is set, or if a send fails, the message goes to the operator
    # outbox so signup never breaks on mail delivery.
    host = os.environ.get("SMTP_HOST", "mail.privateemail.com")
    pw_env_name = "SMTP_" + "PASSWORD"
    password = os.environ.get(pw_env_name, "")
    if host and password:
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["From"] = os.environ.get(
                "SMTP_FROM", "Moonfactory <support@magrify.com>"
            )
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body)
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as smtp:
                if os.environ.get("SMTP_TLS", "1") == "1":
                    smtp.starttls()
                user = os.environ.get("SMTP_USER", "support@magrify.com")
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
            return "smtp"
        except Exception:
            pass  # fall through to the outbox; delivery must not 500 a signup
    items = outbox.get("items") or []
    items.append({"to": to, "subject": subject, "body": body, "at": _now()})
    outbox["items"] = items[-200:]
    return "outbox"


def _rate_limited(bucket: str, ident: str, limit: int, window: int = 60) -> bool:
    key = f"rl:{bucket}:{ident}"
    record = meta.get(key) or {"start": _now(), "count": 0}
    if _now() - record["start"] > window:
        record = {"start": _now(), "count": 0}
    record["count"] += 1
    meta[key] = record
    return record["count"] > limit


# ---------------------------------------------------------------------------
# Bot check: Cloudflare Turnstile when configured, else proof of work.
# ---------------------------------------------------------------------------


def _turnstile_configured() -> bool:
    return bool(os.environ.get("TURNSTILE_SECRET_KEY"))


def _verify_bot_check(req_body: dict, remote_ip: str) -> bool:
    import requests

    # Turnstile when the browser sends a token; the proof-of-work
    # challenge stays accepted as a fallback check (it is also what
    # the end-to-end smoke test uses, since it has no browser).
    token = req_body.get("turnstile_token") or ""
    if _turnstile_configured() and token:
        try:
            resp = requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": os.environ["TURNSTILE_SECRET_KEY"],
                    "response": token,
                    "remoteip": remote_ip,
                },
                timeout=10,
            )
            result = resp.json()
            if not result.get("success"):
                # Name the rejection in the app log (error codes
                # only, never the token): a spent or expired token
                # shows up here as timeout-or-duplicate.
                print(
                    "turnstile siteverify rejected: "
                    f"{result.get('error-codes')}",
                    file=sys.stderr)
            return bool(result.get("success"))
        except Exception:
            return False
    challenge_id = req_body.get("challenge_id") or ""
    nonce = req_body.get("challenge_nonce") or ""
    record = meta.get(f"pow:{challenge_id}")
    if not record or record.get("used") or record["expires"] < _now():
        return False
    digest = hashlib.sha256(f"{record['challenge']}:{nonce}".encode()).hexdigest()
    ok = int(digest, 16) >> (256 - POW_DIFFICULTY_BITS) == 0
    if ok:
        record["used"] = True
        meta[f"pow:{challenge_id}"] = record
    return ok


def _worker_headers() -> dict:
    return {"X-API-Key": os.environ.get("WORKER_API_KEY", "")}


# ---------------------------------------------------------------------------
# The web app
# ---------------------------------------------------------------------------


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("moonfactory-secrets"),
        modal.Secret.from_name("moonfactory-integrations"),
        modal.Secret.from_name("moonfactory-providers"),
        modal.Secret.from_name("moonfactory-kaggle"),
    ],
    volumes={"/worker-models": worker_models, "/data": data_volume},
    timeout=1200,
    max_containers=1,
    scaledown_window=240,
)
@modal.asgi_app()
def api():
    import requests
    from fastapi import Cookie, FastAPI, File, Header, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

    web = FastAPI(title="Moonfactory")

    @web.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    @web.exception_handler(404)
    async def not_found(request: Request, exc):
        # API callers keep the JSON contract; browsers get the
        # on-brand dark page (finish-run polish, 2026-11-16).
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return HTMLResponse(NOT_FOUND_HTML, status_code=404)

    # -- helpers -----------------------------------------------------------

    def base_url(request: Request) -> str:
        return os.environ.get("PUBLIC_BASE_URL") or str(request.base_url).rstrip("/")

    def current_user(request: Request, mf_session: str = Cookie(default="")):
        uid = _read_session(mf_session) if mf_session else None
        if not uid:
            return None
        return users.get(f"id:{uid}")

    def require_user(request: Request, mf_session: str = Cookie(default="")):
        user = current_user(request, mf_session)
        if not user:
            raise HTTPException(status_code=401, detail="sign in required")
        return user

    def save_user(user: dict):
        users[f"id:{user['id']}"] = user
        users[f"email:{user['email']}"] = user

    def usage(user: dict) -> dict:
        # The period rollover below resets only the paid plan's
        # included budget. Free clips are counted on the account's
        # lifetime counter (free_jobs_total) and never reset.
        period = _period()
        u = user.get("usage") or {}
        if u.get("period") != period:
            u = {"period": period, "free_jobs": 0, "included_seconds": 0.0,
                 "included_cost_cents": 0.0}
            user["usage"] = u
            save_user(user)
        return u

    def included_cost_used(user: dict) -> float:
        """Included budget spent this period, in cents of measured
        cost. One budget for every model (Rule 3 amendment): the old
        included-seconds counter is the same accounting denominated
        in production seconds, and accounts that predate the budget
        field migrate from it exactly."""
        u = usage(user)
        if u.get("included_cost_cents") is None:
            u["included_cost_cents"] = (
                float(u.get("included_seconds") or 0.0)
                * pricing.production_cost_per_second()
            )
            user["usage"] = u
            save_user(user)
        return float(u["included_cost_cents"])

    def included_budget_remaining(user: dict) -> float:
        return pricing.included_budget_cents() - included_cost_used(user)

    def is_paid(user: dict) -> bool:
        """Amendment 3 (Jack, 2026-11-16): "paid" means any paying
        customer. Billing is two first-class paths, subscription and
        pay-per-video, so the feature gates (model switching, custom
        audio, gallery opt-out) open for an active subscription OR
        an account that has ever paid in. The included budget itself
        stays a subscription benefit; a pay-per-video account pays
        from balance on every lane."""
        return (
            user.get("plan") == "creator"
            or float(user.get("net_paid_cents") or 0.0) > 0
        )

    def is_owner(user: dict) -> bool:
        """Jack's owner pricing exemption (2026-11-16): the owner
        account (support@magrify.com) is the ONLY account that pays
        no markup. The exemption is keyed on the explicit is_owner
        flag on the account record, set on that one record in the
        users Dict, never on the email string in pricing logic.
        Owner quotes and charges resolve to raw cost on the cheapest
        route via the owner= paths in pricing.py; every other
        account's prices, gates, holds and settle logic are
        untouched."""
        return bool(user and user.get("is_owner"))

    def ceiling_allows(user: dict, worst_case_cost_cents: float) -> bool:
        """Rule 3, Amendment 2: the hard per-account cost ceiling.
        An account is never served more generation cost (settled
        actuals across all lanes) than it has paid in, net of card
        fees. Checked before dispatch with the job's worst-case
        cost, so nothing ever starts that would cross the wall.
        The owner is the one exception (Jack, 2026-11-17): his use
        is not limited by his credits or by what he has paid in,
        only by the provider itself, so the ceiling never blocks
        his account. Every other account keeps the wall exactly."""
        if is_owner(user):
            return True
        net_paid = float(user.get("net_paid_cents") or 0.0)
        settled = float(user.get("cost_settled_cents") or 0.0)
        return settled + float(worst_case_cost_cents) <= net_paid + 1e-9

    # -- per-second lane state ------------------------------------------

    def rate_table() -> dict:
        """The live provider rate table (design s.6): seeded from
        pricing.py's verified rates, refreshed on a schedule by the
        refresh_provider_rates function. A table older than the
        staleness limit pauses the per-second lanes rather than
        quoting from stale prices."""
        table = meta.get("provider_rates")
        if not table or "rates" not in table:
            table = {
                "rates": pricing.PROVIDER_RATES_CENTS_PER_SECOND,
                "updated_at": _now(),
            }
            meta["provider_rates"] = table
            return table
        # A stored table predates any lane added since it was
        # written: seed values fill the gaps per model and
        # resolution, refreshed values always win.
        merged = {
            model: dict(resolutions)
            for model, resolutions in
            pricing.PROVIDER_RATES_CENTS_PER_SECOND.items()
        }
        for model, resolutions in (table.get("rates") or {}).items():
            if isinstance(resolutions, dict):
                merged.setdefault(model, {}).update(resolutions)
        return {"rates": merged, "updated_at": table.get("updated_at")}

    def api_lane_state(model: str) -> dict:
        table = rate_table()
        age = _now() - float(table.get("updated_at") or 0)
        provider = MODEL_PROVIDER.get(model, "fal")
        configured = bool(os.environ.get(
            PROVIDER_KEY_ENV.get(provider, "FAL_KEY")))
        rates = (table.get("rates") or {}).get(model) or {}
        paused = (
            not configured
            or age > pricing.PROVIDER_RATE_STALE_SECONDS
            or not rates
        )
        return {
            "configured": configured,
            "rates_cents_per_second": rates,
            "rates_updated_at": table.get("updated_at"),
            "rates_age_seconds": int(age),
            "paused": paused,
        }

    def free_jobs_total(user: dict) -> int:
        total = user.get("free_jobs_total")
        if total is None:
            # Accounts from before the lifetime counter carry over
            # what the current period record shows; nothing is
            # handed back by the switch.
            total = int((user.get("usage") or {}).get("free_jobs") or 0)
            user["free_jobs_total"] = total
        return int(total)

    def display_handle(user: dict) -> str:
        """The only name the public gallery ever shows. A handle the
        user picked, or a neutral derived one (creator-XXXXXX from the
        account id). Never the email, never a real name."""
        handle = (user.get("handle") or "").strip()
        if handle:
            return handle
        return "creator-" + user["id"][:6]

    def public_user(user: dict) -> dict:
        budget_used = included_cost_used(user)
        return {
            "email": user["email"],
            "verified": user["verified"],
            "plan": user["plan"],
            "paid_customer": is_paid(user),
            # The access model (Jack, 2026-11-16): the free,
            # pre-payment surface is "Try It"; the paid studio is
            # "Create". A paid account's surface is Create and Try
            # It disappears from its navigation entirely; a free
            # account's surface is Try It, and Create is an upgrade
            # prompt. The server gates behind this split are the
            # is_paid checks at job creation (Draft lane only,
            # model switching, custom audio, voiceover, effects,
            # multi-segment, clipping, gallery opt-out).
            "access_surface": "create" if is_paid(user) else "tryit",
            "surface_label": "Create" if is_paid(user) else "Try It",
            "handle": display_handle(user),
            "gallery_opt_out": bool(user.get("gallery_opt_out")),
            "past_due": user.get("past_due", False),
            "balance_cents": user.get("balance_cents", 0),
            "has_saved_card": bool(user.get("stripe_customer_id")
                                   and user.get("stripe_payment_method_id")),
            "auto_reload_enabled": bool((user.get("auto_reload") or {}).get("enabled")),
            "auto_reload_amount_cents": int((user.get("auto_reload") or {}).get("amount_cents") or 0),
            "free_jobs_used": free_jobs_total(user),
            "free_jobs_limit": FREE_CLIPS_TOTAL,
            "default_model": DEFAULT_MODEL,
            "included_seconds_used": round(
                budget_used / pricing.production_cost_per_second(), 2
            ),
            "included_seconds_total": pricing.creator_included_production_seconds(),
            "included_budget_cents_used": round(budget_used, 2),
            "included_budget_cents_total": round(pricing.included_budget_cents(), 2),
            "net_paid_cents": round(float(user.get("net_paid_cents") or 0.0), 2),
            "cost_settled_cents": round(float(user.get("cost_settled_cents") or 0.0), 2),
        }

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")

    # -- pages -------------------------------------------------------------

    @web.get("/", response_class=HTMLResponse)
    def landing(request: Request, mf_session: str = Cookie(default="")):
        # Session-aware (Direction C rebuild, 2026-11-16): a
        # signed-in paid member never sees Try It on the landing;
        # their top block sends them straight to Create. Everyone
        # else gets the Try It framing with the sign-in-first rule.
        user = current_user(request, mf_session)
        top = _LANDING_TOP_MEMBER if (user and is_paid(user)) else _LANDING_TOP_GUEST
        return HTMLResponse(INDEX_HTML.replace("__LANDING_TOP__", top))

    @web.get("/app", response_class=HTMLResponse)
    def studio():
        return HTMLResponse(APP_HTML)

    @web.get("/terms", response_class=HTMLResponse)
    def terms_page():
        return HTMLResponse(TERMS_HTML)

    @web.get("/privacy", response_class=HTMLResponse)
    def privacy_page():
        return HTMLResponse(PRIVACY_HTML)

    @web.get("/support", response_class=HTMLResponse)
    def support_page():
        return HTMLResponse(SUPPORT_HTML)

    @web.get("/help", response_class=HTMLResponse)
    def help_index():
        return HTMLResponse(HELP_INDEX_HTML)

    @web.get("/help/{slug}", response_class=HTMLResponse)
    def help_page(slug: str):
        page = HELP_PAGES.get(slug)
        if not page:
            raise HTTPException(status_code=404, detail="no such help page")
        title, description, body = page
        return HTMLResponse(_info_page(
            f"Moonfactory help: {title}", description,
            body + '\n<p><a href="/help">Back to the help center</a></p>'))

    @web.post("/api/support/contact")
    async def support_contact(request: Request):
        """The help center contact form: email only, delivered to
        support@magrify.com through the same Resend setup as every
        other platform email. A honeypot field and an IP rate
        limit stand in for a captcha on this static page."""
        if _rate_limited("supportcontact", client_ip(request), 5):
            raise HTTPException(status_code=429, detail="too many messages, slow down")
        body = await request.json()
        if body.get("website"):
            return {"status": "sent"}
        email = str(body.get("email") or "").strip()
        message = str(body.get("message") or "").strip()
        if "@" not in email or len(email) > 200 or not message or len(message) > 4000:
            raise HTTPException(
                status_code=422,
                detail="a valid email and a message are required")
        _send_email(
            "support@magrify.com",
            f"Moonfactory help contact from {email}",
            f"From: {email}\n\n{message}")
        return {"status": "sent"}

    @web.get("/robots.txt", response_class=PlainTextResponse)
    def robots_txt(request: Request):
        return (
            "User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: "
            + base_url(request) + "/sitemap.xml\n"
        )

    @web.get("/sitemap.xml")
    def sitemap_xml(request: Request):
        base = base_url(request)
        paths = ["/", "/app", "/gallery", "/terms", "/privacy", "/support",
                 "/help"] + [f"/help/{slug}" for slug in HELP_PAGES]
        body = "".join(f"<url><loc>{base}{x}</loc></url>" for x in paths)
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + body + "</urlset>",
            media_type="application/xml",
        )

    @web.get("/favicon.ico")
    def favicon_ico():
        return Response(content=FAVICON_SVG, media_type="image/svg+xml")

    @web.get("/manifest.webmanifest")
    def manifest():
        return Response(content=MANIFEST_JSON,
                        media_type="application/manifest+json")

    @web.get("/sw.js")
    def service_worker():
        return Response(content=SERVICE_WORKER_JS,
                        media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})

    @web.get("/icons/{name}")
    def icon_file(name: str):
        if name not in ("icon-192.png", "icon-512.png",
                        "icon-maskable-512.png"):
            raise HTTPException(status_code=404, detail="no such icon")
        path = Path("/platform/icons") / name
        if not path.exists():
            path = Path(__file__).parent / "icons" / name
        if not path.exists():
            raise HTTPException(status_code=404, detail="icon missing")
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    @web.get("/healthz")
    def healthz():
        return {"ok": True}

    def model_lowest_sell_rate(model_id: str, table: dict):
        """The model's LOWEST customer sell rate across its
        resolutions, cents per second, from the same live rate
        table /api/pricing serves. This is the basis for both the
        $ tier signs and the dropdown order (cheapest first). The
        house lane is priced per job, so its basis is the
        Production job price spread over the standard 5-second
        segment (99c / 5s = 19.8c/s). None when the lane has no
        customer per-second price (the price-paused lanes)."""
        if model_id in HOUSE_MODEL_PRICING_LANE:
            return pricing.JOB_PRICE_CENTS[
                HOUSE_MODEL_PRICING_LANE[model_id]] / 5.0
        rates = (table.get("rates") or {}).get(model_id) or {}
        sells = []
        for res in rates:
            try:
                sells.append(pricing.frontier_lane(
                    model_id, res, table["rates"]
                )["sell_cents_per_second"])
            except Exception:  # noqa: BLE001 - unpriced combo
                continue
        return min(sells) if sells else None

    def model_price_tier(model_id: str, table: dict):
        """The studio dropdown's price sign for one model: 1-4, or
        None when the lane has no customer per-second price (the
        price-paused lanes, which already read "(unavailable)").
        Basis: model_lowest_sell_rate, the same basis the
        dropdown is sorted by."""
        rate = model_lowest_sell_rate(model_id, table)
        if rate is None:
            return None
        bands = PRICE_TIER_BANDS_CENTS_PER_SECOND
        if rate < bands[0]:
            return 1
        if rate < bands[1]:
            return 2
        if rate < bands[2]:
            return 3
        return 4

    @web.get("/api/config")
    def config():
        tier_table = rate_table()
        # Menu order (Jack, 2026-11-17): the studio dropdown
        # renders the models in config order. Variants of the
        # same base model sit together (the uncensored build
        # next to its base model); groups are ordered by their
        # cheapest member's lowest customer sell rate, the same
        # basis as the $ tier signs; inside a group the order
        # is alphabetical by display name. Unpriced lanes
        # (price-paused) go last. See grouped_model_order.
        def entry_for(m):
            return {
                "id": m["id"],
                "label": m["label"],
                "paid_only": m["paid_only"],
                "uncensored": m["id"] in UNCENSORED_VIDEO_MODELS,
                "price_tier": model_price_tier(m["id"], tier_table),
                "audio": {
                    "native_speech": lane_audio(m["id"])["native_speech"],
                    "native_audio": lane_audio(m["id"])["native_audio"],
                },
                # The model's real reference caps, so the studio
                # shows the selected model's own limits.
                "ref_caps": dict(zip(
                    ("images", "videos", "audios"),
                    ref_caps_for(m["id"]))),
                "ref_video_seconds_cap": MODEL_REF_VIDEO_SECONDS_CAP.get(
                    m["id"], 15.0),
                # The lengths this model can actually make
                # (no-trim rule): the picker offers exactly
                # these, starting at the model's shortest clip.
                "lengths": (
                    offered_lengths(m["id"])
                    if m["id"] in PER_SECOND_MODELS else []),
                "available": (
                    True if m["id"] in HOUSE_MODEL_PRICING_LANE
                    else not api_lane_state(m["id"])["paused"]
                ),
            }

        model_entries = [
            entry_for(m)
            for m in grouped_model_order(
                MODELS,
                lambda mid: model_lowest_sell_rate(mid, tier_table))
        ]
        return {
            "turnstile_site_key": os.environ.get("TURNSTILE_SITE_KEY") or None,
            "pow_required": not _turnstile_configured(),
            "pow_difficulty_bits": POW_DIFFICULTY_BITS,
            "payments_mode": stripe_mode(),
            "payments_configured": stripe_mode() != "off",
            "default_model": DEFAULT_MODEL,
            "house_lanes": {
                "aspects": list(HOUSE_ASPECTS),
                "max_segments": MAX_SEGMENTS,
                "concurrency": {"free": CONCURRENCY_FREE, "paid": CONCURRENCY_PAID},
                "end_frame_lane": "production",
                "text_only_lane": "draft",
            },
            "start_image": {
                "price_cents": pricing.IMAGE_PRICE_CENTS,
                "measured_cost_cents": pricing.IMAGE_MEASURED_COST_CENTS,
                "aspects": list(HOUSE_ASPECTS),
            },
            # The curated image shelf (2026-11-17): the house
            # image lane plus a few provider image models, priced
            # per image. The studio's image mode reads this.
            "image_models": [
                {"id": "house", "label": "Moonfactory Image",
                 "sizes": list(HOUSE_ASPECTS),
                 "size_labels": {
                     a: pricing.image_size_label("house", a)
                     for a in HOUSE_ASPECTS},
                 "paid_only": False, "edit": False},
            ] + [
                {"id": mid, "label": lane["label"],
                 "sizes": list(lane["raw_cents"].keys()),
                 "size_labels": {
                     s: pricing.image_size_label(mid, s)
                     for s in lane["raw_cents"]},
                 "paid_only": True,
                 "edit": bool(lane.get("edit"))}
                for mid, lane in pricing.IMAGE_LANES.items()
            ],
            "effects": {
                "paid_only": True,
                "requires_start_image": True,
                "price_cents": pricing.JOB_PRICE_CENTS["effects"],
                "measured_cost_cents": pricing.MEASURED_COST_CENTS["effects"],
                "items": [
                    {"id": eid, "name": name}
                    for eid, name in EFFECTS.items()
                ],
            },
            "models": model_entries,
            # The Clip picking brain's fallback chain (free
            # OpenRouter models, llm_providers.py) is internal
            # plumbing: it is not served here. Customers pick video
            # models, never text models (Jack, 2026-11-17).
        }

    @web.get("/api/pricing")
    def get_pricing(request: Request, mf_session: str = Cookie(default="")):
        # Anonymous and ordinary sessions see the standard marked-up
        # prices. The owner account's own session sees its at-cost
        # prices (owner pricing exemption, Jack 2026-11-16); the
        # session is resolved optionally, never required, here.
        session_user = current_user(request, mf_session)
        owner = is_owner(session_user)
        data = pricing.public_pricing(owner=owner)
        table = rate_table()
        lanes = {}
        for model in PER_SECOND_MODELS:
            state = api_lane_state(model)
            frontier = {
                res: pricing.frontier_lane(model, res, table["rates"],
                                           owner=owner)
                for res in state["rates_cents_per_second"]
            }
            lanes[model] = {
                "label": MODEL_LABELS[model],
                "rates_cents_per_second": state["rates_cents_per_second"],
                "balance_debit_cents_per_second": {
                    res: math.ceil(frontier[res]["sell_cents_per_second"])
                    for res in frontier
                },
                "sell_cents_per_second": {
                    res: frontier[res]["sell_cents_per_second"]
                    for res in frontier
                },
                "higgsfield_effective_cents_per_second": {
                    res: frontier[res]["higgsfield_effective_cents_per_second"]
                    for res in frontier
                },
                "can_undercut": {
                    res: frontier[res]["can_undercut"]
                    for res in frontier
                },
                "native_speech": lane_audio(model)["native_speech"],
                "native_audio": lane_audio(model)["native_audio"],
                "paused": state["paused"],
                "rates_updated_at": state["rates_updated_at"],
            }
        data["clipping"] = {
            "included_cents_per_source_minute": pricing.CLIP_INCLUDED_CENTS_PER_MINUTE,
            "balance_cents_per_source_minute": pricing.clip_balance_rate_cents(owner),
            "cost_cents_per_source_minute": pricing.CLIP_COST_CENTS_PER_MINUTE,
            "max_source_seconds": pricing.CLIP_MAX_SOURCE_SECONDS,
            "max_upload_bytes": pricing.CLIP_MAX_UPLOAD_BYTES,
            "max_clips_per_job": pricing.CLIP_MAX_CLIPS_PER_JOB,
            "picking_mode": "ai" if os.environ.get("GEMINI_API_KEY") else "loudness",
        }
        data["api_lanes"] = lanes
        data["models"] = [
            {"id": m["id"], "label": m["label"], "paid_only": m["paid_only"]}
            for m in grouped_model_order(
                MODELS,
                lambda mid: model_lowest_sell_rate(mid, table))
        ]
        data["default_model"] = DEFAULT_MODEL
        return data

    @web.get("/api/effects")
    def effects_list():
        """The studio's effects picker, rendered from the EFFECTS
        table, never hand-built per effect. Effects are a paid
        feature and price as a Production segment."""
        return {
            "paid_only": True,
            "requires_start_image": True,
            "price_cents": pricing.JOB_PRICE_CENTS["effects"],
            "effects": [
                {"id": eid, "name": name} for eid, name in EFFECTS.items()
            ],
        }

    @web.get("/api/camera-presets")
    def camera_presets():
        """The studio's camera picker, rendered from the preset
        library data (workflows/camera-presets/presets-index.json).
        Unavailable presets are listed with available=false and a
        plain coming-soon note: shown, never silently dropped."""
        return {
            "max_moves": CAMERA_MAX_MOVES,
            "presets": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "description": p.get("description") or "",
                    "group": p.get("group") or "",
                    "mechanism": p.get("mechanism"),
                    "available": camera_preset_available(p),
                    "unavailable_note": (
                        None if camera_preset_available(p) else "coming soon"
                    ),
                }
                for p in CAMERA_LIBRARY.get("presets") or []
            ],
            "looks": {
                slot: list((CAMERA_LOOKS.get(slot) or {}).keys())
                for slot in LOOK_SLOTS
            },
        }

    @web.get("/api/challenge")
    def challenge(request: Request):
        if _rate_limited("challenge", client_ip(request), 30):
            raise HTTPException(status_code=429, detail="too many requests")
        challenge_id = pysecrets.token_hex(8)
        challenge = pysecrets.token_hex(16)
        meta[f"pow:{challenge_id}"] = {
            "challenge": challenge,
            "expires": _now() + 600,
            "used": False,
        }
        return {"challenge_id": challenge_id, "challenge": challenge,
                "difficulty_bits": POW_DIFFICULTY_BITS}

    # -- accounts ----------------------------------------------------------

    @web.post("/api/signup")
    async def signup(request: Request):
        ip = client_ip(request)
        if _rate_limited("signup", ip, 5):
            raise HTTPException(status_code=429, detail="too many signups, slow down")
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
            raise HTTPException(status_code=422, detail="a valid email is required")
        if len(password) < 8:
            raise HTTPException(status_code=422, detail="password must be at least 8 characters")
        if not _verify_bot_check(body, ip):
            raise HTTPException(status_code=403, detail="bot check failed")
        if users.get(f"email:{email}"):
            raise HTTPException(status_code=409, detail="an account with this email already exists")
        user = {
            "id": pysecrets.token_hex(8),
            "email": email,
            "pw": _hash_password(password),
            "verified": False,
            "created_at": _now(),
            "plan": "free",
            "handle": None,
            "gallery_opt_out": False,
            "past_due": False,
            "balance_cents": 0,
            "net_paid_cents": 0.0,
            "cost_settled_cents": 0.0,
            "free_jobs_total": 0,
            "usage": {"period": _period(), "free_jobs": 0, "included_seconds": 0.0,
                      "included_cost_cents": 0.0},
        }
        save_user(user)
        token = pysecrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        meta[f"verify:{token_hash}"] = {"user_id": user["id"], "expires": _now() + 24 * 3600}
        link = f"{base_url(request)}/api/verify?token={token}"
        mode = _send_email(
            email,
            "Verify your Moonfactory account",
            "Welcome to Moonfactory.\n\n"
            "Open the link below to verify your email.\n"
            f"{link}\n\n"
            "The link works for 24 hours. If you did not sign up, ignore this email.\n",
        )
        return {"status": "pending_verification", "email_mode": mode}

    @web.get("/api/verify")
    def verify(token: str, request: Request):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        record = meta.get(f"verify:{token_hash}")
        if not record or record["expires"] < _now():
            raise HTTPException(status_code=400, detail="this verification link is invalid or expired")
        user = users.get(f"id:{record['user_id']}")
        if not user:
            raise HTTPException(status_code=400, detail="account not found")
        user["verified"] = True
        save_user(user)
        meta[f"verify:{token_hash}"] = {"user_id": record["user_id"], "expires": 0}
        return RedirectResponse(url="/app?verified=1", status_code=303)

    @web.post("/api/login")
    async def login(request: Request):
        ip = client_ip(request)
        if _rate_limited("login", ip, 10):
            raise HTTPException(status_code=429, detail="too many attempts, slow down")
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        user = users.get(f"email:{email}")
        if not user or not _check_password(body.get("password") or "", user["pw"]):
            raise HTTPException(status_code=401, detail="email or password is wrong")
        resp = JSONResponse({"status": "ok", "user": public_user(user)})
        resp.set_cookie(
            "mf_session", _make_session(user["id"]),
            max_age=7 * 24 * 3600, httponly=True, samesite="lax",
        )
        return resp

    @web.post("/api/logout")
    def logout():
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie("mf_session")
        return resp

    @web.get("/api/me")
    def me(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        return public_user(user)

    @web.post("/api/me/handle")
    async def set_handle(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        body = await request.json()
        handle = (body.get("handle") or "").strip()
        if not HANDLE_RE.fullmatch(handle):
            raise HTTPException(
                status_code=422,
                detail="handle must be 3 to 20 characters, using letters, numbers, _ or -",
            )
        for key in list(users.keys()):
            if not key.startswith("id:"):
                continue
            other = users.get(key)
            if (other and other["id"] != user["id"]
                    and (other.get("handle") or "").lower() == handle.lower()):
                raise HTTPException(status_code=409, detail="that handle is taken")
        user["handle"] = handle
        save_user(user)
        return {"handle": handle}

    @web.post("/api/me/gallery-optout")
    async def set_gallery_optout(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        body = await request.json()
        opt_out = bool(body.get("opt_out"))
        if opt_out and not is_paid(user):
            raise HTTPException(
                status_code=403,
                detail="hiding your clips from the gallery is a paid feature",
            )
        user["gallery_opt_out"] = opt_out
        save_user(user)
        return {"gallery_opt_out": opt_out}

    # -- characters ---------------------------------------------------------
    # Persistent character profiles (Jack's scope call,
    # 2026-11-17): a customer saves a character once (a name and
    # reference images) and reuses it across jobs. At job creation
    # the character's first image becomes the start image when the
    # job has none, and its hosted images ride as references on
    # lanes that take references. A character can also be TRAINED
    # into a portable LoRA (the Soul ID equivalent) on fal's
    # portrait trainer; the trained weights URL is stored on the
    # profile. Character images are also uploaded to fal storage
    # at save time so reference lanes can take them by URL.

    MAX_CHARACTERS = 5
    MAX_CHARACTER_IMAGES = 10
    MIN_TRAIN_IMAGES = 10

    def _public_character(character: dict) -> dict:
        return {
            "id": character["id"],
            "name": character["name"],
            "image_count": len(character.get("images") or []),
            "hosted_count": len([
                im for im in character.get("images") or []
                if im.get("fal_url")]),
            "train_status": character.get("train_status") or "none",
            "has_lora": bool(character.get("lora_url")),
            "created_at": character.get("created_at"),
        }

    def _find_character(user: dict, character_id: str):
        for c in user.get("characters") or []:
            if c.get("id") == character_id:
                return c
        return None

    def _refresh_character_training(user: dict, character: dict):
        """Poll a running training job once (on profile reads).
        Completion stores the LoRA weights URL and books the raw
        training cost against the account ceiling; failure
        refunds the charge in full."""
        if character.get("train_status") != "training":
            return
        started = character.get("train_started_at")
        if not started:
            # Trainings submitted before the bound existed carry
            # no stamp; start their clock at first sight rather
            # than failing them over a missing field.
            character["train_started_at"] = _now()
            save_user(user)
            started = character["train_started_at"]
        if _now() - float(started) > TRAINING_POLL_LIMIT_SECONDS:
            # fal training takes minutes, not hours: a job still
            # "training" past the bound is orphaned (its queue
            # answer never parsed, or the job died supplier
            # side). Fail it and refund the charge in full, the
            # same shape as the FAILED branch below.
            character["train_status"] = "failed"
            user["balance_cents"] = int(
                user.get("balance_cents", 0)) + int(
                character.get("train_charge_cents") or 0)
            save_user(user)
            return
        try:
            resp = requests.get(
                character["train_status_url"],
                headers=_fal_headers(), timeout=15)
            if resp.status_code != 200:
                return
            status = (resp.json() or {}).get("status")
            if status == "COMPLETED":
                got = requests.get(
                    character["train_response_url"],
                    headers=_fal_headers(), timeout=30)
                payload = got.json() or {}
                payload = payload.get("data") or payload
                lora_url = None
                stack = [payload]
                while stack and not lora_url:
                    node = stack.pop()
                    if isinstance(node, dict):
                        stack.extend(node.values())
                    elif isinstance(node, list):
                        stack.extend(node)
                    elif isinstance(node, str) and \
                            node.endswith(".safetensors"):
                        lora_url = node
                if lora_url:
                    character["lora_url"] = lora_url
                    character["train_status"] = "ready"
                    user["cost_settled_cents"] = (
                        float(user.get("cost_settled_cents") or 0.0)
                        + pricing.CHARACTER_TRAIN_RAW_CENTS)
                else:
                    character["train_status"] = "failed"
                    user["balance_cents"] = int(
                        user.get("balance_cents", 0)) + int(
                        character.get("train_charge_cents") or 0)
                save_user(user)
            elif status in ("FAILED", "ERROR"):
                character["train_status"] = "failed"
                user["balance_cents"] = int(
                    user.get("balance_cents", 0)) + int(
                    character.get("train_charge_cents") or 0)
                save_user(user)
        except Exception:
            pass

    @web.get("/api/characters")
    def list_characters(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        for c in user.get("characters") or []:
            _refresh_character_training(user, c)
        return {"characters": [
            _public_character(c) for c in user.get("characters") or []]}

    @web.post("/api/characters")
    async def create_character(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if not is_paid(user):
            raise HTTPException(
                status_code=403, detail="characters are a paid feature")
        body = await request.json()
        name = (body.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(status_code=422, detail="a character needs a name")
        images = body.get("images") or []
        if not isinstance(images, list) or not images:
            raise HTTPException(
                status_code=422,
                detail="a character needs at least one reference image")
        if len(images) > MAX_CHARACTER_IMAGES:
            raise HTTPException(
                status_code=422,
                detail=f"a character holds up to {MAX_CHARACTER_IMAGES} images")
        characters = user.get("characters") or []
        if len(characters) >= MAX_CHARACTERS:
            raise HTTPException(
                status_code=422,
                detail=f"an account holds up to {MAX_CHARACTERS} characters")
        stored = []
        for raw in images:
            try:
                _validate_image_b64(raw, "character image")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            data = base64.b64decode(raw)
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            ext = "png" if mime == "image/png" else "jpg"
            hosted = _fal_upload_bytes(
                data, mime,
                f"character-{pysecrets.token_hex(4)}.{ext}")
            stored.append({"b64": raw, "fal_url": hosted})
        character = {
            "id": pysecrets.token_hex(8),
            "name": name,
            "images": stored,
            "lora_url": None,
            "train_status": "none",
            "created_at": _now(),
        }
        characters.append(character)
        user["characters"] = characters
        save_user(user)
        return _public_character(character)

    @web.delete("/api/characters/{character_id}")
    def delete_character(character_id: str, request: Request,
                         mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        characters = user.get("characters") or []
        kept = [c for c in characters if c.get("id") != character_id]
        if len(kept) == len(characters):
            raise HTTPException(status_code=404, detail="unknown character")
        user["characters"] = kept
        save_user(user)
        return {"status": "deleted"}

    @web.post("/api/characters/{character_id}/train")
    def train_character(character_id: str, request: Request,
                        mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if not is_paid(user):
            raise HTTPException(
                status_code=403, detail="characters are a paid feature")
        character = _find_character(user, character_id)
        if not character:
            raise HTTPException(status_code=404, detail="unknown character")
        if character.get("train_status") == "training":
            raise HTTPException(
                status_code=409, detail="this character is already training")
        images = character.get("images") or []
        if len(images) < MIN_TRAIN_IMAGES:
            raise HTTPException(
                status_code=422,
                detail=f"training needs at least {MIN_TRAIN_IMAGES} reference images (this character has {len(images)})")
        if not os.environ.get("FAL_KEY"):
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        charge = int(pricing.CHARACTER_TRAIN_RAW_CENTS) if is_owner(user) \
            else int(pricing.CHARACTER_TRAIN_RAW_CENTS
                     * pricing.FRONTIER_MIN_MARKUP)
        reload_state = {}
        # The owner never balance blocks (Jack, 2026-11-17): the
        # charge below is taken from his balance whatever it holds,
        # and the refund path on a failed submit stays exact.
        if not is_owner(user):
            if int(user.get("balance_cents", 0)) < charge:
                attempt_auto_reload(user, reload_state)
            if int(user.get("balance_cents", 0)) < charge:
                raise HTTPException(
                    status_code=402,
                    detail=reload_fail_detail(reload_state, f"training this character costs {charge} cents of prepaid balance"))
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, im in enumerate(images):
                data = base64.b64decode(im["b64"])
                ext = "png" if data.startswith(b"\x89PNG") else "jpg"
                zf.writestr(f"image_{i + 1:02d}.{ext}", data)
        zip_url = _fal_upload_bytes(
            buffer.getvalue(), "application/zip",
            f"character-{character['id']}.zip")
        if not zip_url:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        user["balance_cents"] = int(user.get("balance_cents", 0)) - charge
        character["train_charge_cents"] = charge
        character["train_status"] = "training"
        character["train_started_at"] = _now()
        save_user(user)
        try:
            resp = requests.post(
                f"https://queue.fal.run/{pricing.CHARACTER_TRAIN_MODEL}",
                headers=_fal_headers(),
                json={
                    "images_data_url": zip_url,
                    "trigger_word": f"mfchar_{character['id'][:8]}",
                    "steps": pricing.CHARACTER_TRAIN_STEPS,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"train submit {resp.status_code}")
            sub = resp.json() or {}
            character["train_status_url"] = sub.get("status_url")
            character["train_response_url"] = sub.get("response_url")
            if not character["train_status_url"]:
                raise RuntimeError("train submit returned no queue urls")
            save_user(user)
        except Exception:
            # Nothing was accepted: refund in full, back to none.
            user = users.get(f"id:{user['id']}") or user
            user["balance_cents"] = int(user.get("balance_cents", 0)) + charge
            character = _find_character(user, character_id) or character
            character["train_status"] = "none"
            character["train_charge_cents"] = 0
            save_user(user)
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        return _public_character(character)

    # -- jobs ---------------------------------------------------------------

    # Holds (design s.3): before a per-second job is dispatched, its
    # spend cap is held against the included budget (in cost cents)
    # or the prepaid balance (in debit cents). Settling charges the
    # actual and returns the difference; failing releases the hold in
    # full. A hold is released or settled exactly once.

    def release_hold(job: dict):
        if job.get("settled") or job.get("hold_released"):
            return
        hold = float(job.get("hold_amount") or 0)
        if hold > 0:
            owner = users.get(f"id:{job['user_id']}")
            if owner:
                if job.get("hold_kind") == "balance":
                    owner["balance_cents"] = int(owner.get("balance_cents", 0)) + int(hold)
                elif job.get("hold_kind") == "included":
                    u = usage(owner)
                    u["included_cost_cents"] = max(
                        0.0, included_cost_used(owner) - hold
                    )
                    owner["usage"] = u
                save_user(owner)
        job["hold_released"] = True
        jobs[job["id"]] = job

    def settle_fal_job(job: dict):
        """Settle a completed per-second job at its actual billed
        seconds, never above the cap the user saw (design s.6), and
        record the Rule 7 numbers on the job."""
        if job.get("settled") or job.get("hold_released"):
            return
        billed = (
            float(job.get("bill_generated_seconds")
                  or job.get("result_duration")
                  or job.get("duration_seconds") or 0)
            + float(job.get("reference_video_seconds") or 0.0)
        )
        table = rate_table()
        raw = pricing.per_second_lane_cost_cents(
            job["model"], job["resolution"], table["rates"]
        ) * billed
        # A supplier that reports its own settled charge (the task
        # API's usage record) is the truest cost there is: it already
        # reflects token-based pricing and reference-video rates the
        # per-second estimate can only approximate. Settle at the
        # reported charge when the job carries one; the cap below
        # still binds, so a bad report can never overcharge the
        # account's budget accounting.
        if job.get("provider_cost_cents") is not None:
            raw = float(job["provider_cost_cents"])
        if job.get("lip_sync"):
            # The built-in lip sync stage is part of this job's
            # cost (planned at creation, folded into the quote and
            # the cap): settle its raw cost on the output seconds,
            # never on reference seconds.
            lip_raw, _lip_debit = pricing.lip_sync_costs(
                billed - float(job.get("reference_video_seconds") or 0.0))
            raw += lip_raw
            job["lipsync_cost_cents"] = round(lip_raw, 2)
        cap = float(job.get("cap_raw_cents") or raw)
        settled_raw = min(raw, cap)
        job["billed_seconds"] = billed
        job["raw_cost_cents"] = round(settled_raw, 2)
        job["over_cap"] = raw > cap
        hold = float(job.get("hold_amount") or 0)
        owner = users.get(f"id:{job['user_id']}")
        if owner:
            if job.get("hold_kind") == "balance":
                # Settle at the Amendment 3 sell rate for the lane,
                # never above the hold the user saw. The owner
                # account settles at raw cost (owner exemption).
                debit = pricing.per_second_balance_debit_cents(
                    job["model"], job["resolution"], billed, table["rates"],
                    owner=bool(owner.get("is_owner")),
                )
                if job.get("lip_sync"):
                    _lr, lip_debit = pricing.lip_sync_costs(
                        billed - float(
                            job.get("reference_video_seconds") or 0.0),
                        owner=bool(owner.get("is_owner")))
                    debit = int(debit) + int(math.ceil(lip_debit))
                debit = min(debit, int(hold))
                refund = int(hold) - debit
                if refund > 0:
                    owner["balance_cents"] = int(owner.get("balance_cents", 0)) + refund
                job["charged_cents"] = debit
            elif job.get("hold_kind") == "included":
                refund = hold - settled_raw
                if refund > 0:
                    u = usage(owner)
                    u["included_cost_cents"] = max(
                        0.0, included_cost_used(owner) - refund
                    )
                    owner["usage"] = u
                job["budget_cost_cents"] = round(settled_raw, 2)
            owner["cost_settled_cents"] = (
                float(owner.get("cost_settled_cents") or 0.0) + settled_raw
            )
            save_user(owner)
        job["settled"] = True
        jobs[job["id"]] = job

    def fail_fal_job(job: dict):
        """Every per-second-lane failure surfaces as the same clean
        sentence; the provider's own words stay in an internal field
        the public job view never exposes."""
        if job.get("chain_results") and not job.get("settled") \
                and not job.get("hold_released"):
            # A chained length that dies after at least one
            # segment landed follows the house lane's Rule 8:
            # the completed segments deliver as one finished
            # (shorter) video, settled on the seconds actually
            # generated; the rest of the hold releases through
            # the settlement.
            done_segs = len(job["chain_results"])
            plan = job.get("chain_plan") or []
            generated = float(sum(plan[:done_segs]))
            job["partial"] = True
            job["status"] = "done"
            job["bill_generated_seconds"] = generated
            job["result_duration"] = generated
            job["segments_done"] = done_segs
            if job.get("chain_cost_seen"):
                job["provider_cost_cents"] = round(
                    float(job.get("chain_cost_cents") or 0), 2)
            jobs[job["id"]] = job
            settle_fal_job(job)
            return
        job["status"] = "error"
        job["error"] = MODEL_UNAVAILABLE
        jobs[job["id"]] = job
        release_hold(job)

    def _fal_headers() -> dict:
        return {
            "Authorization": f"Key {os.environ.get('FAL_KEY', '')}",
            "Content-Type": "application/json",
        }

    def _fal_token(job_id: str) -> str:
        return hmac.new(
            _session_secret().encode(), f"fal:{job_id}".encode(), hashlib.sha256
        ).hexdigest()

    def mirror_fal_status(job: dict) -> dict:
        if job["status"] in ("queued", "running") and _now() - job.get("last_poll", 0) > 8:
            job["last_poll"] = _now()
            jobs[job["id"]] = job
            try:
                resp = requests.get(
                    job["provider_status_url"], headers=_fal_headers(), timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")
                    if status in ("IN_QUEUE", "IN_PROGRESS"):
                        job["status"] = "running"
                        jobs[job["id"]] = job
                    elif status == "COMPLETED":
                        if data.get("error"):
                            job["provider_error"] = str(data.get("error"))[:200]
                            fail_fal_job(job)
                        else:
                            result = requests.get(
                                job["provider_response_url"],
                                headers=_fal_headers(), timeout=30,
                            )
                            payload = None
                            if result.status_code == 200:
                                body = result.json()
                                payload = body.get("data") or body
                            video = (payload or {}).get("video") or {}
                            if video.get("url"):
                                job["bytes"] = video.get("file_size")
                                _poll_done(
                                    job, video["url"],
                                    duration=payload.get("duration"))
                            else:
                                # Result fetch hiccup right after
                                # completion: retry on the next poll,
                                # fail only after many attempts.
                                job["poll_errors"] = int(job.get("poll_errors") or 0) + 1
                                if job["poll_errors"] > 30:
                                    fail_fal_job(job)
                                else:
                                    jobs[job["id"]] = job
                    else:
                        _note_unparsed(job, "fal", status, data)
            except Exception:
                pass
        return job

    def _beatapi_headers() -> dict:
        return {
            "Authorization": f"Bearer {os.environ.get('BEATAPI_KEY', '')}",
            "Content-Type": "application/json",
        }

    def mirror_beatapi_status(job: dict) -> dict:
        """Poll the task API on the same cadence as the fal lanes.
        On success the task carries the result URL, the billed
        output seconds, and the supplier's own settled charge in
        USD; settlement prefers that charge (see settle_fal_job)."""
        if job["status"] in ("queued", "running") and _now() - job.get("last_poll", 0) > 8:
            job["last_poll"] = _now()
            jobs[job["id"]] = job
            try:
                resp = requests.get(
                    f"{BEATAPI_BASE}/v1/tasks/{job['provider_task_id']}",
                    headers=_beatapi_headers(), timeout=15,
                )
                if resp.status_code == 200:
                    task = (resp.json() or {}).get("data") or {}
                    status = task.get("status")
                    if status in ("queued", "processing"):
                        job["status"] = "running"
                        jobs[job["id"]] = job
                    elif status == "succeeded":
                        media = (
                            (task.get("output") or {}).get("media") or []
                        )
                        video = next(
                            (m for m in media if m.get("type") == "video"),
                            media[0] if media else {},
                        )
                        usage = task.get("usage") or {}
                        if video.get("url"):
                            seg_cost = None
                            if usage.get("credits_settled") is not None:
                                seg_cost = round(
                                    float(usage["credits_settled"]) * 100, 2
                                )
                            seg_duration = None
                            if usage.get("billable_duration_seconds"):
                                seg_duration = float(
                                    usage["billable_duration_seconds"]
                                )
                            _poll_done(
                                job, video["url"], cost_cents=seg_cost,
                                duration=seg_duration)
                        else:
                            job["poll_errors"] = int(job.get("poll_errors") or 0) + 1
                            if job["poll_errors"] > 30:
                                fail_fal_job(job)
                            else:
                                jobs[job["id"]] = job
                    elif status == "failed":
                        job["provider_error"] = (
                            f"{task.get('error_code')}: {task.get('error_message')}"
                        )[:200]
                        fail_fal_job(job)
                    else:
                        _note_unparsed(job, "beatapi", status, task)
            except Exception:
                pass
        return job

    def _submit_provider_image(job: dict, prompt: str,
                               source_image: str = None):
        """Submit one shelf image job to its provider, using the
        same transports as the video lanes. Payload shapes are
        the providers' minimal documented forms and are
        PAYLOAD-UNVERIFIED until a real job proves them; on an
        unfunded provider a submit can only fail clean, which
        releases the hold in the caller. Edit lanes pass their
        source image in at submit time only; it is never stored
        on the job."""
        lane = pricing.IMAGE_LANES[job["image_model"]]
        provider = lane["provider"]
        endpoint = lane["endpoint"]
        size = job.get("image_size") or ""
        if provider == "fal":
            payload = {"prompt": prompt, "num_images": 1}
            # Pixel-keyed sizes (GPT Image) go out as the explicit
            # dimensions fal's schema accepts; without this the
            # request silently took fal's landscape default instead
            # of the size the picker sold.
            if "x" in size:
                w, h = size.split("x", 1)
                payload["image_size"] = {"width": int(w),
                                         "height": int(h)}
            resp = requests.post(
                f"https://queue.fal.run/{endpoint}",
                headers=_fal_headers(),
                json=payload,
                timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"image submit http {resp.status_code}")
            sub = resp.json() or {}
            if not sub.get("status_url") or not sub.get("response_url"):
                raise RuntimeError("image submit returned no queue urls")
            job["fal_status_url"] = sub["status_url"]
            job["fal_response_url"] = sub["response_url"]
        elif provider == "beatapi":
            headers = dict(_beatapi_headers())
            headers["Idempotency-Key"] = job["id"]
            resp = requests.post(
                f"{BEATAPI_BASE}/v1/images/tasks",
                headers=headers,
                json={"model": endpoint, "prompt": prompt,
                      "resolution": size},
                timeout=30)
            if resp.status_code not in (200, 201, 202):
                raise RuntimeError(
                    f"image submit http {resp.status_code}")
            data = (resp.json() or {}).get("data") or {}
            task_id = data.get("task_id") or data.get("id")
            if not task_id:
                raise RuntimeError("image submit returned no task id")
            job["provider_task_id"] = task_id
        elif provider == "spicyapi":
            headers = dict(_api_headers("spicyapi"))
            headers["Idempotency-Key"] = job["id"]
            if "qwen-image" in endpoint:
                # Qwen Image Edit: one source image as a public
                # URL plus the edit instruction; that pair is
                # the endpoint's whole input surface. It declares
                # no size fields: the output keeps the source
                # image's size. The upload rides the same media
                # helper the video lanes use for task inputs.
                image_input = {
                    "prompt": prompt,
                    "image_url": _beatapi_media_url(
                        source_image, "edit-source")}
            elif "z-image" in endpoint:
                # Z-Image has no resolution tiers: SpicyAPI takes
                # independent width/height (256-1536 px each on
                # the base tier, 256-2560 on Pro; the picker only
                # offers sizes inside the lane's own range).
                w, h = size.split("x", 1)
                image_input = {"prompt": prompt,
                               "width": int(w), "height": int(h)}
            else:
                # SpicyAPI's Seedream examples use lowercase
                # resolution values ("2k").
                image_input = {"prompt": prompt,
                               "resolution": size.lower()}
            data = _submit_post(
                job, f"{SPICYAPI_BASE}/api/v1/jobs/createTask",
                headers, {"model": endpoint,
                          "input": image_input})
            payload = data.get("data") or {}
            task_id = (payload.get("taskId") or payload.get("task_id")
                       or payload.get("id") or data.get("taskId"))
            if not task_id:
                raise RuntimeError("image submit returned no task id")
            job["provider_task_id"] = task_id
        elif provider == "wiro":
            # Wiro's own quick-start requires a resolution on this
            # model (lowercase values, "1k"/"1.5k"/"2k"); the
            # submit previously sent none at all.
            data = _submit_post(
                job, f"{WIRO_BASE}/v1/Run/{endpoint}",
                _wiro_headers(), {"prompt": prompt,
                                  "resolution": size.lower()})
            task_id = (data.get("taskid") or data.get("taskId")
                       or data.get("id"))
            if not task_id:
                raise RuntimeError("image submit returned no task id")
            job["provider_task_id"] = task_id
            job["provider_socket_token"] = data.get("socketaccesstoken")
        else:
            raise RuntimeError(f"no image route for {provider}")
        jobs[job["id"]] = job

    def _settle_provider_image(job: dict, image_url: str):
        """Download a finished shelf image onto the Volume and
        settle it: the balance was charged at submit (the
        character-training pattern), so settling only books the
        raw provider cost onto the account's ceiling total."""
        resp = requests.get(image_url, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError("finished image download failed")
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FILES_DIR / f"{job['id']}.png"
        out_path.write_bytes(resp.content)
        data_volume.commit()
        job["bytes"] = len(resp.content)
        owner = users.get(f"id:{job['user_id']}")
        if owner:
            owner["cost_settled_cents"] = (
                float(owner.get("cost_settled_cents") or 0.0)
                + float(job.get("image_raw_cents") or 0.0))
            save_user(owner)
        job["status"] = "done"
        job["error"] = None
        jobs[job["id"]] = job

    def _fail_provider_image(job: dict, message: str):
        """A failed shelf image refunds its submit-time charge
        in full: the customer pays only for a delivered image."""
        if not job.get("image_refunded"):
            owner = users.get(f"id:{job['user_id']}")
            charge = int(job.get("image_charge_cents") or 0)
            if owner and charge:
                owner["balance_cents"] = (
                    int(owner.get("balance_cents", 0)) + charge)
                save_user(owner)
            job["image_refunded"] = True
        job["status"] = "error"
        job["error"] = message
        jobs[job["id"]] = job

    def mirror_provider_image(job: dict) -> dict:
        """Poll a shelf image job on the standard cadence. Any
        provider failure refunds the hold in full: the customer
        is charged only for a delivered image."""
        if job["status"] not in ("queued", "running"):
            return job
        if _now() - float(job.get("created_at") or _now()) > 1800:
            # No image model takes half an hour (SpicyAPI's real
            # jobs finish in ~15 seconds). A job still
            # non-terminal here is orphaned: the provider never
            # answered, or its answer never parsed. Fail it clean
            # with a full refund rather than spin forever (the
            # video lanes' _poll_retry_or_fail give-up pattern).
            _fail_provider_image(
                job, "the image took too long, nothing was charged")
            return job
        if _now() - job.get("last_poll", 0) <= 8:
            return job
        job["last_poll"] = _now()
        jobs[job["id"]] = job
        provider = job.get("provider")
        try:
            if provider == "fal":
                st = requests.get(
                    job["fal_status_url"], headers=_fal_headers(),
                    timeout=15)
                if st.status_code == 200:
                    status = (st.json() or {}).get("status")
                    if status == "COMPLETED":
                        got = requests.get(
                            job["fal_response_url"],
                            headers=_fal_headers(), timeout=30)
                        body = got.json() or {}
                        # fal's documented image result location
                        # first (the response payload's images
                        # list); the payload scan stays as a
                        # fallback for shapes like a single
                        # top-level image object.
                        payload = body.get("data") or body
                        url = None
                        images = payload.get("images") or []
                        if images and isinstance(images[0], dict):
                            url = images[0].get("url")
                        if not url:
                            url = _first_image_url(payload)
                        if url:
                            _settle_provider_image(job, url)
                        else:
                            _fail_provider_image(
                                job, "the image came back empty, nothing was charged")
                    elif status in ("FAILED", "ERROR"):
                        _fail_provider_image(
                            job, "the image could not be made, nothing was charged")
                    else:
                        job["status"] = "running"
                        jobs[job["id"]] = job
            elif provider == "beatapi":
                resp = requests.get(
                    f"{BEATAPI_BASE}/v1/tasks/{job['provider_task_id']}",
                    headers=_beatapi_headers(), timeout=15)
                if resp.status_code == 200:
                    task = (resp.json() or {}).get("data") or {}
                    status = task.get("status")
                    if status == "succeeded":
                        # Documented result location first (the
                        # task's output media, the same envelope
                        # the video lanes read); the whole-payload
                        # scan stays as a fallback only, so a URL
                        # echoed anywhere else in the payload can
                        # never be mistaken for the result ahead
                        # of it.
                        url = None
                        media = (
                            (task.get("output") or {}).get("media")
                            or [])
                        for entry in media:
                            if isinstance(entry, dict) \
                                    and entry.get("url"):
                                url = entry["url"]
                                break
                        if not url:
                            url = _first_image_url(task)
                        if url:
                            _settle_provider_image(job, url)
                        else:
                            _fail_provider_image(
                                job, "the image came back empty, nothing was charged")
                    elif status in ("failed", "error", "cancelled"):
                        _fail_provider_image(
                            job, "the image could not be made, nothing was charged")
                    else:
                        job["status"] = "running"
                        jobs[job["id"]] = job
            elif provider == "spicyapi":
                resp = requests.get(
                    f"{SPICYAPI_BASE}/api/v1/jobs/recordInfo",
                    params={"taskId": job["provider_task_id"]},
                    headers=_api_headers("spicyapi"), timeout=15)
                if resp.status_code == 200:
                    data = (resp.json() or {}).get("data") or {}
                    # SpicyAPI's real terminal state is
                    # "succeeded", not the Kie-shaped "success"
                    # this branch was written against (verified
                    # live 2026-11-17: every real job sat at
                    # "succeeded" while this check waited for
                    # "success", so no image job ever resolved).
                    state = str(data.get("state")
                                or data.get("status") or "").lower()
                    if state in ("succeeded", "success"):
                        url = _spicyapi_result_url(data)
                        if url:
                            _settle_provider_image(job, url)
                        else:
                            _fail_provider_image(
                                job, "the image came back empty, nothing was charged")
                    elif state in ("failure", "failed", "fail",
                                   "error", "cancelled", "canceled"):
                        _fail_provider_image(
                            job, "the image could not be made, nothing was charged")
                    else:
                        job["status"] = "running"
                        jobs[job["id"]] = job
            elif provider == "wiro":
                poll_body = (
                    {"tasktoken": job["provider_socket_token"]}
                    if job.get("provider_socket_token")
                    else {"taskid": job["provider_task_id"]})
                resp = requests.post(
                    f"{WIRO_BASE}/v1/Task/Detail",
                    headers=_wiro_headers(), json=poll_body, timeout=15)
                if resp.status_code == 200:
                    data = resp.json() or {}
                    # Wiro's documented Task/Detail envelope, the
                    # same one the video lanes read: the result
                    # rides tasklist[0].outputs, and the task is
                    # finished only at task_postprocess_end, with
                    # pexit "0" for success. The old branch
                    # scanned the whole payload for any image URL
                    # and settled on sight, without ever reading
                    # the task state: the SpicyAPI loose-scan
                    # trap, on the one image lane that had it.
                    tasks = data.get("tasklist") or []
                    task = tasks[0] if tasks else {}
                    state = str(task.get("status") or "")
                    if state == "task_postprocess_end":
                        if str(task.get("pexit")) == "0":
                            outputs = task.get("outputs") or []
                            first = outputs[0] if outputs else {}
                            url = first.get("url") if isinstance(
                                first, dict) else None
                            if url:
                                _settle_provider_image(job, url)
                            else:
                                _fail_provider_image(
                                    job, "the image came back empty, nothing was charged")
                        else:
                            _fail_provider_image(
                                job, "the image could not be made, nothing was charged")
                    elif state == "task_cancel":
                        _fail_provider_image(
                            job, "the image could not be made, nothing was charged")
                    else:
                        job["status"] = "running"
                        jobs[job["id"]] = job
        except Exception:
            pass
        return job

    def mirror_image_status(job: dict) -> dict:
        """Start-image jobs (Pass 3) poll the worker's image
        endpoint. A failed image job restores everything it held
        (Rule 8): the balance charge returns, the included-budget
        debit returns, and the dispatch-time settled cost comes
        back off the account's ceiling total."""
        if job["status"] in ("queued", "running") and _now() - job.get("last_poll", 0) > 8:
            try:
                resp = requests.get(
                    f"{WORKER_BASE}/image-jobs/{job['worker_job_id']}",
                    headers=_worker_headers(), timeout=15,
                )
                if resp.status_code == 200:
                    remote = resp.json()
                    job["status"] = remote.get("status", job["status"])
                    job["bytes"] = remote.get("bytes")
                    if remote.get("gpu_seconds") is not None:
                        job["gpu_seconds"] = remote.get("gpu_seconds")
                    if job["status"] == "error":
                        job["internal_error"] = remote.get("error")
                        job["error"] = (
                            "the first frame could not be made, nothing was charged"
                        )
                        _restore_house_image(job)
            except Exception:
                pass
            job["last_poll"] = _now()
            jobs[job["id"]] = job
        return job

    def mirror_status(job: dict) -> dict:
        if job.get("provider") == "clip":
            # Clip jobs run inside process_clip_job, whose runner
            # carries a 3600 second function timeout. A clip job
            # still marked running past the bound below is dead:
            # the runner was killed before its own failure path
            # could release the hold. Fail it here and release
            # the hold in full.
            if job.get("kind") == "clip_job" and \
                    job["status"] in ("queued", "running") and \
                    _now() - float(job.get("created_at") or _now()) \
                    > CLIP_POLL_LIMIT_SECONDS:
                job["status"] = "error"
                job["error"] = "clipping failed, nothing was charged"
                job["internal_error"] = (
                    "clip job passed its runner time limit")
                jobs[job["id"]] = job
                _clip_release_hold(job)
            return job
        if job.get("provider") == "hyperframes":
            # Template jobs are worked by the Kaggle kernel,
            # which drives status through the worker endpoints.
            # The mirror only enforces the lane's bound (a cold
            # start plus a render fits inside it; past it the job
            # is dead and fails clean, at zero charge there is
            # nothing to refund) and nudges a kernel awake when a
            # queued job has none.
            if job.get("kind") == "template" and \
                    job["status"] in ("queued", "running") and \
                    _now() - float(job.get("created_at") or _now()) \
                    > TEMPLATE_POLL_LIMIT_SECONDS:
                job["status"] = "error"
                job["error"] = "the free render took too long, nothing was charged"
                job["internal_error"] = (
                    "template job passed its poll time limit")
                jobs[job["id"]] = job
            elif job.get("kind") == "template" and \
                    job["status"] == "queued":
                _hf_maybe_trigger()
            return job
        if job.get("provider") == "shorts":
            # Short jobs run inside process_short_job, whose
            # runner carries a 3600 second function timeout like
            # the clip runner. A short still marked running past
            # the bound below is dead: fail it clean. The lane is
            # free, so there is no hold to release and nothing to
            # refund.
            if job.get("kind") == "short" and \
                    job["status"] in ("queued", "running") and \
                    _now() - float(job.get("created_at") or _now()) \
                    > SHORT_POLL_LIMIT_SECONDS:
                job["status"] = "error"
                job["error"] = "the short took too long, nothing was charged"
                job["internal_error"] = (
                    "short job passed its runner time limit")
                job["stage"] = None
                jobs[job["id"]] = job
            return job
        if job["status"] in ("queued", "running") and \
                _now() - float(job.get("created_at") or _now()) \
                > _poll_limit_for(job):
            # The universal bound: whatever the mirrors below
            # make of a provider's answers, no job stays in a
            # running state past its family's poll lifetime.
            _fail_stale_job(job)
            return job
        if job.get("kind") == "image" and job.get("image_model"):
            return mirror_provider_image(job)
        if job.get("kind") == "image":
            return mirror_image_status(job)
        if job.get("provider") == "fal":
            return mirror_fal_status(job)
        if job.get("provider") == "beatapi":
            return mirror_beatapi_status(job)
        if job.get("provider") in API_POLL_PROVIDERS:
            return mirror_api_status(job)
        if job["status"] in ("queued", "running") and _now() - job.get("last_poll", 0) > 8:
            try:
                resp = requests.get(
                    f"{WORKER_BASE}/jobs/{job['worker_job_id']}",
                    headers=_worker_headers(), timeout=15,
                )
                if resp.status_code == 200:
                    remote = resp.json()
                    job["status"] = remote.get("status", job["status"])
                    job["error"] = remote.get("error")
                    job["bytes"] = remote.get("bytes")
                    if remote.get("segments_done") is not None:
                        job["segments_done"] = remote.get("segments_done")
                    if remote.get("delivered_seconds") is not None:
                        job["delivered_seconds"] = remote.get("delivered_seconds")
                    if remote.get("partial"):
                        job["partial"] = True
                        job["partial_error"] = remote.get("partial_error")
                    if (job["status"] == "done" and job.get("partial")
                            and not job.get("partial_settled")):
                        # Rule 8, stated behavior for a mid-chain
                        # failure: the completed segments deliver as
                        # one finished (shorter) video and stay
                        # charged per segment; the segments that
                        # never rendered are refunded in full (the
                        # balance share returns, and the settled
                        # cost comes back off the ceiling total).
                        job["partial_settled"] = True
                        total = int(job.get("segments_total") or 1)
                        done_segs = int(job.get("segments_done") or 0)
                        missing = max(0, total - done_segs)
                        if missing and job.get("charged_cents"):
                            owner = users.get(f"id:{job['user_id']}")
                            # Refund at the rate the job was charged:
                            # the owner was charged raw cost per
                            # segment, everyone else the job price.
                            per_seg = pricing.job_price_cents(
                                job["lane"],
                                owner=bool(owner and owner.get("is_owner")),
                            )
                            refund = per_seg * missing
                            if owner:
                                owner["balance_cents"] = (
                                    owner.get("balance_cents", 0) + refund)
                                save_user(owner)
                            job["charged_cents"] -= refund
                        if missing and job.get("included_cost_cents"):
                            per_seg_budget = float(
                                pricing.MEASURED_COST_CENTS["production"])
                            owner = users.get(f"id:{job['user_id']}")
                            if owner:
                                u = usage(owner)
                                u["included_cost_cents"] = max(
                                    0.0, included_cost_used(owner)
                                    - per_seg_budget * missing)
                                owner["usage"] = u
                                save_user(owner)
                            job["included_cost_cents"] = (
                                float(job["included_cost_cents"])
                                - per_seg_budget * missing)
                        if missing and job.get("cost_cents"):
                            per_seg_cost = float(
                                pricing.MEASURED_COST_CENTS[job["lane"]])
                            owner = users.get(f"id:{job['user_id']}")
                            if owner:
                                owner["cost_settled_cents"] = max(
                                    0.0,
                                    float(owner.get("cost_settled_cents") or 0.0)
                                    - per_seg_cost * missing)
                                save_user(owner)
                            job["cost_cents"] = (
                                float(job["cost_cents"]) - per_seg_cost * missing)
                    if job["status"] == "error" and job.get("charged_cents"):
                        # Rule 8: a failed generation restores the balance.
                        owner = users.get(f"id:{job['user_id']}")
                        if owner and not job.get("refunded"):
                            owner["balance_cents"] = owner.get("balance_cents", 0) + job["charged_cents"]
                            job["refunded"] = True
                            save_user(owner)
                    if job["status"] == "error" and job.get("cost_cents") and not job.get("cost_released"):
                        # The ceiling counts settled cost; a failed
                        # job settles nothing, so its dispatch-time
                        # cost comes back off the account total.
                        owner = users.get(f"id:{job['user_id']}")
                        if owner:
                            owner["cost_settled_cents"] = max(
                                0.0,
                                float(owner.get("cost_settled_cents") or 0.0)
                                - float(job["cost_cents"]),
                            )
                            job["cost_released"] = True
                            save_user(owner)
            except Exception:
                pass
            job["last_poll"] = _now()
            jobs[job["id"]] = job
        return job

    def public_job(job: dict, queue_position=None) -> dict:
        return {
            "id": job["id"],
            "lane": job["lane"],
            "model": (
                "First frame" if job.get("kind") == "image"
                else "Clip" if job.get("kind") in ("clip_job", "clip_output")
                else (job.get("template_name") or "Title card")
                if job.get("kind") == "template"
                else "Faceless short" if job.get("kind") == "short"
                else "Effects" if job.get("effect")
                else MODEL_LABELS.get(job.get("model") or DEFAULT_MODEL, "Wan 2.2")
            ),
            "model_id": job.get("model") or DEFAULT_MODEL,
            "duration_seconds": job.get("duration_seconds"),
            "resolution": job.get("resolution"),
            "prompt": job["prompt"],
            "user_prompt": job.get("user_prompt") or job["prompt"],
            "camera_presets": job.get("camera_preset_names") or [],
            "camera_preset_ids": job.get("camera_presets") or [],
            "camera_look": job.get("camera_look") or {},
            "effect": job.get("effect_name"),
            "effect_id": job.get("effect"),
            "status": job["status"],
            "error": job.get("error"),
            "created_at": job["created_at"],
            "charged_cents": job.get("charged_cents", 0),
            "included_cost_cents": (
                job.get("included_cost_cents")
                or job.get("budget_cost_cents") or 0
            ),
            "free_tier": bool(job.get("free_tier")),
            "has_file": job["status"] == "done",
            "has_custom_audio": bool(job.get("music_prompt")),
            "has_speech_script": bool(job.get("speech_script")),
            "voiceover_status": job.get("voiceover_status"),
            "lipsync_status": job.get("lipsync_status"),
            "word_check_status": job.get("word_check_status"),
            "word_check_listen_at": job.get("word_check_listen_at") or [],
            "captions": bool(job.get("captions")),
            "captions_status": job.get("captions_status"),
            "native_speech": lane_audio(
                job.get("model") or DEFAULT_MODEL)["native_speech"],
            "kind": job.get("kind") or "generation",
            "stage": job.get("stage"),
            "hook": job.get("hook"),
            "score": job.get("score"),
            "source_minutes": job.get("source_minutes"),
            "clip_ids": job.get("clip_ids") or [],
            "has_thumb": bool(job.get("has_thumb")),
            "pick_mode": job.get("pick_mode"),
            "aspect_ratio": job.get("aspect_ratio") or "9:16",
            "segments_total": int(job.get("segments_total") or 1),
            "segments_done": int(job.get("segments_done") or 0),
            "partial": bool(job.get("partial")),
            "has_end_frame": bool(job.get("has_end_frame")),
            "queue_position": queue_position,
        }

    def _api_job_params(body: dict, model: str = "") -> dict:
        """Validate a per-second-lane job's shape once, for both the
        quote and the create path, so the two can never disagree.
        Duration and resolution limits are per model: each lane
        refuses what its model cannot actually produce instead of
        letting the provider silently change the order."""
        try:
            duration = int(body.get("duration_seconds") or 5)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="duration must be a whole number of seconds")
        # The length menu (Jack, 2026-11-17, refined the same
        # day): the picker lists each model's real native
        # lengths. duration_plan maps the request onto native
        # segments (one native generation for a native length,
        # exact native segments summing to a universal longer
        # length past the maximum; nothing below the model's
        # minimum, nothing between its steps, nothing trimmed,
        # ever). params["duration"] below is the FIRST
        # segment's native duration, the only duration a single
        # provider submission ever sees; the plan rides along
        # for pricing and for the chain driver.
        if model == "wan_motion":
            # Motion transfer's length comes from the driving
            # video, not the length menu; the real plan is built
            # in the motion branch below.
            plan = {"segments": [duration],
                    "generated_seconds": float(duration), "trim": False}
        else:
            try:
                plan = duration_plan(model, duration)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        resolution = body.get("resolution") or "720p"
        allowed_res = pricing.MODEL_RESOLUTIONS.get(model, pricing.RESOLUTIONS)
        if resolution not in allowed_res:
            raise HTTPException(
                status_code=422,
                detail="resolution for this model must be one of "
                + ", ".join(allowed_res),
            )
        ref_images = body.get("reference_image_urls") or []
        ref_videos = body.get("reference_video_urls") or []
        ref_audios = body.get("reference_audio_urls") or []
        if not all(isinstance(x, list) for x in (ref_images, ref_videos, ref_audios)):
            raise HTTPException(status_code=422, detail="reference inputs must be lists of URLs")
        ref_video_seconds = 0.0
        if ref_videos:
            try:
                ref_video_seconds = float(body.get("reference_video_seconds") or 0)
            except (TypeError, ValueError):
                ref_video_seconds = 0.0
            seconds_cap = MODEL_REF_VIDEO_SECONDS_CAP.get(model, 15.0)
            if ref_video_seconds <= 0 or ref_video_seconds > seconds_cap:
                raise HTTPException(
                    status_code=422,
                    detail=f"reference_video_seconds (the total length of the reference videos, up to {int(seconds_cap)}) is required: it is part of the price",
                )
        start_image = body.get("start_image_png_b64") or None
        end_image = body.get("end_image_png_b64") or None
        if model in ("veo3", "veo3_fast", "veo3_lite") and (ref_videos or ref_audios):
            raise HTTPException(
                status_code=422,
                detail="this model takes image references only",
            )
        if model in TEXT_IMAGE_ONLY_MODELS and (ref_images or ref_videos or ref_audios):
            raise HTTPException(
                status_code=422,
                detail="this model takes a prompt or a start image only",
            )
        if model == "grok15_lite" and end_image:
            # Lite takes no end frame on any route (xAI's docs,
            # read 2026-11-17); refused rather than silently
            # dropped, like the motion lane's end frame above.
            raise HTTPException(
                status_code=422,
                detail="this model takes no end frame",
            )
        if model in IMAGE_REFS_ONLY_MODELS and (ref_videos or ref_audios):
            raise HTTPException(
                status_code=422,
                detail="this model takes image references only",
            )
        if model == "kling3" and (ref_images or ref_videos or ref_audios):
            # Kling 3.0's primary route has no confirmed reference
            # mode; reference media belongs to the Omni lane.
            # Refused here rather than silently dropped.
            raise HTTPException(
                status_code=422,
                detail="this model takes a prompt or a start image only",
            )
        if model == "omni12" and ref_videos and not ref_images:
            # The previous route rejected reference videos
            # submitted without at least one reference image; the
            # current route's behavior is unconfirmed, so the
            # refusal stays rather than risk a silent drop.
            raise HTTPException(
                status_code=422,
                detail="reference videos on this model need a reference image too",
            )
        if ref_images or ref_videos or ref_audios:
            mode = "reference"
        elif start_image:
            mode = "image"
        else:
            mode = "text"
        if model == "vidu_q4" and mode == "text":
            # Vidu Q4 has no text-to-video mode on any published
            # route; it animates a start image or references.
            raise HTTPException(
                status_code=422,
                detail="this model needs a start image or reference images",
            )
        if model in ("wan22_unc", "wan3_unc", "seedance25_unc",
                     "vidu_q3_unc", "wan3prime_unc", "wan22_lora_unc",
                     "seedance2_unc", "seedance2fast_unc",
                     "seedance2mini_unc", "seedance15pro_unc",
                     "ltx23_unc", "ltx23_lora_unc", "wan26_unc",
                     "wan27_unc") and mode != "image":
            # The uncensored builds' wired supplier publishes an
            # image-to-video endpoint only for them (its catalog,
            # read 2026-11-17): no text or reference mode exists
            # to send a job to.
            raise HTTPException(
                status_code=422,
                detail="this model needs a start image",
            )
        if model == "wan_motion":
            # Motion transfer (2026-11-17): the subject is the
            # start image and the motion comes from exactly one
            # reference video. The output follows the driving
            # video's length, so the plan is a single segment of
            # that length, and the reference seconds are not
            # billed on top: the output IS the reference length.
            if not start_image:
                raise HTTPException(
                    status_code=422,
                    detail="motion transfer needs a subject image")
            if end_image:
                raise HTTPException(
                    status_code=422,
                    detail="motion transfer takes no end frame")
            if len(ref_videos) != 1:
                raise HTTPException(
                    status_code=422,
                    detail="motion transfer needs exactly one motion reference video")
            if ref_images or ref_audios:
                raise HTTPException(
                    status_code=422,
                    detail="motion transfer takes no other references")
            driving = ref_video_seconds or float(duration)
            if driving < 2 or driving > 30:
                raise HTTPException(
                    status_code=422,
                    detail="the motion video must be 2 to 30 seconds long")
            duration = int(round(driving))
            plan = {"segments": [duration],
                    "generated_seconds": float(duration), "trim": False}
            ref_video_seconds = 0.0
        cap_images, cap_videos, cap_audios = ref_caps_for(model)
        if (len(ref_images) > cap_images or len(ref_videos) > cap_videos
                or len(ref_audios) > cap_audios):
            raise HTTPException(
                status_code=422,
                detail=(f"this model takes up to {cap_images} reference "
                        f"images, {cap_videos} reference videos and "
                        f"{cap_audios} reference audio files"))
        aspect_ratio = body.get("aspect_ratio") or "9:16"
        if aspect_ratio not in HOUSE_ASPECTS:
            raise HTTPException(
                status_code=422,
                detail="aspect_ratio must be one of "
                + ", ".join(HOUSE_ASPECTS))
        return {
            "duration": plan["segments"][0],
            "requested_duration": duration,
            "generated_seconds": plan["generated_seconds"],
            "plan_segments": plan["segments"],
            "plan_trim": False,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "reference_image_urls": ref_images,
            "reference_video_urls": ref_videos,
            "reference_audio_urls": ref_audios,
            "reference_video_seconds": ref_video_seconds,
            "ref_caps": {
                "images": cap_images, "videos": cap_videos,
                "audios": cap_audios,
            },
            "speech_script": (body.get("speech_script") or "").strip(),
            "start_image": start_image,
            "end_image": end_image,
        }

    def _quote_for(user: dict, model: str, params: dict) -> dict:
        """The exact quote for a per-second job (design s.3 step 1,
        s.5): the included-budget debit or the balance charge, and
        which one this account would actually pay, before anything
        runs. The ceiling is reported too, so a refusal is never a
        surprise at create time."""
        state = api_lane_state(model)
        prov = provider_for(model, params["resolution"])
        if not os.environ.get(PROVIDER_KEY_ENV.get(prov, "FAL_KEY")):
            # The supplier serving this resolution has no key in
            # the secret store: the lane cannot dispatch, so the
            # quote reports the pause instead of a price.
            state = {**state, "paused": True}
        if state["paused"]:
            # A paused lane has no live quote (a price-paused model
            # has no rates at all, and estimating one would raise
            # instead of answering). Report the pause plainly.
            return {
                "model": model,
                "model_label": MODEL_LABELS[model],
                "duration_seconds": params["requested_duration"],
                "resolution": params["resolution"],
                "billed_seconds": float(params["generated_seconds"]),
                "raw_cost_cents": 0.0,
                "balance_debit_cents": 0,
                "cap_cents": 0.0,
                "lane_paused": True,
                "ceiling_ok": False,
                "audio": lane_audio(model),
                "payment": {"method": "unavailable"},
            }
        # The estimate prices the GENERATED seconds: every
        # segment of a chained length. Generated seconds always
        # equal the ordered length (no trimming, ever), and the
        # ceiling check and the hold therefore cover the whole
        # job, never just its first segment.
        est = pricing.per_second_estimate(
            model, params["resolution"], params["generated_seconds"],
            params["reference_video_seconds"], rate_table()["rates"],
            owner=is_owner(user),
            lip_sync=lip_sync_applies(model, params.get("speech_script")),
        )
        remaining = included_budget_remaining(user)
        ceiling_ok = ceiling_allows(user, est["cap_raw_cents"])
        # The included budget is a subscription benefit (Amendment
        # 3): pay-per-video accounts always pay these lanes from
        # balance, even though the budget formula is global.
        if user["plan"] == "creator" and remaining >= est["cap_raw_cents"]:
            payment = {
                "method": "included",
                "included_debit_cents": round(est["raw_cost_cents"], 2),
                "included_budget_remaining_cents": round(remaining, 2),
            }
        elif int(user.get("balance_cents", 0)) >= est["cap_balance_cents"] \
                or is_owner(user):
            # The owner pays from balance even when the balance
            # cannot cover it (Jack, 2026-11-17): create takes the
            # hold anyway, so the quote names the rail he will
            # actually use instead of calling him insufficient.
            payment = {
                "method": "balance",
                "charge_cents": est["balance_debit_cents"],
            }
        else:
            payment = {
                "method": "insufficient",
                "charge_cents": est["balance_debit_cents"],
                "balance_cents": int(user.get("balance_cents", 0)),
            }
        return {
            "model": model,
            "model_label": MODEL_LABELS[model],
            "duration_seconds": params["requested_duration"],
            "generated_seconds": params["generated_seconds"],
            "segments": len(params["plan_segments"]),
            "resolution": params["resolution"],
            "aspect_ratio": params["aspect_ratio"],
            "billed_seconds": est["billed_seconds"],
            "raw_cost_cents": round(est["raw_cost_cents"], 2),
            "balance_debit_cents": est["balance_debit_cents"],
            "cap_cents": round(est["cap_raw_cents"], 2),
            "lane_paused": state["paused"],
            "ceiling_ok": ceiling_ok,
            "audio": lane_audio(model),
            "payment": payment,
        }

    @web.post("/api/jobs/quote")
    async def quote_job(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        body = await request.json()
        model = body.get("model") or DEFAULT_MODEL
        if model not in MODEL_LABELS:
            raise HTTPException(status_code=422, detail="unknown model")
        if model in PER_SECOND_MODELS:
            if not is_paid(user):
                raise HTTPException(status_code=403, detail="model switching is a paid feature")
            return _quote_for(user, model, _api_job_params(body, model))
        return {
            "model": model,
            "model_label": MODEL_LABELS[model],
            "job_price_cents": pricing.job_price_table(owner=is_owner(user)),
            "included_budget_cents_total": round(pricing.included_budget_cents(), 2),
            "included_budget_cents_remaining": round(included_budget_remaining(user), 2),
        }

    def _submit_failed(job: dict, detail: str):
        """Shared submit-failure path for every per-second lane:
        the internal detail stays on the record, the job fails, the
        hold releases, and the user sees the one clean sentence."""
        job["provider_error"] = detail[:200]
        jobs[job["id"]] = job
        fail_fal_job(job)
        raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)

    def submit_fal_job(job: dict, body: dict, params: dict, prompt: str, base: str):
        model = job["model"]
        endpoint = fal_endpoint_for(model, params["mode"], params["resolution"])
        if model == "wan_motion":
            # Motion transfer: the subject image performs the
            # driving video's motion. fal's published input shape
            # is image_url + video_url with an optional prompt
            # (fal model page, read 2026-11-17).
            # PAYLOAD-UNVERIFIED until the funded pass.
            payload = {
                "prompt": prompt,
                "image_url": f"data:image/png;base64,{params['start_image']}",
                "video_url": params["reference_video_urls"][0],
            }
        elif model in ("veo3", "veo3_fast", "veo3_lite"):
            # Veo's fal schema: clip length as a string, audio as
            # generate_audio, frames as image_url / end_image_url,
            # references as image_urls (images only; validation
            # already refused video and audio references).
            payload = {
                "prompt": prompt,
                "aspect_ratio": params["aspect_ratio"],
                "duration": f"{params['duration']}s",
                # fal's Veo schema names the 4K tier "4k"
                # (lowercase; 4K expansion, 2026-11-17).
                "resolution": (
                    "4k" if params["resolution"] == "4K"
                    else params["resolution"]),
                "generate_audio": True,
            }
            if params["mode"] == "image":
                payload["image_url"] = f"data:image/png;base64,{params['start_image']}"
                if params["end_image"]:
                    payload["end_image_url"] = f"data:image/png;base64,{params['end_image']}"
            elif params["mode"] == "reference":
                if params["reference_image_urls"]:
                    payload["image_urls"] = params["reference_image_urls"]
        elif model in ("kling3", "kling3_omni"):
            # Kling's tier rides in the endpoint path (see
            # FAL_ENDPOINTS), so no resolution field goes in the
            # body; native audio is part of the priced tier.
            payload = {
                "prompt": prompt,
                "aspect_ratio": params["aspect_ratio"],
                "duration": params["duration"],
            }
            if params["mode"] == "image":
                payload["image_url"] = f"data:image/png;base64,{params['start_image']}"
                if params["end_image"]:
                    payload["end_image_url"] = f"data:image/png;base64,{params['end_image']}"
            elif params["mode"] == "reference":
                if params["reference_image_urls"]:
                    payload["reference_image_urls"] = params["reference_image_urls"]
                if params["reference_video_urls"]:
                    payload["reference_video_urls"] = params["reference_video_urls"]
                if params["reference_audio_urls"]:
                    payload["reference_audio_urls"] = params["reference_audio_urls"]
        elif model == "hailuo23":
            # Hailuo's tier rides in the endpoint path like
            # Kling's; the model is silent and its schema takes
            # no aspect or resolution fields.
            payload = {
                "prompt": prompt,
                "duration": params["duration"],
            }
            if params["mode"] == "image":
                payload["image_url"] = f"data:image/png;base64,{params['start_image']}"
        elif model == "grok15_lite":
            # Grok Imagine Video 1.5 Lite on fal's xAI endpoints
            # (2026-11-17): fal's documented shape for the family
            # is prompt + resolution + duration, aspect_ratio on
            # text mode, and the start frame as image_url on
            # image mode (fal's Grok Imagine quickstart and API
            # docs, read 2026-11-17). Native synchronized audio
            # incl. lip-synced dialogue is always on; there is no
            # audio field and no reference mode.
            # PAYLOAD-UNVERIFIED until the funded pass.
            payload = {
                "prompt": prompt,
                "duration": params["duration"],
                "resolution": params["resolution"],
            }
            if params["mode"] == "text":
                payload["aspect_ratio"] = params["aspect_ratio"]
            elif params["mode"] == "image":
                payload["image_url"] = f"data:image/png;base64,{params['start_image']}"
        elif model in ("luma_ray32", "ltx25", "ltx25_fast", "pika22", "vidu_q2"):
            # Minimal text/image payloads in the shared fal shape.
            # These lanes are dark (the supplier account is
            # unfunded), so a schema mismatch surfaces as the
            # clean unavailable sentence with the hold released,
            # never a charge; the payloads get their funded
            # verification pass before any of these lanes lights.
            payload = {
                "prompt": prompt,
                "aspect_ratio": params["aspect_ratio"],
                "duration": params["duration"],
                # fal's LTX schema names the 4K tier 2160p
                # (parity pass, 2026-11-17; payload-unverified).
                "resolution": (
                    "2160p" if params["resolution"] == "4K"
                    else params["resolution"]),
            }
            if params["mode"] == "image":
                payload["image_url"] = f"data:image/png;base64,{params['start_image']}"
        else:
            payload = {
                "prompt": prompt,
                "resolution": params["resolution"],
                "aspect_ratio": params["aspect_ratio"],
                "duration": params["duration"],
                "audio": True,
            }
            if params["mode"] == "image":
                payload["start_image_url"] = f"data:image/png;base64,{params['start_image']}"
                if params["end_image"]:
                    payload["end_image_url"] = f"data:image/png;base64,{params['end_image']}"
            elif params["mode"] == "reference":
                if params["reference_image_urls"]:
                    payload["reference_image_urls"] = params["reference_image_urls"]
                if params["reference_video_urls"]:
                    payload["reference_video_urls"] = params["reference_video_urls"]
                if params["reference_audio_urls"]:
                    payload["reference_audio_urls"] = params["reference_audio_urls"]
        if isinstance(body.get("seed"), int):
            payload["seed"] = body["seed"]
        webhook_url = f"{base}/api/fal/webhook/{job['id']}?token={_fal_token(job['id'])}"
        submit_url = f"{FAL_BASE}/{endpoint}?fal_webhook={urllib.parse.quote(webhook_url, safe='')}"
        try:
            resp = requests.post(
                submit_url, headers=_fal_headers(), json=payload, timeout=30
            )
        except Exception as exc:
            _submit_failed(job, f"submit unreachable: {exc}")
        if resp.status_code not in (200, 201, 202):
            _submit_failed(job, f"submit http {resp.status_code}: {resp.text[:150]}")
        data = resp.json()
        job["provider_request_id"] = data.get("request_id")
        job["provider_status_url"] = data.get("status_url")
        job["provider_response_url"] = data.get("response_url")
        jobs[job["id"]] = job

    def _beatapi_upload(data: bytes, filename: str, mime: str) -> str:
        """Upload one media file to the task supplier and return
        its public URL. Their task inputs must be public https
        URLs; the platform's own inputs arrive as uploads, so they
        go through the supplier's file endpoint first."""
        resp = requests.post(
            f"{BEATAPI_BASE}/v1/files",
            headers={
                "Authorization": f"Bearer {os.environ.get('BEATAPI_KEY', '')}"
            },
            files={"file": (filename, data, mime)},
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            raise ValueError(f"file upload http {resp.status_code}")
        url = ((resp.json() or {}).get("data") or {}).get("url")
        if not url:
            raise ValueError("file upload returned no url")
        return url

    def _beatapi_media_url(value: str, kind: str) -> str:
        """One task input as a public URL: pass https URLs through,
        upload anything else (data URLs, raw base64 frames)."""
        if value.startswith("https://"):
            return value
        if value.startswith("data:"):
            header, _, b64 = value.partition(",")
            mime = header[5:].split(";")[0] or "application/octet-stream"
            raw = base64.b64decode(b64)
        else:
            # Start and end frames arrive as raw base64 PNG.
            mime = "image/png"
            raw = base64.b64decode(value)
        ext = {"image/png": "png", "image/jpeg": "jpg",
               "video/mp4": "mp4", "audio/mpeg": "mp3"}.get(mime, "bin")
        return _beatapi_upload(raw, f"{kind}.{ext}", mime)

    def submit_beatapi_job(job: dict, body: dict, params: dict, prompt: str):
        """Create one task on the BeatAPI supplier. The price is
        reserved from the supplier balance at creation; a failed
        task is refunded there in full, and any refusal here (a
        $0 balance answers 402) fails the job and releases the
        platform hold, exactly like the fal lanes."""
        model = job["model"]
        payload = {
            "model": BEATAPI_MODEL_IDS[model],
            "prompt": prompt,
            "duration": params["duration"],
            "resolution": BEATAPI_RESOLUTION_MAP.get(model, {}).get(
                params["resolution"], params["resolution"]
            ),
        }
        # The supplier's schemas are closed: an audio switch goes
        # only to the models that define one, under the name each
        # defines. Every other model on this supplier either
        # always includes audio or is silent.
        if model in BEATAPI_AUDIO_MODELS:
            payload["generate_audio"] = True
        elif model == "kling3":
            payload["sound"] = True
        # Image-led requests on these models follow the start
        # image's own shape; the supplier's contract omits the
        # aspect ratio there.
        if not (model in ("grok", "kling3") and params["mode"] == "image"):
            payload["aspect_ratio"] = params["aspect_ratio"]
        try:
            if params["mode"] == "image":
                frames = [_beatapi_media_url(params["start_image"], "first-frame")]
                if params["end_image"]:
                    frames.append(_beatapi_media_url(params["end_image"], "last-frame"))
                payload["images"] = frames
            elif params["mode"] == "reference":
                if params["reference_image_urls"]:
                    payload["reference_images"] = [
                        _beatapi_media_url(u, "reference-image")
                        for u in params["reference_image_urls"]
                    ]
                if params["reference_video_urls"]:
                    payload["reference_videos"] = [
                        _beatapi_media_url(u, "reference-video")
                        for u in params["reference_video_urls"]
                    ]
                if params["reference_audio_urls"]:
                    payload["reference_audios"] = [
                        _beatapi_media_url(u, "reference-audio")
                        for u in params["reference_audio_urls"]
                    ]
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        if isinstance(body.get("seed"), int):
            payload["seed"] = body["seed"]
        headers = dict(_beatapi_headers())
        # The job id doubles as the idempotency key: a retried
        # submit within the supplier's window replays the first
        # task instead of reserving twice.
        headers["Idempotency-Key"] = job["id"]
        try:
            resp = requests.post(
                f"{BEATAPI_BASE}/v1/videos/tasks",
                headers=headers, json=payload, timeout=30,
            )
        except Exception as exc:
            _submit_failed(job, f"submit unreachable: {exc}")
        if resp.status_code not in (200, 201, 202):
            _submit_failed(job, f"submit http {resp.status_code}: {resp.text[:150]}")
        data = (resp.json() or {}).get("data") or {}
        if not data.get("id"):
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = data["id"]
        jobs[job["id"]] = job

    # ------------------------------------------------------------------
    # Follow-up wiring pass (2026-11-16): the poll-based suppliers.
    # Every lane below is DARK-SAFE: the supplier accounts are
    # unfunded, so a submit either succeeds in being accepted (and
    # then cannot be paid for) or fails clean through _submit_failed
    # with the hold released. Payloads follow each supplier's own
    # published API docs as of 2026-11-16; anything not confirmed
    # against a live account is marked PAYLOAD-UNVERIFIED in its
    # docstring and gets the funded verification pass before the
    # lane lights. Kie prices in pricing.py are published "from"
    # prices pending console confirmation.
    # ------------------------------------------------------------------
    KIE_BASE = "https://api.kie.ai"
    COMETAPI_BASE = "https://api.cometapi.com"
    PIAPI_BASE = "https://api.piapi.ai"
    OPENROUTER_BASE = "https://openrouter.ai"
    NOVITA_BASE = "https://api.novita.ai"
    WAVESPEED_BASE = "https://api.wavespeed.ai"
    EACHLABS_BASE = "https://api.eachlabs.ai"
    RUNWAY_BASE = "https://api.dev.runwayml.com"
    VIDU_BASE = "https://api.vidu.com"
    MINIMAX_BASE = "https://api.minimax.io"
    # Uncensored-lane suppliers (2026-11-17 wiring pass).
    SPICYAPI_BASE = "https://api.spicyapi.ai"
    HOTAPI_BASE = "https://api.hotapi.ai"
    WIRO_BASE = "https://api.wiro.ai"

    def _api_headers(provider: str) -> dict:
        key = os.environ.get(PROVIDER_KEY_ENV.get(provider, ""), "")
        if provider == "piapi":
            return {"X-API-Key": key, "Content-Type": "application/json"}
        if provider == "vidu":
            # Vidu's published auth scheme is a Token header.
            return {"Authorization": f"Token {key}",
                    "Content-Type": "application/json"}
        if provider == "eachlabs":
            return {"X-API-Key": key, "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {key}",
                "Content-Type": "application/json"}

    def _submit_post(job: dict, url: str, headers: dict, payload: dict):
        """The shared submit POST for the poll-based suppliers: any
        refusal fails the job cleanly through _submit_failed."""
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=30)
        except Exception as exc:
            _submit_failed(job, f"submit unreachable: {exc}")
        if resp.status_code not in (200, 201, 202):
            _submit_failed(
                job, f"submit http {resp.status_code}: {resp.text[:150]}")
        try:
            return resp.json() or {}
        except Exception:
            _submit_failed(job, "submit returned non-json")

    def _data_uri(value: str) -> str:
        if value.startswith("data:"):
            return value
        return f"data:image/png;base64,{value}"

    def _poll_gate(job: dict) -> bool:
        """The shared 8-second poll throttle for the poll-based
        supplier lanes (fal and BeatAPI keep their own mirrors)."""
        if job["status"] in ("queued", "running") and \
                _now() - job.get("last_poll", 0) > 8:
            job["last_poll"] = _now()
            jobs[job["id"]] = job
            return True
        return False

    def _chain_segment_file(job: dict, idx: int,
                            result_url: str) -> Path:
        """Materialize one finished chain segment on the data
        Volume (cached): the chain driver needs the file to take
        the next segment's first frame from it, and delivery
        needs every segment to join."""
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        seg_path = FILES_DIR / f"{job['id']}.seg{idx}.mp4"
        if seg_path.exists():
            return seg_path
        if result_url == "stored://raw":
            # OpenRouter's no-URL path already cached the file
            # as the job's raw file.
            src = FILES_DIR / f"{job['id']}.raw.mp4"
            seg_path.write_bytes(src.read_bytes())
        elif result_url.startswith("https://"):
            resp = requests.get(result_url, timeout=300)
            if resp.status_code != 200:
                raise ValueError(
                    f"segment download http {resp.status_code}")
            seg_path.write_bytes(resp.content)
        else:
            raise ValueError("segment result is not a file or URL")
        data_volume.commit()
        return seg_path

    def _chain_last_frame(job: dict, idx: int, seg_path: Path) -> str:
        """The last frame of a finished segment as base64 PNG:
        the next segment's start image, so the chain continues
        from exactly where the previous segment stopped."""
        frame_path = FILES_DIR / f"{job['id']}.seg{idx}.frame.png"
        attempts = [
            ["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(seg_path),
             "-frames:v", "1", "-q:v", "3", str(frame_path)],
        ]
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(seg_path)],
                capture_output=True, text=True, timeout=60)
            dur = float(probe.stdout.strip())
            attempts.append(
                ["ffmpeg", "-y", "-ss", f"{max(0.0, dur - 0.15):.3f}",
                 "-i", str(seg_path), "-frames:v", "1", "-q:v", "3",
                 str(frame_path)])
        except Exception:
            pass
        for cmd in attempts:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=300)
            if proc.returncode == 0 and frame_path.exists():
                return base64.b64encode(
                    frame_path.read_bytes()).decode()
        raise ValueError("could not take a frame from the segment")

    def _chain_segment_done(job: dict, result_url: str,
                            cost_cents=None):
        """One segment of a chained length finished: bank its
        result and cost, take its last frame, and submit the
        next segment from that frame. Any failure here lands in
        fail_fal_job, which delivers the completed segments as
        a partial video (Rule 8) instead of losing them."""
        idx = int(job.get("chain_index") or 0)
        plan = job.get("chain_plan") or []
        results = list(job.get("chain_results") or [])
        results.append(result_url)
        job["chain_results"] = results
        if cost_cents is not None:
            job["chain_cost_cents"] = round(
                float(job.get("chain_cost_cents") or 0)
                + float(cost_cents), 2)
            job["chain_cost_seen"] = True
        job["segments_done"] = idx + 1
        job["status"] = "running"
        jobs[job["id"]] = job
        try:
            seg_path = _chain_segment_file(job, idx, result_url)
            frame = _chain_last_frame(job, idx, seg_path)
            params = {
                "duration": plan[idx + 1],
                "resolution": job["resolution"],
                "aspect_ratio": job.get("aspect_ratio") or "9:16",
                "mode": "image",
                "reference_image_urls": [],
                "reference_video_urls": [],
                "reference_audio_urls": [],
                "reference_video_seconds": 0.0,
                "start_image": frame,
                "end_image": None,
            }
            for field in ("provider_task_id", "provider_request_id",
                          "provider_status_url",
                          "provider_response_url",
                          "provider_socket_token"):
                job[field] = None
            job["last_poll"] = 0
            job["chain_index"] = idx + 1
            jobs[job["id"]] = job
            submit_api_job(
                job, {"seed": job.get("chain_seed")}, params,
                job["prompt"], job.get("base_url") or "")
        except HTTPException:
            # _submit_failed already ran the failure path (a
            # partial delivery when segments exist, a clean fail
            # otherwise); the job record carries the outcome.
            pass
        except Exception:
            fail_fal_job(job)

    def _poll_done(job: dict, result_url: str, cost_cents=None,
                   duration=None):
        plan = job.get("chain_plan") or []
        if len(plan) > 1:
            idx = int(job.get("chain_index") or 0)
            if idx + 1 < len(plan):
                # A middle segment of a chained length: the job
                # is not done, it continues from this segment's
                # last frame.
                _chain_segment_done(job, result_url, cost_cents)
                return
            # The final segment: delivery joins chain_results,
            # settlement prices the generated total (already on
            # the job as bill_generated_seconds), and the
            # supplier-reported cost is the sum over segments.
            job["chain_results"] = (
                job.get("chain_results") or []) + [result_url]
            job["segments_done"] = len(plan)
            if job.get("chain_cost_seen"):
                total = float(job.get("chain_cost_cents") or 0)
                if cost_cents is not None:
                    total += float(cost_cents)
                cost_cents = total
            duration = float(job.get("generated_seconds") or 0) \
                or duration
        job["result_url"] = result_url
        if cost_cents is not None:
            job["provider_cost_cents"] = round(float(cost_cents), 2)
        if duration:
            job["result_duration"] = float(duration)
        job["status"] = "done"
        jobs[job["id"]] = job
        settle_fal_job(job)

    def _poll_fail(job: dict, detail: str):
        job["provider_error"] = detail[:200]
        fail_fal_job(job)

    def _poll_retry_or_fail(job: dict):
        job["poll_errors"] = int(job.get("poll_errors") or 0) + 1
        if job["poll_errors"] > 30:
            fail_fal_job(job)
        else:
            jobs[job["id"]] = job

    def _note_unparsed(job: dict, provider: str, status_word,
                       payload=None):
        """A provider answered 200 with a status word that is in
        none of its documented sets (success, failure, running).
        That is the SpicyAPI failure shape: the envelope moved
        and the mirror cannot understand the answer. One odd
        answer can be a hiccup, so the job keeps running; a
        steady diet of them (the same 30-strike bound the
        poll-error path uses) fails the job clean with a full
        refund, and the last payload shape stays on the job for
        the record. Transport failures (non-200, exceptions) do
        NOT count here: a provider outage should not kill jobs
        that would land when it recovers; the wall-clock poll
        limit in mirror_status is their catch."""
        job["unparsed_polls"] = int(job.get("unparsed_polls") or 0) + 1
        job["unparsed_status"] = str(status_word)[:80]
        if payload is not None:
            try:
                job["unparsed_payload"] = json.dumps(payload)[:400]
            except Exception:
                job["unparsed_payload"] = str(payload)[:400]
        if job["unparsed_polls"] > 30:
            job["provider_error"] = (
                f"{provider} sent a status this platform does not "
                f"recognise: {job['unparsed_status']}")
            fail_fal_job(job)
        else:
            job["status"] = "running"
            jobs[job["id"]] = job

    def _restore_house_image(job: dict):
        """Rule 8 restore for a failed house (worker) image job:
        the balance charge returns, the included-budget debit
        returns, and the dispatch-time settled cost comes back
        off the account's ceiling total. Shared by the worker
        error path in mirror_image_status and the stale-job
        path in _fail_stale_job; the per-leg flags keep it
        idempotent."""
        owner = users.get(f"id:{job['user_id']}")
        if not owner:
            return
        if job.get("charged_cents") and not job.get("refunded"):
            owner["balance_cents"] = (
                owner.get("balance_cents", 0) + job["charged_cents"])
            job["refunded"] = True
        if job.get("included_cost_cents") \
                and not job.get("included_released"):
            u = usage(owner)
            u["included_cost_cents"] = max(
                0.0, included_cost_used(owner)
                - float(job["included_cost_cents"]))
            owner["usage"] = u
            job["included_released"] = True
        if job.get("cost_cents") and not job.get("cost_released"):
            owner["cost_settled_cents"] = max(
                0.0,
                float(owner.get("cost_settled_cents") or 0.0)
                - float(job["cost_cents"]))
            job["cost_released"] = True
        save_user(owner)

    def _poll_limit_for(job: dict):
        """The bounded poll lifetime for a job's family."""
        if job.get("provider") == "hyperframes":
            return TEMPLATE_POLL_LIMIT_SECONDS
        if job.get("kind") == "image":
            return IMAGE_POLL_LIMIT_SECONDS
        if job.get("provider") in URL_RESULT_PROVIDERS:
            return PROVIDER_POLL_LIMIT_SECONDS
        return HOUSE_POLL_LIMIT_SECONDS

    def _fail_stale_job(job: dict):
        """A job that outlived its poll bound fails clean on the
        spot: per-second provider lanes fail through fail_fal_job
        (hold released in full; a chained job with finished
        segments still delivers them as a partial, per Rule 8),
        shelf images refund through their own path, and house
        jobs restore exactly what their own error paths restore.
        No job may sit in a running state past its bound."""
        if job.get("kind") == "image" and job.get("image_model"):
            _fail_provider_image(
                job, "the image took too long, nothing was charged")
            return
        if job.get("kind") == "image":
            job["status"] = "error"
            job["internal_error"] = "house image job passed its time limit"
            job["error"] = (
                "the first frame could not be made, nothing was charged")
            _restore_house_image(job)
            jobs[job["id"]] = job
            return
        if job.get("provider") in URL_RESULT_PROVIDERS:
            job["provider_error"] = "provider job passed its poll time limit"
            fail_fal_job(job)
            return
        # House worker video job: the worker never reported back.
        job["status"] = "error"
        job["error"] = "the video took too long, nothing was charged"
        owner = users.get(f"id:{job['user_id']}")
        if owner and job.get("charged_cents") \
                and not job.get("refunded"):
            owner["balance_cents"] = (
                owner.get("balance_cents", 0) + job["charged_cents"])
            job["refunded"] = True
            save_user(owner)
        if owner and job.get("cost_cents") \
                and not job.get("cost_released"):
            owner["cost_settled_cents"] = max(
                0.0,
                float(owner.get("cost_settled_cents") or 0.0)
                - float(job["cost_cents"]))
            job["cost_released"] = True
            save_user(owner)
        jobs[job["id"]] = job

    # -- Kie ------------------------------------------------------------
    def submit_kie_job(job: dict, body: dict, params: dict, prompt: str):
        """Kie task API: POST /api/v1/jobs/createTask {model, input},
        poll GET /api/v1/jobs/recordInfo?taskId=. PAYLOAD-UNVERIFIED:
        only the Kling 4.0 slug pair is confirmed on Kie's own
        pages; the rest follow the same naming pattern. Where the
        lane has a native audio mode it is selected by default
        (sound for the Kling family, generate_audio for Seedance
        Fast); Seedance Mini and Hailuo are silent models and Grok /
        Omni audio fields are unconfirmed, so nothing is sent for
        them."""
        model = job["model"]
        mode = params["mode"]
        slugs = {
            ("kling4", "text"): "kling-4.0/reference-to-video",
            ("kling4", "reference"): "kling-4.0/reference-to-video",
            ("kling4", "image"): "kling-4.0/keyframe-to-video",
            ("kling3", "text"): "kling-3.0/reference-to-video",
            ("kling3", "reference"): "kling-3.0/reference-to-video",
            ("kling3", "image"): "kling-3.0/keyframe-to-video",
            ("kling3_omni", "text"): "kling-3.0-omni/reference-to-video",
            ("kling3_omni", "reference"): "kling-3.0-omni/reference-to-video",
            ("kling3_omni", "image"): "kling-3.0-omni/keyframe-to-video",
            ("seedance2_fast", "text"): "bytedance/seedance-2.0-fast/text-to-video",
            ("seedance2_fast", "image"): "bytedance/seedance-2.0-fast/image-to-video",
            ("seedance2_fast", "reference"): "bytedance/seedance-2.0-fast/reference-to-video",
            ("seedance2_mini", "text"): "bytedance/seedance-2.0-mini/text-to-video",
            ("seedance2_mini", "image"): "bytedance/seedance-2.0-mini/image-to-video",
            ("seedance2_mini", "reference"): "bytedance/seedance-2.0-mini/reference-to-video",
            ("omni12", "text"): "gemini-omni-1.2-flash/text-to-video",
            ("omni12", "image"): "gemini-omni-1.2-flash/image-to-video",
            ("omni12", "reference"): "gemini-omni-1.2-flash/reference-to-video",
            ("grok", "text"): "grok-imagine/text-to-video",
            ("grok", "image"): "grok-imagine/image-to-video",
            # Grok Imagine Video 1.5 as its own lane (2026-11-17):
            # Kie's own 1.5 page names a single model id,
            # grok-imagine-video-1-5-preview, for every mode; the
            # mode rides in the input (prompt alone, first/last
            # frame, or image_urls references), exactly like the
            # generic branches below build it.
            ("grok15", "text"): "grok-imagine-video-1-5-preview",
            ("grok15", "image"): "grok-imagine-video-1-5-preview",
            ("grok15", "reference"): "grok-imagine-video-1-5-preview",
            ("hailuo23", "text"): "hailuo-2.3/text-to-video",
            ("hailuo23", "image"): "hailuo-2.3/image-to-video",
        }
        slug = slugs.get((model, mode))
        if not slug:
            _submit_failed(job, f"no kie slug for {model}/{mode}")
        inp = {"prompt": prompt, "duration": params["duration"],
               "aspect_ratio": params["aspect_ratio"]}
        if model in ("kling3", "kling4", "kling3_omni"):
            inp["sound"] = True
        elif model == "seedance2_fast":
            inp["generate_audio"] = True
        if params.get("resolution") == "4K":
            # 4K tiers (2026-11-17, 4K expansion): Kie's own
            # pages price a 4K tier for both Kling 3.0 lanes.
            # kling-3.0/video takes it as mode="4K" (its page
            # lists modes std / pro / 4K); kling-3.0-omni takes
            # a resolution field whose documented enum is
            # 720p / 1080p / 4k. Other tiers send nothing,
            # exactly as before; payload-unverified like the
            # rest of this submit until the funded pass.
            if model == "kling3":
                inp["mode"] = "4K"
            elif model == "kling3_omni":
                inp["resolution"] = "4k"
        if model == "grok" and params.get("resolution") == "1080p":
            # Grok 1080p tier (2026-11-17): Kie's own Grok page
            # documents a resolution parameter (480p default /
            # 720p / 1080p, chosen per job). The lower tiers send
            # nothing, exactly as before; payload-unverified like
            # the rest of this submit until the funded pass.
            inp["resolution"] = "1080p"
        if model == "grok15":
            # Grok Imagine Video 1.5 (2026-11-17): Kie's own 1.5
            # page documents the resolution as a per-job parameter
            # on this model id, so it is sent at every tier
            # (480p / 720p / 1080p). Native audio incl. dialogue
            # is generated in the same pass; the page documents
            # no audio switch, so nothing is sent for it.
            # Payload-unverified like the rest of this submit
            # until the funded pass.
            inp["resolution"] = params["resolution"]
        try:
            if mode == "image":
                inp["first_frame"] = _beatapi_media_url(
                    params["start_image"], "first-frame")
                if params["end_image"]:
                    inp["last_frame"] = _beatapi_media_url(
                        params["end_image"], "last-frame")
            elif mode == "reference":
                if params["reference_image_urls"]:
                    inp["image_urls"] = [
                        _beatapi_media_url(u, "reference-image")
                        for u in params["reference_image_urls"]]
                if params["reference_video_urls"]:
                    inp["video_urls"] = [
                        _beatapi_media_url(u, "reference-video")
                        for u in params["reference_video_urls"]]
                if params["reference_audio_urls"]:
                    inp["audio_urls"] = [
                        _beatapi_media_url(u, "reference-audio")
                        for u in params["reference_audio_urls"]]
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(
            job, f"{KIE_BASE}/api/v1/jobs/createTask",
            _api_headers("kie"), {"model": slug, "input": inp})
        payload = data.get("data") or {}
        task_id = (payload.get("taskId") or payload.get("task_id")
                   or payload.get("id"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_kie_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{KIE_BASE}/api/v1/jobs/recordInfo",
                params={"taskId": job["provider_task_id"]},
                headers=_api_headers("kie"), timeout=15)
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data") or {}
                state = data.get("state")
                if state == "success":
                    urls = []
                    raw = data.get("resultJson")
                    if raw:
                        try:
                            urls = (json.loads(raw) or {}).get(
                                "resultUrls") or []
                        except Exception:
                            urls = []
                    if urls:
                        cost = None
                        credits = data.get("consumeCredits")
                        if credits is not None:
                            # Kie: 1 credit is $0.005 = 0.5 cents.
                            cost = float(credits) * 0.5
                        _poll_done(job, urls[0], cost_cents=cost)
                    else:
                        _poll_retry_or_fail(job)
                elif state == "fail":
                    _poll_fail(
                        job, f"{data.get('failCode')}: {data.get('failMsg')}")
                elif state in ("waiting", "queuing", "generating"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "kie", state, data)
        except Exception:
            pass
        return job

    # -- CometAPI ---------------------------------------------------------
    def submit_cometapi_job(job: dict, body: dict, params: dict,
                            prompt: str):
        """CometAPI videos API (OpenAI-videos shape): POST
        /v1/videos, poll GET /v1/videos/{id}. PAYLOAD-UNVERIFIED:
        the Seedance 2.5 model id is confirmed on CometAPI's own
        model page (seedance-2-5-260628); the Wan 3.0 Prime slug
        follows its naming and is unconfirmed. No audio switch is
        sent: none is published for these endpoints."""
        ids = {"seedance": "seedance-2-5-260628",
               "wan3_prime": "wan-3-0-prime"}
        # CometAPI takes a pixel size. The 480p tier routes here
        # since pricing round 2 (2026-11-16); its catalog lists
        # that tier as 854x480 landscape, and the 9:16 raster is
        # 480x854. Other cells are the standard rasters for the
        # tier and aspect (parity pass, 2026-11-17).
        comet_sizes = {
            ("480p", "9:16"): "480x854", ("480p", "16:9"): "854x480",
            ("480p", "1:1"): "480x480",
            ("720p", "9:16"): "720x1280", ("720p", "16:9"): "1280x720",
            ("720p", "1:1"): "720x720",
            ("1080p", "9:16"): "1080x1920",
            ("1080p", "16:9"): "1920x1080",
            ("1080p", "1:1"): "1080x1080",
        }
        size = comet_sizes.get(
            (params.get("resolution"), params["aspect_ratio"]),
            "720x1280")
        payload = {"model": ids[job["model"]], "prompt": prompt,
                   "duration": params["duration"], "size": size}
        try:
            ref = None
            if params["mode"] == "image":
                ref = params["start_image"]
            elif params["mode"] == "reference" and \
                    params["reference_image_urls"]:
                ref = params["reference_image_urls"][0]
            if ref:
                payload["input_reference"] = _beatapi_media_url(
                    ref, "reference-image")
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(job, f"{COMETAPI_BASE}/v1/videos",
                            _api_headers("cometapi"), payload)
        task_id = data.get("id") or (data.get("data") or {}).get("id")
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_cometapi_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{COMETAPI_BASE}/v1/videos/{job['provider_task_id']}",
                headers=_api_headers("cometapi"), timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                status = str(data.get("status") or "").lower()
                if status in ("completed", "succeeded", "success"):
                    # The old one-line expression parsed as
                    # (video_url or url or output.url) when output
                    # was a dict, else output itself: a non-dict
                    # output silently discarded a present
                    # video_url, and a list output would have been
                    # handed to _poll_done as the "URL". Read the
                    # documented spots in order instead.
                    output = data.get("output")
                    url = data.get("video_url") or data.get("url")
                    if not url and isinstance(output, dict):
                        url = output.get("url")
                    if not url and isinstance(output, str):
                        url = output
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "error", "cancelled"):
                    _poll_fail(job, str(data.get("error"))[:200])
                elif status in ("queued", "in_progress", "pending"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "cometapi", status, data)
        except Exception:
            pass
        return job

    # -- PiAPI ------------------------------------------------------------
    def submit_piapi_job(job: dict, body: dict, params: dict,
                         prompt: str):
        """PiAPI task API: POST /api/v1/task (X-API-Key), poll GET
        /api/v1/task/{id}. PAYLOAD-UNVERIFIED beyond the documented
        task envelope. Veo 3.1 only, and the priced route is the
        NO-AUDIO one: generate_audio stays off on purpose, because
        the with-audio rate is a different published price recorded
        in pricing.py. Talking content on this lane is served by
        the voiceover stage, never by silently switching the price
        basis."""
        inp = {"prompt": prompt, "duration": params["duration"],
               "aspect_ratio": params["aspect_ratio"], "generate_audio": False}
        try:
            if params["mode"] == "image":
                inp["image_url"] = _beatapi_media_url(
                    params["start_image"], "first-frame")
            elif params["mode"] == "reference" and \
                    params["reference_image_urls"]:
                inp["image_urls"] = params["reference_image_urls"]
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(
            job, f"{PIAPI_BASE}/api/v1/task", _api_headers("piapi"),
            {"model": "veo3.1", "task_type": "veo3.1-video",
             "input": inp})
        payload = data.get("data") or {}
        task_id = payload.get("task_id") or payload.get("id")
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_piapi_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{PIAPI_BASE}/api/v1/task/{job['provider_task_id']}",
                headers=_api_headers("piapi"), timeout=15)
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data") or {}
                status = str(data.get("status") or "").lower()
                if status in ("completed", "complete", "success",
                              "succeeded"):
                    output = data.get("output") or {}
                    url = output.get("video") if isinstance(
                        output, dict) else output
                    if isinstance(url, dict):
                        url = url.get("url")
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "failure", "error",
                                "cancelled", "canceled", "rejected"):
                    _poll_fail(
                        job, str(data.get("error")
                                 or data.get("message"))[:200])
                elif status in ("pending", "processing", "running",
                                "staged"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "piapi", status, data)
        except Exception:
            pass
        return job

    # -- OpenRouter ---------------------------------------------------------
    def submit_openrouter_job(job: dict, body: dict, params: dict,
                              prompt: str):
        """OpenRouter videos API: POST /api/v1/videos -> 202 {id,
        polling_url}; poll GET /api/v1/videos/{id}. Documented
        contract; payload details PAYLOAD-UNVERIFIED. HappyHorse
        1.1 only in this pass."""
        payload = {"model": "alibaba/happyhorse-1.1", "prompt": prompt,
                   "duration": params["duration"],
                   "aspect_ratio": params["aspect_ratio"]}
        if params["mode"] == "image":
            payload["image_url"] = _data_uri(params["start_image"])
        elif params["mode"] == "reference" and \
                params["reference_image_urls"]:
            payload["image_url"] = params["reference_image_urls"][0]
        data = _submit_post(job, f"{OPENROUTER_BASE}/api/v1/videos",
                            _api_headers("openrouter"), payload)
        task_id = data.get("id")
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        if data.get("polling_url"):
            job["provider_status_url"] = data["polling_url"]
        jobs[job["id"]] = job

    def mirror_openrouter_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            url = job.get("provider_status_url") or \
                f"{OPENROUTER_BASE}/api/v1/videos/{job['provider_task_id']}"
            resp = requests.get(url, headers=_api_headers("openrouter"),
                                timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                status = str(data.get("status") or "").lower()
                if status == "completed":
                    urls = data.get("urls") or data.get(
                        "unsigned_urls") or []
                    if urls:
                        _poll_done(job, urls[0])
                    else:
                        # No public URL in the payload: the content
                        # endpoint needs the key, so fetch the file
                        # now and let delivery read the raw cache.
                        content = requests.get(
                            f"{OPENROUTER_BASE}/api/v1/videos/"
                            f"{job['provider_task_id']}/content",
                            params={"index": 0},
                            headers=_api_headers("openrouter"),
                            timeout=300)
                        if content.status_code == 200 and content.content:
                            FILES_DIR.mkdir(parents=True, exist_ok=True)
                            (FILES_DIR / f"{job['id']}.raw.mp4"
                             ).write_bytes(content.content)
                            data_volume.commit()
                            _poll_done(job, "stored://raw")
                        else:
                            _poll_retry_or_fail(job)
                elif status in ("failed", "cancelled", "expired"):
                    _poll_fail(job, str(data.get("error"))[:200])
                elif status in ("pending", "in_progress", "queued"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "openrouter", status, data)
        except Exception:
            pass
        return job

    # -- Novita -------------------------------------------------------------
    def submit_novita_job(job: dict, body: dict, params: dict,
                          prompt: str):
        """Novita async Vidu Q3 Turbo endpoints (t2v / i2v / f2v).
        PAYLOAD-UNVERIFIED beyond Novita's published endpoint
        names and rate card. The priced route is off-peak, so
        off_peak is sent true; audio is on (Q3 generates speech
        natively). The platform's 480p tier maps to Novita's 540p
        tier."""
        mode = params["mode"]
        if mode == "image" and params["end_image"]:
            suffix = "f2v"
        elif mode == "image":
            suffix = "i2v"
        else:
            suffix = "t2v"
        payload = {
            "prompt": prompt,
            "duration": params["duration"],
            "resolution": "540p" if params["resolution"] == "480p"
            else "720p",
            "aspect_ratio": params["aspect_ratio"],
            "audio": True,
            "off_peak": True,
        }
        try:
            if mode == "image":
                start_url = _beatapi_media_url(
                    params["start_image"], "first-frame")
                if suffix == "f2v":
                    payload["images"] = [
                        start_url,
                        _beatapi_media_url(params["end_image"],
                                           "last-frame")]
                else:
                    payload["image_url"] = start_url
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(
            job, f"{NOVITA_BASE}/v3/async/vidu-q3-turbo-{suffix}",
            _api_headers("novita"), payload)
        task_id = (data.get("task_id")
                   or (data.get("data") or {}).get("task_id"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_novita_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{NOVITA_BASE}/v3/async/task-result",
                params={"task_id": job["provider_task_id"]},
                headers=_api_headers("novita"), timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                task = data.get("task") or {}
                status = str(task.get("status")
                             or data.get("status") or "")
                videos = data.get("videos") or task.get("videos") or []
                if status in ("TASK_STATUS_SUCCEED", "SUCCEED",
                              "succeeded", "success"):
                    url = None
                    if videos:
                        first = videos[0]
                        url = first.get("video_url") or first.get(
                            "url") if isinstance(first, dict) else first
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif "FAIL" in status or status in ("failed", "error"):
                    _poll_fail(job, str(task.get("reason")
                                        or status)[:200])
                elif status in ("TASK_STATUS_QUEUED",
                                "TASK_STATUS_RUNNING", "queued",
                                "running", "processing", "pending"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "novita", status, data)
        except Exception:
            pass
        return job

    # -- WaveSpeed ------------------------------------------------------------
    def submit_wavespeed_job(job: dict, body: dict, params: dict,
                             prompt: str):
        """WaveSpeed v3 predictions: POST /api/v3/{model-path},
        poll GET /api/v3/predictions/{id}/result. Endpoint shapes
        are documented on WaveSpeed's own model pages. The three
        models wired here are PRICE-PAUSED (no per-second rate is
        published for them), so this path is unreachable until a
        rate lands in pricing.py; it is wired now so unpausing is
        a rate entry, not a rebuild. Where the model generates
        audio natively, generate_audio is on by default."""
        model = job["model"]
        mode = params["mode"]
        paths = {
            ("seedance25_flash", "text"):
                "bytedance/seedance-2.5-flash/text-to-video",
            ("seedance25_flash", "image"):
                "bytedance/seedance-2.5-flash/image-to-video",
            ("seedance25_flash", "reference"):
                "bytedance/seedance-2.5-flash/reference-to-video",
            ("vidu_q4", "image"): "vidu/q4/image-to-video",
            ("vidu_q4", "reference"): "vidu/q4/reference-to-video",
            ("pixverse_v6", "text"): "pixverse/pixverse-v6/text-to-video",
            ("pixverse_v6", "image"):
                "pixverse/pixverse-v6/image-to-video",
        }
        path = paths.get((model, mode))
        if not path:
            _submit_failed(job, f"no wavespeed path for {model}/{mode}")
        payload = {"prompt": prompt, "duration": params["duration"],
                   "resolution": params["resolution"],
                   "aspect_ratio": params["aspect_ratio"]}
        if model in ("seedance25_flash", "vidu_q4"):
            payload["generate_audio"] = True
        try:
            if mode == "image":
                payload["image"] = _beatapi_media_url(
                    params["start_image"], "first-frame")
            elif mode == "reference":
                if model == "vidu_q4":
                    payload["images"] = [
                        _beatapi_media_url(u, "reference-image")
                        for u in params["reference_image_urls"]]
                else:
                    if params["reference_image_urls"]:
                        payload["reference_images"] = [
                            _beatapi_media_url(u, "reference-image")
                            for u in params["reference_image_urls"]]
                    if params["reference_video_urls"]:
                        payload["reference_videos"] = [
                            _beatapi_media_url(u, "reference-video")
                            for u in params["reference_video_urls"]]
                    if params["reference_audio_urls"]:
                        payload["reference_audios"] = [
                            _beatapi_media_url(u, "reference-audio")
                            for u in params["reference_audio_urls"]]
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(job, f"{WAVESPEED_BASE}/api/v3/{path}",
                            _api_headers("wavespeed"), payload)
        task_id = (data.get("data") or {}).get("id") or data.get("id")
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_wavespeed_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{WAVESPEED_BASE}/api/v3/predictions/"
                f"{job['provider_task_id']}/result",
                headers=_api_headers("wavespeed"), timeout=15)
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data") or {}
                status = str(data.get("status") or "").lower()
                if status == "completed":
                    outputs = data.get("outputs") or []
                    url = outputs[0] if outputs else None
                    if isinstance(url, dict):
                        url = url.get("url")
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "cancelled", "timeout",
                                "deleted"):
                    _poll_fail(job, str(data.get("error"))[:200])
                elif status in ("created", "processing"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "wavespeed", status, data)
        except Exception:
            pass
        return job

    # -- Eachlabs -------------------------------------------------------------
    def submit_eachlabs_job(job: dict, body: dict, params: dict,
                            prompt: str):
        """Eachlabs predictions API: POST /v1/prediction {model,
        version, input}, poll GET /v1/prediction/{id}.
        PAYLOAD-UNVERIFIED: the envelope is documented; the Veo
        3.1 Lite slugs follow Eachlabs' naming pattern and are
        unconfirmed. The priced tier is the no-audio one; no audio
        field is sent, and talking content rides the voiceover
        stage."""
        slug = ("veo-3-1-lite-image-to-video" if params["mode"] == "image"
                else "veo-3-1-lite-text-to-video")
        inp = {"prompt": prompt, "duration": params["duration"],
               "aspect_ratio": params["aspect_ratio"]}
        try:
            if params["mode"] == "image":
                inp["image_url"] = _beatapi_media_url(
                    params["start_image"], "first-frame")
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(
            job, f"{EACHLABS_BASE}/v1/prediction",
            _api_headers("eachlabs"),
            {"model": slug, "version": "0.0.1", "input": inp})
        task_id = (data.get("id") or data.get("prediction_id")
                   or (data.get("data") or {}).get("id"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_eachlabs_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{EACHLABS_BASE}/v1/prediction/{job['provider_task_id']}",
                headers=_api_headers("eachlabs"), timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                payload = data.get("data") or data
                status = str(payload.get("status") or "").lower()
                if status in ("success", "succeeded", "completed"):
                    output = payload.get("output")
                    url = output[0] if isinstance(output, list) \
                        and output else output
                    if isinstance(url, dict):
                        url = url.get("url")
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "error", "cancelled"):
                    _poll_fail(job, str(payload.get("error"))[:200])
                elif status in ("created", "starting", "processing"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "eachlabs", status, payload)
        except Exception:
            pass
        return job

    # -- Runway ---------------------------------------------------------------
    def submit_runway_job(job: dict, body: dict, params: dict,
                          prompt: str):
        """Runway API (api.dev.runwayml.com), version header
        2024-11-06: POST /v1/text_to_video or /v1/image_to_video,
        poll GET /v1/tasks/{id}. Documented contract; payload
        PAYLOAD-UNVERIFIED against the live account. Gen-4.5 is
        silent: there is no audio mode to select."""
        headers = dict(_api_headers("runway"))
        headers["X-Runway-Version"] = "2024-11-06"
        runway_ratios = {
            ("720p", "9:16"): "720:1280", ("720p", "16:9"): "1280:720",
            ("720p", "1:1"): "720:720",
            ("1080p", "9:16"): "1080:1920",
            ("1080p", "16:9"): "1920:1080",
            ("1080p", "1:1"): "1080:1080",
        }
        ratio = runway_ratios.get(
            (params["resolution"], params["aspect_ratio"]), "720:1280")
        if params["mode"] == "image":
            payload = {"model": "gen4.5",
                       "promptImage": _data_uri(params["start_image"]),
                       "promptText": prompt, "ratio": ratio,
                       "duration": params["duration"]}
            url = f"{RUNWAY_BASE}/v1/image_to_video"
        else:
            payload = {"model": "gen4.5", "promptText": prompt,
                       "ratio": ratio,
                       "duration": params["duration"]}
            url = f"{RUNWAY_BASE}/v1/text_to_video"
        data = _submit_post(job, url, headers, payload)
        task_id = data.get("id") or (data.get("data") or {}).get("id")
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_runway_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            headers = dict(_api_headers("runway"))
            headers["X-Runway-Version"] = "2024-11-06"
            resp = requests.get(
                f"{RUNWAY_BASE}/v1/tasks/{job['provider_task_id']}",
                headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                status = str(data.get("status") or "")
                if status == "SUCCEEDED":
                    outputs = data.get("output") or []
                    if outputs:
                        _poll_done(job, outputs[0])
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("FAILED", "ABORTED", "CANCELLED"):
                    _poll_fail(job, str(data.get("error"))[:200])
                elif status in ("PENDING", "THROTTLED", "RUNNING"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "runway", status, data)
        except Exception:
            pass
        return job

    # -- Vidu direct (spare route) ---------------------------------------------
    def submit_vidu_job(job: dict, body: dict, params: dict,
                        prompt: str):
        """SPARE route: Vidu's own API for Q3 (Novita is primary).
        Wired so flipping MODEL_PROVIDER is one line; the direct
        route stays unpriced (Vidu sells credits and publishes no
        per-second price), so it must not become primary until a
        price exists. Endpoints per Vidu's published API docs;
        PAYLOAD-UNVERIFIED. Audio is on: Q3 speaks natively."""
        mode = params["mode"]
        endpoints = {"text": "text2video", "image": "img2video",
                     "reference": "reference2video"}
        if mode == "image" and params["end_image"]:
            endpoint = "start-end2video"
        else:
            endpoint = endpoints[mode]
        payload = {
            "model": "viduq3-turbo",
            "prompt": prompt,
            "duration": params["duration"],
            "resolution": "540p" if params["resolution"] == "480p"
            else params["resolution"],
            "aspect_ratio": params["aspect_ratio"],
            "audio": True,
            "off_peak": True,
        }
        try:
            if mode == "image":
                images = [_beatapi_media_url(params["start_image"],
                                             "first-frame")]
                if params["end_image"]:
                    images.append(_beatapi_media_url(
                        params["end_image"], "last-frame"))
                payload["images"] = images
            elif mode == "reference" and params["reference_image_urls"]:
                payload["images"] = [
                    _beatapi_media_url(u, "reference-image")
                    for u in params["reference_image_urls"]]
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(job, f"{VIDU_BASE}/ent/v2/{endpoint}",
                            _api_headers("vidu"), payload)
        task_id = data.get("task_id") or (data.get("data") or {}).get(
            "task_id")
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_vidu_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{VIDU_BASE}/ent/v2/tasks/{job['provider_task_id']}"
                "/creations",
                headers=_api_headers("vidu"), timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                state = str(data.get("state") or "")
                if state == "success":
                    creations = data.get("creations") or []
                    url = creations[0].get("url") if creations else None
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif state in ("failed", "error"):
                    _poll_fail(job, str(data.get("err_code"))[:200])
                elif state in ("created", "queueing", "processing"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "vidu", state, data)
        except Exception:
            pass
        return job

    # -- MiniMax direct (spare route) --------------------------------------------
    def submit_minimax_job(job: dict, body: dict, params: dict,
                           prompt: str):
        """SPARE route: MiniMax direct for H3 (BeatAPI is primary;
        the published direct rate is recorded in pricing.py's
        DISPLACED_RATES). H3 generation runs on the v2 endpoint
        with a multimodal content array per MiniMax's H3 guide;
        PAYLOAD-UNVERIFIED. H3 generates speech natively; there is
        no audio switch to send."""
        content = [{"type": "text", "text": prompt}]
        if params["mode"] in ("image", "reference"):
            first = params["start_image"] or (
                params["reference_image_urls"] or [None])[0]
            if first:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": _data_uri(first)},
                    "role": "first_frame",
                })
            if params["end_image"]:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": _data_uri(params["end_image"])},
                    "role": "last_frame",
                })
        payload = {"model": "MiniMax-H3", "content": content,
                   "duration": params["duration"],
                   "resolution": "768P"}
        data = _submit_post(job, f"{MINIMAX_BASE}/v2/video_generation",
                            _api_headers("minimax"), payload)
        task_id = (data.get("task_id")
                   or (data.get("data") or {}).get("task_id")
                   or data.get("id"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_minimax_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{MINIMAX_BASE}/v2/query/video_generation",
                params={"task_id": job["provider_task_id"]},
                headers=_api_headers("minimax"), timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                payload = data.get("data") or data
                status = str(payload.get("status") or "")
                task = payload.get("task") or {}
                url = ((task.get("content") or {}).get("url")
                       or payload.get("video_url"))
                if status in ("Success", "succeeded", "success"):
                    if url:
                        _poll_done(job, url)
                    else:
                        # Terminal word with no result URL used
                        # to fall into the running branch and
                        # spin forever; count it like every other
                        # success-without-a-result.
                        _poll_retry_or_fail(job)
                elif status in ("Fail", "Failed", "failed", "error"):
                    _poll_fail(job, str(payload.get("error"))[:200])
                elif status in ("Preparing", "Queueing", "Processing",
                                "Running"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "minimax", status, payload)
        except Exception:
            pass
        return job

    # -- SpicyAPI (uncensored lanes) -------------------------------------------
    SPICYAPI_MODEL_IDS = {
        "wan22_unc": "alibaba/wan-2.2-spicy/image-to-video",
        "wan3_unc": "alibaba/wan-3.0-spicy/image-to-video",
        "seedance25_unc": "bytedance/seedance-2.5-spicy/image-to-video",
        "vidu_q3_unc": "vidu/q3-spicy/image-to-video",
        # Catalog completion (2026-11-17): the rest of the
        # supplier's spicy video families, ids from each
        # family's own model page. Wan 2.6 / 2.7 joined this
        # table when their lanes moved routes the same day.
        "wan26_unc": "alibaba/wan-2.6-spicy/image-to-video",
        "wan27_unc": "alibaba/wan-2.7-spicy/image-to-video",
        "wan3prime_unc": "alibaba/wan-3.0-prime-spicy/image-to-video",
        "wan22_lora_unc": "alibaba/wan-2.2-spicy-lora/image-to-video",
        "seedance2_unc": "bytedance/seedance-2.0-spicy/image-to-video",
        "seedance2fast_unc": "bytedance/seedance-2.0-fast-spicy/image-to-video",
        "seedance2mini_unc": "bytedance/seedance-2.0-mini-spicy/image-to-video",
        "seedance15pro_unc": "bytedance/seedance-1.5-pro-spicy/image-to-video",
        "ltx23_unc": "lightricks/ltx-2.3-spicy/image-to-video",
        "ltx23_lora_unc": "lightricks/ltx-2.3-spicy-lora/image-to-video",
    }
    # SpicyAPI lanes whose published schema carries a
    # generate_audio field (selected on: the platform ships
    # audio on where the build generates it). Sending the
    # field to a schema that does not list it fails the
    # supplier's validation (Wan 2.6's page says so outright),
    # so the set is per-lane from each model page, read
    # 2026-11-17.
    SPICYAPI_AUDIO_MODELS = (
        "wan3_unc", "wan3prime_unc", "wan27_unc", "seedance25_unc",
        "seedance2_unc", "seedance2fast_unc", "seedance2mini_unc",
        "seedance15pro_unc", "vidu_q3_unc",
    )
    # SpicyAPI lanes whose schema carries aspect_ratio: the
    # two Wan 3.0 builds and the Seedance 2.0/1.5 builds
    # except 2.5 (whose schema has none). The rest inherit
    # the start image's shape.
    SPICYAPI_ASPECT_MODELS = (
        "wan3_unc", "wan3prime_unc", "seedance2_unc",
        "seedance2fast_unc", "seedance2mini_unc", "seedance15pro_unc",
    )
    # SpicyAPI lanes whose schema carries a closing-frame
    # field (last_image_url). Vidu Q3, Wan 2.6, Wan 2.7 and
    # both LTX builds publish no such field on their pages,
    # so an end frame is never sent to them.
    SPICYAPI_END_FRAME_MODELS = (
        "wan22_unc", "wan3_unc", "wan3prime_unc", "seedance25_unc",
        "seedance2_unc", "seedance2fast_unc", "seedance2mini_unc",
        "seedance15pro_unc",
    )

    def submit_spicyapi_job(job: dict, body: dict, params: dict,
                            prompt: str):
        """SpicyAPI task API: POST /api/v1/jobs/createTask
        {model, input} with an Idempotency-Key header, poll GET
        /api/v1/jobs/recordInfo?taskId=. The endpoint paths, the
        Bearer auth and every input field below are confirmed
        from the supplier's published docs and the per-model
        input schemas its catalog API serves (read 2026-11-17);
        the response envelope is PAYLOAD-UNVERIFIED. The schemas
        are closed, so only fields the wired endpoint's schema
        lists are sent: generate_audio (default true there) is
        selected on the lanes whose schema carries it, and the
        platform's 480p tier maps to the supplier's 540p tier
        for Vidu Q3, the same mapping the Novita lane uses."""
        model = job["model"]
        slug = SPICYAPI_MODEL_IDS.get(model)
        if not slug:
            _submit_failed(job, f"no spicyapi model id for {model}")
        inp = {"prompt": prompt,
               "duration_seconds": params["duration"],
               "resolution": params["resolution"]}
        if model == "vidu_q3_unc" and params["resolution"] == "480p":
            inp["resolution"] = "540p"
        if model in SPICYAPI_AUDIO_MODELS:
            inp["generate_audio"] = True
        if model in SPICYAPI_ASPECT_MODELS:
            inp["aspect_ratio"] = params["aspect_ratio"]
        try:
            inp["image_url"] = _beatapi_media_url(
                params["start_image"], "first-frame")
            if params["end_image"] and model in SPICYAPI_END_FRAME_MODELS:
                inp["last_image_url"] = _beatapi_media_url(
                    params["end_image"], "last-frame")
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        headers = dict(_api_headers("spicyapi"))
        headers["Idempotency-Key"] = job["id"]
        data = _submit_post(
            job, f"{SPICYAPI_BASE}/api/v1/jobs/createTask",
            headers, {"model": slug, "input": inp})
        payload = data.get("data") or {}
        task_id = (payload.get("taskId") or payload.get("task_id")
                   or payload.get("id") or data.get("taskId"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_spicyapi_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{SPICYAPI_BASE}/api/v1/jobs/recordInfo",
                params={"taskId": job["provider_task_id"]},
                headers=_api_headers("spicyapi"), timeout=15)
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data") or {}
                state = str(data.get("state")
                            or data.get("status") or "").lower()
                if state in ("success", "succeeded"):
                    # SpicyAPI's real envelope (verified live
                    # 2026-11-17 on image jobs) reports state
                    # "succeeded" and carries results under
                    # output.assets; the resultJson/resultUrl
                    # forms stay as fallbacks.
                    # The verified envelope (live, 2026-11-17)
                    # carries results under output.assets, so
                    # read those first, exactly like the image
                    # mirror's _spicyapi_result_url; the
                    # Kie-shaped resultJson/resultUrl forms stay
                    # as fallbacks, in that order.
                    urls = []
                    for asset in (data.get("output")
                                  or {}).get("assets") or []:
                        if isinstance(asset, dict) \
                                and asset.get("url"):
                            urls.append(asset["url"])
                    if not urls:
                        raw = data.get("resultJson")
                        if raw:
                            try:
                                urls = (json.loads(raw) or {}).get(
                                    "resultUrls") or []
                            except Exception:
                                urls = []
                    if not urls and data.get("resultUrl"):
                        urls = [data["resultUrl"]]
                    if urls:
                        _poll_done(job, urls[0])
                    else:
                        _poll_retry_or_fail(job)
                elif state in ("fail", "failed", "failure", "error",
                               "cancelled", "canceled"):
                    _poll_fail(
                        job, f"{data.get('failCode')}: {data.get('failMsg')}")
                elif state in ("waiting", "queuing", "generating",
                               "running", "processing", "pending"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "spicyapi", state, data)
        except Exception:
            pass
        return job

    # -- HotAPI (uncensored lanes) ----------------------------------------------
    def submit_hotapi_job(job: dict, body: dict, params: dict,
                          prompt: str):
        """HotAPI task API: POST /v1/minimax-h3-spicy, poll GET
        /v1/tasks/{id}. The endpoint, the input fields and the
        mode-by-fields shape are from the supplier's published
        model page and getting-started guide (read 2026-11-17);
        the response envelope is PAYLOAD-UNVERIFIED. One
        endpoint covers every mode: a prompt alone is
        text-to-video, image_url (+ last_frame_url) is
        frame-guided, and the reference fields carry
        references. The platform's 720p tier is the supplier's
        768p tier. Audio is always on for this build; there is
        no switch to send."""
        inp = {"prompt": prompt, "duration": params["duration"],
               "resolution": {"480p": "480p", "720p": "768p",
                              "1080p": "1080p"}[params["resolution"]],
               "aspect_ratio": params["aspect_ratio"]}
        try:
            if params["mode"] == "image":
                inp["image_url"] = _beatapi_media_url(
                    params["start_image"], "first-frame")
                if params["end_image"]:
                    inp["last_frame_url"] = _beatapi_media_url(
                        params["end_image"], "last-frame")
            elif params["mode"] == "reference":
                if params["reference_image_urls"]:
                    inp["reference_image_urls"] = [
                        _beatapi_media_url(u, "reference-image")
                        for u in params["reference_image_urls"]]
                if params["reference_video_urls"]:
                    inp["reference_video_urls"] = [
                        _beatapi_media_url(u, "reference-video")
                        for u in params["reference_video_urls"]]
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(
            job, f"{HOTAPI_BASE}/v1/minimax-h3-spicy",
            _api_headers("hotapi"), inp)
        payload = data.get("data") or data
        task_id = (payload.get("task_id") or payload.get("taskId")
                   or payload.get("id"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        jobs[job["id"]] = job

    def mirror_hotapi_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            resp = requests.get(
                f"{HOTAPI_BASE}/v1/tasks/{job['provider_task_id']}",
                headers=_api_headers("hotapi"), timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                payload = data.get("data") or data
                status = str(payload.get("status")
                             or payload.get("state") or "").lower()
                if status in ("completed", "succeeded", "success",
                              "done", "finished"):
                    output = payload.get("output")
                    url = (payload.get("url")
                           or payload.get("video_url")
                           or payload.get("result_url"))
                    if not url and isinstance(output, dict):
                        url = output.get("url") or output.get("video_url")
                    if not url and isinstance(output, list) and output:
                        first = output[0]
                        url = first.get("url") if isinstance(
                            first, dict) else first
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "error", "cancelled",
                                "canceled"):
                    _poll_fail(job, str(payload.get("error"))[:200])
                elif status in ("processing", "running", "pending",
                                "queued", "in_progress", "created",
                                "starting"):
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "hotapi", status, payload)
        except Exception:
            pass
        return job

    # -- Wiro (uncensored lanes) --------------------------------------------------
    WIRO_MODEL_SLUGS = {
        "wan26_unc": "wan-2-6-uncensored",
        "wan27_unc": "wan-2-7-uncensored",
        "wan3pro_unc": "wan-3-0-pro-uncensored",
    }
    # The supplier's resolution enum differs per model (its own
    # Tool/Detail parameter lists, read 2026-11-17).
    WIRO_RESOLUTIONS = {
        "wan26_unc": {"720p": "720P", "1080p": "1080P"},
        "wan27_unc": {"720p": "720p", "1080p": "1080p"},
        "wan3pro_unc": {"1080p": "1080p", "2K": "2K", "4K": "4K"},
    }

    def _wiro_headers() -> dict:
        """Wiro signature auth: the x-api-key header plus an
        HMAC-SHA256 signature of (api secret + nonce), keyed
        with the API key, fresh per request. (The supplier also
        offers an API-Key-Only project setting; signing works
        under the signature setting the stored project uses and
        was verified live 2026-11-17.)"""
        key = os.environ.get("WIRO_API_KEY", "")
        secret = os.environ.get("WIRO_API_SECRET", "")
        nonce = str(int(_now() * 1000))
        signature = hmac.new(
            key.encode(), (secret + nonce).encode(),
            hashlib.sha256).hexdigest()
        return {"x-api-key": key, "x-nonce": nonce,
                "x-signature": signature,
                "Content-Type": "application/json"}

    def submit_wiro_job(job: dict, body: dict, params: dict,
                        prompt: str):
        """Wiro run API: POST /v1/Run/wiro-partners/{slug} with
        the model's parameters flat in the body, then poll POST
        /v1/Task/Detail with the run's socket access token. The
        endpoint shapes, parameter ids and enum values are from
        the supplier's published docs and its Tool/Detail
        parameter lists (read 2026-11-17); only parameters a
        model's own list carries are sent (the 2.7 build has no
        ratio parameter; the Pro build takes a last-frame
        image). Generated audio is a mode on all three builds
        and is selected by default."""
        model = job["model"]
        slug = WIRO_MODEL_SLUGS.get(model)
        if not slug:
            _submit_failed(job, f"no wiro slug for {model}")
        payload = {
            "prompt": prompt,
            "duration": str(params["duration"]),
            "resolution": WIRO_RESOLUTIONS[model][params["resolution"]],
            "audioEnabled": "true",
        }
        if model in ("wan26_unc", "wan3pro_unc"):
            payload["ratio"] = params["aspect_ratio"]
        try:
            if params["mode"] == "image":
                payload["inputImage"] = _beatapi_media_url(
                    params["start_image"], "first-frame")
                if model == "wan3pro_unc" and params["end_image"]:
                    payload["inputImageLast"] = _beatapi_media_url(
                        params["end_image"], "last-frame")
        except Exception as exc:
            _submit_failed(job, f"media upload failed: {exc}")
        data = _submit_post(
            job, f"{WIRO_BASE}/v1/Run/wiro-partners/{slug}",
            _wiro_headers(), payload)
        task_id = (data.get("taskid") or data.get("taskId")
                   or data.get("id"))
        if not task_id:
            _submit_failed(job, "submit returned no task id")
        job["provider_task_id"] = task_id
        job["provider_socket_token"] = data.get("socketaccesstoken")
        jobs[job["id"]] = job

    def mirror_wiro_status(job: dict) -> dict:
        if not _poll_gate(job):
            return job
        try:
            poll_body = (
                {"tasktoken": job["provider_socket_token"]}
                if job.get("provider_socket_token")
                else {"taskid": job["provider_task_id"]})
            resp = requests.post(
                f"{WIRO_BASE}/v1/Task/Detail",
                headers=_wiro_headers(), json=poll_body, timeout=15)
            if resp.status_code == 200:
                data = resp.json() or {}
                tasks = data.get("tasklist") or []
                task = tasks[0] if tasks else {}
                status = str(task.get("status") or "")
                if status == "task_postprocess_end":
                    if str(task.get("pexit")) == "0":
                        outputs = task.get("outputs") or []
                        first = outputs[0] if outputs else {}
                        url = first.get("url") if isinstance(
                            first, dict) else None
                        if url:
                            cost = None
                            charged = task.get("chargedcost")
                            if charged is not None:
                                # Wiro bills in dollars.
                                cost = float(charged) * 100
                            _poll_done(job, url, cost_cents=cost)
                        else:
                            _poll_retry_or_fail(job)
                    else:
                        _poll_fail(
                            job,
                            f"wiro pexit {task.get('pexit')}: "
                            f"{str(task.get('debugoutput'))[:150]}")
                elif status == "task_cancel":
                    _poll_fail(job, "wiro task cancelled")
                elif status.startswith("task_"):
                    # Every other task_* state is a live stage.
                    # task_error included: Wiro's own docs say it
                    # does not close a task (only
                    # task_postprocess_end and task_cancel do).
                    job["status"] = "running"
                    jobs[job["id"]] = job
                else:
                    _note_unparsed(job, "wiro", status, task)
        except Exception:
            pass
        return job

    # -- dispatchers -------------------------------------------------------------
    def submit_api_job(job: dict, body: dict, params: dict, prompt: str,
                       base: str):
        """Dispatch one per-second job to the supplier recorded on
        it. fal and BeatAPI keep their original submit functions;
        every supplier added by the follow-up pass routes here."""
        provider = job["provider"]
        if provider == "beatapi":
            submit_beatapi_job(job, body, params, prompt)
        elif provider == "kie":
            submit_kie_job(job, body, params, prompt)
        elif provider == "cometapi":
            submit_cometapi_job(job, body, params, prompt)
        elif provider == "piapi":
            submit_piapi_job(job, body, params, prompt)
        elif provider == "openrouter":
            submit_openrouter_job(job, body, params, prompt)
        elif provider == "novita":
            submit_novita_job(job, body, params, prompt)
        elif provider == "wavespeed":
            submit_wavespeed_job(job, body, params, prompt)
        elif provider == "eachlabs":
            submit_eachlabs_job(job, body, params, prompt)
        elif provider == "runway":
            submit_runway_job(job, body, params, prompt)
        elif provider == "vidu":
            submit_vidu_job(job, body, params, prompt)
        elif provider == "minimax":
            submit_minimax_job(job, body, params, prompt)
        elif provider == "spicyapi":
            submit_spicyapi_job(job, body, params, prompt)
        elif provider == "hotapi":
            submit_hotapi_job(job, body, params, prompt)
        elif provider == "wiro":
            submit_wiro_job(job, body, params, prompt)
        else:
            submit_fal_job(job, body, params, prompt, base)

    def mirror_api_status(job: dict) -> dict:
        """Poll dispatcher for the follow-up-pass suppliers."""
        provider = job.get("provider")
        if provider == "kie":
            return mirror_kie_status(job)
        if provider == "cometapi":
            return mirror_cometapi_status(job)
        if provider == "piapi":
            return mirror_piapi_status(job)
        if provider == "openrouter":
            return mirror_openrouter_status(job)
        if provider == "novita":
            return mirror_novita_status(job)
        if provider == "wavespeed":
            return mirror_wavespeed_status(job)
        if provider == "eachlabs":
            return mirror_eachlabs_status(job)
        if provider == "runway":
            return mirror_runway_status(job)
        if provider == "vidu":
            return mirror_vidu_status(job)
        if provider == "minimax":
            return mirror_minimax_status(job)
        if provider == "spicyapi":
            return mirror_spicyapi_status(job)
        if provider == "hotapi":
            return mirror_hotapi_status(job)
        if provider == "wiro":
            return mirror_wiro_status(job)
        return job


    def create_api_job(user: dict, body: dict, model: str, base: str,
                       prompt: str, music_prompt: str, camera: dict = None,
                       speech_script: str = "", speech_voice: str = "",
                       captions: bool = False):
        """Quote -> hold -> submit, per design s.3. Any failure at or
        before submission leaves an error job with the clean public
        message and the hold fully released: no charge, nothing
        stuck."""
        params = _api_job_params(body, model)
        reload_state = {}
        state = api_lane_state(model)
        if state["paused"]:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        prov = provider_for(model, params["resolution"])
        if not os.environ.get(PROVIDER_KEY_ENV.get(prov, "FAL_KEY")):
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        est = pricing.per_second_estimate(
            model, params["resolution"], params["generated_seconds"],
            params["reference_video_seconds"], rate_table()["rates"],
            owner=is_owner(user),
            lip_sync=lip_sync_applies(model, speech_script),
        )
        if not ceiling_allows(user, est["cap_raw_cents"]):
            attempt_auto_reload(user, reload_state)
        if not ceiling_allows(user, est["cap_raw_cents"]):
            raise HTTPException(
                status_code=402,
                detail=reload_fail_detail(reload_state, "this job would exceed the total generation your payments cover"),
            )
        # Hold the cap before the provider call exists (Rule 1).
        # Included budget pays first for subscribers only;
        # pay-per-video accounts hold against balance (Amendment 3).
        u = usage(user)
        if user["plan"] == "creator" and included_budget_remaining(user) >= est["cap_raw_cents"]:
            hold_kind = "included"
            hold_amount = est["cap_raw_cents"]
            u["included_cost_cents"] = included_cost_used(user) + hold_amount
            user["usage"] = u
        elif int(user.get("balance_cents", 0)) >= est["cap_balance_cents"]:
            hold_kind = "balance"
            hold_amount = est["cap_balance_cents"]
            user["balance_cents"] = int(user.get("balance_cents", 0)) - int(hold_amount)
        elif is_owner(user):
            # The owner answers to the provider only (Jack,
            # 2026-11-17): the hold is taken against his balance
            # whatever it holds, so settle and release keep their
            # exact accounting and nothing ever blocks him. This
            # branch sits before the auto reload branch, so a
            # reload never fires for his account.
            hold_kind = "balance"
            hold_amount = est["cap_balance_cents"]
            user["balance_cents"] = int(user.get("balance_cents", 0)) - int(hold_amount)
        elif attempt_auto_reload(user, reload_state) and int(user.get("balance_cents", 0)) >= est["cap_balance_cents"]:
            hold_kind = "balance"
            hold_amount = est["cap_balance_cents"]
            user["balance_cents"] = int(user.get("balance_cents", 0)) - int(hold_amount)
        else:
            raise HTTPException(
                status_code=402,
                detail=reload_fail_detail(reload_state, f"included budget used up; this job costs {est['balance_debit_cents']} cents of prepaid balance"),
            )
        save_user(user)
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": None,
            "provider": provider_for(model, params["resolution"]),
            "provider_request_id": None,
            "provider_task_id": None,
            "provider_cost_cents": None,
            "provider_status_url": None,
            "provider_response_url": None,
            "lane": model,
            "model": model,
            "prompt": prompt,
            "user_prompt": (camera or {}).get("user_prompt", prompt),
            "camera_presets": (camera or {}).get("camera_presets") or [],
            "camera_preset_names": (camera or {}).get("camera_preset_names") or [],
            "camera_look": (camera or {}).get("camera_look") or {},
            "status": "queued",
            "error": None,
            "bytes": None,
            "created_at": _now(),
            "charged_cents": 0,
            "included_seconds": 0.0,
            "budget_cost_cents": 0.0,
            "raw_cost_cents": 0.0,
            "music_prompt": music_prompt or None,
            "speech_script": speech_script or None,
            "speech_voice": speech_voice or None,
            "voiceover_status": None,
            "word_check_status": None,
            "word_check_listen_at": [],
            "captions": bool(captions),
            "captions_status": None,
            "character_id": body.get("character_id"),
            # Built-in lip sync (Jack's rule, 2026-11-17): planned
            # at creation when the job carries speech on a lane
            # that does not speak natively; the finishing stage
            # runs automatically and its cost is already in the
            # quote, the hold and the ceiling above.
            "lip_sync": lip_sync_applies(model, speech_script),
            "lipsync_status": (
                "planned" if lip_sync_applies(model, speech_script)
                else "native" if speech_script else None),
            "lipsync_cost_cents": 0.0,
            "plan_at_job": user["plan"],
            "retry_of": body.get("retry_of"),
            "last_poll": 0,
            "duration_seconds": params["requested_duration"],
            "resolution": params["resolution"],
            "aspect_ratio": params["aspect_ratio"],
            # Length plan (2026-11-17): the native segments
            # behind the requested length and the chain driver's
            # state. The hold above was taken on
            # generated_seconds, the full multi-segment cost.
            "generated_seconds": params["generated_seconds"],
            "chain_plan": list(params["plan_segments"]),
            "chain_index": 0,
            "chain_results": [],
            "chain_cost_cents": 0.0,
            "chain_cost_seen": False,
            "segments_total": len(params["plan_segments"]),
            "segments_done": 0,
            # No trimming, ever (Jack's rule, 2026-11-17):
            # generated seconds always equal the ordered length,
            # so there is no trim target; chained jobs bill the
            # generated total, which is the ordered total.
            "trim_to_seconds": None,
            "bill_generated_seconds": (
                float(params["generated_seconds"])
                if len(params["plan_segments"]) > 1 else None),
            "base_url": base,
            "chain_seed": (
                body.get("seed") if isinstance(body.get("seed"), int)
                else None),
            "reference_video_seconds": params["reference_video_seconds"],
            "reference_image_urls": params["reference_image_urls"],
            "reference_video_urls": params["reference_video_urls"],
            "reference_audio_urls": params["reference_audio_urls"],
            "hold_kind": hold_kind,
            "hold_amount": hold_amount,
            "cap_raw_cents": est["cap_raw_cents"],
        }
        # NSFW jobs are stamped not public at creation (Jack,
        # 2026-11-18): gallery_visible also classifies live, so
        # the stamp is belt and braces for any other surface.
        if job_is_nsfw(job):
            job["gallery_public"] = False
        jobs[job["id"]] = job
        submit_api_job(job, body, params, prompt, base)
        return JSONResponse(public_job(job), status_code=202)

    @web.post("/api/jobs")
    async def create_job(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if _rate_limited("jobcreate", user["id"], 10):
            raise HTTPException(status_code=429, detail="too many jobs, slow down")
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if user.get("past_due"):
            raise HTTPException(status_code=403, detail="your subscription payment is past due")
        body = await request.json()
        if not _verify_bot_check(body, client_ip(request)):
            raise HTTPException(status_code=403, detail="bot check failed")
        # The default model is Wan 2.2 for every account (Rule 3,
        # Amendment 2); a bigger model is always an active choice.
        model = body.get("model") or DEFAULT_MODEL
        if model not in MODEL_LABELS:
            raise HTTPException(status_code=422, detail="unknown model")
        lane = body.get("lane") or "draft"
        if model == DEFAULT_MODEL:
            if lane not in pricing.MEASURED_COST_CENTS:
                raise HTTPException(status_code=422, detail="lane must be draft or production")
            if pricing.lane_paused(lane):
                raise HTTPException(status_code=503, detail="this lane is paused while its costs are reviewed")
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > 2000:
            raise HTTPException(status_code=422, detail="a prompt of up to 2000 characters is required")
        # Custom audio is a paid feature (Jack, 2026-11-16;
        # Amendment 3: any paying customer, subscription or
        # pay-per-video), enforced here on the server: an account
        # that has never paid gets a clean rejection before any
        # dispatch.
        music_prompt = (body.get("music_prompt") or "").strip()
        if len(music_prompt) > CUSTOM_AUDIO_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"music prompt must be {CUSTOM_AUDIO_MAX_CHARS} characters or fewer",
            )
        if music_prompt and not is_paid(user):
            raise HTTPException(
                status_code=403, detail="custom audio is a paid feature"
            )
        # Speech script (Jack's correction, 2026-11-16): a spoken
        # line for the voiceover stage, or the dialogue the user
        # wants a speech-native lane to perform. Paid custom audio,
        # same gate as the music prompt.
        speech_script = (body.get("speech_script") or "").strip()
        if len(speech_script) > SPEECH_SCRIPT_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"speech script must be {SPEECH_SCRIPT_MAX_CHARS} characters or fewer",
            )
        speech_voice = (body.get("speech_voice") or "").strip()
        if speech_voice and speech_voice not in SPEECH_VOICES:
            raise HTTPException(status_code=422, detail="unknown speech voice")
        # Captions (Jack's call, 2026-11-18): a generation time
        # opt in, either checked or not. The finishing stage
        # burns word timed captions into the delivered file when
        # the job has speech. No gate and no charge, and existing
        # gallery videos are never captioned after the fact.
        captions = bool(body.get("captions"))
        if speech_script and not is_paid(user):
            raise HTTPException(
                status_code=403, detail="voiceover is a paid feature"
            )
        # Camera presets (available to every plan; model switching
        # stays the paid gate, presets are not one). The final
        # prompt sent to any lane is the composed one; the record
        # keeps the user's original prompt and the preset names.
        try:
            camera_ids, camera_look = validate_camera_selection(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        # Effects: an unknown effect id is a clean 422 for every
        # account, checked here before any gate, hold, or dispatch.
        effect_id = body.get("effect") or None
        if effect_id is not None and not isinstance(effect_id, str):
            raise HTTPException(status_code=422, detail="effect must be an effect id")
        if effect_id and effect_id not in EFFECTS:
            raise HTTPException(
                status_code=422, detail=f"unknown effect: {effect_id}")
        camera = {
            "user_prompt": prompt,
            "camera_presets": camera_ids,
            "camera_preset_names": [
                CAMERA_PRESETS[pid]["name"] for pid in camera_ids
            ],
            "camera_look": camera_look,
        }
        if camera_ids or camera_look:
            prompt = compose_camera_prompt(prompt, camera_ids, camera_look)
        # Form finishers (Pass 1): shape validation for every lane,
        # always before a quote, hold, charge, or dispatch. Unknown
        # or unsupported combinations are clean 422s.
        try:
            segments = int(body.get("segments") if body.get("segments") is not None else 1)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="segments must be a whole number")
        if segments < 1 or segments > MAX_SEGMENTS:
            raise HTTPException(
                status_code=422,
                detail=f"segments must be between 1 and {MAX_SEGMENTS}")
        aspect = body.get("aspect_ratio") or "9:16"
        if aspect not in HOUSE_ASPECTS:
            raise HTTPException(
                status_code=422,
                detail="aspect_ratio must be one of " + ", ".join(HOUSE_ASPECTS))
        start_image = body.get("start_image_png_b64") or None
        end_image = body.get("end_image_png_b64") or None
        if body.get("start_image_job_id") and not start_image:
            # One-click attach (Pass 3): the start frame is one of
            # the user's own finished image jobs. Resolve it here,
            # server-side, so the rest of creation treats it exactly
            # like an uploaded start image.
            image_job = jobs.get(body["start_image_job_id"])
            if not image_job or image_job.get("user_id") != user["id"] \
                    or image_job.get("kind") != "image":
                raise HTTPException(status_code=404, detail="unknown start image")
            image_job = mirror_status(image_job)
            if image_job["status"] != "done":
                raise HTTPException(
                    status_code=409, detail="that first frame is not finished yet")
            start_image = base64.b64encode(
                ensure_image_file(image_job).read_bytes()).decode()
            body["start_image_png_b64"] = start_image
        if body.get("character_id"):
            # Character reuse (2026-11-17): the saved character's
            # first image becomes the start image when the job has
            # none, and its hosted images ride as references on
            # lanes that take references. Character references are
            # images, so they never change the price.
            character = _find_character(user, body["character_id"])
            if not character:
                raise HTTPException(status_code=404, detail="unknown character")
            char_images = character.get("images") or []
            if char_images and not start_image:
                start_image = char_images[0].get("b64")
                body["start_image_png_b64"] = start_image
            if model in PER_SECOND_MODELS and char_images:
                hosted = [im["fal_url"] for im in char_images
                          if im.get("fal_url")]
                if hosted and model not in TEXT_IMAGE_ONLY_MODELS \
                        and model not in ("wan22_unc", "wan3_unc",
                                          "seedance25_unc", "vidu_q3_unc",
                                          "wan3prime_unc", "wan22_lora_unc",
                                          "seedance2_unc", "seedance2fast_unc",
                                          "seedance2mini_unc",
                                          "seedance15pro_unc", "ltx23_unc",
                                          "ltx23_lora_unc", "wan26_unc",
                                          "wan27_unc",
                                          "wan_motion"):
                    existing = body.get("reference_image_urls") or []
                    body["reference_image_urls"] = hosted + list(existing)
        try:
            _validate_image_b64(start_image, "start image")
            _validate_image_b64(end_image, "end image")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        # Fun Camera routing (Pass 4): a fun-* preset is camera
        # conditioning on the worker's Fun Camera path, Production
        # lane semantics and pricing. It is never prompt text, and
        # the shapes it cannot take are clean 422s here, before any
        # quote, hold, or dispatch.
        fun_preset = fun_camera_preset(camera_ids)
        camera_recipe = None
        # None means the request carried no speed at all; the Fun
        # Camera resolver below turns that into the default. (The
        # old code initialized this to 1.0, which silently put every
        # Fun Camera job on the measured-dead speed.)
        camera_speed = None
        if body.get("camera_speed") is not None:
            try:
                camera_speed = float(body["camera_speed"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="camera_speed must be a number")
        if fun_preset is not None:
            # The (recipe, speed) a Fun Camera job runs with resolves
            # in fun_camera.resolve_fun_camera, the one place that
            # mapping lives. Speed is the user's choice, honored and
            # clamped there (3.0 only when the request carries no
            # speed); the recipe stays on the distilled path, the
            # only one measured to pan (Jack's correction, 2026-11-16:
            # the finish-pass lock to 3.0 overshot — people choose).
            camera_recipe, camera_speed = fun_camera.resolve_fun_camera(
                camera_speed
            )
            if model in PER_SECOND_MODELS:
                raise HTTPException(
                    status_code=422,
                    detail="Fun Camera presets run on the Wan 2.2 Production lane")
            if lane != "production":
                raise HTTPException(
                    status_code=422,
                    detail="Fun Camera presets run on the Production lane")
            if segments > 1:
                raise HTTPException(
                    status_code=422,
                    detail="Fun Camera presets render one segment at a time")
            if end_image:
                raise HTTPException(
                    status_code=422,
                    detail="Fun Camera presets do not take an end image")
            if not 0.1 <= camera_speed <= 3.0:
                raise HTTPException(
                    status_code=422,
                    detail="camera speed must be between 0.1 and 3.0")
        # Effects routing (Pass 9): an effect job runs on its own
        # lane in the worker, priced as a Production segment. The
        # prompt the user wrote is the subject the effect acts on;
        # the worker builds the effect's full prompt from it. The
        # shapes an effect cannot take are clean 422s here, before
        # any quote, hold, or dispatch. Paid-only is enforced by
        # the paid path below: the lane is not Draft, so a
        # never-paid free account gets the usual 403 there.
        if effect_id:
            if model in PER_SECOND_MODELS:
                raise HTTPException(
                    status_code=422,
                    detail="effects run on Wan 2.2")
            if lane == "draft":
                raise HTTPException(
                    status_code=422,
                    detail="effects are a paid feature and do not run on the Draft lane")
            if not start_image:
                raise HTTPException(
                    status_code=422,
                    detail="effects need a start image. Pick an effect and upload the photo it acts on")
            if segments > 1:
                raise HTTPException(
                    status_code=422,
                    detail="effects render one segment at a time")
            if end_image:
                raise HTTPException(
                    status_code=422,
                    detail="effects do not take an end image")
            if camera_ids or camera_look:
                raise HTTPException(
                    status_code=422,
                    detail="effects do not combine with camera presets")
            lane = "effects"
        # House Uncut lane (2026-11-18, Jack's order): the
        # community Wan 2.2 NSFW LoRA pair on our own worker.
        # The model choice is the lane. It is paid only (the free
        # path below refuses every non Draft lane), it needs a
        # start image because the LoRA is image to video only,
        # and it combines with neither an effect nor a Fun Camera
        # preset. An end image is refused by the house shape
        # checks below, the same as every non Production lane.
        # Everything downstream (the ceiling, the included
        # budget, the balance price, the dispatch) reads the
        # worker lane exactly as it does for Production.
        if model == "wan22_uncut":
            if pricing.lane_paused("nsfw"):
                raise HTTPException(
                    status_code=503,
                    detail="this lane is paused while its costs are reviewed")
            if effect_id:
                raise HTTPException(
                    status_code=422,
                    detail="Effects run on Wan 2.2. Pick that model for an effect video")
            if fun_preset is not None:
                raise HTTPException(
                    status_code=422,
                    detail="Fun Camera presets run on Wan 2.2. Pick that model for a camera move")
            if not start_image:
                raise HTTPException(
                    status_code=422,
                    detail="This lane needs a start image. Upload the photo the video starts from")
            lane = "nsfw"
        if model in PER_SECOND_MODELS:
            # Per-second lanes take their length from
            # duration_seconds (the nine-length menu), never from
            # the house lane's segments field: lengths past the
            # lane's native maximum chain native segments inside
            # the lane itself (duration_plan), so a segments value
            # in the body is ignored here, not refused. Aspect
            # runs at the house aspects: every wired submit
            # carries the job's aspect ratio to its provider
            # (parity pass, 2026-11-17); the shapes stay
            # payload-unverified until the funded pass.
            pass
        else:
            if end_image and not start_image:
                raise HTTPException(
                    status_code=422, detail="an end image needs a start image")
            if end_image and lane != "production":
                raise HTTPException(
                    status_code=422,
                    detail="end images run on the Production lane")
            if lane == "production" and not start_image:
                raise HTTPException(
                    status_code=422,
                    detail="Production needs a start image. Videos from text alone run on the Draft lane")
            if segments > 1 and not is_paid(user):
                raise HTTPException(
                    status_code=403,
                    detail="videos with more than one segment are a paid feature. Free accounts make single Draft clips")
        # Per-plan concurrency: a user can have several jobs in
        # flight at once, up to their plan limit, and the studio
        # list shows each one's position. No serial babysitting.
        limit = CONCURRENCY_PAID if is_paid(user) else CONCURRENCY_FREE
        in_flight = 0
        for key in list(jobs.keys()):
            other = jobs.get(key)
            if (other and other.get("user_id") == user["id"]
                    and other.get("status") in ("queued", "running")
                    and other.get("provider") != "clip"
                    and other.get("kind") not in ("clip_job", "clip_output")):
                in_flight += 1
        if in_flight >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"you already have {in_flight} videos in flight. Your plan allows {limit} at a time, and one needs to finish before the next starts")
        if model in PER_SECOND_MODELS:
            # Model switching is paid-only (Amendment 3: any paying
            # customer), enforced here on the server before any
            # quote, hold, or dispatch.
            if not is_paid(user):
                raise HTTPException(
                    status_code=403, detail="model switching is a paid feature"
                )
            return create_api_job(
                user, body, model, base_url(request), prompt, music_prompt,
                camera=camera, speech_script=speech_script,
                speech_voice=speech_voice, captions=captions,
            )
        u = usage(user)
        charged = 0
        used_included = 0.0
        used_budget = 0.0
        job_cost_cents = 0.0
        is_free_job = False
        if user["plan"] == "free" and not is_paid(user):
            if lane != "draft":
                raise HTTPException(status_code=403, detail="the free plan covers the Draft lane only")
            if free_jobs_total(user) >= FREE_CLIPS_TOTAL:
                raise HTTPException(
                    status_code=403,
                    detail=f"your free clips are used up. The free plan is {FREE_CLIPS_TOTAL} clips in total",
                )
            if int(meta.get("freecount:total") or 0) >= FREE_PLATFORM_TOTAL_CEILING:
                raise HTTPException(status_code=503, detail="the free tier is paused")
            is_free_job = True
        else:
            # Paid path (Amendment 3): a Creator subscription, or a
            # pay-per-video account that has paid in. The Amendment 2
            # ceiling is the outer wall for both, checked before
            # anything is dispatched: this job's measured cost must
            # fit inside what the account has paid in.
            job_cost = float(pricing.MEASURED_COST_CENTS[lane]) * segments
            reload_state = {}
            if not ceiling_allows(user, job_cost):
                attempt_auto_reload(user, reload_state)
            if not ceiling_allows(user, job_cost):
                raise HTTPException(
                    status_code=402,
                    detail=reload_fail_detail(reload_state, "this job would exceed the total generation your payments cover"),
                )
            # Inside the ceiling, a subscriber's included budget
            # pays first (production-segment cost per segment, the
            # accounting the included-seconds counter always used);
            # everyone else pays the lane's fixed job price from
            # prepaid balance. Pay-per-video accounts have no
            # included budget, so balance is their only rail.
            # Effects debit the included budget at their own raw
            # carried cost; the other house lanes keep the
            # production-segment accounting they have always used.
            included_unit = (
                pricing.MEASURED_COST_CENTS[lane] if lane == "effects"
                else pricing.MEASURED_COST_CENTS["production"]
            )
            if user["plan"] == "creator" and included_budget_remaining(user) >= included_unit * segments:
                used_included = SEGMENT_SECONDS * segments
                used_budget = float(included_unit) * segments
            else:
                price = pricing.job_price_cents(
                    lane, owner=is_owner(user)) * segments
                # The owner never balance blocks (Jack, 2026-11-17):
                # no reload attempt and no refusal; the charge below
                # is taken from his balance whatever it holds.
                if not is_owner(user):
                    if user.get("balance_cents", 0) < price:
                        attempt_auto_reload(user, reload_state)
                    if user.get("balance_cents", 0) < price:
                        detail = (
                            f"included time used up; this job costs {price} cents of prepaid balance"
                            if user["plan"] == "creator"
                            else f"this job costs {price} cents of prepaid balance"
                        )
                        raise HTTPException(status_code=402, detail=reload_fail_detail(reload_state, detail))
                charged = price
            job_cost_cents = job_cost
        try:
            resp = requests.post(
                f"{WORKER_BASE}/jobs",
                headers=_worker_headers(),
                json={
                    "lane": lane,
                    "prompt": prompt,
                    "negative_prompt": body.get("negative_prompt") or None,
                    "seed": body.get("seed"),
                    "start_image_png_b64": start_image,
                    "end_image_png_b64": end_image,
                    "aspect_ratio": aspect,
                    "segments": segments,
                    "camera_pose": (
                        (fun_preset.get("patch") or {}).get("value")
                        if fun_preset else None
                    ),
                    "camera_speed": camera_speed if fun_preset else None,
                    "camera_recipe": camera_recipe if fun_preset else None,
                    "effect": effect_id,
                },
                timeout=30,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"generation worker unreachable: {exc}")
        if resp.status_code != 202:
            raise HTTPException(status_code=502, detail=f"generation worker refused the job ({resp.status_code})")
        worker_job_id = resp.json()["job_id"]
        # Commit the quota/balance only after the worker accepted the job.
        if is_free_job:
            u["free_jobs"] += 1
            user["free_jobs_total"] = free_jobs_total(user) + 1
            meta["freecount:total"] = int(meta.get("freecount:total") or 0) + 1
        if used_included:
            u["included_seconds"] += used_included
            u["included_cost_cents"] = included_cost_used(user) + used_budget
        if charged:
            user["balance_cents"] = user.get("balance_cents", 0) - charged
        if job_cost_cents:
            # Paid-path jobs count toward the account's settled cost
            # at dispatch (self-hosted cost is measured per job);
            # mirror_status gives it back if the job errors.
            user["cost_settled_cents"] = (
                float(user.get("cost_settled_cents") or 0.0) + job_cost_cents
            )
        user["usage"] = u
        save_user(user)
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": worker_job_id,
            "lane": lane,
            "model": model,
            "prompt": prompt,
            "user_prompt": camera["user_prompt"],
            "camera_presets": camera["camera_presets"],
            "camera_preset_names": camera["camera_preset_names"],
            "camera_look": camera["camera_look"],
            "camera_pose": (
                (fun_preset.get("patch") or {}).get("value")
                if fun_preset else None
            ),
            "camera_speed": camera_speed if fun_preset else None,
            "camera_recipe": camera_recipe if fun_preset else None,
            "effect": effect_id,
            "effect_name": EFFECTS.get(effect_id) if effect_id else None,
            "status": "queued",
            "error": None,
            "bytes": None,
            "created_at": _now(),
            "charged_cents": charged,
            "free_tier": is_free_job,
            "included_seconds": used_included,
            "included_cost_cents": used_budget,
            "cost_cents": job_cost_cents,
            "music_prompt": music_prompt or None,
            "speech_script": speech_script or None,
            "speech_voice": speech_voice or None,
            "voiceover_status": None,
            "word_check_status": None,
            "word_check_listen_at": [],
            "captions": captions,
            "captions_status": None,
            "character_id": body.get("character_id"),
            # Built-in lip sync (Jack's rule, 2026-11-17): the
            # house lanes do not speak natively, so a spoken line
            # here always plans the finishing lip sync stage.
            "lip_sync": lip_sync_applies(model, speech_script),
            "lipsync_status": (
                "planned" if speech_script else None),
            "lipsync_cost_cents": 0.0,
            "plan_at_job": user["plan"],
            "retry_of": body.get("retry_of"),
            "last_poll": 0,
            "aspect_ratio": aspect,
            "segments_total": segments,
            "segments_done": 0,
            "has_end_frame": bool(end_image),
            "start_image_png_b64": start_image,
            "end_image_png_b64": end_image,
            "partial": False,
        }
        # NSFW jobs are stamped not public at creation (Jack,
        # 2026-11-18): gallery_visible also classifies live, so
        # the stamp is belt and braces for any other surface.
        if job_is_nsfw(job):
            job["gallery_public"] = False
        jobs[job["id"]] = job
        return JSONResponse(public_job(job), status_code=202)

    @web.get("/api/jobs")
    def list_jobs(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        mine = []
        for key in list(jobs.keys()):
            job = jobs.get(key)
            if job and job.get("user_id") == user["id"]:
                mine.append(mirror_status(job))
        mine.sort(key=lambda j: j["created_at"], reverse=True)
        active = sorted(
            (j for j in mine if j["status"] in ("queued", "running")),
            key=lambda j: j["created_at"])
        positions = {j["id"]: i + 1 for i, j in enumerate(active)}
        return {"jobs": [
            public_job(j, queue_position=positions.get(j["id"]))
            for j in mine[:50]
        ]}

    @web.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request,
                mf_session: str = Cookie(default="")):
        # Single-job status read. The studio's image flow polls here
        # after POST /api/images returns a job id. This route was
        # missing until 2026-11-17: the path matched only the DELETE
        # route below, so every poll answered 405 "Method Not
        # Allowed" even though the create POST had succeeded, and
        # the studio printed that 405 text as if the image had
        # failed. Mirrors list_jobs' shape and ownership rules.
        user = require_user(request, mf_session)
        job = jobs.get(job_id)
        if not job or job.get("user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="job not found")
        job = mirror_status(job)
        position = None
        if job["status"] in ("queued", "running"):
            active = sorted(
                (j for j in (jobs.get(k) for k in list(jobs.keys()))
                 if j and j.get("user_id") == user["id"]
                 and j["status"] in ("queued", "running")),
                key=lambda j: j["created_at"])
            position = [j["id"] for j in active].index(job["id"]) + 1
        return public_job(job, queue_position=position)

    # -- start-image generation (Pass 3) --------------------------------
    # An image job is a normal platform job record (kind "image"):
    # same accounts, same holds, same Amendment 2 ceiling, same
    # Rule 8 failure restore as video jobs (mirror_image_status).
    # Gate decision: any account that could run the matching video
    # job may generate its start image. Free accounts generate for
    # their Draft clips, counted exactly like a Draft render
    # against the free ceilings, because the free tier is a capped
    # marketing budget and the first frame is part of that clip's
    # cost either way. Paid paths: a Creator's included budget
    # debits at the raw measured cost; balance pays the Rule 2
    # floor price in pricing.py. Each generation is charged,
    # including regenerations.

    def _image_params(body: dict):
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > IMAGE_PROMPT_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"an image prompt of up to {IMAGE_PROMPT_MAX_CHARS} characters is required")
        aspect = body.get("aspect_ratio") or "9:16"
        if aspect not in HOUSE_ASPECTS:
            raise HTTPException(
                status_code=422,
                detail="aspect_ratio must be one of " + ", ".join(HOUSE_ASPECTS))
        return prompt, aspect

    def _image_payment(user: dict) -> dict:
        """Which rail an image job would pay from, before anything
        runs, mirroring the video lanes' order: free ceilings for
        accounts that have never paid, then a subscriber's included
        budget at raw cost, then balance at the floor price."""
        if user["plan"] == "free" and not is_paid(user):
            return {"method": "free"}
        if (user["plan"] == "creator"
                and included_budget_remaining(user) >= pricing.IMAGE_MEASURED_COST_CENTS):
            return {"method": "included",
                    "included_debit_cents": pricing.IMAGE_MEASURED_COST_CENTS}
        charge = pricing.image_price_cents(owner=is_owner(user))
        if int(user.get("balance_cents", 0)) >= charge or is_owner(user):
            # The owner pays from balance even when the balance
            # cannot cover it (Jack, 2026-11-17), so the quote
            # names the rail create will actually use for him.
            return {"method": "balance", "charge_cents": charge}
        return {"method": "insufficient", "charge_cents": charge,
                "balance_cents": int(user.get("balance_cents", 0))}

    @web.post("/api/images/quote")
    async def quote_image(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        body = await request.json()
        _image_params(body)
        if body.get("image_model"):
            lane = pricing.IMAGE_LANES.get(str(body["image_model"]))
            if not lane:
                raise HTTPException(status_code=400, detail="unknown image model")
            size = str(body.get("image_size") or "")
            if size not in lane["raw_cents"]:
                raise HTTPException(status_code=400, detail="pick a size this model makes")
            raw, debit = pricing.image_lane_price(
                str(body["image_model"]), size, owner=is_owner(user))
            return {
                "price_cents": round(debit, 2),
                "raw_cost_cents": raw,
                "paid_only": True,
                "ceiling_ok": (
                    True if is_owner(user) else ceiling_allows(user, raw)),
                "balance_ok": int(user.get("balance_cents", 0)) >= int(round(debit)),
            }
        return {
            **pricing.image_quote(owner=is_owner(user)),
            "payment": _image_payment(user),
            "ceiling_ok": (
                True if not is_paid(user) and user["plan"] == "free"
                else ceiling_allows(user, pricing.IMAGE_MEASURED_COST_CENTS)
            ),
        }

    @web.post("/api/images")
    async def create_image(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if _rate_limited("imagecreate", user["id"], 10):
            raise HTTPException(status_code=429, detail="too many jobs, slow down")
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if user.get("past_due"):
            raise HTTPException(status_code=403, detail="your subscription payment is past due")
        body = await request.json()
        if not _verify_bot_check(body, client_ip(request)):
            raise HTTPException(status_code=403, detail="bot check failed")
        prompt, aspect = _image_params(body)
        if body.get("image_model"):
            # The curated image shelf: provider image models,
            # paid accounts only, charged per image at submit
            # and refunded in full if the image is not delivered.
            lane_id = str(body["image_model"])
            lane = pricing.IMAGE_LANES.get(lane_id)
            if not lane:
                raise HTTPException(status_code=400, detail="unknown image model")
            if not is_paid(user):
                raise HTTPException(
                    status_code=402,
                    detail="image models are for paid accounts. The image maker built into Moonfactory stays free to try")
            size = str(body.get("image_size") or "")
            if size not in lane["raw_cents"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"this image model makes {', '.join(lane['raw_cents'])} images")
            source_image = None
            if lane.get("edit"):
                # Edit lanes take one source image alongside the
                # instruction: an https URL or an uploaded data
                # URL, the same forms the video lanes accept.
                # SpicyAPI caps the source image at 10 MB, which
                # is about 14M data-URL characters.
                source_image = str(body.get("source_image") or "")
                if (not source_image.startswith(("data:image/",
                                                  "https://"))
                        or len(source_image) > 14_000_000):
                    raise HTTPException(
                        status_code=400,
                        detail="this model edits one image. Add the image you want edited first")
            raw, debit = pricing.image_lane_price(
                lane_id, size, owner=is_owner(user))
            reload_state = {}
            if not is_owner(user) and not ceiling_allows(user, raw):
                attempt_auto_reload(user, reload_state)
            if not is_owner(user) and not ceiling_allows(user, raw):
                raise HTTPException(
                    status_code=402,
                    detail=reload_fail_detail(reload_state, "this image would pass what you have paid in. Top up first"))
            charge = int(round(debit))
            # The owner never balance blocks (Jack, 2026-11-17):
            # no reload attempt and no refusal; the charge below
            # is taken from his balance whatever it holds.
            if not is_owner(user):
                if int(user.get("balance_cents", 0)) < charge:
                    attempt_auto_reload(user, reload_state)
                if int(user.get("balance_cents", 0)) < charge:
                    raise HTTPException(
                        status_code=402,
                        detail=reload_fail_detail(reload_state, "your balance is too low for this image. Top up first"))
            job_id = uuid.uuid4().hex[:16]
            job = {
                "id": job_id, "user_id": user["id"], "kind": "image",
                "lane": "image",
                "image_model": lane_id, "image_size": size,
                "provider": lane["provider"], "model": lane_id,
                "prompt": prompt, "status": "running",
                "created_at": _now(), "last_poll": 0,
                "image_raw_cents": raw, "image_charge_cents": charge,
                "error": None,
            }
            user["balance_cents"] = int(user.get("balance_cents", 0)) - charge
            save_user(user)
            jobs[job_id] = job
            try:
                _submit_provider_image(job, prompt,
                                       source_image=source_image)
            except Exception:
                _fail_provider_image(
                    job, "the image model is unavailable right now, nothing was charged")
                raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
            return {"job_id": job_id, "status": "running",
                    "charged_cents": charge}
        # Same in-flight limits as video jobs, counted together:
        # an image render is a render.
        limit = CONCURRENCY_PAID if is_paid(user) else CONCURRENCY_FREE
        in_flight = 0
        for key in list(jobs.keys()):
            other = jobs.get(key)
            if (other and other.get("user_id") == user["id"]
                    and other.get("status") in ("queued", "running")
                    and other.get("provider") != "clip"
                    and other.get("kind") not in ("clip_job", "clip_output")):
                in_flight += 1
        if in_flight >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"you already have {in_flight} jobs in flight. Your plan allows {limit} at a time, and one needs to finish before the next starts")
        u = usage(user)
        charged = 0
        used_budget = 0.0
        job_cost_cents = 0.0
        is_free_job = False
        if user["plan"] == "free" and not is_paid(user):
            if free_jobs_total(user) >= FREE_CLIPS_TOTAL:
                raise HTTPException(
                    status_code=403,
                    detail=f"your free clips are used up. The free plan is {FREE_CLIPS_TOTAL} clips in total",
                )
            if int(meta.get("freecount:total") or 0) >= FREE_PLATFORM_TOTAL_CEILING:
                raise HTTPException(status_code=503, detail="the free tier is paused")
            is_free_job = True
        else:
            # Amendment 2 ceiling, before dispatch, as on every
            # paid path.
            if not ceiling_allows(user, pricing.IMAGE_MEASURED_COST_CENTS):
                raise HTTPException(
                    status_code=402,
                    detail="this job would exceed the total generation your payments cover",
                )
            payment = _image_payment(user)
            if payment["method"] == "included":
                used_budget = float(pricing.IMAGE_MEASURED_COST_CENTS)
            elif payment["method"] == "balance":
                charged = payment["charge_cents"]
            else:
                raise HTTPException(
                    status_code=402,
                    detail=f"included budget used up. A first frame costs {payment['charge_cents']} cents of prepaid balance")
            job_cost_cents = float(pricing.IMAGE_MEASURED_COST_CENTS)
        try:
            resp = requests.post(
                f"{WORKER_BASE}/image-jobs",
                headers=_worker_headers(),
                json={
                    "prompt": prompt,
                    "aspect_ratio": aspect,
                    "seed": body.get("seed"),
                },
                timeout=30,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"generation worker unreachable: {exc}")
        if resp.status_code != 202:
            raise HTTPException(status_code=502, detail=f"generation worker refused the job ({resp.status_code})")
        worker_job_id = resp.json()["job_id"]
        # Commit the quota/balance only after the worker accepted
        # the job, exactly like the video lanes.
        if is_free_job:
            u["free_jobs"] += 1
            user["free_jobs_total"] = free_jobs_total(user) + 1
            meta["freecount:total"] = int(meta.get("freecount:total") or 0) + 1
        if used_budget:
            u["included_cost_cents"] = included_cost_used(user) + used_budget
        if charged:
            user["balance_cents"] = user.get("balance_cents", 0) - charged
        if job_cost_cents:
            user["cost_settled_cents"] = (
                float(user.get("cost_settled_cents") or 0.0) + job_cost_cents
            )
        user["usage"] = u
        save_user(user)
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": worker_job_id,
            "provider": "worker-image",
            "kind": "image",
            "lane": "image",
            "model": None,
            "prompt": prompt,
            "user_prompt": prompt,
            "camera_presets": [],
            "camera_preset_names": [],
            "camera_look": {},
            "status": "queued",
            "error": None,
            "bytes": None,
            "created_at": _now(),
            "charged_cents": charged,
            "free_tier": is_free_job,
            "included_cost_cents": used_budget,
            "cost_cents": job_cost_cents,
            "music_prompt": None,
            "plan_at_job": user["plan"],
            "retry_of": body.get("retry_of"),
            "last_poll": 0,
            "aspect_ratio": aspect,
        }
        jobs[job["id"]] = job
        return JSONResponse(
            {**public_job(job), "quote": pricing.image_quote(owner=is_owner(user))},
            status_code=202)

    @web.post("/api/jobs/{job_id}/regenerate")
    async def regenerate(job_id: str, request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        parent = jobs.get(job_id)
        if not parent or parent.get("user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="unknown job")
        if parent["status"] not in ("done", "error"):
            raise HTTPException(status_code=409, detail="job is still running")
        root_id = parent.get("retry_of") or parent["id"]
        children = 0
        free_retries = 0
        for key in list(jobs.keys()):
            other = jobs.get(key)
            if other and other.get("retry_of") == root_id:
                children += 1
                if other.get("free_retry"):
                    free_retries += 1
        body = await request.json()
        new_body = {
            "lane": parent["lane"],
            # Recompose from the original prompt and the stored
            # camera selection, so a regenerate runs the identical
            # final prompt instead of composing a second time on top
            # of the already-composed one.
            "prompt": parent.get("user_prompt") or parent["prompt"],
            "camera_presets": parent.get("camera_presets") or [],
            "camera_look": parent.get("camera_look") or {},
            "camera_speed": parent.get("camera_speed"),
            "music_prompt": parent.get("music_prompt"),
            "speech_script": parent.get("speech_script"),
            "speech_voice": parent.get("speech_voice"),
            "captions": parent.get("captions"),
            "model": parent.get("model") or DEFAULT_MODEL,
            "duration_seconds": parent.get("duration_seconds"),
            "resolution": parent.get("resolution"),
            "reference_video_seconds": parent.get("reference_video_seconds"),
            "reference_image_urls": parent.get("reference_image_urls"),
            "reference_video_urls": parent.get("reference_video_urls"),
            "reference_audio_urls": parent.get("reference_audio_urls"),
            "segments": parent.get("segments_total") or 1,
            "effect": parent.get("effect"),
            "aspect_ratio": parent.get("aspect_ratio") or "9:16",
            "start_image_png_b64": parent.get("start_image_png_b64"),
            "end_image_png_b64": parent.get("end_image_png_b64"),
            "retry_of": root_id,
            "challenge_id": body.get("challenge_id"),
            "challenge_nonce": body.get("challenge_nonce"),
            "turnstile_token": body.get("turnstile_token"),
        }
        # A worker-side error earns free retries (Rule 8), capped.
        request._body = json.dumps(new_body).encode()
        result = await create_job(request, mf_session)
        created = json.loads(result.body)
        if parent["status"] == "error" and free_retries < MAX_FREE_RETRIES:
            child = jobs.get(created["id"])
            if child and child.get("charged_cents"):
                user = users.get(f"id:{user['id']}")
                user["balance_cents"] = user.get("balance_cents", 0) + child["charged_cents"]
                child["charged_cents"] = 0
                child["free_retry"] = True
                save_user(user)
                jobs[child["id"]] = child
                created["charged_cents"] = 0
        return created

    def _fal_upload_bytes(data: bytes, content_type: str, file_name: str):
        """Upload bytes to fal storage and return the hosted file
        URL. The lip sync endpoint and the character trainer take
        URLs, not bytes. Returns None on any failure."""
        try:
            resp = requests.post(
                "https://rest.alpha.fal.ai/storage/upload/initiate",
                headers=_fal_headers(),
                json={"content_type": content_type, "file_name": file_name},
                timeout=30,
            )
            if resp.status_code != 200:
                return None
            info = resp.json() or {}
            put = requests.put(
                info["upload_url"], data=data,
                headers={"Content-Type": content_type}, timeout=300)
            if put.status_code not in (200, 201, 204):
                return None
            return info.get("file_url")
        except Exception:
            return None

    def _lip_sync_for_job(job: dict, video_path: Path, voice_path: Path):
        """The built-in lip sync stage (Jack's rule, 2026-11-17):
        re-render the finished picture so the mouth matches the
        spoken line, via fal's Sync Lipsync v2 (the cheapest
        reliable hosted route with a published per-second price).
        Runs only for jobs flagged at creation: a spoken line the
        platform voices in post, on a lane that does not speak
        natively. The same rule covers dubbing: the voice track is
        the job's own spoken line, and the mouth is matched to it.
        Any failure flags the job and returns None: the un-synced
        video still delivers, the same way a failed voiceover
        never blocks delivery."""
        out_path = FILES_DIR / f"{job['id']}.lipsync.mp4"
        if out_path.exists():
            job["lipsync_status"] = "applied"
            jobs[job["id"]] = job
            return out_path
        try:
            video_url = _fal_upload_bytes(
                video_path.read_bytes(), "video/mp4", f"{job['id']}.mp4")
            audio_url = _fal_upload_bytes(
                voice_path.read_bytes(), "audio/wav", f"{job['id']}.wav")
            if not video_url or not audio_url:
                raise RuntimeError("fal storage upload failed")
            resp = requests.post(
                f"https://queue.fal.run/{pricing.LIPSYNC_MODEL}",
                headers=_fal_headers(),
                json={"video_url": video_url, "audio_url": audio_url},
                timeout=30,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"lip sync submit {resp.status_code}")
            sub = resp.json() or {}
            status_url = sub.get("status_url")
            response_url = sub.get("response_url")
            if not status_url or not response_url:
                raise RuntimeError("lip sync submit returned no queue urls")
            result_url = None
            deadline = _now() + 900
            while _now() < deadline:
                time.sleep(10)
                st = requests.get(
                    status_url, headers=_fal_headers(), timeout=15)
                if st.status_code != 200:
                    continue
                status = (st.json() or {}).get("status")
                if status == "COMPLETED":
                    got = requests.get(
                        response_url, headers=_fal_headers(), timeout=30)
                    body = got.json() or {}
                    payload = body.get("data") or body
                    result_url = ((payload.get("video") or {}).get("url"))
                    break
                if status in ("FAILED", "ERROR"):
                    break
            if not result_url:
                raise RuntimeError("lip sync produced no result")
            got = requests.get(result_url, timeout=300)
            if got.status_code != 200:
                raise RuntimeError("lip sync download failed")
            out_path.write_bytes(got.content)
            job["lipsync_status"] = "applied"
            jobs[job["id"]] = job
            return out_path
        except Exception:
            job["lipsync_status"] = "unavailable"
            jobs[job["id"]] = job
            return None

    def _voiceover_for_job(job: dict):
        """The Kokoro voiceover stage (Jack's correction,
        2026-11-16): speak the job's speech_script with Kokoro
        (Apache-2.0, the shippable TTS) and cache the wav per job
        on the data Volume. The synthesis callable lives in the
        sinclip-audio app (generate_voiceover in
        modal-worker/audio_app.py). Since the parallel-audio change
        (2026-11-16) the synthesis is normally spawned at dispatch
        and only collected here; the synchronous call below remains
        as the fallback for jobs with no spawned call. If the stage
        cannot produce the wav either way, the lookup fails cleanly
        here: the job is flagged voiceover_status "unavailable",
        delivery proceeds with the lane's own audio and any music
        bed, and nothing is charged or blocked. A music bed is never
        silently passed off as speech: the flag on the job record
        says exactly what happened."""
        script = (job.get("speech_script") or "").strip()
        if not script:
            return None
        voice_file = FILES_DIR / "voice" / f"{job['id']}.wav"
        if voice_file.exists():
            if job.get("voiceover_status") != "ready":
                job["voiceover_status"] = "ready"
                jobs[job["id"]] = job
            return voice_file
        try:
            generate_voiceover = modal.Function.from_name(
                "sinclip-audio", "generate_voiceover")
            data = generate_voiceover.remote(
                script, job.get("speech_voice") or DEFAULT_SPEECH_VOICE)
            voice_file.parent.mkdir(parents=True, exist_ok=True)
            voice_file.write_bytes(data)
            job["voiceover_status"] = "ready"
            jobs[job["id"]] = job
            return voice_file
        except Exception:
            job["voiceover_status"] = "unavailable"
            job["audio_fallback"] = True
            jobs[job["id"]] = job
            return None

    def _word_check_for_job(job: dict, out_path: Path):
        """The word check stage (Jack's call, 2026-11-18, the UGC
        Studio word fix idea rebuilt for Moonfactory): once the
        delivered file exists, its final audio is transcribed word
        by word and aligned against the job's speech_script by the
        word_check callable in the sinclip-audio app. Running
        against the final audio means the check sees what the
        customer hears, after lip sync, native generation and the
        voiceover mix alike. Jobs the platform voiced itself carry
        a same-voice clean track, so the stage may also splice a
        corrected word back in; when it returns repaired audio, the
        delivered file's audio track is swapped for it here.
        Outcomes land on the job record: passed, fixed, flagged
        (with word_check_listen_at naming the seconds to listen
        to), take_failed (more than a fifth of the script still
        wrong; the video still delivers), or unavailable (the stage
        itself failed). Like every audio stage here, a failure
        never blocks delivery and never changes the price: the
        check is a finishing stage, not a charge."""
        script = (job.get("speech_script") or "").strip()
        if not script or job.get("word_check_status"):
            return
        audio_path = FILES_DIR / f"{job['id']}.wordcheck.wav"
        fixed_path = FILES_DIR / f"{job['id']}.wordfix.wav"
        swapped_path = FILES_DIR / f"{job['id']}.wordfix.mp4"
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(out_path), "-vn",
                 "-acodec", "pcm_s16le", str(audio_path)],
                capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not audio_path.exists():
                raise RuntimeError("final audio extraction failed")
            # The fix pass is only possible where the platform's
            # Kokoro stage voiced the job: that clean track is the
            # same voice. Native lanes get the check and the flag.
            voice = ""
            if job.get("voiceover_status") == "ready":
                voice = job.get("speech_voice") or "af_heart"
            word_check = modal.Function.from_name(
                "sinclip-audio", "word_check")
            result = word_check.remote(
                audio_path.read_bytes(), script, voice)
            fixed = result.pop("audio", None)
            heard_words = result.pop("words", None)
            if heard_words:
                # One transcription of the final audio serves
                # both finishing stages: the captions stage burns
                # these same words in when the job asked for it.
                _CAPTION_TRANSCRIPTS[job["id"]] = heard_words
            if fixed:
                fixed_path.write_bytes(fixed)
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(out_path),
                     "-i", str(fixed_path),
                     "-map", "0:v:0", "-map", "1:a:0",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                     "-shortest", str(swapped_path)],
                    capture_output=True, text=True, timeout=1800)
                if proc.returncode != 0 or not swapped_path.exists():
                    raise RuntimeError("fixed audio swap failed")
                swapped_path.replace(out_path)
            job["word_check_status"] = result.get("status") \
                or "unavailable"
            job["word_check_listen_at"] = \
                result.get("listen_at") or []
            job["word_check_wrong"] = result.get("wrong") or []
            jobs[job["id"]] = job
        except Exception:
            job["word_check_status"] = "unavailable"
            jobs[job["id"]] = job
        finally:
            for path in (audio_path, fixed_path, swapped_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _captions_for_job(job: dict, out_path: Path):
        """The captions stage (Jack's call, 2026-11-18): a job
        created with the captions box checked gets word timed
        captions burned into its delivered file here, in the
        Shorts lane's house style. The words and their timings
        are the faster-whisper transcription of the final audio:
        the one the word check stage just produced when it ran
        (it waits in _CAPTION_TRANSCRIPTS), or a fresh run of the
        same transcription through the sinclip-audio transcribe
        callable when the job has none. The choice exists only
        at generation time; existing gallery videos are never
        captioned after the fact, and an unchecked job takes
        exactly the path it always took. Outcomes land on the
        job record: applied, no_speech (the job carries no
        spoken line, or its final audio has no words to caption),
        or unavailable (the transcription or the burn in itself
        failed). Like every finishing stage here, a failure never
        blocks delivery and never changes the price."""
        if not job.get("captions"):
            return
        if job.get("captions_status"):
            return
        has_speech = bool((job.get("speech_script") or "").strip()) \
            or job.get("voiceover_status") in ("ready", "native")
        if not has_speech:
            job["captions_status"] = "no_speech"
            jobs[job["id"]] = job
            return
        audio_path = FILES_DIR / f"{job['id']}.captions.wav"
        ass_path = FILES_DIR / f"{job['id']}.captions.ass"
        burned_path = FILES_DIR / f"{job['id']}.captioned.mp4"
        try:
            words = _CAPTION_TRANSCRIPTS.pop(job["id"], None)
            if not words:
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(out_path), "-vn",
                     "-acodec", "pcm_s16le", str(audio_path)],
                    capture_output=True, text=True, timeout=600)
                if proc.returncode != 0 or not audio_path.exists():
                    raise RuntimeError("final audio extraction failed")
                transcribe = modal.Function.from_name(
                    "sinclip-audio", "transcribe")
                result = transcribe.remote(audio_path.read_bytes())
                words = result.get("words") or []
            words = [w for w in words
                     if str(w.get("word", "")).strip()]
            if not words:
                job["captions_status"] = "no_speech"
                jobs[job["id"]] = job
                return
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", str(out_path)],
                capture_output=True, text=True, timeout=60)
            parts = (proc.stdout or "").strip().split(",")
            if len(parts) != 2:
                raise RuntimeError("finished file has no readable size")
            width, height = int(parts[0]), int(parts[1])
            if width <= 0 or height <= 0:
                raise RuntimeError("finished file has no readable size")
            ass_path.write_text(
                build_captions_ass(words, width, height),
                encoding="utf-8")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(out_path),
                 "-vf", f"subtitles={ass_path.name}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", str(burned_path)],
                capture_output=True, text=True, timeout=1800,
                cwd=FILES_DIR)
            if proc.returncode != 0 or not burned_path.exists():
                raise RuntimeError("caption burn in failed")
            burned_path.replace(out_path)
            job["captions_status"] = "applied"
            jobs[job["id"]] = job
        except Exception:
            job["captions_status"] = "unavailable"
            jobs[job["id"]] = job
        finally:
            for path in (audio_path, ass_path, burned_path):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _bed_for_job(job: dict):
        """The music bed for a job's delivery. Jobs without a music
        prompt get the house bed. Jobs with one (paid customers
        only, gated at creation) get an ACE-Step bed generated from the prompt,
        cached per job on the data Volume. Any audio-stage failure
        falls back to the house bed and is flagged on the job record,
        so delivery never breaks on the music stage."""
        prompt = (job.get("music_prompt") or "").strip()
        if not prompt:
            return BED_PATH if BED_PATH.exists() else None
        bed_file = FILES_DIR / "beds" / f"{job['id']}.wav"
        if bed_file.exists():
            return bed_file
        try:
            seed = int(hashlib.sha256(job["id"].encode()).hexdigest()[:8], 16) % 90000 + 10000
            caption = f"{prompt}, instrumental, no vocals"
            generate_bed = modal.Function.from_name("sinclip-audio", "generate_bed")
            data = generate_bed.remote(caption, seed)
            bed_file.parent.mkdir(parents=True, exist_ok=True)
            bed_file.write_bytes(data)
            return bed_file
        except Exception:
            job["audio_fallback"] = True
            jobs[job["id"]] = job
            return BED_PATH if BED_PATH.exists() else None

    def _chain_concat(job: dict) -> Path:
        """Join a chained length's finished segments into one
        raw file (CPU re-encode: segment files from one lane
        share a shape, but the join must not depend on their
        stream layout matching to the byte)."""
        job_id = job["id"]
        joined = FILES_DIR / f"{job_id}.chain.mp4"
        if joined.exists():
            return joined
        seg_paths = [
            _chain_segment_file(job, idx, url)
            for idx, url in enumerate(job.get("chain_results") or [])
        ]
        list_path = FILES_DIR / f"{job_id}.chain.txt"
        list_path.write_text(
            "".join(
                f"file '{p.name}'\n" for p in seg_paths),
            encoding="utf-8")
        audio = ["-c:a", "aac", "-b:a", "160k"] \
            if _has_audio_stream(seg_paths[0]) else ["-an"]
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
               "-i", str(list_path),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               *audio, str(joined)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=1800)
        if proc.returncode != 0 or not joined.exists():
            raise HTTPException(
                status_code=502,
                detail="finished file is not available yet")
        data_volume.commit()
        return joined

    def ensure_processed(job: dict) -> Path:
        """Fetch the finished file if needed and produce the
        delivered file (music muxed, watermark for free-tier jobs;
        per-second-lane files keep their own sound, finished by
        _process_api_file)."""
        job_id = job["id"]
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FILES_DIR / f"{job_id}.mp4"
        if job.get("provider") == "clip":
            if not out_path.exists():
                try:
                    data_volume.reload()
                except Exception:
                    pass
            if out_path.exists():
                return out_path
            raise HTTPException(status_code=404, detail="this job has no single file")
        if job.get("provider") == "hyperframes":
            # The Kaggle worker uploads the finished file itself;
            # there is no raw stage and no finishing chain.
            if not out_path.exists():
                try:
                    data_volume.reload()
                except Exception:
                    pass
            if out_path.exists():
                return out_path
            raise HTTPException(status_code=404, detail="this job has no file yet")
        if job.get("provider") == "shorts":
            # The shorts processor assembles the finished file
            # itself, captions, narration, bed and all; there is
            # no raw stage and no finishing chain here either.
            if not out_path.exists():
                try:
                    data_volume.reload()
                except Exception:
                    pass
            if out_path.exists():
                return out_path
            raise HTTPException(status_code=404, detail="this job has no file yet")
        if not out_path.exists():
            raw_path = FILES_DIR / f"{job_id}.raw.mp4"
            if len(job.get("chain_results") or []) > 1:
                # A chained length: the delivered video is the
                # segments joined, never just the last segment.
                raw_path = _chain_concat(job)
            elif not raw_path.exists():
                if job.get("provider") in URL_RESULT_PROVIDERS:
                    if not job.get("result_url"):
                        raise HTTPException(status_code=502, detail="finished file is not available yet")
                    resp = requests.get(job["result_url"], timeout=300)
                    if resp.status_code != 200:
                        raise HTTPException(status_code=502, detail="finished file is not available yet")
                    raw_path.write_bytes(resp.content)
                else:
                    resp = requests.get(
                        f"{WORKER_BASE}/jobs/{job['worker_job_id']}/file",
                        headers=_worker_headers(), timeout=120,
                    )
                    if resp.status_code != 200:
                        raise HTTPException(status_code=502, detail="finished file is not available yet")
                    raw_path.write_bytes(resp.content)
            # Voiceover resolution (Jack's correction, 2026-11-16):
            # a speech script on a speech-native lane is performed
            # by the model itself; on any other lane the Kokoro
            # voiceover stage speaks it in post. No script, no
            # voiceover: a music bed is never passed off as speech.
            voice = None
            if job.get("speech_script"):
                if lane_audio(job.get("model") or DEFAULT_MODEL)["native_speech"]:
                    if job.get("voiceover_status") != "native":
                        job["voiceover_status"] = "native"
                        job["lipsync_status"] = "native"
                        jobs[job["id"]] = job
                else:
                    voice = _voiceover_for_job(job)
            if voice is not None and job.get("lip_sync"):
                # Built-in lip sync: the picture is re-rendered
                # against the job's own voice track before the
                # normal finishing mux, so the delivered video's
                # mouth matches the spoken line.
                synced = _lip_sync_for_job(job, raw_path, voice)
                if synced is not None:
                    raw_path = synced
            if job.get("provider") in URL_RESULT_PROVIDERS:
                bed = _bed_for_job(job) if job.get("music_prompt") else None
                try:
                    _process_api_file(raw_path, out_path, bed_path=bed,
                                      voice_path=voice)
                except Exception:
                    # Delivery never breaks on the finishing stage:
                    # fall back to the standard mux with a bed.
                    job["audio_fallback"] = True
                    jobs[job["id"]] = job
                    _process_file(raw_path, out_path, watermark=False,
                                  bed_path=_bed_for_job(job),
                                  voice_path=voice)
            else:
                # Watermark marks free-tier work, not a plan name:
                # a pay-per-video job on a free-plan account was
                # paid for and ships clean (Amendment 3). Jobs from
                # before the flag fall back to the plan recorded at
                # creation.
                _process_file(raw_path, out_path,
                              watermark=bool(job.get(
                                  "free_tier",
                                  job.get("plan_at_job") == "free")),
                              bed_path=_bed_for_job(job),
                              voice_path=voice)
            # Word check (2026-11-18): the delivered file's final
            # audio is checked against the spoken line, and a
            # same-voice repair is spliced in when one is possible.
            # The stage flags the job record and never blocks the
            # delivery above.
            _word_check_for_job(job, out_path)
            # Captions (2026-11-18): after the word check, so the
            # burned words are timed from the final audio exactly
            # as delivered, repaired track included. Only jobs
            # created with the box checked enter the stage.
            _captions_for_job(job, out_path)
            data_volume.commit()
        return out_path

    def ensure_poster(job: dict) -> Path:
        """The preview picture for a finished video, its first
        frame, cut with ffmpeg the first time anyone needs it and
        cached beside the job's files under the same name the
        clipping pipeline already uses for its thumbnails. Later
        calls serve the cached picture."""
        poster_path = FILES_DIR / f"{job['id']}.jpg"
        if not poster_path.exists():
            try:
                data_volume.reload()
            except Exception:
                pass
        if poster_path.exists():
            return poster_path
        src = ensure_processed(job)
        tmp_path = FILES_DIR / f"{job['id']}.poster.tmp.jpg"
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        for seek in ("0.5", None):
            cmd = ["ffmpeg", "-y"]
            if seek:
                cmd += ["-ss", seek]
            cmd += ["-i", str(src), "-vframes", "1", "-q:v", "3",
                    str(tmp_path)]
            try:
                proc = subprocess.run(cmd, capture_output=True,
                                      text=True, timeout=120)
            except Exception:
                proc = None
            if (proc is not None and proc.returncode == 0
                    and tmp_path.exists()
                    and tmp_path.stat().st_size > 0):
                break
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        if not tmp_path.exists():
            raise HTTPException(status_code=404,
                                detail="no preview picture")
        tmp_path.replace(poster_path)
        data_volume.commit()
        return poster_path

    def ensure_image_file(job: dict) -> Path:
        """Fetch a finished start image from the worker once and
        cache it on the data Volume. Images are delivered as made:
        no post chain, no watermark, no music stage."""
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FILES_DIR / f"{job['id']}.png"
        if not out_path.exists():
            try:
                data_volume.reload()
            except Exception:
                pass
        if not out_path.exists():
            resp = requests.get(
                f"{WORKER_BASE}/image-jobs/{job['worker_job_id']}/file",
                headers=_worker_headers(), timeout=120,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="finished file is not available yet")
            out_path.write_bytes(resp.content)
            data_volume.commit()
        return out_path

    @web.get("/api/jobs/{job_id}/file")
    def job_file(job_id: str, request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        job = jobs.get(job_id)
        if not job or job.get("user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="unknown job")
        job = mirror_status(job)
        if job["status"] != "done":
            raise HTTPException(status_code=409, detail=f"job status is {job['status']}")
        if job.get("kind") == "image":
            return FileResponse(ensure_image_file(job), media_type="image/png",
                                filename=f"moonfactory-{job_id}.png")
        if job.get("kind") == "clip_job":
            raise HTTPException(status_code=409, detail="this clip job made several clips, open one of them")
        out_path = ensure_processed(job)
        try:
            # The finished file exists now, so its gallery preview
            # picture can be cut and cached with it.
            ensure_poster(job)
        except Exception:
            pass
        return FileResponse(out_path, media_type="video/mp4",
                            filename=f"moonfactory-{job_id}.mp4")

    @web.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        job = jobs.get(job_id)
        if not job or job.get("user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="unknown job")
        del jobs[job_id]
        for path in (FILES_DIR / f"{job_id}.mp4",
                     FILES_DIR / f"{job_id}.raw.mp4",
                     FILES_DIR / f"{job_id}.png",
                     FILES_DIR / "beds" / f"{job_id}.wav"):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        data_volume.commit()
        return {"status": "deleted"}


    # -- clipping (CLIPPING-DESIGN.md sections 4, 6, 8) --------------------
    # Clip jobs are platform jobs: same accounts, holds, ceilings,
    # library and gallery rules as generation jobs. Free accounts are
    # refused (no free clipping at launch). Creator subscribers debit
    # the shared included budget at the 0.9c per source minute floor
    # rate; everyone else pays 5c per source minute from balance. The
    # exact charge is quoted before the job runs, from the probed
    # duration rounded up to whole minutes.

    def _clip_quote_for(user: dict, duration_seconds: float) -> dict:
        q = pricing.clip_quote(duration_seconds, owner=is_owner(user))
        remaining = included_budget_remaining(user)
        if user["plan"] == "creator" and remaining >= q["included_debit_cents"]:
            method = "included"
        elif int(user.get("balance_cents", 0)) >= q["balance_charge_cents"] \
                or is_owner(user):
            # The owner pays from balance even when the balance
            # cannot cover it (Jack, 2026-11-17), matching create.
            method = "balance"
        else:
            method = "insufficient"
        return {
            **q,
            "duration_seconds": round(float(duration_seconds), 2),
            "method": method,
            "ceiling_ok": ceiling_allows(user, q["worst_case_cost_cents"]),
            "included_budget_remaining_cents": round(remaining, 2),
            "balance_cents": int(user.get("balance_cents", 0)),
        }

    @web.post("/api/clips/quote")
    async def quote_clip(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if not is_paid(user):
            raise HTTPException(status_code=403, detail="clipping is a paid feature")
        body = await request.json()
        if body.get("url"):
            try:
                clipping.validate_fetch_url(body["url"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            try:
                info = clip_probe.remote(body["url"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            except Exception:
                raise HTTPException(status_code=502,
                                    detail=clipping.LINK_DEAD_MESSAGE)
            duration = float(info.get("duration") or 0)
            if duration <= 0:
                raise HTTPException(status_code=422, detail="that link has no readable duration")
            if duration > pricing.CLIP_MAX_SOURCE_SECONDS:
                raise HTTPException(status_code=422, detail="this video is longer than the 3 hour clipping limit")
            return {**_clip_quote_for(user, duration),
                    "title": info.get("title") or ""}
        try:
            duration = float(body.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            raise HTTPException(status_code=422, detail="duration_seconds or a link is required")
        if duration > pricing.CLIP_MAX_SOURCE_SECONDS:
            raise HTTPException(status_code=422, detail="this video is longer than the 3 hour clipping limit")
        return _clip_quote_for(user, duration)

    @web.post("/api/clips/upload")
    async def upload_clip_source(request: Request, file: UploadFile = File(...),
                                 mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if not is_paid(user):
            raise HTTPException(status_code=403, detail="clipping is a paid feature")
        if _rate_limited("clipupload", user["id"], 6):
            raise HTTPException(status_code=429, detail="too many uploads, slow down")
        upload_id = pysecrets.token_hex(8)
        src_dir = Path("/data/clip-sources")
        src_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "upload.mp4").suffix.lower()
        if suffix not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
            suffix = ".mp4"
        dest = src_dir / f"{upload_id}{suffix}"
        size = 0
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > pricing.CLIP_MAX_UPLOAD_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file is larger than the 2 GiB upload limit")
                fh.write(chunk)
        data_volume.commit()
        try:
            info = clipping.probe(dest)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)[:200])
        if float(info["duration"]) > pricing.CLIP_MAX_SOURCE_SECONDS:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="this video is longer than the 3 hour clipping limit")
        meta[f"clipupload:{upload_id}"] = {
            "user_id": user["id"], "path": str(dest),
            "duration": float(info["duration"]),
            "filename": (file.filename or "upload")[:120],
            "created_at": _now(),
        }
        return {"upload_id": upload_id,
                **_clip_quote_for(user, float(info["duration"]))}

    @web.post("/api/clips")
    async def create_clip(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if _rate_limited("clipcreate", user["id"], 6):
            raise HTTPException(status_code=429, detail="too many jobs, slow down")
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if user.get("past_due"):
            raise HTTPException(status_code=403, detail="your subscription payment is past due")
        body = await request.json()
        if not _verify_bot_check(body, client_ip(request)):
            raise HTTPException(status_code=403, detail="bot check failed")
        if not is_paid(user):
            raise HTTPException(status_code=403, detail="clipping is a paid feature")
        try:
            clip_count = int(body.get("clip_count") or 3)
        except (TypeError, ValueError):
            clip_count = 3
        if clip_count < 1 or clip_count > pricing.CLIP_MAX_CLIPS_PER_JOB:
            raise HTTPException(
                status_code=422,
                detail=f"clip count must be between 1 and {pricing.CLIP_MAX_CLIPS_PER_JOB}")
        length = body.get("length") or "medium"
        if length not in clipping.LENGTH_RANGES:
            raise HTTPException(status_code=422, detail="length must be short, medium or long")
        caption_style = body.get("caption_style") or "bounce"
        if caption_style not in clipping.STYLES:
            raise HTTPException(
                status_code=422,
                detail="caption style must be one of "
                + ", ".join(clipping.STYLE_NAMES.values()))
        layout = body.get("layout") or "blur"
        if layout not in clipping.LAYOUTS:
            raise HTTPException(
                status_code=422,
                detail="layout must be one of "
                + ", ".join(clipping.LAYOUTS.values()))
        upload_path = None
        source_url = None
        source_title = ""
        if body.get("upload_id"):
            rec = meta.get(f"clipupload:{body['upload_id']}")
            if not rec or rec.get("user_id") != user["id"]:
                raise HTTPException(status_code=404, detail="unknown upload")
            upload_path = rec["path"]
            duration = float(rec["duration"])
            source_title = rec.get("filename") or ""
        elif body.get("url"):
            try:
                source_url = clipping.validate_fetch_url(body["url"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            try:
                info = clip_probe.remote(source_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            except Exception:
                raise HTTPException(status_code=502,
                                    detail=clipping.LINK_DEAD_MESSAGE)
            duration = float(info.get("duration") or 0)
            source_title = info.get("title") or ""
            if duration <= 0:
                raise HTTPException(status_code=422, detail="that link has no readable duration")
        else:
            raise HTTPException(status_code=422, detail="paste a link or upload a file")
        if duration > pricing.CLIP_MAX_SOURCE_SECONDS:
            raise HTTPException(status_code=422, detail="this video is longer than the 3 hour clipping limit")
        q = pricing.clip_quote(duration, owner=is_owner(user))
        reload_state = {}
        if not ceiling_allows(user, q["worst_case_cost_cents"]):
            attempt_auto_reload(user, reload_state)
        if not ceiling_allows(user, q["worst_case_cost_cents"]):
            raise HTTPException(
                status_code=402,
                detail=reload_fail_detail(reload_state, "this job would exceed the total generation your payments cover"))
        # Hold the exact charge before dispatch (Rule 1). Included
        # budget pays first for subscribers; balance is the other rail.
        u = usage(user)
        if user["plan"] == "creator" and included_budget_remaining(user) >= q["included_debit_cents"]:
            hold_kind = "included"
            hold_amount = q["included_debit_cents"]
            u["included_cost_cents"] = included_cost_used(user) + hold_amount
            user["usage"] = u
        elif int(user.get("balance_cents", 0)) >= q["balance_charge_cents"]:
            hold_kind = "balance"
            hold_amount = q["balance_charge_cents"]
            user["balance_cents"] = int(user.get("balance_cents", 0)) - int(hold_amount)
        elif is_owner(user):
            # The owner answers to the provider only (Jack,
            # 2026-11-17): the hold is taken against his balance
            # whatever it holds, before the auto reload branch, so
            # a reload never fires for his account.
            hold_kind = "balance"
            hold_amount = q["balance_charge_cents"]
            user["balance_cents"] = int(user.get("balance_cents", 0)) - int(hold_amount)
        elif attempt_auto_reload(user, reload_state) and int(user.get("balance_cents", 0)) >= q["balance_charge_cents"]:
            hold_kind = "balance"
            hold_amount = q["balance_charge_cents"]
            user["balance_cents"] = int(user.get("balance_cents", 0)) - int(hold_amount)
        else:
            raise HTTPException(
                status_code=402,
                detail=reload_fail_detail(reload_state, f"included budget used up; clipping this video costs {q['balance_charge_cents']} cents of prepaid balance"))
        save_user(user)
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": None,
            "provider": "clip",
            "kind": "clip_job",
            "lane": "clip",
            "model": "clip",
            "prompt": source_title or source_url or "Uploaded video",
            "user_prompt": source_title or source_url or "Uploaded video",
            "camera_presets": [],
            "camera_preset_names": [],
            "camera_look": {},
            "status": "queued",
            "stage": CLIP_STAGES[0],
            "error": None,
            "created_at": _now(),
            "charged_cents": 0,
            "source_url": source_url,
            "upload_path": upload_path,
            "source_title": source_title,
            "duration_seconds": round(duration, 2),
            "source_minutes": q["source_minutes"],
            "clip_count": clip_count,
            "length": length,
            "caption_style": caption_style,
            "layout": layout,
            "hold_kind": hold_kind,
            "hold_amount": hold_amount,
            "plan_at_job": user["plan"],
            "free_tier": False,
            "music_prompt": None,
            "clip_ids": [],
        }
        jobs[job["id"]] = job
        process_clip_job.spawn(job["id"])
        return JSONResponse({**public_job(job), "quote": q}, status_code=202)

    # -- template lane (HyperFrames on Kaggle) ----------------------------
    # Free motion graphics: title cards, captions over a clip, stat
    # cards, promo cards. Zero credits and zero charge. A Kaggle
    # kernel does the rendering; the platform only queues, wakes
    # the kernel, and serves the finished file. The worker
    # endpoints below are how the kernel talks back, guarded by a
    # dedicated key that lives in the moonfactory-secrets store on
    # this side and in a private Kaggle dataset on that side.

    def _hf_maybe_trigger():
        """Wake the Kaggle worker when a queued template job has
        no live kernel. A push of a fresh kernel version is what
        makes Kaggle run it. Throttled by the heartbeat's freshness
        and a minimum gap between attempts, and it never raises:
        a failed wake simply leaves the job queued for the next
        nudge, inside its poll bound."""
        try:
            now = _now()
            if now - float(meta.get("hf:heartbeat") or 0) \
                    < HF_HEARTBEAT_STALE_SECONDS:
                return
            if now - float(meta.get("hf:last_trigger") or 0) \
                    < HF_TRIGGER_MIN_INTERVAL_SECONDS:
                return
            token = os.environ.get("KAGGLE_API_TOKEN", "")
            if not token:
                return
            meta["hf:last_trigger"] = now
            try:
                script_text = HF_WORKER_SCRIPT_PATH.read_text()
            except Exception:
                return
            ok, detail = hf_kaggle_push(token, script_text)
            meta["hf:last_trigger_result"] = {
                "ok": ok, "detail": detail, "at": now}
        except Exception:
            pass

    def _hf_worker_ok(x_api_key: str) -> bool:
        expected = os.environ.get("TEMPLATE_WORKER_KEY", "")
        return bool(expected) and hmac.compare_digest(
            x_api_key or "", expected)

    def _hf_daily_used(user: dict) -> int:
        day_start = _now() - (_now() % 86400)
        used = 0
        for key in list(jobs.keys()):
            other = jobs.get(key)
            if (other and other.get("user_id") == user["id"]
                    and other.get("kind") == "template"
                    and float(other.get("created_at") or 0) >= day_start):
                used += 1
        return used

    @web.get("/api/templates/config")
    def template_config(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        return {
            "templates": [
                {"id": tid, "name": spec["name"]}
                for tid, spec in HF_TEMPLATES.items()
            ],
            "daily_cap": TEMPLATE_DAILY_CAP_PAID,
            "daily_used": _hf_daily_used(user),
            "free_clips_total": FREE_CLIPS_TOTAL,
            "free_used": free_jobs_total(user),
            "paid": is_paid(user),
        }

    @web.post("/api/templates/upload")
    async def upload_template_source(request: Request, file: UploadFile = File(...),
                                     mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if _rate_limited("templateupload", user["id"], 6):
            raise HTTPException(status_code=429, detail="too many uploads, slow down")
        upload_id = pysecrets.token_hex(8)
        src_dir = Path("/data/template-sources")
        src_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "upload.mp4").suffix.lower()
        if suffix not in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
            suffix = ".mp4"
        dest = src_dir / f"{upload_id}{suffix}"
        size = 0
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > TEMPLATE_MAX_UPLOAD_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="that file is too big, keep it under 200 MB")
                fh.write(chunk)
        data_volume.commit()
        try:
            info = clipping.probe(dest)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(exc)[:200])
        duration = float(info["duration"])
        if duration > TEMPLATE_MAX_SOURCE_SECONDS:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail="that clip is too long for this one, keep it under a minute")
        meta[f"templateupload:{upload_id}"] = {
            "user_id": user["id"], "path": str(dest),
            "duration": duration,
            "filename": (file.filename or "upload")[:120],
            "created_at": _now(),
        }
        return {"upload_id": upload_id,
                "duration_seconds": round(duration, 2)}

    @web.post("/api/templates")
    async def create_template(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if _rate_limited("templatecreate", user["id"], 10):
            raise HTTPException(status_code=429, detail="too many jobs, slow down")
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if user.get("past_due"):
            raise HTTPException(status_code=403, detail="your subscription payment is past due")
        body = await request.json()
        if not _verify_bot_check(body, client_ip(request)):
            raise HTTPException(status_code=403, detail="bot check failed")
        template_id = str(body.get("template") or "")
        try:
            fields = hf_clean_fields(template_id, body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        upload_path = None
        source_duration = 0.0
        if template_id == "captions":
            rec = meta.get(f"templateupload:{body.get('upload_id') or ''}")
            if not rec or rec.get("user_id") != user["id"]:
                raise HTTPException(status_code=422, detail="upload your clip first, then make it")
            upload_path = rec["path"]
            source_duration = float(rec["duration"])
        is_free_job = False
        if not is_paid(user):
            if free_jobs_total(user) >= FREE_CLIPS_TOTAL:
                raise HTTPException(
                    status_code=403,
                    detail=f"your free clips are used up. The free plan is {FREE_CLIPS_TOTAL} clips in total")
            if int(meta.get("freecount:total") or 0) >= FREE_PLATFORM_TOTAL_CEILING:
                raise HTTPException(status_code=503, detail="the free tier is paused")
            is_free_job = True
        else:
            if _hf_daily_used(user) >= TEMPLATE_DAILY_CAP_PAID:
                raise HTTPException(
                    status_code=429,
                    detail="you have made your free titles for today, the rest wait until tomorrow")
        spec = HF_TEMPLATES[template_id]
        if template_id == "story":
            plan = hf_story_plan(fields["title"],
                                 fields["lines"].split("\n"))
            duration = plan[-1]["b"]
        elif template_id == "captions":
            duration = source_duration
        else:
            duration = float(spec["duration"])
        display = (
            fields.get("headline") or fields.get("caption")
            or fields.get("stat") or fields.get("title")
            or spec["name"])
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": None,
            "provider": "hyperframes",
            "kind": "template",
            "lane": "template",
            "model": "hyperframes",
            "template": template_id,
            "template_name": spec["name"],
            "hf_fields": fields,
            "prompt": display,
            "user_prompt": display,
            "camera_presets": [],
            "camera_preset_names": [],
            "camera_look": {},
            "status": "queued",
            "stage": "Waiting for the free renderer",
            "error": None,
            "created_at": _now(),
            "charged_cents": 0,
            "upload_path": upload_path,
            "source_duration": source_duration,
            "duration_seconds": round(duration, 2),
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "plan_at_job": user["plan"],
            "free_tier": is_free_job,
            "music_prompt": None,
        }
        jobs[job["id"]] = job
        if is_free_job:
            u = usage(user)
            u["free_jobs"] += 1
            user["free_jobs_total"] = free_jobs_total(user) + 1
            user["usage"] = u
            meta["freecount:total"] = int(meta.get("freecount:total") or 0) + 1
            save_user(user)
        _hf_maybe_trigger()
        return JSONResponse(public_job(job), status_code=202)

    @web.post("/api/worker/hf/heartbeat")
    def hf_heartbeat(x_api_key: str = Header(default="")):
        if not _hf_worker_ok(x_api_key):
            raise HTTPException(status_code=401, detail="bad worker key")
        meta["hf:heartbeat"] = _now()
        return {"ok": True}

    @web.get("/api/worker/hf/jobs/next")
    def hf_next_job(x_api_key: str = Header(default="")):
        if not _hf_worker_ok(x_api_key):
            raise HTTPException(status_code=401, detail="bad worker key")
        meta["hf:heartbeat"] = _now()
        queued = []
        for key in list(jobs.keys()):
            other = jobs.get(key)
            if (other and other.get("kind") == "template"
                    and other.get("status") == "queued"):
                queued.append(other)
        if not queued:
            return {"job": None}
        queued.sort(key=lambda j: j["created_at"])
        job = queued[0]
        job["status"] = "running"
        job["stage"] = "Rendering now"
        job["claimed_at"] = _now()
        jobs[job["id"]] = job
        payload = {
            "id": job["id"],
            "template": job["template"],
            "fields": job.get("hf_fields") or {},
            "html": build_hf_composition(
                job["template"], job.get("hf_fields") or {},
                job.get("source_duration") or 0.0),
            "has_source": bool(job.get("upload_path")),
        }
        if job["template"] == "story":
            # The worker stages the scene script beside the
            # composition and synthesizes the score before
            # rendering. The plan rides along so the music and
            # the scene change sounds land on the scene cuts.
            fields = job.get("hf_fields") or {}
            plan = hf_story_plan(fields.get("title") or "",
                                 (fields.get("lines") or "").split("\n"))
            payload["motion"] = True
            payload["audio_plan"] = {
                "duration": plan[-1]["b"],
                "scenes": [span["a"] for span in plan],
            }
        return {"job": payload}

    @web.get("/api/worker/hf/jobs/{job_id}/source")
    def hf_job_source(job_id: str, x_api_key: str = Header(default="")):
        if not _hf_worker_ok(x_api_key):
            raise HTTPException(status_code=401, detail="bad worker key")
        job = jobs.get(job_id)
        if not job or job.get("kind") != "template" \
                or not job.get("upload_path"):
            raise HTTPException(status_code=404, detail="unknown job")
        path = Path(job["upload_path"])
        if not path.exists():
            try:
                data_volume.reload()
            except Exception:
                pass
        if not path.exists():
            raise HTTPException(status_code=404, detail="the uploaded file is missing")
        return FileResponse(path, media_type="video/mp4",
                            filename="source.mp4")

    @web.post("/api/worker/hf/jobs/{job_id}/file")
    async def hf_job_file(job_id: str, request: Request,
                          x_api_key: str = Header(default="")):
        if not _hf_worker_ok(x_api_key):
            raise HTTPException(status_code=401, detail="bad worker key")
        job = jobs.get(job_id)
        if not job or job.get("kind") != "template":
            raise HTTPException(status_code=404, detail="unknown job")
        if job["status"] != "running":
            raise HTTPException(status_code=409, detail=f"job status is {job['status']}")
        data = await request.body()
        if not data or len(data) > 500 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="bad file size")
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FILES_DIR / f"{job_id}.mp4"
        out_path.write_bytes(data)
        data_volume.commit()
        job["bytes"] = len(data)
        try:
            info = clipping.probe(out_path)
            job["duration_seconds"] = round(float(info["duration"]), 2)
        except Exception:
            pass
        job["status"] = "done"
        job["stage"] = None
        jobs[job["id"]] = job
        return {"ok": True, "bytes": len(data)}

    @web.post("/api/worker/hf/jobs/{job_id}/fail")
    async def hf_job_fail(job_id: str, request: Request,
                          x_api_key: str = Header(default="")):
        if not _hf_worker_ok(x_api_key):
            raise HTTPException(status_code=401, detail="bad worker key")
        job = jobs.get(job_id)
        if not job or job.get("kind") != "template":
            raise HTTPException(status_code=404, detail="unknown job")
        if job["status"] not in ("queued", "running"):
            raise HTTPException(status_code=409, detail=f"job status is {job['status']}")
        body = await request.json()
        job["status"] = "error"
        job["error"] = "the render failed, nothing was charged"
        job["internal_error"] = str(body.get("error") or "")[:300]
        job["stage"] = None
        jobs[job["id"]] = job
        return {"ok": True}

    # -- faceless shorts lane ----------------------------------------------
    # The MoneyPrinterTurbo shape on the platform's own pieces
    # (platform/shorts.py): a topic or a pasted script, one stock
    # clip per line, Kokoro narration per line, timed captions, a
    # music bed, one assembled vertical short. A rendered lane
    # with the Titles pricing treatment: zero credits and zero
    # charge; unpaid accounts count against the free lifetime
    # clips, paid accounts get a daily cap. The work runs inside
    # process_short_job on CPU, spawned at creation like a clip
    # job. Gating note: on generation jobs a spoken line is a
    # paid add-on and an unpaid account is refused one; here the
    # narration is the lane itself, so the lane's own quota is
    # the gate, and what stays paid is the custom part, picking
    # a voice. A free short is spoken in the standard voice.

    def _shorts_daily_used(user: dict) -> int:
        day_start = _now() - (_now() % 86400)
        used = 0
        for key in list(jobs.keys()):
            other = jobs.get(key)
            if (other and other.get("user_id") == user["id"]
                    and other.get("kind") == "short"
                    and float(other.get("created_at") or 0) >= day_start):
                used += 1
        return used

    @web.get("/api/shorts/config")
    def shorts_config(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        return {
            "voices": [
                {"id": voice, "label": SPEECH_VOICE_LABELS[voice]}
                for voice in SPEECH_VOICES
            ],
            "default_voice": DEFAULT_SPEECH_VOICE,
            "paid": is_paid(user),
            "topic_available": shorts.script_routes_configured(),
            "stock_provider": (
                "pexels" if shorts._env_value("PEXELS_API_KEY")
                else "commons"),
            "daily_cap": SHORTS_DAILY_CAP_PAID,
            "daily_used": _shorts_daily_used(user),
            "free_clips_total": FREE_CLIPS_TOTAL,
            "free_used": free_jobs_total(user),
            "max_lines": shorts.SHORTS_MAX_LINES,
            "max_script_chars": shorts.SHORTS_MAX_SCRIPT_CHARS,
        }

    @web.post("/api/shorts")
    async def create_short(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        if _rate_limited("shortcreate", user["id"], 10):
            raise HTTPException(status_code=429, detail="too many jobs, slow down")
        if not user["verified"]:
            raise HTTPException(status_code=403, detail="verify your email before creating")
        if user.get("past_due"):
            raise HTTPException(status_code=403, detail="your subscription payment is past due")
        body = await request.json()
        if not _verify_bot_check(body, client_ip(request)):
            raise HTTPException(status_code=403, detail="bot check failed")
        topic = (body.get("topic") or "").strip()
        if len(topic) > 200:
            raise HTTPException(
                status_code=422,
                detail="a topic must be 200 characters or fewer")
        script_text = (body.get("script") or "").strip()
        if script_text:
            script_source = "pasted"
        elif topic:
            # Topic writing rides the text routes already wired
            # for the Clip lane's picking brain, behind their
            # dark-safe gates. When no route answers, the clean
            # failure asks for the script instead of inventing
            # one.
            try:
                script_text = shorts.script_from_topic(topic)
            except Exception:
                raise HTTPException(
                    status_code=422,
                    detail="we could not write that topic into a script, paste your script instead")
            script_source = "topic"
        else:
            raise HTTPException(
                status_code=422,
                detail="give the short a topic or paste a script")
        try:
            lines = shorts.split_script(script_text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        voice = (body.get("voice") or "").strip() or DEFAULT_SPEECH_VOICE
        if voice not in SPEECH_VOICES:
            raise HTTPException(
                status_code=422, detail="pick one of the voices on the list")
        if voice != DEFAULT_SPEECH_VOICE and not is_paid(user):
            raise HTTPException(
                status_code=403, detail="picking a voice is a paid feature")
        is_free_job = False
        if not is_paid(user):
            if free_jobs_total(user) >= FREE_CLIPS_TOTAL:
                raise HTTPException(
                    status_code=403,
                    detail=f"your free clips are used up. The free plan is {FREE_CLIPS_TOTAL} clips in total")
            if int(meta.get("freecount:total") or 0) >= FREE_PLATFORM_TOTAL_CEILING:
                raise HTTPException(status_code=503, detail="the free tier is paused")
            is_free_job = True
        else:
            if _shorts_daily_used(user) >= SHORTS_DAILY_CAP_PAID:
                raise HTTPException(
                    status_code=429,
                    detail="you have made your free shorts for today, the rest wait until tomorrow")
        display = topic or lines[0]
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": None,
            "provider": "shorts",
            "kind": "short",
            "lane": "shorts",
            "model": "shorts",
            "prompt": display,
            "user_prompt": display,
            "camera_presets": [],
            "camera_preset_names": [],
            "camera_look": {},
            "status": "queued",
            "stage": SHORTS_STAGES[0],
            "error": None,
            "created_at": _now(),
            "charged_cents": 0,
            "short_topic": topic or None,
            "short_lines": lines,
            "short_scenes": [],
            "script_source": script_source,
            "speech_script": " ".join(lines),
            "speech_voice": voice,
            "voiceover_status": None,
            "word_check_status": None,
            "word_check_listen_at": [],
            "duration_seconds": None,
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "plan_at_job": user["plan"],
            "free_tier": is_free_job,
            "music_prompt": None,
        }
        jobs[job["id"]] = job
        if is_free_job:
            u = usage(user)
            u["free_jobs"] += 1
            user["free_jobs_total"] = free_jobs_total(user) + 1
            user["usage"] = u
            meta["freecount:total"] = int(meta.get("freecount:total") or 0) + 1
            save_user(user)
        process_short_job.spawn(job["id"])
        return JSONResponse(public_job(job), status_code=202)

    @web.get("/api/jobs/{job_id}/thumb")
    def job_thumb(job_id: str, request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        job = jobs.get(job_id)
        if not job or job.get("user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="unknown job")
        path = FILES_DIR / f"{job_id}.jpg"
        if not path.exists():
            try:
                data_volume.reload()
            except Exception:
                pass
        if not path.exists():
            raise HTTPException(status_code=404, detail="no thumbnail")
        return FileResponse(path, media_type="image/jpeg")

    # -- public gallery -----------------------------------------------------
    # Every finished clip is listed by default (Jack, 2026-11-16).
    # Safety shape: only the owner's handle is shown (never an email
    # or real name), no identifying job metadata, every item carries
    # a report control, owners can delete their clips at any time
    # (which removes them here), and an admin can remove any item.
    # Opting out is a paid feature (Amendment 3); clips from
    # accounts that have never paid always list. NSFW jobs are
    # the one hard exception (Jack, 2026-11-18): a job on the
    # NSFW lane or on an uncensored model never lists, whatever
    # the owner's setting. See job_is_nsfw.

    def gallery_visible(job: dict) -> bool:
        # Finished images list alongside finished clips (Jack,
        # 2026-11-17): an image a customer made is finished work
        # the same way a clip is, under the same rules.
        if job.get("status") != "done" or job.get("removed"):
            return False
        # NSFW jobs never list publicly (Jack, 2026-11-18),
        # whatever the owner's opt-out setting: the classification
        # is computed live here, so jobs finished before this rule
        # existed are covered too. The gallery_public stamp set at
        # creation is a belt-and-braces copy of the same verdict.
        if job_is_nsfw(job) or job.get("gallery_public") is False:
            return False
        owner = users.get(f"id:{job.get('user_id')}")
        if not owner:
            return False
        if is_paid(owner) and owner.get("gallery_opt_out"):
            return False
        return True

    def gallery_file_ready(job: dict) -> bool:
        """Whether the gallery file route can actually serve this
        job's file. The feed lists only ready items, so a job whose
        file is gone never shows as a dead tile. Mirrors what
        ensure_processed and ensure_image_file can reach: the
        finished file itself, a raw file to finish from, chained
        segment results, a provider result URL, or the worker copy.
        Clip outputs have no rebuild path, so their finished file
        has to be on the volume."""
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        job_id = job["id"]
        if job.get("kind") == "image":
            if (FILES_DIR / f"{job_id}.png").exists():
                return True
            return bool(job.get("worker_job_id"))
        if (FILES_DIR / f"{job_id}.mp4").exists():
            return True
        if job.get("provider") == "clip":
            return False
        if (FILES_DIR / f"{job_id}.raw.mp4").exists():
            return True
        if len(job.get("chain_results") or []) > 1:
            return True
        if job.get("provider") in URL_RESULT_PROVIDERS:
            return bool(job.get("result_url"))
        return bool(job.get("worker_job_id"))

    def public_gallery_item(job: dict) -> dict:
        owner = users.get(f"id:{job.get('user_id')}") or {}
        is_image = job.get("kind") == "image"
        if is_image:
            lane = pricing.IMAGE_LANES.get(
                job.get("image_model") or "") or {}
            model_label = lane.get("label") or "Moonfactory's own image maker"
            cost_cents = int(job.get("image_charge_cents")
                             or job.get("charged_cents") or 0)
        else:
            model_label = (
                "Clip" if job.get("kind") in ("clip_job", "clip_output")
                else (job.get("template_name") or "Title card")
                if job.get("kind") == "template"
                else "Faceless short" if job.get("kind") == "short"
                else "Effects" if job.get("effect")
                else MODEL_LABELS.get(job.get("model") or DEFAULT_MODEL, "Wan 2.2")
            )
            cost_cents = (
                None if job.get("kind") in ("clip_job", "clip_output")
                else int(job.get("charged_cents") or 0)
                + float(job.get("included_cost_cents")
                        or job.get("budget_cost_cents") or 0)
            )
        return {
            "id": job["id"],
            "kind": "image" if is_image else "video",
            "handle": display_handle(owner),
            "lane": job["lane"],
            "model": model_label,
            "prompt": (job.get("prompt") or "")[:200],
            "camera_presets": job.get("camera_preset_names") or [],
            "camera_look": job.get("camera_look") or {},
            "effect": job.get("effect_name"),
            "duration_seconds": job.get("duration_seconds"),
            "resolution": job.get("resolution"),
            "aspect_ratio": job.get("aspect_ratio") or "9:16",
            "segments_total": int(job.get("segments_total") or 1),
            "cost_cents": cost_cents,
            "created_at": job["created_at"],
            "file_url": f"/gallery/{job['id']}/file",
            "poster_url": (
                None if is_image else f"/gallery/{job['id']}/poster"),
        }

    @web.get("/gallery", response_class=HTMLResponse)
    def gallery_page():
        return HTMLResponse(GALLERY_HTML)

    @web.get("/api/gallery")
    def gallery_list():
        try:
            data_volume.reload()
        except Exception:
            pass
        items = []
        for key in list(jobs.keys()):
            job = jobs.get(key)
            if (job and gallery_visible(job)
                    and gallery_file_ready(job)):
                items.append(public_gallery_item(job))
        items.sort(key=lambda i: i["created_at"], reverse=True)
        return {"items": items[:100]}

    def _image_media_type(path: Path) -> str:
        """The real type of a finished image from its first bytes.
        Providers hand back more than one format under the same
        stored name, and the gallery serves the true one."""
        try:
            with path.open("rb") as fh:
                head = fh.read(16)
        except Exception:
            return "image/png"
        if head.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
            return "image/webp"
        if head.startswith(b"GIF8"):
            return "image/gif"
        return "image/png"

    @web.get("/gallery/{job_id}/file")
    def gallery_file(job_id: str):
        job = jobs.get(job_id)
        if not job or not gallery_visible(job):
            raise HTTPException(status_code=404, detail="not in the gallery")
        if job.get("kind") == "image":
            path = FILES_DIR / f"{job_id}.png"
            if not path.exists():
                try:
                    data_volume.reload()
                except Exception:
                    pass
            if not path.exists() and job.get("worker_job_id"):
                path = ensure_image_file(job)
            if not path.exists():
                raise HTTPException(status_code=404,
                                    detail="file is not available")
            return FileResponse(path,
                                media_type=_image_media_type(path),
                                filename=f"moonfactory-{job_id}.png")
        out_path = ensure_processed(job)
        try:
            ensure_poster(job)
        except Exception:
            pass
        return FileResponse(out_path, media_type="video/mp4",
                            filename=f"moonfactory-{job_id}.mp4")

    @web.get("/gallery/{job_id}/poster")
    def gallery_poster(job_id: str):
        job = jobs.get(job_id)
        if (not job or not gallery_visible(job)
                or job.get("kind") == "image"):
            raise HTTPException(status_code=404, detail="not in the gallery")
        return FileResponse(ensure_poster(job), media_type="image/jpeg",
                            filename=f"moonfactory-{job_id}.jpg")

    @web.post("/api/gallery/{job_id}/report")
    async def report_gallery_item(job_id: str, request: Request):
        if _rate_limited("report", client_ip(request), 20):
            raise HTTPException(status_code=429, detail="too many reports, slow down")
        job = jobs.get(job_id)
        if not job or not gallery_visible(job):
            raise HTTPException(status_code=404, detail="not in the gallery")
        try:
            body = await request.json()
        except Exception:
            body = {}
        reason = ""
        if isinstance(body, dict):
            reason = (body.get("reason") or "").strip()[:500]
        reports = meta.get("reports") or []
        reports.append({"job_id": job_id, "reason": reason, "at": _now()})
        meta["reports"] = reports[-500:]
        return {"status": "reported"}

    def add_net_paid(user: dict, gross_cents) -> None:
        """Amendment 2 accounting: cumulative payments, net of
        card fees, are the account's cost ceiling."""
        if gross_cents:
            user["net_paid_cents"] = (
                float(user.get("net_paid_cents") or 0.0)
                + pricing.net_of(int(gross_cents))
            )

    def credit_topup(user: dict, credit_cents, gross_cents) -> None:
        """The one balance credit path, shared by manual top ups
        (credited by the webhook) and auto reload charges, so both
        raise the balance and the net paid ceiling the same way."""
        user["balance_cents"] = int(user.get("balance_cents", 0)) + int(credit_cents)
        add_net_paid(user, gross_cents)

    def attempt_auto_reload(user: dict, state: dict) -> bool:
        """Auto reload (Jack, 2026-11-17). When a job is blocked
        because the balance can no longer cover it, charge the
        saved card once for the reload amount the customer picked,
        credit the balance through the top up path, and let the
        job proceed. The state dict is per job, so there is never
        more than one attempt per blocked job. A failed charge
        changes nothing; the job then fails the way it already
        did, with the reload named in the message. Only the Stripe
        customer id and payment method id are ever stored, never
        card numbers."""
        if state.get("tried"):
            return bool(state.get("ok"))
        cfg = user.get("auto_reload") or {}
        amount = int(cfg.get("amount_cents") or 0)
        if (not cfg.get("enabled")
                or amount not in pricing.TOPUP_PACKS_CENTS
                or not user.get("stripe_customer_id")
                or not user.get("stripe_payment_method_id")):
            return False
        state["tried"] = True
        state["attempted"] = True
        stripe, err = stripe_client()
        if err:
            state["ok"] = False
            return False
        try:
            pi = stripe.PaymentIntent.create(
                amount=amount,
                currency="usd",
                customer=user["stripe_customer_id"],
                payment_method=user["stripe_payment_method_id"],
                off_session=True,
                confirm=True,
                statement_descriptor_suffix="MOONFACTORY",
                metadata={"user_id": user["id"], "kind": "auto_reload",
                          "credit_cents": str(amount)},
            )
        except Exception:
            state["ok"] = False
            return False
        if getattr(pi, "status", None) != "succeeded":
            state["ok"] = False
            return False
        credited = meta.get("reload_credited") or {}
        if pi.id not in credited:
            credited[pi.id] = True
            meta["reload_credited"] = credited
            credit_topup(user, amount, pi.amount_received or amount)
            save_user(user)
        state["ok"] = True
        return True

    def reload_fail_detail(state: dict, plain_detail: str) -> str:
        """The failure message for a blocked job. When an auto
        reload was attempted and the charge did not go through,
        the message says so, in plain words."""
        if state.get("attempted") and not state.get("ok"):
            return plain_detail + ", and the reload did not go through"
        return plain_detail

    # -- stripe -------------------------------------------------------------

    def stripe_mode() -> str:
        """Which Stripe mode the configured key belongs to. The
        platform runs on a restricted key (rk_), which is a full
        API key for its permitted resources, so it counts the same
        as the matching secret key."""
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if key.startswith(("sk_live_", "rk_live_")):
            return "live"
        if key.startswith(("sk_test_", "rk_test_")):
            return "test"
        return "off"

    def stripe_client():
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if not key:
            return None, "payments are not configured yet"
        if stripe_mode() == "off":
            return None, "the configured Stripe key is not a secret key"
        import stripe

        stripe.api_key = key
        return stripe, None

    def ensure_stripe_customer(user: dict, stripe) -> str:
        """Every checkout runs on the account's one Stripe
        customer. A card saved at checkout can only be charged
        again later (auto reload) when it belongs to a customer.
        A checkout with no customer leaves the card attached to
        nobody and impossible to reuse, which is exactly what the
        first real top up did before this helper existed (found
        live, 2026-11-17)."""
        if not user.get("stripe_customer_id"):
            customer = stripe.Customer.create(
                email=user["email"],
                metadata={"user_id": user["id"]},
            )
            user["stripe_customer_id"] = customer.id
            save_user(user)
        return user["stripe_customer_id"]

    @web.post("/api/checkout/subscription")
    async def checkout_subscription(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        stripe, err = stripe_client()
        if err:
            raise HTTPException(status_code=503, detail=err)
        base = base_url(request)
        # A configured Stripe price ID wins; otherwise the price is
        # created inline from pricing.py so the amounts can never
        # drift apart.
        price_id = os.environ.get("STRIPE_PRICE_CREATOR", "")
        if price_id:
            line_items = [{"price": price_id, "quantity": 1}]
        else:
            line_items = [{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Moonfactory Creator"},
                    "unit_amount": pricing.CREATOR_PRICE_CENTS,
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }]
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=line_items,
            customer=ensure_stripe_customer(user, stripe),
            client_reference_id=user["id"],
            metadata={"user_id": user["id"]},
            success_url=f"{base}/app?checkout=success",
            cancel_url=f"{base}/app?checkout=cancel",
        )
        return {"url": session.url}

    @web.post("/api/checkout/topup")
    async def checkout_topup(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        stripe, err = stripe_client()
        if err:
            raise HTTPException(status_code=503, detail=err)
        body = await request.json()
        pack = int(body.get("pack_cents") or 0)
        if pack not in pricing.TOPUP_PACKS_CENTS:
            raise HTTPException(status_code=422, detail="unknown top up pack")
        base = base_url(request)
        price_id = os.environ.get(f"STRIPE_PRICE_TOPUP_{pack}", "")
        if price_id:
            line_items = [{"price": price_id, "quantity": 1}]
        else:
            line_items = [{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Moonfactory prepaid credit"},
                    "unit_amount": pack,
                },
                "quantity": 1,
            }]
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=line_items,
            payment_intent_data={"setup_future_usage": "off_session",
                                 "statement_descriptor_suffix": "MOONFACTORY"},
            customer=ensure_stripe_customer(user, stripe),
            client_reference_id=user["id"],
            metadata={"user_id": user["id"], "credit_cents": str(pack)},
            success_url=f"{base}/app?checkout=success",
            cancel_url=f"{base}/app?checkout=cancel",
        )
        return {"url": session.url}

    @web.post("/api/billing/setup-card")
    async def billing_setup_card(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        stripe, err = stripe_client()
        if err:
            raise HTTPException(status_code=503, detail=err)
        ensure_stripe_customer(user, stripe)
        base = base_url(request)
        session = stripe.checkout.Session.create(
            mode="setup",
            currency="usd",
            customer=user["stripe_customer_id"],
            setup_intent_data={"metadata": {"user_id": user["id"]}},
            metadata={"user_id": user["id"]},
            success_url=f"{base}/app?checkout=success",
            cancel_url=f"{base}/app?checkout=cancel",
        )
        return {"url": session.url}

    @web.post("/api/billing/autoreload")
    async def billing_autoreload(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        body = await request.json()
        enabled = bool(body.get("enabled"))
        amount = int(body.get("amount_cents") or 0)
        if enabled:
            if amount not in pricing.TOPUP_PACKS_CENTS:
                raise HTTPException(
                    status_code=422,
                    detail="pick a reload amount of $1, $5, $10, or $20")
            if not (user.get("stripe_customer_id")
                    and user.get("stripe_payment_method_id")):
                raise HTTPException(
                    status_code=422,
                    detail="save a card first, then turn auto reload on")
            user["auto_reload"] = {"enabled": True, "amount_cents": amount}
        else:
            cfg = dict(user.get("auto_reload") or {})
            cfg["enabled"] = False
            user["auto_reload"] = cfg
        save_user(user)
        cfg = user.get("auto_reload") or {}
        return {"auto_reload_enabled": bool(cfg.get("enabled")),
                "auto_reload_amount_cents": int(cfg.get("amount_cents") or 0),
                "has_saved_card": bool(user.get("stripe_customer_id")
                                       and user.get("stripe_payment_method_id"))}

    @web.post("/api/stripe/webhook")
    async def stripe_webhook(request: Request):
        stripe, err = stripe_client()
        wh_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        if err or not wh_secret:
            raise HTTPException(status_code=503, detail="webhook is not configured")
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig, wh_secret)
        except Exception:
            raise HTTPException(status_code=400, detail="bad signature")
        # construct_event verifies the signature, but the SDK returns
        # typed objects whose .get() raises AttributeError in current
        # stripe-python versions; every delivery crashed at the first
        # field read until this line (found by the signed-webhook
        # verification run, 2026-11-16). The payload bytes are
        # signature-verified at this point, so handle the event as
        # the plain JSON it is.
        event = json.loads(payload)
        seen = meta.get("stripe_events") or {}
        if event["id"] in seen:
            return {"received": True, "duplicate": True}
        seen[event["id"]] = True
        meta["stripe_events"] = seen
        etype = event["type"]
        obj = event["data"]["object"]

        if etype == "checkout.session.completed":
            uid = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
            user = users.get(f"id:{uid}") if uid else None
            if user:
                if obj.get("mode") == "setup":
                    user["stripe_customer_id"] = obj.get("customer") or user.get("stripe_customer_id")
                    si_id = obj.get("setup_intent")
                    if si_id:
                        try:
                            si = stripe.SetupIntent.retrieve(si_id)
                            if si and si.payment_method:
                                user["stripe_payment_method_id"] = si.payment_method
                        except Exception:
                            pass
                    if user.get("stripe_payment_method_id"):
                        cfg = dict(user.get("auto_reload") or {})
                        cfg["enabled"] = True
                        cfg["amount_cents"] = int(cfg.get("amount_cents") or 1000)
                        user["auto_reload"] = cfg
                elif obj.get("mode") == "subscription":
                    user["plan"] = "creator"
                    user["past_due"] = False
                    user["stripe_customer_id"] = obj.get("customer")
                    user["stripe_subscription_id"] = obj.get("subscription")
                    try:
                        sub = stripe.Subscription.retrieve(obj.get("subscription"))
                        pm = getattr(sub, "default_payment_method", None)
                        if not pm:
                            pms = stripe.PaymentMethod.list(
                                customer=obj.get("customer"), type="card", limit=1)
                            if pms and pms.data:
                                pm = pms.data[0].id
                        if pm:
                            user["stripe_payment_method_id"] = pm
                    except Exception:
                        pass
                    if user.get("stripe_payment_method_id"):
                        cfg = dict(user.get("auto_reload") or {})
                        cfg["enabled"] = True
                        cfg["amount_cents"] = int(cfg.get("amount_cents") or 1000)
                        user["auto_reload"] = cfg
                    u = user.get("usage") or {}
                    u["included_seconds"] = 0.0
                    u["included_cost_cents"] = 0.0
                    user["usage"] = u
                    # The money itself is counted on invoice.paid,
                    # which fires for the first subscription payment
                    # too; counting it here as well would double it.
                elif obj.get("mode") == "payment":
                    credit = int((obj.get("metadata") or {}).get("credit_cents") or 0)
                    credit_topup(user, credit, obj.get("amount_total") or credit)
                    user["stripe_customer_id"] = obj.get("customer") or user.get("stripe_customer_id")
                    pi_id = obj.get("payment_intent")
                    if pi_id:
                        try:
                            pi = stripe.PaymentIntent.retrieve(pi_id)
                            # Only a card that belongs to the
                            # session's customer can ever be charged
                            # again. A card from a customerless
                            # checkout is stored nowhere, so the
                            # account never claims a saved card it
                            # cannot use.
                            if pi and pi.payment_method and obj.get("customer"):
                                user["stripe_payment_method_id"] = pi.payment_method
                        except Exception:
                            pass
                    if user.get("stripe_payment_method_id") and credit:
                        user["auto_reload"] = {"enabled": True,
                                                "amount_cents": credit}
                save_user(user)
        elif etype == "invoice.paid":
            counted = meta.get("net_counted_invoices") or {}
            invoice_id = obj.get("id")
            first_count = bool(invoice_id) and invoice_id not in counted
            if invoice_id:
                counted[invoice_id] = True
                meta["net_counted_invoices"] = counted
            customer = obj.get("customer")
            for key in list(users.keys()):
                if not key.startswith("id:"):
                    continue
                user = users.get(key)
                if user and user.get("stripe_customer_id") == customer:
                    u = user.get("usage") or {}
                    u["included_seconds"] = 0.0
                    u["included_cost_cents"] = 0.0
                    user["usage"] = u
                    user["past_due"] = False
                    if first_count:
                        add_net_paid(user, obj.get("amount_paid"))
                    save_user(user)
        elif etype == "invoice.payment_failed":
            customer = obj.get("customer")
            for key in list(users.keys()):
                if not key.startswith("id:"):
                    continue
                user = users.get(key)
                if user and user.get("stripe_customer_id") == customer:
                    user["past_due"] = True  # Rule 1: failed payment freezes dispatch.
                    save_user(user)
        elif etype == "customer.subscription.deleted":
            customer = obj.get("customer")
            for key in list(users.keys()):
                if not key.startswith("id:"):
                    continue
                user = users.get(key)
                if user and user.get("stripe_customer_id") == customer:
                    user["plan"] = "free"
                    user["stripe_subscription_id"] = None
                    save_user(user)
        elif etype == "setup_intent.succeeded":
            uid = (obj.get("metadata") or {}).get("user_id")
            user = users.get(f"id:{uid}") if uid else None
            if user and obj.get("payment_method"):
                user["stripe_payment_method_id"] = obj.get("payment_method")
                if obj.get("customer"):
                    user["stripe_customer_id"] = obj.get("customer")
                cfg = dict(user.get("auto_reload") or {})
                cfg["enabled"] = True
                cfg["amount_cents"] = int(cfg.get("amount_cents") or 1000)
                user["auto_reload"] = cfg
                save_user(user)
        elif etype == "payment_intent.succeeded":
            md = obj.get("metadata") or {}
            if md.get("kind") == "auto_reload":
                credited = meta.get("reload_credited") or {}
                if obj.get("id") not in credited:
                    uid = md.get("user_id")
                    user = users.get(f"id:{uid}") if uid else None
                    if user:
                        credited[obj["id"]] = True
                        meta["reload_credited"] = credited
                        credit_topup(user, int(md.get("credit_cents") or 0),
                                      obj.get("amount_received") or obj.get("amount") or 0)
                        save_user(user)
        return {"received": True}

    # -- per-second lane completion callback ----------------------------

    @web.post("/api/fal/webhook/{job_id}")
    async def fal_webhook(job_id: str, request: Request):
        """Completion callback for per-second jobs. The URL carries
        a per-job signed token (issued at submission); deliveries
        are idempotent, and polling in mirror_status covers any
        callback that never arrives."""
        job = jobs.get(job_id)
        if not job or job.get("provider") != "fal":
            raise HTTPException(status_code=404, detail="unknown job")
        token = request.query_params.get("token") or ""
        if not hmac.compare_digest(token, _fal_token(job_id)):
            raise HTTPException(status_code=403, detail="bad token")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if body.get("request_id") and body.get("request_id") != job.get("provider_request_id"):
            return {"received": True, "ignored": True}
        if job["status"] in ("done", "error"):
            return {"received": True, "duplicate": True}
        if body.get("status") == "OK":
            payload = body.get("payload") or {}
            video = payload.get("video") or {}
            if video.get("url"):
                job["bytes"] = video.get("file_size")
                _poll_done(job, video["url"],
                           duration=payload.get("duration"))
            else:
                # No file in the payload: let the poller fetch the
                # result on its next pass.
                job["last_poll"] = 0
                jobs[job["id"]] = job
        else:
            if body.get("error"):
                job["provider_error"] = str(body.get("error"))[:200]
                jobs[job["id"]] = job
            fail_fal_job(job)
        return {"received": True}

    # -- operator -----------------------------------------------------------

    def require_admin(x_admin_key: str):
        if not hmac.compare_digest(x_admin_key, os.environ.get("ADMIN_KEY", "\0")):
            raise HTTPException(status_code=401, detail="bad admin key")

    @web.get("/api/admin/outbox")
    def admin_outbox(x_admin_key: str = Header(default="")):
        require_admin(x_admin_key)
        return {"items": outbox.get("items") or []}

    @web.get("/api/admin/reports")
    def admin_reports(x_admin_key: str = Header(default="")):
        require_admin(x_admin_key)
        return {"reports": meta.get("reports") or []}

    @web.post("/api/admin/gallery/{job_id}/remove")
    def admin_gallery_remove(job_id: str, x_admin_key: str = Header(default="")):
        require_admin(x_admin_key)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="unknown job")
        job["removed"] = True
        jobs[job_id] = job
        return {"status": "removed"}

    @web.post("/api/admin/gallery/{job_id}/restore")
    def admin_gallery_restore(job_id: str, x_admin_key: str = Header(default="")):
        require_admin(x_admin_key)
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="unknown job")
        job["removed"] = False
        jobs[job_id] = job
        return {"status": "restored"}

    return web


# -- captions (Jack's call, 2026-11-18) -------------------------------------
# A generation time opt in: the Create form's captions box.
# Existing gallery videos are never captioned after the fact;
# the choice exists only when a video is made. The style is the
# Shorts lane's house caption style (bold white words, heavy
# black outline, low on the frame), scaled from the shorts
# frame (1080x1920) to whatever size the finished video is.

CAPTIONS_MAX_WORDS = 4
CAPTIONS_MIN_SECONDS = 0.35

# Word timed transcripts of finished audio, keyed by job id.
# The word check stage transcribes every spoken job's final
# audio; when it ran, its words wait here so the captions stage
# burns in from that same transcription instead of paying for a
# second one. A missing entry just means the captions stage
# runs the same transcription itself.
_CAPTION_TRANSCRIPTS: dict = {}


def _caption_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _caption_ass_escape(text: str) -> str:
    return (text.replace("\\", " ").replace("{", "(")
            .replace("}", ")").replace("\n", " "))


def caption_events(words: list) -> list:
    """Group word timed transcript entries ({word, start, end},
    the faster-whisper shape) into caption chunks of at most
    CAPTIONS_MAX_WORDS words, the Shorts lane's chunking. A chunk
    shows from its first word's start to its last word's end,
    held at least CAPTIONS_MIN_SECONDS. Returns (start, end,
    text) events in order."""
    clean = []
    for w in words or []:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        clean.append((float(w.get("start") or 0),
                      float(w.get("end") or 0), text))
    events = []
    for i in range(0, len(clean), CAPTIONS_MAX_WORDS):
        chunk = clean[i:i + CAPTIONS_MAX_WORDS]
        start = chunk[0][0]
        end = chunk[-1][1]
        if end < start + CAPTIONS_MIN_SECONDS:
            end = start + CAPTIONS_MIN_SECONDS
        events.append((start, end,
                       " ".join(t for _s, _e, t in chunk)))
    return events


def build_captions_ass(words: list, width: int, height: int) -> str:
    """The ASS subtitle file for one finished video: the Shorts
    lane's caption style (DejaVu Sans bold, white with a heavy
    black outline, bottom center, low on the frame), with the
    size, outline and margins scaled from the 1080x1920 shorts
    frame to this video's own dimensions."""
    scale = (height or 1920) / 1920.0
    font_size = max(14, round(72 * scale))
    outline = max(1, round(5 * scale))
    margin_v = round(300 * scale)
    margin_lr = round(60 * (width or 1080) / 1080.0)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        f"Style: Captions,DejaVu Sans,{font_size},&H00FFFFFF,"
        f"&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{outline},0,2,{margin_lr},{margin_lr},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    body = "".join(
        f"Dialogue: 0,{_caption_ass_time(start)},"
        f"{_caption_ass_time(end)},Captions,,0,0,0,,"
        f"{_caption_ass_escape(text)}\n"
        for start, end, text in caption_events(words)
    )
    return header + body


def _process_file(raw_path: Path, out_path: Path, watermark: bool, bed_path=None,
                  voice_path=None):
    """Mux a music bed onto the video (CPU): the job's custom bed
    when one is passed, else the house bed. Free-tier files also get
    the watermark burned in, which needs a re-encode. When the job
    carries a voiceover track, the voice sits on top and the bed
    ducks underneath: speech is speech, never just a bed."""
    if bed_path is None:
        bed_path = BED_PATH
    bed = bed_path if bed_path and Path(bed_path).exists() else None
    voice = voice_path if voice_path and Path(voice_path).exists() else None
    if voice:
        vf = (
            f"drawtext=fontfile={FONT_PATH}:text='Moonfactory':"
            "fontcolor=white@0.55:fontsize=h/26:borderw=2:bordercolor=black@0.3:"
            "x=w-tw-24:y=h-th-24"
        ) if watermark else None
        inputs = ["-i", str(raw_path)]
        filters = []
        mix = []
        idx = 1
        if bed:
            inputs += ["-stream_loop", "-1", "-i", str(bed)]
            filters.append(f"[{idx}:a]volume=0.22[bed]")
            mix.append("[bed]")
            idx += 1
        inputs += ["-i", str(voice)]
        filters.append(f"[{idx}:a]volume=1.0,apad[vox]")
        mix.append("[vox]")
        filters.append(
            "".join(mix)
            + f"amix=inputs={len(mix)}:duration=first:dropout_transition=0,"
              "loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
        cmd = ["ffmpeg", "-y", *inputs,
               "-filter_complex", ";".join(filters),
               "-map", "0:v:0", "-map", "[aout]",
               "-c:a", "aac", "-b:a", "160k"]
        if watermark:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-vf", vf]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-shortest", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"ffmpeg failed: {proc.stderr[-400:]}")
        return
    # The bed loops: a chained video is longer than one bed pass,
    # and -shortest must trim to the video, never to the bed.
    audio_in = ["-stream_loop", "-1", "-i", str(bed)] if bed else []
    audio_map = ["-map", "0:v:0", "-map", "1:a:0"] if bed else ["-map", "0:v:0"]
    audio_codec = ["-c:a", "aac", "-b:a", "160k",
                   "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"] if bed else []
    if watermark:
        vf = (
            f"drawtext=fontfile={FONT_PATH}:text='Moonfactory':"
            "fontcolor=white@0.55:fontsize=h/26:borderw=2:bordercolor=black@0.3:"
            "x=w-tw-24:y=h-th-24"
        )
        cmd = ["ffmpeg", "-y", "-i", str(raw_path), *audio_in, *audio_map,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-vf", vf, *audio_codec, "-shortest", str(out_path)]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(raw_path), *audio_in, *audio_map,
               "-c:v", "copy", *audio_codec, "-shortest", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-400:]}")


def _has_audio_stream(path: Path) -> bool:
    """Whether a media file carries an audio stream at all. The
    no-audio routes (Veo 3.1 and Veo 3.1 Lite on their cheapest
    published rates) deliver silent files, and the finishing stage
    must know that instead of failing on a missing stream."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0",
             str(path)],
            capture_output=True, text=True, timeout=60)
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


def _process_api_file(raw_path: Path, out_path: Path, bed_path=None,
                      voice_path=None):
    """Finishing for per-second-lane outputs (CPU). The model's own
    sound is the delivered audio, loudness-normalized to the house
    standard (about -14 LUFS / -1 dBTP); nothing replaces it. When
    the job ordered a custom bed, the bed is mixed under the model's
    sound. When the job carries a voiceover track (a speech script
    on a lane that cannot speak natively), the voice sits on top
    and everything else ducks under it. A silent output with
    nothing to mix ships with a silent track rather than failing.
    Paid lanes are never watermarked. The optional GPU upscale
    pass is not part of this lane yet (design s.3 step 6 lists it
    as optional); the file ships at the resolution the user
    picked."""
    bed = bed_path if bed_path and Path(bed_path).exists() else None
    voice = voice_path if voice_path and Path(voice_path).exists() else None
    inputs = ["-i", str(raw_path)]
    filters = []
    mix = []
    idx = 1
    if _has_audio_stream(raw_path):
        filters.append("[0:a]loudnorm=I=-14:TP=-1:LRA=11[na]")
        mix.append("[na]")
    if bed:
        inputs += ["-stream_loop", "-1", "-i", str(bed)]
        filters.append(f"[{idx}:a]volume={'0.18' if voice else '0.35'}[bed]")
        mix.append("[bed]")
        idx += 1
    if voice:
        inputs += ["-i", str(voice)]
        filters.append(f"[{idx}:a]volume=1.0,apad[vox]")
        mix.append("[vox]")
        idx += 1
    if mix:
        filters.append(
            "".join(mix)
            + f"amix=inputs={len(mix)}:duration=first:dropout_transition=0[aout]"
        )
        cmd = ["ffmpeg", "-y", *inputs,
               "-filter_complex", ";".join(filters),
               "-map", "0:v:0", "-map", "[aout]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
               "-shortest", str(out_path)]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(raw_path),
               "-f", "lavfi", "-i",
               "anullsrc=channel_layout=stereo:sample_rate=44100",
               "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
               "-shortest", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-400:]}")


@app.function(image=image, schedule=modal.Period(hours=6))
def refresh_provider_rates():
    """Re-read the providers' published per-model prices and refresh
    the live rate table (design s.6). The table's timestamp advances
    only when every required model's full price set was re-read, so
    a partial or failed read leaves the staleness pause armed
    instead of quietly extending stale prices. Required reads: the
    Seedance and Wan 3.0 pages on the BeatAPI supplier's site.
    (Wan 3.0 Prime's page was a required read until the follow-up
    pass moved that lane to CometAPI; its rate is seeded-only
    now.) The fal
    pages are read opportunistically (their prices update when the
    pages parse, and the weekly margin report is the backstop
    until they do). Runs on CPU; no key needed, the pages are
    public."""
    import re as _re

    import requests as _requests

    def fal_pairs(text: str) -> dict:
        text = text.replace("\\", "")
        pairs = {}
        for price, res in _re.findall(
            r"\$(\d+\.\d+)\*\*\s*\*\*(480p|720p|1080p)\*\*", text
        ):
            pairs[res] = round(float(price) * 100, 3)
        if not pairs:
            for price, res in _re.findall(
                r"\$(\d+\.\d+)\s+at\s+(480p|720p|1080p)", text
            ):
                pairs[res] = round(float(price) * 100, 3)
        return pairs

    # Only models whose primary route is still fal are read here
    # (follow-up pass, 2026-11-16): a fal page read for a re-routed
    # lane would shadow its seeded primary rate in the stored
    # table while dispatch goes elsewhere. The re-routed lanes'
    # rates are seeded-only until a parseable page on their own
    # supplier is wired; that coverage gap is recorded in
    # BUILD-LOG.
    fal_pages = {
        "veo3_fast": [("fal-ai/veo3.1/fast", ("720p", "1080p"))],
        "ltx25": [("lightricks/ltx-2.5/text-to-video", ("720p", "1080p"))],
        "ltx25_fast": [("lightricks/ltx-2.5/text-to-video/fast", ("720p", "1080p"))],
    }
    found = {}
    for model, page_specs in fal_pages.items():
        pairs = {}
        expected = set()
        for page, resolutions in page_specs:
            expected.update(resolutions)
            try:
                resp = _requests.get(
                    f"https://fal.ai/models/{page}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=30,
                )
                if resp.status_code != 200:
                    continue
                for res, cents in fal_pairs(resp.text).items():
                    if res in resolutions:
                        pairs[res] = cents
            except Exception:
                continue
        if expected and expected.issubset(pairs):
            found[model] = {res: pairs[res] for res in sorted(expected)}

    # The BeatAPI supplier publishes per-clip prices for 8-second
    # clips on each model's own page; per-second is price / 8.
    # Seedance's rows used are the no-video-input ones, the same
    # basis as the seeded table; the Wan pages carry a single rate
    # per resolution. The pages carry the rows as structured-data
    # offers ("name": "... 8 sec · 720p ...", "price": 1.5633) and
    # as a rendered table; try both shapes. The offers pattern is
    # pinned to the page's own URL so a cross-sold model's rows on
    # the same page can never land in the wrong lane.
    beatapi_pages = {
        "seedance": ("https://beatapi.io/seedance-2.5-api", " · no video input"),
        "wan3": ("https://beatapi.io/wan-3-0-api", ""),
    }
    for beat_model, (beat_url, qualifier) in beatapi_pages.items():
        try:
            resp = _requests.get(
                beat_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            text = resp.text.replace("\\u00b7", "·").replace("\\", "")
            pairs = {}
            for res, price in _re.findall(
                r"8 sec · (480p|720p|1080p)" + _re.escape(qualifier)
                + r"\",\"url\":\"" + _re.escape(beat_url)
                + r"\",\"price\":(\d+\.\d+)",
                text,
            ):
                pairs[res] = round(float(price) / 8 * 100, 3)
            if len(pairs) < 3:
                pairs = {}
                for res, price in _re.findall(
                    r"8 sec · (480p|720p|1080p)" + _re.escape(qualifier)
                    + r"[^$\d]{0,80}\$(\d+\.\d+)",
                    text,
                ):
                    pairs[res] = round(float(price) / 8 * 100, 3)
            if len(pairs) == 3:
                found[beat_model] = {
                    res: pairs[res] for res in pricing.RESOLUTIONS
                }
        except Exception:
            continue

    required = {"wan3", "seedance"}
    if not required.issubset(found):
        return {"verified": sorted(found), "updated": False}
    # Seedance's 480p and 720p tiers ride CometAPI (720p since
    # the follow-up pass, 480p since pricing round 2, both
    # 2026-11-16); the BeatAPI page read covers only the tier
    # BeatAPI still serves (1080p), so the seeded CometAPI rates
    # are never shadowed.
    if "seedance" in found:
        found["seedance"] = {
            res: cents for res, cents in found["seedance"].items()
            if res not in ("480p", "720p")
        }
    table = meta.get("provider_rates") or {
        "rates": pricing.PROVIDER_RATES_CENTS_PER_SECOND,
        "updated_at": _now(),
    }
    rates = dict(table.get("rates") or {})
    changed = []
    for m, r in found.items():
        merged = dict(rates.get(m) or {})
        merged.update(r)
        if merged != rates.get(m):
            changed.append(m)
        rates[m] = merged
    meta["provider_rates"] = {"rates": rates, "updated_at": _now()}
    return {"verified": sorted(found), "updated": True, "changed": changed}


# ---------------------------------------------------------------------------
# Pages. Plain copy, prices always loaded from /api/pricing.
# ---------------------------------------------------------------------------

_CSS = """
<style>
  :root{
    --ink:#f4f4f1; --gray:#b8b8c0; --faint:#92929c;
    --line:#26262e; --bg:#0c0c0f; --card:#17171d;
    --accent:#6366f1; --accent-bright:#818cf8;
    --inset:#101014; --seg-on:#2b2b34; --seg-bg:#1b1b21;
    --err:#ff8a8a; --ok:#7ddba3;
  }
  *{box-sizing:border-box}
  body{font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;margin:0;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased}
  /* App behavior on the web (PWA pass, 2026-11-17): no tap
     flash, no double-tap zoom delay on controls, no pull-to-
     refresh fighting the studio, and safe-area padding when
     installed standalone. */
  html{-webkit-tap-highlight-color:transparent;overscroll-behavior-y:none}
  button,a,select,input,textarea,label{touch-action:manipulation}
  body{padding-bottom:env(safe-area-inset-bottom)}
  input,textarea,select{font-size:16px}
  main{max-width:960px;margin:0 auto;padding:26px 24px 90px}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .logo{font-weight:800;font-size:19px;letter-spacing:-.02em;text-decoration:none;color:var(--ink);display:flex;align-items:center;gap:9px}
  .logo svg{flex:none;display:block}
  .logo em{font-style:normal;color:var(--accent-bright)}
  header nav{display:flex;gap:26px;align-items:center}
  header nav a{text-decoration:none;color:var(--gray);font-size:15px}
  header nav a:hover{color:var(--ink)}
  .profile{position:relative}
  .avatar{width:34px;height:34px;border-radius:50%;background:var(--accent);color:#fff;border:0;padding:0;font:inherit;font-size:15px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center}
  .avatar:hover{background:var(--accent-bright)}
  .menu{position:absolute;right:0;top:calc(100% + 10px);z-index:60;min-width:216px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:8px;box-shadow:0 12px 32px rgba(0,0,0,.5);flex-direction:column}
  .menu a,.menu button{display:block;width:100%;box-sizing:border-box;text-align:left;background:none;border:0;color:var(--ink);font:inherit;font-size:14.5px;padding:9px 10px;border-radius:9px;cursor:pointer;text-decoration:none}
  .menu a:hover,.menu button:hover{background:var(--seg-on)}
  .menu-email{display:block;color:var(--faint);font-size:13px;padding:8px 10px 9px;border-bottom:1px solid var(--line);margin-bottom:6px;word-break:break-all}
  .overlay{position:fixed;inset:0;background:rgba(6,6,9,.72);z-index:80;display:flex;align-items:flex-start;justify-content:center;padding:22px 14px;overflow-y:auto}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:16px;max-width:560px;width:100%;padding:22px;margin-top:4vh}
  .panel-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
  .panel-close{background:var(--seg-on);color:var(--ink);border:0;width:34px;height:34px;border-radius:50%;font-size:17px;cursor:pointer;line-height:1;flex:none}
  h1{font-size:46px;line-height:1.12;letter-spacing:-.03em;font-weight:800;margin:0}
  h2{font-size:26px;letter-spacing:-.02em;margin:0 0 6px}
  a{color:var(--accent-bright)}
  .btn{display:inline-block;background:var(--accent);color:#fff;padding:11px 20px;border-radius:12px;text-decoration:none;border:0;cursor:pointer;font:inherit;font-size:15px;font-weight:600;transition:.15s}
  .btn:hover{background:var(--accent-bright)}
  .btn.secondary{background:var(--seg-on);color:var(--ink)}
  .btn.secondary:hover{background:#3a3a46}
  .topup-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:2px 0 4px}
  .topup-grid .btn{width:100%;text-align:center;padding-left:8px;padding-right:8px}
  .btn:disabled{opacity:.45;cursor:default}
  .btn.gen{width:100%;margin-top:24px;border-radius:14px;font-size:17px;font-weight:700;padding:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px;margin:18px 0}
  input,textarea,select{width:100%;padding:12px 14px;margin:6px 0 12px;border:1px solid var(--line);border-radius:12px;font:inherit;font-size:15px;background:var(--inset);color:var(--ink)}
  textarea{line-height:1.5;resize:vertical}
  textarea:focus,select:focus,input:focus{outline:2px solid var(--accent);outline-offset:0;border-color:transparent}
  textarea::placeholder,input::placeholder{color:var(--faint)}
  select{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23a1a1aa' stroke-width='2' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 14px center;padding-right:36px}
  label{font-weight:600;font-size:14px;color:var(--gray);display:block}
  label.flabel{font-size:13px;margin-bottom:7px}
  .muted{color:var(--faint);font-size:14px;line-height:1.6}
  .err{color:var(--err)} .ok{color:var(--ok)}
  video{width:100%;border-radius:14px;background:#000}
  table{border-collapse:collapse} td,th{border:1px solid var(--line);padding:8px 10px;text-align:left}
  .chip{display:inline-block;border:1px solid var(--line);background:var(--inset);color:var(--ink);border-radius:999px;padding:6px 13px;cursor:pointer;font-size:13px;margin:0 6px 6px 0;width:auto}
  .chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
  .chip:disabled{opacity:.45;cursor:default}
  #looks{display:flex;flex-wrap:wrap;gap:8px 14px;margin-bottom:12px}
  #looks label{font-weight:400;font-size:13px}
  #looks select{width:auto;margin:2px 0 0;display:block;padding:8px 30px 8px 10px}
  .tabs{display:flex;justify-content:center;margin:18px 0 6px}
  .tabs .seg{display:flex;background:var(--seg-bg);border-radius:999px;padding:3px;gap:2px}
  .tabs .btn{background:transparent;color:var(--gray);padding:8px 20px;border-radius:999px;font-size:14px;box-shadow:none}
  .tabs .btn:hover{background:transparent;color:var(--ink)}
  .tabs .btn:not(.secondary){background:var(--seg-on);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.4)}
  .tabs .btn:not(.secondary):hover{background:var(--seg-on)}
  .mode-toggle{display:flex;justify-content:center;gap:10px;margin-top:14px}
  .group-label{font-size:13px;color:var(--faint);font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin:22px 0 8px}
  .composer-prompt{font-size:16px;min-height:84px}
  .row{display:flex;gap:12px;margin-top:16px}
  .row .grow{flex:1}
  .dur{display:flex;background:var(--inset);border-radius:12px;padding:3px;gap:2px;flex-wrap:wrap}
  .dur button{flex:1;border:0;background:transparent;padding:9px 10px;border-radius:9px;font:inherit;font-size:15px;font-weight:600;color:var(--gray);cursor:pointer;white-space:nowrap}
  .dur button.on{background:var(--seg-on);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.4)}
  .dur.off{opacity:.45;pointer-events:none}
  .drop{border:1.5px dashed #34343e;border-radius:14px;padding:30px 20px;text-align:center;color:var(--gray);cursor:pointer;transition:.15s;background:var(--inset)}
  .drop:hover,.drop.over{border-color:var(--accent);color:var(--ink)}
  .drop svg{display:block;margin:0 auto 10px}
  .drop .big{font-size:15.5px;font-weight:600;color:var(--ink)}
  .drop .small{font-size:13px;margin-top:3px}
  .drop.filled{border-style:solid;border-color:var(--line);padding:14px;display:flex;align-items:center;gap:14px;text-align:left;cursor:default}
  .drop .thumb{max-width:52px;max-height:70px;border-radius:9px;flex:none}
  .drop .fname{font-weight:600;color:var(--ink);font-size:14.5px}
  .drop .fmeta{color:var(--faint);font-size:13px}
  .drop .remove{margin-left:auto;border:0;background:none;color:var(--faint);font-size:20px;cursor:pointer;line-height:1;padding:6px}
  .drop .remove:hover{color:var(--ink)}
  details.more{margin-top:20px;border-top:1px solid var(--line);padding-top:16px}
  details.more summary{cursor:pointer;color:var(--gray);font-size:14.5px;font-weight:600;list-style:none}
  details.more summary::-webkit-details-marker{display:none}
  details.more summary:hover{color:var(--ink)}
  .under{margin-top:14px;text-align:center;color:var(--faint);font-size:13.5px;line-height:1.6}
  .create-head{display:flex;align-items:center;gap:12px;margin-top:8px}
  .paywall p{color:var(--gray);font-size:15.5px;line-height:1.6}
  .hero{max-width:680px;margin:64px auto 0;text-align:center}
  .hero p{margin:18px auto 0;color:var(--gray);font-size:18px;line-height:1.55;max-width:520px}
  .hero-entry{max-width:640px;margin:40px auto 0;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px}
  .hero-entry .row2{display:flex;gap:12px}
  .hero-entry input{margin:0;flex:1}
  .hero-entry .btn{flex:none}
  .strip-label{max-width:880px;margin:72px auto 14px;color:var(--faint);font-size:13px;letter-spacing:.06em;text-transform:uppercase;font-weight:700}
  .strip{max-width:880px;margin:0 auto;display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
  .pricing-line{max-width:680px;margin:64px auto 0;text-align:center;color:var(--gray);font-size:15.5px;line-height:1.7}
  .pricing-line b{color:var(--ink)}
  .grid{max-width:880px;margin:0 auto;display:grid;grid-template-columns:repeat(3,1fr);gap:22px 16px}
  .tile .frame{aspect-ratio:9/16;border-radius:16px;overflow:hidden;background:var(--card)}
  .tile .frame video,.tile .frame img{width:100%;height:100%;object-fit:cover;border-radius:0}
  .tile .handle{margin-top:9px;font-size:14px;color:var(--gray)}
  .tile .report{margin-top:6px;background:none;border:0;color:var(--faint);font:inherit;font-size:13px;cursor:pointer;padding:0}
  .tile .report:hover{color:var(--ink)}
  .tile .report:disabled{color:var(--faint);cursor:default}
  .job video{max-width:360px}
  .job img.thumb{width:140px;border-radius:10px}
  footer{max-width:960px;margin:0 auto;padding:0 24px 44px;color:var(--faint);font-size:13.5px;display:flex;justify-content:space-between}
  /* Accessibility pass (2026-11-18, audit F1/F4/F5): a
     visually hidden utility for the live regions, one focus
     ring that reads on the dark theme for every interactive
     element the form field rule does not already cover, and
     a reduced motion block that stills transitions and
     animations for users who ask for it, the same treatment
     magrify.com gives its own motion. */
  .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
  :focus-visible{outline:2px solid var(--accent-bright);outline-offset:2px}
  @media (prefers-reduced-motion: reduce){
    *,*::before,*::after{animation-duration:.01ms !important;animation-iteration-count:1 !important;transition-duration:.01ms !important;scroll-behavior:auto !important}
  }
  @media(max-width:700px){
    h1{font-size:34px}
    .strip{grid-template-columns:repeat(2,1fr)}
    .grid{grid-template-columns:repeat(2,1fr)}
    .row{flex-direction:column}
    .hero-entry .row2{flex-direction:column}
  }
</style>
"""

# ------------------------------------------------------------------
# Landing top blocks: the landing is session-aware (Direction C
# rebuild, 2026-11-16). A signed-in paid member never sees Try It:
# their top block sends them straight to Create. Everyone else gets
# the Try It framing with the sign-in-first rule stated. landing()
# swaps the block into INDEX_HTML at the __LANDING_TOP__ token.
# ------------------------------------------------------------------

_LANDING_TOP_GUEST = """<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/gallery">Gallery</a><a href="/app" style="color:#f4f4f1;font-weight:600">Sign in</a></nav>
</header>
<div class="hero">
  <h1>Turn one image into<br>a short video.</h1>
  <p>Upload a photo, describe what happens, and get a vertical clip
  with sound. Music, and a voice whenever someone is talking.
  Your first 5 clips are free.</p>
</div>
<div class="hero-entry">
  <div class="row2">
    <input id="hero-prompt" type="text" maxlength="500"
      placeholder="Describe your video, like a red hot air balloon rising over green hills">
    <button class="btn" id="hero-go" type="button">Try It free</button>
  </div>
  <p class="under" style="margin-top:12px">Trying anything takes an account.
  Sign up free, verify your email, and Try It gives you 5 clips total.
  No card.</p>
</div>"""

_LANDING_TOP_MEMBER = """<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/gallery">Gallery</a><a href="/app" style="color:#f4f4f1;font-weight:600">Open Create</a></nav>
</header>
<div class="hero">
  <h1>Turn one image into<br>a short video.</h1>
  <p>Welcome back. Create is your studio, with every model, custom sound,
  and the full shelf. It is one click away.</p>
</div>
<div class="hero-entry">
  <div class="row2">
    <input id="hero-prompt" type="text" maxlength="500"
      placeholder="Describe your video, like a red hot air balloon rising over green hills">
    <button class="btn" id="hero-go" type="button">Open Create</button>
  </div>
  <p class="under" style="margin-top:12px">Members open straight into Create.
  The free trial is for people who have not paid yet, so it is not in your way.</p>
</div>"""

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found | Moonfactory</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
<style>
  body{background:#0c0c0f;color:#f4f4f1;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .box{text-align:center;padding:24px;max-width:420px}
  h1{font-size:26px;margin:18px 0 8px}
  p{color:#b8b8c0;line-height:1.6;margin:0 0 22px}
  a{display:inline-block;background:#6366f1;color:#fff;text-decoration:none;font-weight:700;padding:11px 22px;border-radius:999px;margin:0 4px}
  a.ghost{background:none;border:1px solid #26262e;color:#f4f4f1}
</style></head><body>
<div class="box">
  <svg width="54" height="54" viewBox="0 0 48 48"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg>
  <h1>That page is not here</h1>
  <p>The link may be old, or the page may have moved. The studio and the gallery are right where they should be.</p>
  <a href="/app">Open the studio</a><a class="ghost" href="/gallery">Gallery</a>
</div>
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
</script>
</body></html>"""

FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'>"
    "<path fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/>"
    "<path fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' "
    "d='M24 18.5L24 27.5L31.5 23Z'/></svg>"
)

_ICON_LINK = """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0c0c0f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Moonfactory">
<link rel="apple-touch-icon" href="/icons/icon-192.png">"""

# The PWA pass (Jack, 2026-11-17): Moonfactory is an app on the
# web. Installable, standalone, Direction C colors, and a basic
# offline shell for the static pages. The API is never cached.
_PWA_HEAD = """<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0c0c0f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Moonfactory">
<link rel="apple-touch-icon" href="/icons/icon-192.png">"""

_PWA_SCRIPT = """<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
</script>"""

MANIFEST_JSON = """{
  "name": "Moonfactory",
  "short_name": "Moonfactory",
  "description": "Make short AI videos with sound, and images, from a prompt.",
  "start_url": "/app",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#0c0c0f",
  "theme_color": "#0c0c0f",
  "icons": [
    {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
    {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
    {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
  ]
}"""

SERVICE_WORKER_JS = """// Moonfactory service worker: a basic offline shell for the
// static pages. The API and job files are never cached: account
// data and media always come from the network.
const CACHE = 'moonfactory-shell-v1';
const SHELL = ['/', '/app', '/help', '/gallery', '/favicon.ico',
  '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png'];
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) {
    return;
  }
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return resp;
        })
        .catch(() => caches.match(event.request)
          .then((hit) => hit || caches.match('/app'))));
    return;
  }
  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request)));
});
"""

_INFO_CSS = """
<style>
  .legal{max-width:760px;margin:40px auto 0}
  .legal h2{margin:30px 0 8px;font-size:21px}
  .legal p,.legal li{color:var(--gray);font-size:15.5px;line-height:1.65}
  .legal ul{margin:6px 0 6px 18px;padding:0}
  .legal li{margin:4px 0}
</style>
"""

_INFO_HEADER = """<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/gallery">Gallery</a><a href="/app">Studio</a></nav>
</header>"""

_INFO_FOOTER = """<footer><span>Moonfactory</span><span><a href="/help">Help</a> &nbsp;<a href="/terms">Terms</a> &nbsp;<a href="/privacy">Privacy</a> &nbsp;<a href="/support">Support</a></span></footer>"""


def _info_page(title: str, description: str, body: str) -> str:
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>"""
        + title
        + """</title>
<meta name="description" content=\""""
        + description
        + """\">"""
        + _CSS
        + _INFO_CSS
        + _ICON_LINK
        + _PWA_HEAD
        + "\n</head><body><main>\n"
        + _INFO_HEADER
        + '\n<div class="legal">\n'
        + body
        + "\n</div>\n</main>\n"
        + _INFO_FOOTER
        + _PWA_SCRIPT
        + "\n</body></html>"
    )


TERMS_HTML = _info_page(
    "Moonfactory terms of service",
    "The terms for using Moonfactory, covering accounts, the free tier, payments, your content, the public gallery, and the rules for generated video.",
    """
<h1 style="font-size:34px">Terms of service</h1>
<p>Last updated: November 17, 2026. By creating an account or using
Moonfactory, you agree to these terms. If you do not agree, do not
use the service.</p>

<h2>What Moonfactory is</h2>
<p>Moonfactory turns a start image and a written prompt into a short
video with sound, and can cut a longer video you supply into short
clips. Output is generated by AI models, including models run by
third party providers. AI output can be unpredictable, can resemble
other output, and comes with no promise of exclusivity or fitness
for a particular purpose.</p>

<h2>Accounts</h2>
<ul>
<li>You need an account to generate anything, and your email address
must be verified before your first generation.</li>
<li>Keep your password to yourself. Activity on your account is your
responsibility.</li>
<li>You must be at least 18 years old to use any model marked
Uncensored.</li>
</ul>

<h2>Free tier and payments</h2>
<ul>
<li>The free tier is 5 clips per account, total, for the life of the
account. Free clips are watermarked. A platform-wide free budget
also applies.</li>
<li>Paid access comes two ways: the Creator plan at $20 per month
with an included generation budget, or one-time top-ups of $1, $5,
$10, or $20 that you spend per video. Payments are processed by Stripe.
We never see or store your full card number.</li>
<li>The exact price of a generation is shown before it runs. Starting
a generation authorizes the charge or balance debit shown.</li>
<li>Failed generations caused by our pipeline are re-run or the
balance is restored in credits. Refunds are issued in credits, not
cash, except where the law requires otherwise.</li>
<li>Creator renews monthly until cancelled. Cancelling stops the
next renewal. It does not refund the current month.</li>
</ul>

<h2>Auto reload</h2>
<p>When you choose a plan or add credit, we save the payment method
you paid with and turn on auto reload. If you add credit, the reload
amount starts at the size of the credit pack you bought. If you only
choose a plan, it starts at $10. You can change the amount or turn
auto reload off at any time in Billing in the studio.</p>
<p>With auto reload on, when your balance cannot cover a job, we
charge your saved card the reload amount once, add it to your
balance, and the job goes ahead. This repeats as your balance runs
out, one charge for each job that needs it.</p>
<p>If a reload charge fails, the job it was meant to cover can stop,
the same way a job stops when a balance runs out with auto reload
off. Turning auto reload off stops future reload charges. It does
not refund credit that was already added to your balance.</p>

<h2>Your content and the gallery</h2>
<ul>
<li>You keep your rights in what you upload and what you generate.
You give us the permission needed to process, store, and deliver it,
and to send your prompt and media to the model provider you picked
so it can do the generation.</li>
<li>Finished clips appear in the public gallery by default, under
your handle only, never your email or real name. Paying customers
can opt out in the studio, and anyone can delete their own clips at
any time, which removes them from the gallery.</li>
<li>You are responsible for what you upload and generate. Only use
images, video, and audio you own or have permission to use.</li>
</ul>

<h2>Rules</h2>
<ul>
<li>No content that is illegal where you are or where it is viewed.</li>
<li>No sexual content involving minors, in any form. Zero tolerance:
accounts are closed and material is reported.</li>
<li>No intimate imagery of a real person without that person's
consent.</li>
<li>No content that infringes someone else's copyright, trademark,
or other rights.</li>
<li>No attempts to bypass plan limits, payment, or safety systems,
and no abuse of the free tier.</li>
</ul>
<p>We can remove content and close accounts that break these rules.
Anyone can report a gallery clip with its Report button. Reports are
reviewed by a person.</p>

<h2>Availability and changes</h2>
<p>The service is provided as is. Models, prices, and features can
change. A price change never applies to a generation that already
ran. We can update these terms and will note the date above when we
do. Continued use after an update means you accept it.</p>

<h2>Liability</h2>
<p>To the extent the law allows, our total liability for any claim
about the service is limited to what you paid us in the 3 months
before the claim. Nothing in these terms limits liability that
cannot be limited by law.</p>

<h2>Contact</h2>
<p>Questions about these terms: <a href="mailto:support@magrify.com">support@magrify.com</a>
or the <a href="/support">support page</a>.</p>
""",
)

PRIVACY_HTML = _info_page(
    "Moonfactory privacy policy",
    "What Moonfactory collects, why, who processes it, and how to get your data deleted.",
    """
<h1 style="font-size:34px">Privacy policy</h1>
<p>Last updated: November 17, 2026. This page says what we collect,
why, and what you can do about it, in plain language.</p>

<h2>What we collect</h2>
<ul>
<li>Account details: your email address and a salted, hashed
password (we cannot read your password), plus the public handle you
pick.</li>
<li>Your work: prompts, uploaded images and videos, spoken lines and
music prompts, and the files we generate for you.</li>
<li>Payment records: what you bought and what it cost. Card payments
are processed by Stripe. We never see or store your full card
number.</li>
<li>Basic technical records needed to run jobs and stop abuse,
including a session cookie that keeps you signed in and a bot check
from Cloudflare Turnstile on signup and job creation.</li>
</ul>
<p>We run no advertising trackers and we do not sell personal
data.</p>

<h2>Why we use it</h2>
<p>To run your generations, deliver and store your files, show the
public gallery, take payments, send account email such as
verification links, answer support requests, and keep the free tier
and paid features working as described.</p>

<h2>Who else processes it</h2>
<ul>
<li>The model provider behind the model you pick receives your
prompt and the media needed for that generation.</li>
<li>Stripe processes payments. Resend delivers account email.
Cloudflare provides the bot check. The platform itself runs on
Modal.</li>
<li>Each of them handles your data only to provide its part of the
service.</li>
</ul>

<h2>The gallery</h2>
<p>Finished clips are public by default and show your handle only,
never your email or real name. Paying customers can opt out in the
studio. Deleting a clip removes it from the gallery and from public
serving. Use a clip's Report button if something should not be
there.</p>

<h2>Your choices</h2>
<ul>
<li>Delete your own clips at any time in the studio.</li>
<li>Ask us to delete your account and its data by emailing
<a href="mailto:support@magrify.com">support@magrify.com</a> from
your account email. We will confirm when it is done. Some records,
such as payment receipts, may be kept where the law requires.</li>
<li>Ask what data we hold about you at the same address.</li>
</ul>

<h2>Contact</h2>
<p>Privacy questions: <a href="mailto:support@magrify.com">support@magrify.com</a>
or the <a href="/support">support page</a>.</p>
""",
)

SUPPORT_HTML = _info_page(
    "Moonfactory support",
    "How to reach Moonfactory support, what to include, and how gallery reports and billing questions work.",
    """
<h1 style="font-size:34px">Support</h1>
<p>Email us at <a href="mailto:support@magrify.com">support@magrify.com</a>.
Support answers the same business day, via any channel, and email is
the fastest.</p>

<h2>What to include</h2>
<ul>
<li>The email address on your account.</li>
<li>For a video problem, include the model's name, roughly when you ran it,
and what went wrong. If a generation failed on our side, the balance
is restored in credits. Tell us and we will check the job record.</li>
<li>For a payment question, include the date and amount on your Stripe
receipt. We can see purchases and balances, never your full card
number.</li>
</ul>

<h2>Common answers</h2>
<ul>
<li>The free tier is 5 clips per account, total. It does not refill.
Any payment, a Creator plan or a single top-up, opens the full
studio.</li>
<li>Clips are public in the <a href="/gallery">gallery</a> by
default, under your handle only. Paying customers can opt out in the
studio, and anyone can delete their own clips at any time.</li>
<li>To report a gallery clip, use the Report button on the clip
itself. Reports are reviewed by a person.</li>
<li>To delete your account and its data, email us from your account
email and say so. See the <a href="/privacy">privacy policy</a>.</li>
</ul>
<p>The rules of the service live in the <a href="/terms">terms of
service</a>.</p>
""",
)


# The help center (Jack, 2026-11-17): plain guides, one page per
# topic, contact by email only. No em dashes in this copy.
HELP_PAGES = {
    "getting-started": (
        "Getting started",
        "How Moonfactory works, from sign up and your free clips to your first video.",
        """
<h1 style="font-size:34px">Getting started</h1>
<p>Moonfactory makes short AI videos with sound from a prompt and,
if you want one, a start image. Five things, in order.</p>
<ol>
<li><strong>Start image (optional).</strong> Upload a picture, or
describe one and we make it, or skip it and write text only.</li>
<li><strong>Prompt.</strong> Say what happens in the video.</li>
<li><strong>Model.</strong> Pick how it gets made. Moonfactory's own
Wan 2.2 lane is the free one. The rest open with any payment.</li>
<li><strong>Length.</strong> Pick how long. Every model starts at
its own shortest clip.</li>
<li><strong>Create.</strong> The exact price shows on the button
before anything runs.</li>
</ol>
<h2>Your account</h2>
<p>You need an account, with a verified email, before anything
runs. The free tier is 5 clips per account, total, and it does not
refill. Any payment, the Creator plan or a one-time top-up, opens
the full studio. Every model, longer videos, images, characters,
and the rest of this help center.</p>
<h2>Your library</h2>
<p>Everything you make lands in your library in the studio, ready
to download. Clips are public in the <a href="/gallery">gallery</a>
by default, under your handle only. Paying customers can opt out,
and you can delete your own clips at any time.</p>
<p>Next, read <a href="/help/pricing">how pricing and billing work</a>.</p>
""",
    ),
    "pricing": (
        "Pricing and billing",
        "What Moonfactory costs. The Creator plan, top ups, pricing per second, and refunds.",
        """
<h1 style="font-size:34px">Pricing and billing</h1>
<h2>The short version</h2>
<ul>
<li>The free tier is 5 clips per account, total.</li>
<li>The Creator plan is $20 a month and opens everything, with an
included monthly budget for Moonfactory's own lane.</li>
<li>A top up is a single payment of $1, $5, $10, or $20 added to your
balance. Balance never expires.</li>
<li>Frontier models are priced per second of video. The exact
price for your exact settings shows on the Create button before
anything runs.</li>
</ul>
<h2>How a price is set</h2>
<p>Each model runs on the cheapest route we have verified for it,
and your price is that route's cost plus a small markup. You are
never charged more than the price you saw. If a job fails, the
charge comes back in full. If a job delivers less than it quoted,
you are charged for what was delivered, never more.</p>
<h2>The ceiling</h2>
<p>An account is never served more generation cost than it has
paid in. That rule runs before anything is dispatched, so a job
that would cross it simply asks you to top up first.</p>
<h2>Images and characters</h2>
<p>Images are priced per image, shown on the button the same way.
Training a character is one flat charge, shown before it starts,
and refunded in full if the training does not finish.</p>
<h2>Refunds and receipts</h2>
<p>Payments run through Stripe. We can see purchases and balances,
never your full card number. For a payment question, email
<a href="mailto:support@magrify.com">support@magrify.com</a> with
the date and amount on your Stripe receipt.</p>
""",
    ),
    "lengths": (
        "Lengths and segments",
        "How video lengths work on Moonfactory. Real model lengths, and segments for longer videos.",
        """
<h1 style="font-size:34px">Lengths and segments</h1>
<p>Every model offers every length, starting at its own shortest
clip. Longer than one single clip, your video is made in segments
and joined into one finished video. Longer videos are priced per
second across the segments, exactly like separate clips.</p>
<h2>Why models start at different lengths</h2>
<p>Each model makes clips in its own real lengths, and the picker
shows exactly those. One model might make 4, 6, or 8 second clips
and nothing between. Another makes any whole second from 4 to 30.
Nothing is padded and nothing is trimmed. The length you pick is
the length you get.</p>
<h2>Longer videos</h2>
<p>Past a model's longest single clip, the standard longer lengths
(45, 60, 90, and 120 seconds) are made in segments. Each segment
starts from the last frame of the one before it, so the finished
video plays as one piece. A segment that fails partway still
delivers the finished part, charged for what was made.</p>
<h2>Moonfactory's own lane</h2>
<p>The Wan 2.2 lane built into Moonfactory works in segments of about
5 seconds each, up to 12 segments. Pick the number of segments and the price
follows the total length, the same rule as every other model.</p>
""",
    ),
    "audio": (
        "Audio and lip sync",
        "Music, spoken lines, voiceover, and how lip sync works on Moonfactory.",
        """
<h1 style="font-size:34px">Audio and lip sync</h1>
<h2>Sound on everything</h2>
<p>Every video is delivered with sound. Two fields in More options
shape it.</p>
<ul>
<li><strong>Music prompt.</strong> Describe the music and mood,
and a music bed is made alongside your video.</li>
<li><strong>Spoken line.</strong> The words a voice should say in
the video.</li>
</ul>
<h2>Talking and lip sync</h2>
<p>Some models speak natively. The voice and the picture are made
together, so the mouth already matches. On models that do not
speak, a voiceover is made from your spoken line, and lip sync is
applied automatically so the mouth on screen matches the words.
Lip sync is built in, never an extra. Whenever a job carries
speech, the quote already includes it.</p>
<h2>Dubbing</h2>
<p>The same rule covers dubbing. The voice track is your spoken
line, and the mouth is matched to it. One honest limit. The voice
is a generated voice. Moonfactory does not clone a real person's
voice.</p>
<h2>If audio fails</h2>
<p>Audio problems never block the video. If a voiceover or the lip
sync cannot be made, the video still delivers, the job is marked
so you can see what happened, and support can check the record.</p>
""",
    ),
    "characters": (
        "Characters",
        "Save a character once and reuse it across videos, with optional training.",
        """
<h1 style="font-size:34px">Characters</h1>
<p>A character is a saved identity, a name plus reference images,
saved once in More options and reused on any job.</p>
<h2>Using a character</h2>
<p>Pick the character in More options before you create. Its first
image becomes the video's start image when the job has none, and
its images ride as references on the models that take references,
so the same face shows up from video to video.</p>
<h2>Training</h2>
<p>With 10 reference images saved, you can train the character.
Training is one flat charge, shown on the button before it starts,
and it locks the identity in much harder than references alone.
It runs in the background. The character shows as trained when it
is ready. If training does not finish, the charge is refunded in
full.</p>
<h2>Limits</h2>
<p>Up to 5 characters per account, up to 10 images each. Use
images you have the right to use.</p>
""",
    ),
    "motion-transfer": (
        "Motion transfer",
        "Copy the motion from a reference video onto your subject image.",
        """
<h1 style="font-size:34px">Motion transfer</h1>
<p>Motion transfer takes two things. A subject image, and a short
video whose motion you want to copy. Your subject performs that
video's motion.</p>
<h2>How to run it</h2>
<ol>
<li>Pick the Wan Motion model.</li>
<li>Add your subject as the start image.</li>
<li>In More options, paste the motion video's link and its length
in seconds (2 to 30).</li>
<li>Create. The result follows the motion video's length, and the
price follows that length like any other video.</li>
</ol>
<h2>Tips</h2>
<p>Clear, full-body motion in the reference video copies best.
The subject image should show the whole subject, facing the
camera, in good light.</p>
""",
    ),
    "images": (
        "Images",
        "The image shelf. A few of the best image models, priced per image.",
        """
<h1 style="font-size:34px">Images</h1>
<p>The studio has two modes, Video and Image, at the top of the
composer. Image mode is the same five things. A prompt, a model,
a size, one button with the price on it.</p>
<h2>The shelf</h2>
<p>The shelf is small on purpose. A few of the best image models,
not a wall of them. It carries the current Nano Banana models,
OpenAI's current image model, Moonfactory's own image
maker, and a few uncensored variants, marked with dollar signs
so you can see the price class before you pick.</p>
<h2>Pricing</h2>
<p>Images are priced per image at the size you pick. The charge
lands only when your image is delivered. If it fails, the charge
comes back in full.</p>
<h2>Use an image in a video</h2>
<p>Every image you make is saved in your library. From the result,
one click attaches it as the start image of a new video.</p>
""",
    ),
    "faq": (
        "FAQ",
        "Quick answers about Moonfactory. Free clips, refunds, the gallery, and 4K.",
        """
<h1 style="font-size:34px">FAQ</h1>
<h2>How many free clips do I get?</h2>
<p>5 per account, total. They do not refill. Any payment opens the
full studio.</p>
<h2>What happens if my video fails?</h2>
<p>The charge comes back in full. If a long video fails partway,
the finished segments still deliver and you are charged only for
what was made.</p>
<h2>Is my video public?</h2>
<p>By default, yes, in the gallery, under your handle only.
Paying customers can opt out in the studio, and you can delete
your own clips at any time. Anyone can report a clip from its
gallery page.</p>
<h2>Which models make real 4K?</h2>
<p>Only the ones that list a 4K size, and only where the model
genuinely publishes and delivers it. If a model tops out at 1080p,
its picker says so. Nothing is labeled 4K that is not 4K.</p>
<h2>How long can a video be?</h2>
<p>Up to 120 seconds on most models, made in segments past the
model's longest single clip. See
<a href="/help/lengths">lengths and segments</a>.</p>
<h2>Can I use my videos commercially?</h2>
<p>The terms of service cover what you can do with what you make.
See the <a href="/terms">terms</a>.</p>
<h2>How do I reach a person?</h2>
<p>Email <a href="mailto:support@magrify.com">support@magrify.com</a>.
Support answers the same business day.</p>
""",
    ),
}

HELP_INDEX_HTML = _info_page(
    "Moonfactory help center",
    "Guides and answers for Moonfactory. Getting started, pricing, lengths, audio, characters, motion transfer, and images.",
    """
<h1 style="font-size:34px">Help center</h1>
<p>Plain guides, one page per topic. If you cannot find your
answer, email us. Contact is email only for now, and support
answers the same business day.</p>
<ul>
<li><a href="/help/getting-started">Getting started</a>, accounts,
free clips, and your first video.</li>
<li><a href="/help/pricing">Pricing and billing</a>, plans,
top-ups, per-second pricing, and refunds.</li>
<li><a href="/help/lengths">Lengths and segments</a>, how lengths
work, and how longer videos are joined.</li>
<li><a href="/help/audio">Audio and lip sync</a>, music, spoken
lines, voiceover, and dubbing.</li>
<li><a href="/help/characters">Characters</a>, save an identity
once, reuse it, train it.</li>
<li><a href="/help/motion-transfer">Motion transfer</a>, copy a
video's motion onto your subject.</li>
<li><a href="/help/images">Images</a>, the image shelf and
what images cost.</li>
<li><a href="/help/faq">FAQ</a>, the quick answers.</li>
</ul>
<h2>Contact support</h2>
<p>Email <a href="mailto:support@magrify.com">support@magrify.com</a>,
or use this form and it lands in the same inbox.</p>
<form id="help-contact" style="max-width:520px">
<label>Your email</label>
<input id="hc-email" type="email" maxlength="200" required>
<label>Message</label>
<textarea id="hc-message" rows="5" maxlength="4000" required></textarea>
<input id="hc-website" type="text" style="display:none" tabindex="-1" autocomplete="off">
<p><button class="btn" id="hc-send" type="submit">Send</button></p>
<p id="hc-note"></p>
</form>
<script>
document.getElementById('help-contact').addEventListener('submit', async (e) => {
  e.preventDefault();
  const note = document.getElementById('hc-note');
  note.textContent = 'Sending...';
  try {
    const r = await fetch('/api/support/contact', { method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email: document.getElementById('hc-email').value,
        message: document.getElementById('hc-message').value,
        website: document.getElementById('hc-website').value }) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'send failed');
    note.textContent = 'Sent. Support answers the same business day.';
    document.getElementById('hc-message').value = '';
  } catch (err) { note.textContent = 'Could not send (' + err.message + '). Email support@magrify.com instead.'; }
});
</script>
""",
)


INDEX_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory AI video generation for creators</title>
<meta name="description" content="Turn one image into a short video with sound. Your first 5 clips are free, no card. Creator is $20/mo with an included budget, or pay per clip.">
<meta property="og:title" content="Moonfactory AI video generation for creators">
<meta property="og:description" content="Turn one image into a short video with sound. Your first 5 clips are free, no card.">
<meta property="og:type" content="website">"""
    + _CSS
    + """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0c0c0f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Moonfactory">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Moonfactory",
  "url": "https://magrify--moonfactory-api.us-east.modal.run/",
  "applicationCategory": "MultimediaApplication",
  "operatingSystem": "Web",
  "offers": [
    {
      "@type": "Offer",
      "name": "Free",
      "price": 0,
      "priceCurrency": "USD",
      "description": "Your first 5 clips are free, no card needed."
    },
    {
      "@type": "Offer",
      "name": "Creator",
      "price": 20,
      "priceCurrency": "USD",
      "description": "Creator is $20/mo with an included budget.",
      "priceSpecification": {
        "@type": "UnitPriceSpecification",
        "price": 20,
        "priceCurrency": "USD",
        "unitText": "MON"
      }
    }
  ]
}
</script>
</head><body><main>
__LANDING_TOP__

<div class="strip-label" id="strip-label" style="display:none">Fresh from the community</div>
<div class="strip" id="strip"></div>

<p class="pricing-line">Your first 5 clips are <b>free</b>, no card needed.
<b>Creator</b> is <span id="creator-price">$20</span>/mo with an included
budget. Or <b>pay per clip</b>, with the exact price on the button before
anything runs. No credits and no conversion math.</p>
</main>
<footer><span>Moonfactory</span><span><a href="/gallery">Gallery</a> &nbsp;<a href="/help">Help</a> &nbsp;<a href="/terms">Terms</a> &nbsp;<a href="/privacy">Privacy</a> &nbsp;<a href="/support">Support</a></span></footer>
<script>
document.getElementById('hero-go').onclick = () => {
  const v = document.getElementById('hero-prompt').value.trim();
  location.href = '/app' + (v ? '?prompt=' + encodeURIComponent(v) : '');
};
// The community strip is the public gallery's newest clips, shown
// the same plain way the gallery shows them: clip, handle, nothing
// else.
fetch('/api/gallery').then(r => r.json()).then(d => {
  const items = (d.items || []).slice(0, 4);
  if (!items.length) return;
  const box = document.getElementById('strip');
  for (const item of items) {
    const t = document.createElement('div'); t.className = 'tile';
    const f = document.createElement('div'); f.className = 'frame';
    if (item.kind === 'image') {
      const img = document.createElement('img');
      img.src = item.file_url; img.alt = ''; img.loading = 'lazy';
      f.appendChild(img);
    } else {
      const v = document.createElement('video');
      v.src = item.file_url; v.preload = 'metadata'; v.muted = true;
      if (item.poster_url) v.poster = item.poster_url;
      v.setAttribute('playsinline', '');
      f.appendChild(v);
    }
    const h = document.createElement('div'); h.className = 'handle';
    h.textContent = item.handle;
    t.appendChild(f); t.appendChild(h);
    box.appendChild(t);
  }
  document.getElementById('strip-label').style.display = '';
}).catch(() => {});
// The one pricing line takes its Creator figure from /api/pricing,
// never hand-written.
fetch('/api/pricing').then(r => r.json()).then(p => {
  if (p && p.creator_price_cents) {
    document.getElementById('creator-price').textContent =
      '$' + (p.creator_price_cents / 100).toFixed(0);
  }
}).catch(() => {});
</script>
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
</script>
</body></html>"""
)


APP_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory studio</title>
<meta name="description" content="The Moonfactory studio. Create short AI videos with sound from a single image, or cut long videos into captioned clips.">
<meta property="og:title" content="Moonfactory studio">
<meta property="og:description" content="Create short AI videos with sound from a single image.">
<meta property="og:type" content="website">"""
    + _CSS
    + """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0c0c0f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Moonfactory">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</head><body><main>
<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/gallery">Gallery</a><span class="profile" id="profile" style="display:none"><button class="avatar" id="avatar" type="button" aria-haspopup="true" aria-expanded="false" aria-label="Account menu"></button><span class="menu" id="profile-menu" style="display:none"><span class="menu-email" id="menu-email"></span><a href="#" id="menu-account">Account</a><a href="#" id="menu-billing">Billing</a><a href="/help">Help</a><button type="button" id="menu-logout">Log out</button></span></span></nav>
</header>

<div id="live-status" class="sr-only" role="status" aria-live="polite"></div>
<div id="live-alert" class="sr-only" role="alert" aria-live="assertive"></div>

<div id="auth" class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
  <h2>Sign in or create an account</h2>
  <label for="email">Email</label><input id="email" type="email" autocomplete="email">
  <label for="password">Password (8+ characters)</label><input id="password" type="password" autocomplete="current-password">
  <div id="botcheck"></div>
  <p>
    <button class="btn" id="login">Sign in</button>
    <button class="btn secondary" id="signup">Create account</button>
  </p>
  <p id="auth-msg"></p>
  <p class="muted">New accounts must verify their email before creating
  videos. Check your inbox for the link after you sign up.</p>
</div>

<div id="studio" style="display:none">
  <div class="tabs"><div class="seg">
    <button class="btn" id="tab-tryit">Try It</button>
    <button class="btn secondary" id="tab-create">Create</button>
    <button class="btn secondary" id="tab-clip">Clip</button>
  </div></div>

  <div id="create-pane">
  <div class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
    <div class="create-head">
      <h2 id="composer-heading">Try It</h2>
    </div>
    <p class="muted" id="composer-note"></p>

    <div class="mode-toggle">
      <button class="btn" id="mode-video" type="button">Video</button>
      <button class="btn secondary" id="mode-image" type="button">Image</button>
    </div>

    <div class="drop" id="dropzone" tabindex="0" role="button" aria-label="Add a start image" style="margin-top:14px">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#9c9ca6" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5-9 9"/></svg>
      <div class="big">Add a start image</div>
      <div class="small">Drop it here, or click to browse. Text only works too.</div>
    </div>
    <label class="flabel" for="start-source" style="margin-top:14px">Start image</label>
    <select id="start-source">
      <option value="none" selected>No start image, text only</option>
      <option value="upload">Upload an image</option>
      <option value="generate">Describe it and we make it</option>
    </select>
    <div id="start-upload-wrap" style="display:none">
      <label>Start image file (PNG or JPEG)</label>
      <input id="startimg" type="file" accept="image/png,image/jpeg">
    </div>
    <div id="start-gen-wrap" style="display:none">
      <label>Describe the first frame</label>
      <textarea id="image-prompt" rows="2" maxlength="1000" placeholder="a red hot air balloon over rolling green hills at sunrise, bright daylight"></textarea>
      <button class="btn secondary" id="gen-image" type="button">Make the first frame</button>
      <p class="muted" id="image-note">We make the opening frame at the aspect ratio picked for the video. Check it here, and it attaches to your video with one click. Every frame we make is charged like a small render. The exact price shows before it runs. Not happy with it? Make another.</p>
      <div id="image-preview"></div>
    </div>

    <label class="flabel" for="prompt" style="margin-top:6px">Prompt</label>
    <textarea id="prompt" rows="4" class="composer-prompt" placeholder="Describe what happens… e.g. a red hot air balloon rising over green hills, gentle forward camera movement, bright daylight"></textarea>

    <div id="image-opts" style="display:none">
      <div class="row">
        <div class="grow">
          <label class="flabel" for="image-model">Image model</label>
          <select id="image-model"></select>
        </div>
        <div id="image-size-wrap">
          <label class="flabel" for="image-size">Size</label>
          <select id="image-size"></select>
        </div>
      </div>
      <div id="image-edit-wrap" style="display:none">
        <label class="flabel" for="image-edit-file">Image to edit (PNG or JPEG)</label>
        <input id="image-edit-file" type="file" accept="image/png,image/jpeg">
        <p class="muted">This model edits one image you upload, following the instruction in the prompt. The result comes back at the same size as your image.</p>
        <div id="image-edit-preview"></div>
      </div>
      <p class="muted" id="image-shelf-note"></p>
      <div id="image-result-wrap" style="display:none">
        <img id="image-result" style="max-width:100%;border-radius:12px" alt="Your image">
        <p><button class="btn secondary" id="image-use-start" type="button">Use as the start image for a video</button></p>
      </div>
    </div>

    <div class="row">
      <div class="grow">
        <label class="flabel" for="model">Model</label>
        <select id="model">
          <option value="wan2.2">Wan 2.2</option>
        </select>
      </div>
      <div id="length-house">
        <label class="flabel">Length</label>
        <div class="dur" id="segments-seg"></div>
        <select id="segments" style="display:none">
          <option value="1" selected>1 segment, about 5 seconds</option>
          <option value="2">2 segments, about 10 seconds</option>
          <option value="3">3 segments, about 15 seconds</option>
          <option value="4">4 segments, about 20 seconds</option>
          <option value="6">6 segments, about 30 seconds</option>
          <option value="8">8 segments, about 40 seconds</option>
          <option value="9">9 segments, about 45 seconds</option>
          <option value="12">12 segments, about 60 seconds</option>
        </select>
      </div>
      <div id="length-api" style="display:none">
        <label class="flabel">Length</label>
        <div class="dur" id="duration-seg"></div>
        <select id="duration" style="display:none">
          <option value="5">5 seconds</option>
          <option value="10">10 seconds</option>
          <option value="15">15 seconds</option>
          <option value="20">20 seconds</option>
          <option value="30">30 seconds</option>
        </select>
        <p class="muted">Every model offers every length, starting at its own shortest clip. Longer than one single clip, your video is made in segments and joined into one finished video. Longer videos are priced per second across the segments, exactly like separate clips.</p>
      </div>
    </div>
    <div id="motion-fields" style="display:none">
      <div id="motion-fields-story" style="display:none">
        <label>Story title</label>
        <input id="mf-story-title" type="text" maxlength="90" placeholder="The day the river sang">
        <label>Your story</label>
        <textarea id="mf-story-lines" rows="8" maxlength="1100" placeholder="First line of your story"></textarea>
        <p class="muted">Write one line on each row. Use three to eight lines. Each line gets its own scene in a little animated town, with music made for the story.</p>
      </div>
      <div id="motion-fields-sting" style="display:none">
        <label>Name or words for the sting</label>
        <input id="mf-sting-headline" type="text" maxlength="90" placeholder="Your big words go here">
        <label>Smaller line under it</label>
        <input id="mf-sting-subline" type="text" maxlength="120" placeholder="One short line says the rest">
      </div>
      <label>Accent color</label>
      <input id="mf-color" type="color" value="#6366f1" style="width:90px;height:44px;padding:2px">
      <p class="muted">Free for everyone. Free accounts count it toward their five free clips. Paid accounts can make 20 a day.</p>
    </div>
    <p class="muted" id="model-note"></p>
    <p class="muted" id="segments-note"></p>

    <div id="titlecard-fields" style="display:none">
    <p class="muted">Make a title card, a stat card or a promo card, or put captions over one of your clips. These are free. They run on spare computers, so one can take a few minutes to start.</p>
    <label>Style</label>
    <select id="tpl-template">
      <optgroup label="Titles">
      <option value="title">Title card</option>
      <option value="captions">Captions over a clip</option>
      <option value="stat">Stat card</option>
      <option value="promo">Promo card</option>
      </optgroup>
    </select>
    <div id="tpl-fields-title">
      <label>Headline</label>
      <input id="tpl-title-headline" type="text" maxlength="90" placeholder="Your big words go here">
      <label>Smaller line under it</label>
      <input id="tpl-title-subline" type="text" maxlength="120" placeholder="One short line says the rest">
    </div>
    <div id="tpl-fields-captions" style="display:none">
      <label>Your clip</label>
      <input id="tpl-clip-file" type="file" accept="video/*">
      <label>Words on the clip</label>
      <input id="tpl-caption" type="text" maxlength="120" placeholder="The words that sit on your clip">
    </div>
    <div id="tpl-fields-stat" style="display:none">
      <label>The big number or short stat</label>
      <input id="tpl-stat" type="text" maxlength="20" placeholder="12000">
      <label>What the stat means</label>
      <input id="tpl-stat-label" type="text" maxlength="90" placeholder="happy guests this year">
    </div>
    <div id="tpl-fields-promo" style="display:none">
      <label>Headline</label>
      <input id="tpl-promo-headline" type="text" maxlength="90" placeholder="Your big words go here">
      <label>Smaller line under it</label>
      <input id="tpl-promo-subline" type="text" maxlength="120" placeholder="One short line says the rest">
      <label>Button words at the end</label>
      <input id="tpl-promo-cta" type="text" maxlength="40" placeholder="Book now">
    </div>
    <label>Accent color</label>
    <input id="tpl-color" type="color" value="#6366f1" style="width:90px;height:44px;padding:2px">
    <p class="muted" id="tpl-note">Free titles count toward the five free clips on a free account. Paid accounts can make 20 a day.</p>
    <div id="botcheck4"></div>
    <p id="tpl-msg"></p>
    </div>

    <div id="shorts-fields" style="display:none">
    <p class="muted">Make a short with a voice telling the story over stock footage, with captions and music built in. These are free. They run on our own computers, so one can take a few minutes.</p>
    <div class="mode-toggle">
      <button class="btn" id="short-mode-topic" type="button">Topic</button>
      <button class="btn secondary" id="short-mode-script" type="button">Your script</button>
    </div>
    <div id="short-topic-wrap">
      <label>Topic</label>
      <input id="short-topic" type="text" maxlength="200" placeholder="why the ocean is so deep">
      <p class="muted" id="short-topic-note"></p>
    </div>
    <div id="short-script-wrap" style="display:none">
      <label>Your script</label>
      <textarea id="short-script" rows="6" maxlength="1600" placeholder="One line for each scene. The voice reads it exactly as written."></textarea>
    </div>
    <label>Voice</label>
    <select id="short-voice">
      <option value="af_heart" selected>Heart, American woman</option>
      <option value="af_alloy">Alloy, American woman</option>
      <option value="af_aoede">Aoede, American woman</option>
      <option value="af_bella">Bella, American woman</option>
      <option value="af_jessica">Jessica, American woman</option>
      <option value="af_kore">Kore, American woman</option>
      <option value="af_nicole">Nicole, American woman</option>
      <option value="af_nova">Nova, American woman</option>
      <option value="af_river">River, American woman</option>
      <option value="af_sarah">Sarah, American woman</option>
      <option value="af_sky">Sky, American woman</option>
      <option value="am_adam">Adam, American man</option>
      <option value="am_echo">Echo, American man</option>
      <option value="am_eric">Eric, American man</option>
      <option value="am_fenrir">Fenrir, American man</option>
      <option value="am_liam">Liam, American man</option>
      <option value="am_michael">Michael, American man</option>
      <option value="am_onyx">Onyx, American man</option>
      <option value="am_puck">Puck, American man</option>
      <option value="am_santa">Santa, American man</option>
      <option value="bf_alice">Alice, British woman</option>
      <option value="bf_emma">Emma, British woman</option>
      <option value="bf_isabella">Isabella, British woman</option>
      <option value="bf_lily">Lily, British woman</option>
      <option value="bm_daniel">Daniel, British man</option>
      <option value="bm_fable">Fable, British man</option>
      <option value="bm_george">George, British man</option>
      <option value="bm_lewis">Lewis, British man</option>
    </select>
    <p class="muted" id="short-voice-note"></p>
    <p class="muted" id="short-note">Free shorts count toward the five free clips on a free account. Paid accounts can make 20 a day.</p>
    <div id="botcheck5"></div>
    <p id="short-msg"></p>
    </div>

    <details class="more" id="more-opts">
      <summary>More options</summary>

      <div id="lane-wrap">
        <label>Lane</label>
        <select id="lane">
          <option value="draft">Draft, fast preview, about 5 seconds</option>
          <option value="production">Production, 720x1280, Creator plan or balance</option>
        </select>
        <p class="muted" id="lane-note"></p>
      </div>

      <div id="resolution-wrap" style="display:none">
        <label>Resolution</label>
        <select id="resolution">
          <option value="480p">480 x 854</option>
          <option value="720p" selected>720 x 1280</option>
          <option value="1080p">1080 x 1920</option>
        </select>
      </div>

      <div id="aspect-wrap">
        <label>Aspect ratio</label>
        <select id="aspect">
          <option value="9:16" selected>9:16 vertical</option>
          <option value="16:9">16:9 wide</option>
          <option value="1:1">1:1 square</option>
        </select>
      </div>

      <div id="house-more">
        <label>End image (PNG, optional, Production lane, the video steers to this final frame)</label>
        <input id="endimg" type="file" accept="image/png">
      </div>

      <p class="group-label">Camera and look</p>
      <label>Camera moves (optional, up to 3, applied in the order you pick them)</label>
      <div id="presets"><p class="muted">Loading camera presets...</p></div>
      <p class="muted" id="preset-chain"></p>
      <div id="camspeed-wrap" style="display:none">
        <label>Camera speed</label>
        <select id="camera-speed">
          <option value="0.5">0.5</option>
          <option value="1">1</option>
          <option value="1.5">1.5</option>
          <option value="2">2</option>
          <option value="2.5">2.5</option>
          <option value="3" selected>3</option>
        </select>
      </div>
      <label>Film look (optional)</label>
      <div id="looks"></div>
      <label>Effect (optional, one per video)</label>
      <div id="effects"><p class="muted">Loading effects...</p></div>
      <p class="muted" id="effect-note"></p>

      <div id="character-opts">
      <p class="group-label">Character (optional)</p>
      <label>Saved character</label>
      <select id="character"><option value="">None</option></select>
      <p class="muted" id="character-note"></p>
      <div id="character-new" style="display:none">
        <label>Character name</label>
        <input id="character-name" type="text" maxlength="60" placeholder="e.g. Nova">
        <label>Reference images (up to 10)</label>
        <input id="character-files" type="file" accept="image/png,image/jpeg" multiple>
        <p><button class="btn secondary" id="character-save" type="button">Save character</button></p>
      </div>
      <p>
        <button class="btn secondary" id="character-toggle" type="button">New character</button>
        <button class="btn secondary" id="character-train" type="button" style="display:none">Train this character ($7.50)</button>
      </p>
      </div>

      <div id="motion-opts" style="display:none">
      <p class="group-label">Motion transfer</p>
      <label>Motion video link</label>
      <input id="motion-url" type="url" maxlength="1000" placeholder="https://... a short clip of the motion to copy">
      <label>Motion video length (seconds)</label>
      <input id="motion-seconds" type="number" min="2" max="30" step="1" placeholder="e.g. 8">
      <p class="muted">Your subject image performs this video's motion. The result follows the motion video's length.</p>
      </div>

      <p class="group-label">Sound</p>
      <label>Music prompt (optional)</label>
      <textarea id="music" rows="2" maxlength="300" placeholder="lo-fi piano with soft rain, slow and dreamy"></textarea>
      <p class="muted" id="music-note"></p>
      <label>Spoken line (optional)</label>
      <textarea id="speech" rows="2" maxlength="1000" placeholder="the words a voice should say in this video"></textarea>
      <p class="muted" id="speech-note"></p>
      <label>Voice</label>
      <select id="speech-voice">
        <option value="af_heart" selected>Heart, American woman</option>
        <option value="af_alloy">Alloy, American woman</option>
        <option value="af_aoede">Aoede, American woman</option>
        <option value="af_bella">Bella, American woman</option>
        <option value="af_jessica">Jessica, American woman</option>
        <option value="af_kore">Kore, American woman</option>
        <option value="af_nicole">Nicole, American woman</option>
        <option value="af_nova">Nova, American woman</option>
        <option value="af_river">River, American woman</option>
        <option value="af_sarah">Sarah, American woman</option>
        <option value="af_sky">Sky, American woman</option>
        <option value="am_adam">Adam, American man</option>
        <option value="am_echo">Echo, American man</option>
        <option value="am_eric">Eric, American man</option>
        <option value="am_fenrir">Fenrir, American man</option>
        <option value="am_liam">Liam, American man</option>
        <option value="am_michael">Michael, American man</option>
        <option value="am_onyx">Onyx, American man</option>
        <option value="am_puck">Puck, American man</option>
        <option value="am_santa">Santa, American man</option>
        <option value="bf_alice">Alice, British woman</option>
        <option value="bf_emma">Emma, British woman</option>
        <option value="bf_isabella">Isabella, British woman</option>
        <option value="bf_lily">Lily, British woman</option>
        <option value="bm_daniel">Daniel, British man</option>
        <option value="bm_fable">Fable, British man</option>
        <option value="bm_george">George, British man</option>
        <option value="bm_lewis">Lewis, British man</option>
      </select>
      <p class="muted" id="voice-note"></p>
      <label style="font-weight:400;display:block">
        <input type="checkbox" id="captions" style="width:auto;margin:0 6px 0 0">
        Add captions</label>
      <p class="muted">Captions are written into the video.</p>

      <p class="group-label">Details</p>
      <label>Anything to avoid (optional)</label>
      <input id="negative" type="text" maxlength="500" placeholder="e.g. no text, no extra people">
      <label>Seed (optional)</label>
      <input id="seed" type="number" min="0" step="1" placeholder="Random">
    </details>

    <div id="botcheck2"></div>
    <button class="btn gen" id="create">Create video</button>
    <div id="api-opts" style="display:none"><p class="under" id="quote"></p></div>
    <p class="under" id="house-price"></p>
    <p id="job-msg"></p>
  </div>
  </div>

  <div id="upgrade-pane" style="display:none">
  <div class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
    <div class="create-head">
      <h2>Create</h2>
    </div>
    <div class="paywall">
      <p>Try It is the free plan. Wan 2.2 Draft clips, 5 per account total,
      watermarked. Every model beyond Wan 2.2, the Production lane,
      custom music and voiceover, effects, longer videos in segments,
      clipping, and hiding your clips from the gallery live here,
      locked until an account has paid.</p>
      <p>Any payment opens Create, in one of two ways. Creator, a monthly
      plan with an included generation budget. Or a single top up and
      pay per video, no subscription. Once you have paid, Try It disappears and
      you open straight into Create.</p>
    </div>
    <p>
      <button class="btn" id="upgrade-creator">Get Creator, $20/month</button>
      <button class="btn secondary" id="upgrade-topup">Top up $10</button>
    </p>
  </div>
  </div>

  <div id="clip-pane" style="display:none">
  <div class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
    <div class="create-head">
      <h2>Clip</h2>
    </div>
    <p class="muted">Paste a YouTube, Twitch or Kick link, or upload a file. You get vertical clips with captions and a hook on each one. If a link ever refuses to read, upload the file instead. Uploads always work.</p>
    <label>Video link</label>
    <input id="clip-url" type="url" placeholder="https://www.youtube.com/watch?v=...">
    <label>Or upload a video file</label>
    <input id="clip-file" type="file" accept="video/*">
    <div class="row">
      <div class="grow">
        <label>How many clips</label>
        <select id="clip-count">
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3" selected>3</option>
          <option value="4">4</option>
          <option value="5">5</option>
          <option value="6">6</option>
        </select>
      </div>
      <div class="grow">
        <label>Clip length</label>
        <select id="clip-length">
          <option value="short">Short</option>
          <option value="medium" selected>Medium</option>
          <option value="long">Long</option>
        </select>
      </div>
    </div>
    <div class="row">
      <div class="grow">
        <label>Caption style</label>
        <select id="clip-style">
          <option value="bounce">Bounce</option>
          <option value="hype">Hype</option>
          <option value="clean">Clean</option>
        </select>
      </div>
      <div class="grow">
        <label>Layout</label>
        <select id="clip-layout">
          <option value="blur">Fill with blur</option>
          <option value="zoom">Zoom</option>
          <option value="crop">Crop</option>
        </select>
      </div>
    </div>
    <p id="clip-charge" class="muted"></p>
    <div id="botcheck3"></div>
    <button class="btn gen" id="make-clips">Make clips</button>
    <p id="clip-msg"></p>
    <p class="muted">Clip only content you own or have permission to use. Clipping is a paid feature. Creator plans use included clipping minutes, or you pay 5 cents per source minute from your balance.</p>
  </div>
  <h2 style="max-width:640px;margin:26px auto 6px">Your clips</h2>
  <div id="clip-results" style="max-width:640px;margin:0 auto"><p class="muted">No clips yet. Paste a link above and your clips will show up here.</p></div>
  </div>


  <h2 style="max-width:640px;margin:30px auto 6px">Your videos</h2>
  <div id="jobs" style="max-width:640px;margin:0 auto"><p class="muted">Loading...</p></div>

  <div class="overlay" id="panel-account" style="display:none">
    <div class="panel" role="dialog" aria-label="Account">
      <div class="panel-head"><h2>Account</h2><button type="button" class="panel-close" data-close-panel aria-label="Close">×</button></div>
      <p id="account-email" style="margin:0 0 4px"></p>
      <p id="account" style="margin-top:0"></p>
      <label>Public handle (the only name the gallery ever shows)</label>
      <input id="handle" maxlength="20">
      <button class="btn secondary" id="save-handle">Save handle</button>
      <label style="font-weight:400;display:block;margin-top:12px">
        <input type="checkbox" id="gallery-optout" style="width:auto;margin:0 6px 0 0">
        Hide my clips from the public gallery</label>
      <p class="muted" id="gallery-note"></p>
      <p id="profile-msg"></p>
    </div>
  </div>
  <div class="overlay" id="panel-billing" style="display:none">
    <div class="panel" role="dialog" aria-label="Billing">
      <div class="panel-head"><h2>Billing</h2><button type="button" class="panel-close" data-close-panel aria-label="Close">×</button></div>
      <p class="muted">Paying saves your card and turns on auto reload, so your work never stops over balance. Change the amount or turn it off below at any time.</p>
      <p class="group-label">Monthly plan</p>
      <p>
        <button class="btn" id="buy-creator">Get Creator, $20/month</button>
      </p>
      <p class="muted">Creator opens every model and includes a monthly budget for making videos.</p>
      <p class="group-label">Credits</p>
      <div class="topup-grid">
        <button class="btn secondary" data-topup="100">Top up $1</button>
        <button class="btn secondary" data-topup="500">Top up $5</button>
        <button class="btn secondary" data-topup="1000">Top up $10</button>
        <button class="btn secondary" data-topup="2000">Top up $20</button>
      </div>
      <p class="muted">Top up once and pay per video from your balance. Your balance never expires.</p>
      <p class="group-label">Auto reload</p>
      <p class="muted" id="autoreload-note"></p>
      <p>
        <button class="btn secondary" id="save-card" type="button">Save a card</button>
      </p>
      <div id="autoreload-controls" style="display:none">
        <label>Reload amount</label>
        <select id="autoreload-amount">
          <option value="100">$1</option>
          <option value="500">$5</option>
          <option value="1000" selected>$10</option>
          <option value="2000">$20</option>
        </select>
        <p>
          <button class="btn secondary" id="autoreload-save" type="button">Save auto reload</button>
          <button class="btn secondary" id="autoreload-off" type="button" style="display:none">Turn auto reload off</button>
        </p>
      </div>
      <p id="pay-msg"></p>
      <p class="muted">Any payment unlocks every model, custom music, and hiding your clips from the gallery.</p>
    </div>
  </div>
</div>
</main>
<footer><span>Moonfactory</span><span><a href="/gallery">Gallery</a> &nbsp;<a href="/help">Help</a> &nbsp;<a href="/terms">Terms</a> &nbsp;<a href="/privacy">Privacy</a> &nbsp;<a href="/support">Support</a></span></footer>
<script>
const $ = id => document.getElementById(id);
let cfg = { pow_required: true, pow_difficulty_bits: 18, turnstile_site_key: null };
let startImageB64 = null;
let endImageB64 = null;
let generatedImageJobId = null;

async function solvePow() {
  const r = await fetch('/api/challenge'); const c = await r.json();
  const bits = c.difficulty_bits; let nonce = 0;
  const enc = new TextEncoder();
  while (true) {
    const data = enc.encode(c.challenge + ':' + nonce);
    const h = await crypto.subtle.digest('SHA-256', data);
    const bytes = new Uint8Array(h);
    let ok = true;
    for (let i = 0; i < bits; i++) {
      if ((bytes[i >> 3] >> (7 - (i & 7))) & 1) { ok = false; break; }
    }
    if (ok) return { challenge_id: c.challenge_id, challenge_nonce: String(nonce) };
    nonce++;
    if (nonce % 2000 === 0) await new Promise(res => setTimeout(res, 0));
  }
}
// Turnstile tokens are single-use and live about five minutes.
// The widgets are rendered once per page, so a token handed to one
// submit must never be handed to a second one: siteverify rejects
// the replay as a bot-check failure even though the widget still
// shows "Success!". botFields therefore tracks what it has handed
// out per widget and, when the current token is spent or missing,
// resets the widget and waits for a fresh token before submitting.
const botWidgets = {};
const botHandedOut = {};
async function botFields(containerId) {
  if (cfg.turnstile_site_key) {
    const wid = botWidgets[containerId];
    const readApi = () => {
      if (window.turnstile && wid !== undefined) {
        try { return turnstile.getResponse(wid) || ''; } catch (e) {}
      }
      return '';
    };
    const readDom = () => {
      const el = document.querySelector('#' + containerId + ' input[name="cf-turnstile-response"]');
      return el ? el.value : '';
    };
    let token = readApi() || readDom();
    if (!token || botHandedOut[containerId]) {
      if (window.turnstile && wid !== undefined) {
        try { turnstile.reset(wid); } catch (e) {}
        token = '';
        for (let i = 0; i < 40 && !token; i++) {
          await new Promise(res => setTimeout(res, 150));
          token = readApi();
        }
      }
    }
    if (token) botHandedOut[containerId] = true;
    return { turnstile_token: token };
  }
  return await solvePow();
}
function renderBotChecks() {
  if (cfg.turnstile_site_key && window.turnstile) {
    for (const id of ['botcheck', 'botcheck2', 'botcheck3', 'botcheck5']) {
      if (!document.getElementById(id)) continue;
      botWidgets[id] = turnstile.render('#' + id, {
        sitekey: cfg.turnstile_site_key,
        'expired-callback': () => { try { turnstile.reset(botWidgets[id]); } catch (e) {} },
        'error-callback': () => { try { turnstile.reset(botWidgets[id]); } catch (e) {} },
      });
    }
  }
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || ('HTTP ' + r.status));
  return body;
}
// Screen reader announcements (audit F1, 2026-11-18): every
// status and error surface in the studio writes through msg(),
// so routing msg() through the two live regions announces job,
// upload, and billing feedback without touching each call site.
// Errors go to the assertive region, everything else to the
// polite one. The region is cleared first so a repeated message
// is announced again.
function announce(text, assertive) {
  const el = $(assertive ? 'live-alert' : 'live-status');
  if (!el || !text) return;
  el.textContent = '';
  setTimeout(() => { el.textContent = text; }, 40);
}
function msg(id, text, cls) { const el = $(id); el.textContent = text; el.className = cls || ''; announce(text, cls === 'err'); }

// Start image: one reader path for the file input, the drop zone's
// click, and drag-and-drop, so all three set the same state.
function readStartFile(f) {
  if (!f) { startImageB64 = null; renderDrop(); return; }
  generatedImageJobId = null;
  const reader = new FileReader();
  reader.onload = () => { startImageB64 = String(reader.result).split(',')[1]; renderDrop(f); };
  reader.readAsDataURL(f);
}
function renderDrop(f) {
  const dz = $('dropzone');
  if (startImageB64) {
    dz.classList.add('filled');
    dz.innerHTML = '<img class="thumb" alt="Your start frame">' +
      '<div><div class="fname">' + (f ? f.name : 'start image') + '</div>' +
      '<div class="fmeta">Your start frame</div></div>' +
      '<button type="button" class="remove" aria-label="Remove image">×</button>';
    dz.querySelector('img.thumb').src = 'data:image/png;base64,' + startImageB64;
    dz.querySelector('.remove').addEventListener('click', e => {
      e.stopPropagation();
      startImageB64 = null; $('startimg').value = '';
      if ($('start-source').value === 'upload') { $('start-source').value = 'none'; syncStartSource(); }
      renderDrop();
    });
  } else {
    dz.classList.remove('filled');
    dz.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#9c9ca6" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5-9 9"/></svg>' +
      '<div class="big">Add a start image</div>' +
      '<div class="small">Drop it here, or click to browse. Text only works too.</div>';
  }
}
$('startimg').addEventListener('change', e => { readStartFile(e.target.files[0]); });
$('dropzone').addEventListener('click', () => {
  if (startImageB64) return;
  if ($('start-source').value !== 'upload') { $('start-source').value = 'upload'; syncStartSource(); }
  $('startimg').click();
});
$('dropzone').addEventListener('dragover', e => { e.preventDefault(); $('dropzone').classList.add('over'); });
$('dropzone').addEventListener('dragleave', () => $('dropzone').classList.remove('over'));
$('dropzone').addEventListener('drop', e => {
  e.preventDefault(); $('dropzone').classList.remove('over');
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  if ($('start-source').value !== 'upload') { $('start-source').value = 'upload'; syncStartSource(); }
  readStartFile(f);
});
// Start image source choice: upload your own, go text-only, or
// describe the first frame and have it made. A made frame attaches
// to the video job by its job id, resolved on the server.
function syncStartSource() {
  const src = $('start-source').value;
  $('start-upload-wrap').style.display = src === 'upload' ? 'block' : 'none';
  $('start-gen-wrap').style.display = src === 'generate' ? 'block' : 'none';
  if (src !== 'upload') { startImageB64 = null; renderDrop(); }
  if (src !== 'generate') generatedImageJobId = null;
}
$('start-source').addEventListener('change', syncStartSource);
$('gen-image').onclick = async () => {
  try {
    const prompt = $('image-prompt').value.trim();
    if (!prompt) { msg('image-note', 'Describe the first frame first.', 'err'); return; }
    msg('image-note', 'Checking the price...');
    const q = await api('/api/images/quote', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ prompt: prompt, aspect_ratio: $('aspect').value }) });
    const p = q.payment || {};
    const priceText = p.method === 'free' ? 'it counts as one of your free clips'
      : p.method === 'included' ? 'it debits $' + ((p.included_debit_cents || 0) / 100).toFixed(2) + ' of your included budget'
      : 'it costs $' + ((q.price_cents || 0) / 100).toFixed(2) + ' from your prepaid balance';
    msg('image-note', 'Making your first frame, ' + priceText + '...');
    const bot = await botFields('botcheck2');
    const r = await api('/api/images', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ prompt: prompt, aspect_ratio: $('aspect').value, ...bot }) });
    const imageJobId = r.id;
    const poll = async () => {
      const list = await api('/api/jobs');
      const j = list.jobs.find(x => x.id === imageJobId);
      if (!j) return;
      if (j.status === 'done') {
        generatedImageJobId = imageJobId;
        $('image-preview').innerHTML =
          '<img src="/api/jobs/' + imageJobId + '/file" style="width:180px;border-radius:14px" alt="Your first frame">' +
          '<p class="muted">This frame is attached as your start image. Create your video when you are ready, or make another frame first.</p>';
        msg('image-note', 'Your first frame is ready and attached.', 'ok');
        loadJobs();
      } else if (j.status === 'error') {
        msg('image-note', j.error || 'The first frame could not be made, nothing was charged.', 'err');
        loadJobs();
      } else {
        setTimeout(poll, 5000);
      }
    };
    setTimeout(poll, 3000);
  } catch (e) { msg('image-note', e.message, 'err'); }
};
$('endimg').addEventListener('change', e => {
  const f = e.target.files[0]; if (!f) { endImageB64 = null; return; }
  const reader = new FileReader();
  reader.onload = () => { endImageB64 = String(reader.result).split(',')[1]; };
  reader.readAsDataURL(f);
});

// Camera presets: the picker is rendered from the library data at
// /api/camera-presets, never hand-built per preset. Selected moves
// keep their pick order; the server composes them into the prompt
// in that order (up to the library's max).
let presetData = null;
let selectedPresets = [];
const LOOK_LABELS = { tempo: 'Tempo', genre: 'Genre', era: 'Era',
  camera_body: 'Camera', lens: 'Lens', focal: 'Focal length',
  aperture: 'Aperture', lighting: 'Lighting', palette: 'Color' };
async function loadPresets() {
  try {
    presetData = await api('/api/camera-presets');
    const box = $('presets'); box.innerHTML = '';
    for (const p of presetData.presets) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'chip';
      b.dataset.pid = p.id; b.dataset.name = p.name;
      b.title = p.description || '';
      if (!p.available) {
        b.disabled = true;
        b.textContent = p.name + ' (' + (p.unavailable_note || 'coming soon') + ')';
      } else {
        b.textContent = p.name;
        b.onclick = () => togglePreset(p.id);
      }
      box.appendChild(b);
    }
    const lb = $('looks'); lb.innerHTML = '';
    for (const slot of Object.keys(presetData.looks)) {
      const wrap = document.createElement('label');
      wrap.appendChild(document.createTextNode(LOOK_LABELS[slot] || slot));
      const sel = document.createElement('select');
      sel.id = 'look-' + slot;
      const any = document.createElement('option');
      any.value = ''; any.textContent = 'Any';
      sel.appendChild(any);
      for (const opt of presetData.looks[slot]) {
        const o = document.createElement('option');
        o.value = opt; o.textContent = opt;
        sel.appendChild(o);
      }
      wrap.appendChild(sel);
      lb.appendChild(wrap);
    }
    renderChain();
  } catch (e) {
    $('presets').innerHTML = '<p class="muted">Camera presets could not load. Your prompt still works on its own.</p>';
  }
}
function togglePreset(pid) {
  const i = selectedPresets.indexOf(pid);
  if (i >= 0) { selectedPresets.splice(i, 1); }
  else {
    const max = presetData ? presetData.max_moves : 3;
    if (selectedPresets.length >= max) {
      msg('preset-chain', 'Up to ' + max + ' moves, applied in the order you pick them.', 'err');
      return;
    }
    selectedPresets.push(pid);
  }
  renderChain();
}
function selectedFunPreset() {
  if (!presetData) return null;
  for (const p of presetData.presets) {
    if (p.mechanism === 'workflow_patch' && selectedPresets.indexOf(p.id) >= 0) return p;
  }
  return null;
}
function renderChain() {
  const names = {};
  if (presetData) for (const p of presetData.presets) names[p.id] = p.name;
  document.querySelectorAll('#presets .chip').forEach(b => {
    if (b.disabled) return;
    const i = selectedPresets.indexOf(b.dataset.pid);
    b.classList.toggle('on', i >= 0);
    b.textContent = (i >= 0 ? (i + 1) + '. ' : '') + b.dataset.name;
  });
  $('preset-chain').textContent = selectedPresets.length
    ? selectedPresets.map(id => names[id] || id).join(' \u2192 ')
    : 'No camera move picked. The camera follows your prompt.';
  $('preset-chain').className = 'muted';
  // Camera speed applies to Fun Camera presets only: the control
  // shows when one is picked, and only then is a speed sent.
  $('camspeed-wrap').style.display = selectedFunPreset() ? '' : 'none';
}
// Effects: the picker is rendered from /api/effects, never
// hand-built per effect. One effect per video; picking the active
// chip again clears it. An effect acts on a start image, so the
// note under the picker says so, and the server refuses an effect
// job without one.
let effectData = null;
let selectedEffect = null;
async function loadEffects() {
  try {
    effectData = await api('/api/effects');
    const box = $('effects'); box.innerHTML = '';
    for (const fx of effectData.effects) {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'chip';
      b.dataset.fxid = fx.id; b.dataset.name = fx.name;
      b.textContent = fx.name;
      b.onclick = () => toggleEffect(fx.id);
      box.appendChild(b);
    }
    renderEffects();
    applyEffectGate();
  } catch (e) {
    $('effects').innerHTML = '<p class="muted">Effects could not load. Your video still works without one.</p>';
  }
}
function toggleEffect(fxid) {
  selectedEffect = selectedEffect === fxid ? null : fxid;
  renderEffects();
  updateHousePrice();
}
function applyEffectGate() {
  const paid = !!(currentUser && currentUser.paid_customer);
  document.querySelectorAll('#effects .chip').forEach(b => { b.disabled = !paid; });
  if (!paid) {
    $('effect-note').textContent = 'Effects unlock with any payment. Accounts that have never paid render on Wan 2.2 Draft.';
  } else {
    renderEffects();
  }
}
function renderEffects() {
  document.querySelectorAll('#effects .chip').forEach(b => {
    const on = b.dataset.fxid === selectedEffect;
    b.classList.toggle('on', on);
    b.textContent = (on ? '✓ ' : '') + b.dataset.name;
  });
  if (!currentUser || !currentUser.paid_customer) return;
  $('effect-note').textContent = selectedEffect
    ? 'This effect acts on your start image. Pick one in the Start image section above. Your prompt above is who or what is in the photo. Effects price as a Production segment and render at 480p, delivered with the usual music bed.'
    : 'Pick one effect and it transforms your start image into a short clip. Effects are a paid feature and price as a Production segment.';
}
function selectedLook() {
  const look = {};
  if (presetData) for (const slot of Object.keys(presetData.looks)) {
    const el = $('look-' + slot);
    if (el && el.value) look[slot] = el.value;
  }
  return look;
}

$('signup').onclick = async () => {
  try {
    // One password field serves both forms, so each button
    // sets the autocomplete its own flow needs (audit F3).
    $('password').autocomplete = 'new-password';
    msg('auth-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck');
    const r = await api('/api/signup', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email: $('email').value, password: $('password').value, ...bot }) });
    msg('auth-msg', 'Account created. Open the verification link in your email, then sign in.', 'ok');
  } catch (e) { msg('auth-msg', e.message, 'err'); }
};
$('login').onclick = async () => {
  try {
    $('password').autocomplete = 'current-password';
    const r = await api('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email: $('email').value, password: $('password').value }) });
    showStudio(r.user);
  } catch (e) { msg('auth-msg', e.message, 'err'); }
};

// Profile menu: the header avatar opens the account menu. Account
// and Billing each open their own panel in place. The account
// settings (public handle, gallery opt-out) live in the Account
// panel; the payment controls live in the Billing panel.
function closeProfileMenu() {
  $('profile-menu').style.display = 'none';
  $('avatar').setAttribute('aria-expanded', 'false');
}
$('avatar').onclick = e => {
  e.stopPropagation();
  const open = $('profile-menu').style.display !== 'none';
  $('profile-menu').style.display = open ? 'none' : 'flex';
  $('avatar').setAttribute('aria-expanded', open ? 'false' : 'true');
};
document.addEventListener('click', e => {
  if ($('profile-menu').style.display !== 'none' && !$('profile').contains(e.target)) closeProfileMenu();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeProfileMenu(); closePanels(); } });
function openPanel(id) {
  closePanels();
  $(id).style.display = 'flex';
}
function closePanels() {
  $('panel-account').style.display = 'none';
  $('panel-billing').style.display = 'none';
}
$('menu-account').onclick = e => {
  e.preventDefault(); closeProfileMenu(); openPanel('panel-account');
};
$('menu-billing').onclick = e => {
  e.preventDefault(); closeProfileMenu(); openPanel('panel-billing');
};
document.querySelectorAll('[data-close-panel]').forEach(b => {
  b.onclick = () => closePanels();
});
for (const pid of ['panel-account', 'panel-billing']) {
  $(pid).addEventListener('click', e => { if (e.target === $(pid)) closePanels(); });
}
$('menu-logout').onclick = async () => { await api('/api/logout', { method: 'POST' }); location.reload(); };

let currentUser = null;
// The access model: a paid account's surface is Create (Try It is
// gone from its navigation); a free account's surface is Try It,
// and Create is an upgrade prompt. The server reports the surface
// on /api/me; paid_customer is the same fact under its older name.
function isPaidSurface() {
  if (!currentUser) return false;
  return currentUser.access_surface
    ? currentUser.access_surface === 'create'
    : !!currentUser.paid_customer;
}
// The Generate button carries the exact price, Direction C style:
// the button is the quote. The line under it keeps the detail.
function genLabel(priceCents) {
  const b = $('create');
  if (!isPaidSurface()) { b.textContent = 'Make a free clip'; return; }
  if (priceCents === null || priceCents === undefined) { b.textContent = 'Generate'; return; }
  b.textContent = 'Generate · $' + (priceCents / 100).toFixed(2);
}
function showStudio(u) {
  currentUser = u;
  $('auth').style.display = 'none'; $('studio').style.display = 'block';
  $('profile').style.display = 'inline-block';
  $('avatar').textContent = (u.email || '?').trim().charAt(0).toUpperCase();
  $('menu-email').textContent = u.email;
  $('account-email').textContent = u.email;
  renderAutoReload(u);
  const paidSurface = isPaidSurface();
  $('tab-tryit').style.display = paidSurface ? 'none' : '';
  $('composer-heading').textContent = paidSurface ? 'Create' : 'Try It';
  $('composer-note').textContent = paidSurface
    ? 'The full studio. Every model, custom sound, longer clips.'
    : 'Try It is the free plan. Wan 2.2, Draft lane, watermarked. Create, the full studio, opens with any payment.';
  genLabel(null);
  const inc = u.plan === 'creator'
    ? ('Included production time, ' + Math.max(0, u.included_seconds_total - u.included_seconds_used).toFixed(0) + 's left of ' + u.included_seconds_total + 's. ')
    : (u.paid_customer
      ? 'Pay per video. You pay per clip from your balance, and every paid feature is unlocked. '
      : ('Free clips, ' + u.free_jobs_used + ' of ' + u.free_jobs_limit + ' used in total. The free plan never refills. '));
  $('account').textContent = 'Your plan is ' + u.plan + ' · ' + (paidSurface ? 'Create' : 'Try It') + '. ' + inc +
    'Your prepaid balance is $' + (u.balance_cents / 100).toFixed(2) + '.' +
    (u.verified ? '' : ' Your email is not verified yet, so creating is off.');
  $('handle').value = u.handle || '';
  const opt = $('gallery-optout');
  const prodOption = document.querySelector('#lane option[value="production"]');
  if (u.paid_customer) {
    opt.disabled = false; opt.checked = !!u.gallery_opt_out;
    $('gallery-note').textContent = '';
    prodOption.disabled = false;
    $('lane-note').textContent = '';
    $('music').disabled = false;
    $('music-note').textContent = 'Describe the music and it is generated for this video.';
    $('speech').disabled = false;
    $('speech-note').textContent = 'On models that talk, the model performs the line. On the rest, a voice speaks it over the video.';
    $('speech-voice').disabled = false;
    applyEffectGate();
  } else {
    opt.disabled = true; opt.checked = false;
    $('gallery-note').textContent = 'Clips from accounts that have never paid always show in the gallery. Hiding them unlocks with any payment.';
    prodOption.disabled = true;
    $('lane-note').textContent = '';
    if ([...$('model').options].some(o => o.value === 'wan2.2')) $('model').value = 'wan2.2';
    $('model-note').textContent = 'Model switching unlocks with any payment. Accounts that have never paid render on Wan 2.2 Draft.';
    $('music').disabled = true; $('music').value = '';
    $('music-note').textContent = 'Custom audio unlocks with any payment. Free clips get the house music track.';
    $('speech').disabled = true; $('speech').value = '';
    $('speech-note').textContent = 'Voiceover unlocks with any payment.';
    $('speech-voice').disabled = true;
    selectedEffect = null;
    applyEffectGate();
  }
  applyModelGates();
  const segSel = $('segments');
  if (u.paid_customer) {
    segSel.disabled = false;
    $('segments-note').textContent = 'Longer videos are priced per segment, exactly like separate clips, and the segments join into one finished video.';
  } else {
    segSel.disabled = true; segSel.value = '1';
    $('segments-note').textContent = 'Videos with more than one segment unlock with any payment. Free accounts make single Draft clips.';
  }
  buildSegmentsSeg();
  updateModelUI();
  showTab(paidSurface ? 'create' : 'tryit');
  loadJobs();
  loadCharacters();
  loadShortsConfig();
}
// Quote requests carry a sequence number: only the newest one
// may touch the price. Before this, two overlapping quotes
// could resolve out of order and the slower response for the
// previous model overwrote the new model's price on the
// Generate button; and a failed quote cleared the quote line
// but left the previous model's price sitting on the button.
let quoteSeq = 0;
async function fetchQuote() {
  const q = $('quote');
  if (imageMode) return;
  if (isMotionTemplate($('model').value)) { q.textContent = ''; return; }
  if ($('model').value === TITLE_CARDS_ID) { q.textContent = ''; return; }
  if ($('model').value === SHORTS_ID) { q.textContent = ''; return; }
  if (!currentUser || !currentUser.paid_customer) { q.textContent = ''; return; }
  const seq = ++quoteSeq;
  const want = { model: $('model').value,
    duration_seconds: Number($('duration').value), resolution: $('resolution').value,
    speech_script: ($('speech') && !$('speech').disabled) ? $('speech').value : '' };
  if (want.model === 'wan_motion') {
    want.reference_video_urls = [$('motion-url').value.trim()];
    want.reference_video_seconds = Number($('motion-seconds').value) || want.duration_seconds;
    want.start_image_png_b64 = startImageB64;
  }
  // The old price comes off the button immediately: while the
  // new quote is in flight the button never shows a price that
  // belongs to another model, length, or resolution.
  genLabel(null);
  q.textContent = 'Checking the exact price...';
  try {
    const r = await api('/api/jobs/quote', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(want) });
    if (seq !== quoteSeq) return;
    if ($('model').value !== want.model
        || Number($('duration').value) !== want.duration_seconds
        || $('resolution').value !== want.resolution) return;
    if (r.lane_paused) { q.textContent = 'This model is temporarily unavailable.'; genLabel(null); return; }
    if (!r.ceiling_ok) { q.textContent = 'The exact price is $' + (r.balance_debit_cents / 100).toFixed(2) + '. This job would exceed the total generation your payments cover.'; genLabel(r.balance_debit_cents); return; }
    const p = r.payment || {};
    if (p.method === 'included') {
      q.textContent = 'The exact price is $' + (p.included_debit_cents / 100).toFixed(2) +
        ' of your included budget, with $' + (p.included_budget_remaining_cents / 100).toFixed(2) + ' left. Nothing is charged to your balance.';
      genLabel(p.included_debit_cents);
    } else if (p.method === 'balance') {
      q.textContent = 'The exact price is $' + (p.charge_cents / 100).toFixed(2) + ' from your prepaid balance.';
      genLabel(p.charge_cents);
    } else {
      q.textContent = 'The exact price is $' + ((p.charge_cents || 0) / 100).toFixed(2) +
        '. Your included budget is used up and your balance is $' + ((p.balance_cents || 0) / 100).toFixed(2) + ', so top up to run this.';
      genLabel(p.charge_cents || null);
    }
  } catch (e) {
    if (seq !== quoteSeq) return;
    // A failed quote must not leave the previous model's price
    // on the button: clear both, the next change re-quotes.
    q.textContent = '';
    genLabel(null);
  }
}
// The length menu (Jack, 2026-11-17, no-trim rule): every
// model offers every length starting at its own shortest clip;
// nothing is ever trimmed. Inside the model's native range the
// length is one native generation; past its longest clip the
// video is made in segments that add up to exactly the ordered
// length and joined into one finished video. The picker is fed
// each model's real offered lengths from /api/config (computed
// by duration_plan in this file's Python); this list is the
// full menu and the fallback.
const LENGTHS = [1, 5, 10, 15, 30, 45, 60, 90, 120];
const LEN_LABEL = { 1: '1s', 5: '5s', 10: '10s', 15: '15s', 30: '30s', 45: '45s', 60: '1m', 90: '1.5m', 120: '2m' };
const LEN_WORDS = { 1: '1 second', 5: '5 seconds', 10: '10 seconds', 15: '15 seconds', 30: '30 seconds', 45: '45 seconds', 60: '1 minute', 90: '1.5 minutes', 120: '2 minutes' };
// Per-model resolution choices, mirroring what the server
// accepts for each model (it validates again on quote and
// create, so this only keeps the form honest).
const MODEL_OPTS = {
  wan3: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  wan3_prime: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  seedance: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  veo3: { durations: LENGTHS, resolutions: ['720p', '1080p', '4K'] },
  veo3_fast: { durations: LENGTHS, resolutions: ['720p', '1080p', '4K'] },
  kling3: { durations: LENGTHS, resolutions: ['720p', '1080p', '4K'] },
  kling4: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  kling3_omni: { durations: LENGTHS, resolutions: ['720p', '1080p', '4K'] },
  seedance2: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p', '4K'] },
  seedance2_fast: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  seedance2_mini: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  veo3_lite: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  omni12: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  omni11: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  hailuo23: { durations: LENGTHS, resolutions: ['720p'] },
  minimax_h3: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  grok: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  grok15: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  grok15_lite: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  luma_ray32: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  ltx25: { durations: LENGTHS, resolutions: ['720p', '1080p', '4K'] },
  ltx25_fast: { durations: LENGTHS, resolutions: ['720p', '1080p', '4K'] },
  happyhorse: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  pika22: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  vidu_q2: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  vidu_q3: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  vidu_q4: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  seedance25_flash: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  pixverse_v6: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  runway_gen45: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  wan22_unc: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  wan3_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  minimax_h3_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  seedance25_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  vidu_q3_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  wan26_unc: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  wan27_unc: { durations: LENGTHS, resolutions: ['720p', '1080p'] },
  wan3pro_unc: { durations: LENGTHS, resolutions: ['1080p', '2K', '4K'] },
  wan3prime_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  wan22_lora_unc: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  seedance2_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  seedance2fast_unc: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  seedance2mini_unc: { durations: LENGTHS, resolutions: ['480p', '720p'] },
  seedance15pro_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  ltx23_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  ltx23_lora_unc: { durations: LENGTHS, resolutions: ['480p', '720p', '1080p'] },
  wan_motion: { durations: LENGTHS, resolutions: ['720p'] }
};
// Resolution picker labels (Jack, 2026-11-17): real pixel
// dimensions, never bare 720p / 1080p / 4K labels ("nobody knows
// what K means"). Each pair is [long side, short side] at the
// platform's own rasters (the same sizes the CometAPI and
// Runway payload maps in app.py use; 4K is 3840x2160 per the
// providers' own pages, 2K follows the same convention at
// 2560x1440). Vertical swaps the pair; square takes the short
// side twice. Labels follow the picked aspect live.
const RES_DIMS = {
  '480p': [854, 480], '720p': [1280, 720], '1080p': [1920, 1080],
  '2K': [2560, 1440], '4K': [3840, 2160]
};
function resLabel(v) {
  const d = RES_DIMS[v];
  if (!d) return v;
  const a = ($('aspect') && $('aspect').value) || '9:16';
  if (a === '1:1') return d[1] + ' x ' + d[1];
  if (a === '16:9') return d[0] + ' x ' + d[1];
  return d[1] + ' x ' + d[0];
}
function fillSelect(sel, values, label) {
  const keep = sel.value;
  sel.innerHTML = '';
  for (const v of values) {
    const o = document.createElement('option');
    o.value = v; o.textContent = label(v);
    sel.appendChild(o);
  }
  if (values.map(String).includes(keep)) sel.value = keep;
}
// Segmented length controls (Direction C): a pill row that drives
// the hidden select the rest of the form already reads.
function buildSegButtons(box, sel, values, labelFn, onPick) {
  box.innerHTML = '';
  for (const v of values) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = labelFn(v); b.dataset.val = v;
    if (String(v) === String(sel.value)) b.className = 'on';
    b.onclick = () => {
      if (sel.disabled) return;
      sel.value = v;
      box.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      if (onPick) onPick();
    };
    box.appendChild(b);
  }
  box.classList.toggle('off', !!sel.disabled);
}
function buildSegmentsSeg() {
  buildSegButtons($('segments-seg'), $('segments'), [1, 2, 3, 4, 6, 8, 9, 12],
    v => (v * 5) + 's', updateHousePrice);
}
// The model dropdown is fed from /api/config at runtime (names,
// order, availability), never a hand-written subset: the config is
// the shelf. Picking a model drives the same hidden state the form
// has always read; /api/pricing supplies the lengths and sizes.
let catalogCfg = null;
let pricingData = null;
// House models run on our own worker with the house form
// (segments and a start image), never the per-second form. The
// list mirrors HOUSE_MODEL_PRICING_LANE in the platform code.
const HOUSE_MODEL_IDS = ['wan2.2', 'wan22_uncut'];
function isHouseModel(id) { return HOUSE_MODEL_IDS.includes(id); }
// Motion templates ride the same picker as the models (Jack,
// 2026-11-18: one Create, no separate wing). They are not
// generation models: picking one swaps the composer to the
// template fields and the Create button posts to the free
// template lane, which renders on the same spare computers as
// the title cards. The names match the template names the
// server reports, and the template lane checks pin that.
const MOTION_TEMPLATES = [
  { id: 'story', name: 'Animated story short' },
  { id: 'sting', name: 'Logo sting' }
];
function isMotionTemplate(id) { return MOTION_TEMPLATES.some(t => t.id === id); }
function moneyC(c) { return '$' + (c / 100).toFixed(2); }
function buildCatalog() {
  const sel = $('model');
  if (!sel || !catalogCfg) return;
  const keep = sel.value || 'wan2.2';
  sel.innerHTML = '';
  for (const m of catalogCfg.models) {
    const o = document.createElement('option');
    o.value = m.id;
    o.textContent = m.label
      + (m.price_tier ? ' ' + '$'.repeat(m.price_tier) : '')
      + (m.uncensored ? ' · Uncensored' : '')
      + (m.available ? '' : ' (unavailable)');
    sel.appendChild(o);
  }
  const motionGroup = document.createElement('optgroup');
  motionGroup.label = 'Motion';
  for (const t of MOTION_TEMPLATES) {
    const o = document.createElement('option');
    o.value = t.id;
    o.textContent = t.name;
    motionGroup.appendChild(o);
  }
  sel.appendChild(motionGroup);
  // Title cards (the folded Titles lane) ride the same picker as
  // one more entry, marked free because the lane never charges.
  // Picking it swaps the composer to the template form; the jobs
  // still go out through /api/templates exactly as before.
  const tc = document.createElement('option');
  tc.value = TITLE_CARDS_ID;
  tc.textContent = 'Title cards · Free';
  sel.appendChild(tc);
  // Faceless shorts (the folded Shorts lane) ride the picker the
  // same way: one more free entry whose pick swaps the composer
  // to the shorts form; jobs go out through /api/shorts.
  const sh = document.createElement('option');
  sh.value = SHORTS_ID;
  sh.textContent = 'Faceless shorts · Free';
  sel.appendChild(sh);
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  applyModelGates();
  updateModelUI();
}
function applyModelGates() {
  const sel = $('model');
  const paid = !!(currentUser && currentUser.paid_customer);
  // Motion templates, title cards, and faceless shorts stay open
  // to everyone: they are free, and on a free account they count
  // toward the five free clips, exactly as the title cards always
  // have.
  [...sel.options].forEach(o => { if (o.value !== 'wan2.2' && o.value !== TITLE_CARDS_ID && o.value !== SHORTS_ID && !isMotionTemplate(o.value)) o.disabled = !paid; });
  if (!paid && sel.value !== 'wan2.2' && sel.value !== TITLE_CARDS_ID && sel.value !== SHORTS_ID && !isMotionTemplate(sel.value) && [...sel.options].some(o => o.value === 'wan2.2')) sel.value = 'wan2.2';
}
// The price of the house-lane job being composed, from the served
// pricing data, shown under the Create button before anything runs.
function updateHousePrice() {
  const el = $('house-price');
  if (!el) return;
  if (!pricingData || !currentUser || !isHouseModel($('model').value)) { el.textContent = ''; return; }
  const jp = pricingData.job_price_cents || {};
  if (!currentUser.paid_customer) {
    el.textContent = 'Free plan. A Draft video uses one of your free clips.';
    genLabel(null);
    return;
  }
  if (selectedEffect) {
    el.textContent = 'It costs ' + moneyC(jp.effects || 0) +
      ' from your balance for this effect video. Creator members pay from their included budget instead.';
    genLabel(jp.effects || null);
    return;
  }
  const segs = Number($('segments').value) || 1;
  const per = $('model').value === 'wan22_uncut' ? (jp.nsfw || 0)
    : $('lane').value === 'production' ? (jp.production || 0) : (jp.draft || 0);
  el.textContent = 'It costs ' + moneyC(per * segs) + ' from your balance for ' +
    segs + (segs === 1 ? ' segment' : ' segments') + ' at ' + moneyC(per) +
    ' per segment. Creator members pay from their included budget instead.';
  genLabel(per * segs);
}
// Motion picks swap the whole composer: the generation controls
// (start image, prompt, lengths, more options) step aside and
// the template fields for the pick show instead. Switching back
// restores them, and updateModelUI then repaints the model
// specific blocks itself, as it always has.
function applyMotionUI() {
  const m = $('model').value;
  const motion = isMotionTemplate(m) ? m : null;
  $('motion-fields').style.display = motion ? '' : 'none';
  const bits = [$('dropzone'), $('start-source'),
    document.querySelector('label[for="start-source"]'),
    document.querySelector('label[for="prompt"]'), $('prompt'),
    $('more-opts'), $('segments-note')];
  for (const el of bits) if (el) el.style.display = motion ? 'none' : '';
  if (motion) {
    $('motion-fields-story').style.display = m === 'story' ? '' : 'none';
    $('motion-fields-sting').style.display = m === 'sting' ? '' : 'none';
    $('start-upload-wrap').style.display = 'none';
    $('start-gen-wrap').style.display = 'none';
  } else {
    $('start-upload-wrap').style.display = $('start-source').value === 'upload' ? '' : 'none';
    $('start-gen-wrap').style.display = $('start-source').value === 'generate' ? '' : 'none';
  }
  return motion;
}
// Title cards are the folded Titles lane: one entry in the
// model picker, not a model the catalog serves. Picking it swaps
// the composer to the template form.
const TITLE_CARDS_ID = 'title_cards';
// Faceless shorts are the folded Shorts lane, same shape: one
// picker entry, not a catalog model.
const SHORTS_ID = 'faceless_shorts';
function showTitleCardForm() {
  $('titlecard-fields').style.display = '';
  $('shorts-fields').style.display = 'none';
  $('motion-fields').style.display = 'none';
  const hide = [$('dropzone'), document.querySelector('label[for="start-source"]'),
    $('start-source'), document.querySelector('label[for="prompt"]'), $('prompt'),
    $('length-house'), $('length-api'), document.querySelector('details.more'),
    $('api-opts'), $('house-price')];
  for (const el of hide) if (el) el.style.display = 'none';
  $('segments-note').textContent = '';
  $('model-note').textContent = 'Title cards are free. They count toward the five free clips on a free account, and paid accounts can make 20 a day.';
  $('quote').textContent = '';
  $('create').textContent = 'Make it for free';
  applyModelGates();
}
// Faceless shorts swap the composer the same way: the shorts
// form shows, the video fields step aside, and the shared Create
// button becomes the free lane's button.
function showShortsForm() {
  $('shorts-fields').style.display = '';
  $('titlecard-fields').style.display = 'none';
  $('motion-fields').style.display = 'none';
  const hide = [$('dropzone'), document.querySelector('label[for="start-source"]'),
    $('start-source'), document.querySelector('label[for="prompt"]'), $('prompt'),
    $('length-house'), $('length-api'), document.querySelector('details.more'),
    $('api-opts'), $('house-price')];
  for (const el of hide) if (el) el.style.display = 'none';
  $('segments-note').textContent = '';
  $('model-note').textContent = 'Faceless shorts are free. They count toward the five free clips on a free account, and paid accounts can make 20 a day.';
  $('quote').textContent = '';
  $('create').textContent = 'Make it for free';
  applyModelGates();
}
function updateModelUI() {
  const m = $('model').value;
  if (m === TITLE_CARDS_ID) { showTitleCardForm(); return; }
  if (m === SHORTS_ID) { showShortsForm(); return; }
  $('titlecard-fields').style.display = 'none';
  $('shorts-fields').style.display = 'none';
  if (!imageMode) {
    // Restore the video fields the title card form hid. The
    // length and price blocks are managed by the body below.
    for (const el of [$('dropzone'), document.querySelector('label[for="start-source"]'),
      $('start-source'), document.querySelector('label[for="prompt"]'), $('prompt'),
      document.querySelector('details.more'), $('house-price')]) if (el) el.style.display = '';
    syncStartSource();
  }
  const motion = applyMotionUI();
  if (motion) {
    for (const id of ['length-api', 'length-house', 'lane-wrap', 'house-more', 'resolution-wrap', 'api-opts', 'motion-opts']) {
      $(id).style.display = 'none';
    }
    $('model-note').textContent = motion === 'story'
      ? 'A little animated town tells your story, with music made for it. It renders free, so it can take a while to start.'
      : 'Your words open the show inside a ring of light. It renders free, so it can take a while to start.';
    $('house-price').textContent = '';
    genLabel(null);
    applyModelGates();
    return;
  }
  const api = !isHouseModel(m);
  $('length-api').style.display = api ? '' : 'none';
  $('length-house').style.display = api ? 'none' : '';
  $('lane-wrap').style.display = api ? 'none' : '';
  // The Draft and Production lane choice belongs to Wan 2.2
  // alone. The Uncut lane is its own lane, so the choice hides.
  if (m !== 'wan2.2') $('lane-wrap').style.display = 'none';
  $('house-more').style.display = api ? 'none' : '';
  $('resolution-wrap').style.display = api ? '' : 'none';
  $('api-opts').style.display = api ? '' : 'none';
  $('motion-opts').style.display = m === 'wan_motion' ? '' : 'none';
  $('length-api').style.display = (api && m !== 'wan_motion') ? '' : 'none';
  if (api) {
    const entry = ((catalogCfg && catalogCfg.models) || []).find(x => x.id === m);
    if (m === 'wan_motion') {
      $('model-note').textContent = 'Motion transfer. Add a subject image above, then paste a motion video link in More options. The result follows the motion video\u2019s length.';
    } else if (entry && entry.ref_caps) {
      const c = entry.ref_caps;
      $('model-note').textContent = 'You can add up to ' + c.images + ' images, ' + c.videos + ' videos, and ' + c.audios + ' audio files as references.';
    }
  }
  if (api) {
    const opts = MODEL_OPTS[m];
    if (opts) {
      const entry = ((catalogCfg && catalogCfg.models) || []).find(x => x.id === m);
      const durs = (entry && entry.lengths && entry.lengths.length) ? entry.lengths : opts.durations;
      fillSelect($('duration'), durs, v => LEN_WORDS[v] || (v + ' seconds'));
      buildSegButtons($('duration-seg'), $('duration'), durs, v => LEN_LABEL[v] || (v + 's'), fetchQuote);
      const lane = pricingData ? (pricingData.api_lanes || {})[m] : null;
      const res = lane ? Object.keys(lane.balance_debit_cents_per_second) : opts.resolutions;
      fillSelect($('resolution'), res.length ? res : opts.resolutions, v => resLabel(v));
    }
    fetchQuote();
  }
  // Voice note (voice picker, 2026-11-18): on a speech native
  // lane the model performs the line with its own voice, and
  // the picked voice governs the platform voiceover stage on
  // every other lane. Say which one applies for this model.
  const vEntry = ((catalogCfg && catalogCfg.models) || []).find(x => x.id === m);
  const nativeSpeech = !!(vEntry && vEntry.audio && vEntry.audio.native_speech);
  $('voice-note').textContent = nativeSpeech
    ? 'This model talks with its own voice. Your voice pick applies when Moonfactory speaks the line.'
    : 'Moonfactory speaks your line with the voice you pick.';
  applyModelGates();
  updateHousePrice();
}
$('model').addEventListener('change', updateModelUI);
$('duration').addEventListener('change', fetchQuote);
$('resolution').addEventListener('change', fetchQuote);
$('aspect').addEventListener('change', () => {
  // Resolution labels carry the picked aspect's dimensions;
  // relabel in place, keeping the current pick.
  [...$('resolution').options].forEach(o => { o.textContent = resLabel(o.value); });
});
$('speech').addEventListener('change', fetchQuote);
$('motion-url').addEventListener('change', fetchQuote);
$('motion-seconds').addEventListener('change', fetchQuote);
$('lane').addEventListener('change', updateHousePrice);
$('segments').addEventListener('change', updateHousePrice);
$('save-handle').onclick = async () => {
  try {
    const r = await api('/api/me/handle', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ handle: $('handle').value }) });
    msg('profile-msg', 'Your handle is saved as ' + r.handle, 'ok');
  } catch (e) { msg('profile-msg', e.message, 'err'); }
};
$('gallery-optout').addEventListener('change', async () => {
  try {
    const r = await api('/api/me/gallery-optout', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ opt_out: $('gallery-optout').checked }) });
    msg('profile-msg', r.gallery_opt_out ? 'Your clips are hidden from the gallery.' : 'Your clips show in the gallery.', 'ok');
  } catch (e) { msg('profile-msg', e.message, 'err'); $('gallery-optout').checked = false; }
});
async function refreshMe() {
  try { const u = await api('/api/me'); showStudio(u); } catch (e) { /* signed out */ }
}
$('buy-creator').onclick = async () => {
  try { const r = await api('/api/checkout/subscription', { method: 'POST' }); location.href = r.url; }
  catch (e) { msg('pay-msg', e.message, 'err'); }
};
function renderAutoReload(s) {
  if (!$('save-card')) return;
  const has = !!s.has_saved_card;
  $('save-card').style.display = has ? 'none' : '';
  $('autoreload-controls').style.display = has ? '' : 'none';
  if (s.auto_reload_amount_cents) $('autoreload-amount').value = String(s.auto_reload_amount_cents);
  $('autoreload-off').style.display = s.auto_reload_enabled ? '' : 'none';
  $('autoreload-note').textContent = !has
    ? 'Save a card and you can turn on auto reload.'
    : s.auto_reload_enabled
      ? 'Auto reload is on. If your balance cannot cover a job, your saved card is charged once for the amount below and the job goes ahead.'
      : 'Auto reload is off. Pick an amount and save to turn it on.';
}
$('save-card').onclick = async () => {
  try { const r = await api('/api/billing/setup-card', { method: 'POST' }); location.href = r.url; }
  catch (e) { msg('pay-msg', e.message, 'err'); }
};
$('autoreload-save').onclick = async () => {
  try {
    const r = await api('/api/billing/autoreload', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled: true, amount_cents: Number($('autoreload-amount').value) }) });
    renderAutoReload(r);
    msg('pay-msg', 'Auto reload is on.', 'ok');
  } catch (e) { msg('pay-msg', e.message, 'err'); }
};
$('autoreload-off').onclick = async () => {
  try {
    const r = await api('/api/billing/autoreload', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled: false }) });
    renderAutoReload(r);
    msg('pay-msg', 'Auto reload is off.', 'ok');
  } catch (e) { msg('pay-msg', e.message, 'err'); }
};
document.querySelectorAll('[data-topup]').forEach(b => b.onclick = async () => {
  try {
    const r = await api('/api/checkout/topup', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ pack_cents: Number(b.dataset.topup) }) });
    location.href = r.url;
  } catch (e) { msg('pay-msg', e.message, 'err'); }
});
$('create').onclick = async () => {
  try {
    if (imageMode) { await makeImage(); return; }
    if ($('model').value === TITLE_CARDS_ID) { await makeTitleCard(); return; }
    if ($('model').value === SHORTS_ID) { await makeShort(); return; }
    msg('job-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck2');
    const model = $('model').value;
    if (isMotionTemplate(model)) {
      // A motion pick is a template job, not a generation: same
      // free template lane, same queue and library as everything
      // else, at zero credits.
      const payload = { template: model, color: $('mf-color').value, ...bot };
      if (model === 'story') {
        payload.title = $('mf-story-title').value;
        payload.lines = $('mf-story-lines').value;
      } else {
        payload.headline = $('mf-sting-headline').value;
        payload.subline = $('mf-sting-subline').value;
      }
      await api('/api/templates', { method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload) });
      msg('job-msg', 'Started. Free renders take a few minutes to start, and yours shows up in your videos below as soon as it is done.', 'ok');
      loadJobs();
      return;
    }
    const camera = { camera_presets: selectedPresets, camera_look: selectedLook() };
    if (selectedFunPreset()) camera.camera_speed = Number($('camera-speed').value);
    const startFields = $('start-source').value === 'generate' && generatedImageJobId
      ? { start_image_job_id: generatedImageJobId }
      : { start_image_png_b64: startImageB64 };
    const neg = $('negative').value.trim();
    const seedRaw = $('seed').value.trim();
    const seedNum = seedRaw === '' ? null : Number(seedRaw);
    const detailFields = {
      ...(neg ? { negative_prompt: neg } : {}),
      ...(seedNum !== null && Number.isInteger(seedNum) ? { seed: seedNum } : {})
    };
    const motionFields = model === 'wan_motion'
      ? { reference_video_urls: [$('motion-url').value.trim()],
          reference_video_seconds: Number($('motion-seconds').value) || Number($('duration').value) }
      : {};
    const charFields = $('character').value
      ? { character_id: $('character').value } : {};
    const jobBody = isHouseModel(model)
      ? { model: model, lane: selectedEffect ? 'effects' : $('lane').value, prompt: $('prompt').value,
          music_prompt: $('music').disabled ? '' : $('music').value,
          speech_script: $('speech').disabled ? '' : $('speech').value,
          speech_voice: $('speech').disabled ? '' : $('speech-voice').value,
          captions: $('captions').checked,
          effect: selectedEffect || undefined,
          ...startFields, end_image_png_b64: selectedEffect ? null : endImageB64,
          aspect_ratio: $('aspect').value, segments: selectedEffect ? 1 : Number($('segments').value),
          ...(selectedEffect ? {} : camera), ...detailFields, ...charFields, ...bot }
      : { model: model, prompt: $('prompt').value,
          music_prompt: $('music').disabled ? '' : $('music').value,
          speech_script: $('speech').disabled ? '' : $('speech').value,
          speech_voice: $('speech').disabled ? '' : $('speech-voice').value,
          captions: $('captions').checked,
          aspect_ratio: $('aspect').value,
          duration_seconds: Number($('duration').value), resolution: $('resolution').value,
          ...startFields, ...camera, ...detailFields, ...motionFields, ...charFields, ...bot };
    const r = await api('/api/jobs', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(jobBody) });
    msg('job-msg', isHouseModel(model)
      ? 'Job queued. Draft renders take roughly 6 to 13 minutes. Production takes about 25.'
      : 'Job queued. This model usually finishes in a few minutes.', 'ok');
    loadJobs();
  } catch (e) { msg('job-msg', e.message, 'err'); }
};
function costLine(j) {
  if (j.kind === 'clip_output' || j.status === 'error') return '';
  const bal = j.charged_cents || 0;
  const inc = j.included_cost_cents || 0;
  if (bal > 0) return '<br><span class="muted">It cost $' + (bal / 100).toFixed(2) + ' from your balance</span>';
  if (inc > 0) return '<br><span class="muted">It cost $' + (inc / 100).toFixed(2) + ' of your included budget</span>';
  if (j.free_tier) return '<br><span class="muted">Free clip, nothing charged</span>';
  return '';
}
// Job state announcements (audit F1, 2026-11-18): the poll
// loop below is the only place job states change, so it diffs
// each job against the last status it saw and announces the
// moves a sighted user watches happen. The first list after
// sign in only primes the map, so history is never read out.
// A job first seen already finished (a clip output, a fast
// image) still announces, because it landed while the user
// waited.
const jobStatusSeen = {};
let jobsPrimed = false;
function announceJobStates(jobs) {
  for (const j of jobs) {
    const prev = jobStatusSeen[j.id];
    jobStatusSeen[j.id] = j.status;
    if (!jobsPrimed || prev === j.status) continue;
    const name = j.kind === 'clip_output' ? (j.hook || 'Clip') : (j.model || 'Video');
    if (j.status === 'done') announce(name + ' is ready.');
    else if (j.status === 'error') announce(name + ' failed. ' + (j.error || ''), true);
    else if (j.status === 'running' && prev !== undefined) announce(name + ' is rendering.');
  }
  jobsPrimed = true;
}
async function loadJobs() {
  try {
    const r = await api('/api/jobs');
    const box = $('jobs');
    if (!r.jobs.length) { box.innerHTML = '<p class="muted">No videos yet.</p>'; return; }
    box.innerHTML = '';
    let active = false;
    for (const j of r.jobs) {
      const div = document.createElement('div'); div.className = 'card job';
      const isClipOut = j.kind === 'clip_output';
      const isClipJob = j.kind === 'clip_job';
      const title = isClipOut ? (j.hook || 'Clip') : ((j.model || 'Wan 2.2') + (j.model_id === 'wan2.2' ? ' · ' + j.lane : ''));
      const spec = j.duration_seconds ? ' · ' + j.duration_seconds + 's' + (j.resolution ? ' · ' + j.resolution : '') : '';
      let html = '<strong>' + title + '</strong>' + spec + ' · ' + j.status + '<br><span class="muted">' +
        j.prompt.slice(0, 120) + '</span>';
      html += costLine(j);
      if (j.aspect_ratio && j.model_id === 'wan2.2') html += '<br><span class="muted">Shape ' + j.aspect_ratio + '</span>';
      if (j.segments_total > 1) html += '<br><span class="muted">Segment ' + Math.min(j.segments_done + (j.status === 'done' ? 0 : 1), j.segments_total) + ' of ' + j.segments_total + (j.status === 'done' ? ' joined into one video' : '') + '</span>';
      if ((j.status === 'queued' || j.status === 'running') && j.queue_position) html += '<br><span class="muted">In line at #' + j.queue_position + ' of your running videos</span>';
      if (j.partial) html += '<br><span class="muted">Finished shorter than planned. The later segments could not render, and you were charged only for the segments in this file.</span>';
      if (isClipOut && j.score !== null && j.score !== undefined) html += '<br><span class="muted">Score ' + j.score + '</span>';
      if (isClipOut && j.has_thumb) html += '<br><img class="thumb" src="/api/jobs/' + j.id + '/thumb" alt="">';
      if ((isClipJob || j.kind === 'template' || j.kind === 'short') && j.stage && (j.status === 'queued' || j.status === 'running')) html += '<br><span class="muted">' + j.stage + '</span>';
      if (j.camera_presets && j.camera_presets.length) html +=
        '<br><span class="muted">Camera moves ' + j.camera_presets.join(' \u2192 ') + '</span>';
      if (j.effect) html +=
        '<br><span class="muted">Effect ' + j.effect + '</span>';
      if (j.camera_look && Object.keys(j.camera_look).length) html +=
        '<br><span class="muted">Look ' + Object.values(j.camera_look).join(' · ') + '</span>';
      if (j.has_custom_audio) html +=
        '<br><span class="muted">Music made for this video from its music prompt</span>';
      if (j.status === 'done' && j.kind === 'image') html += '<br><img class="thumb" src="/api/jobs/' + j.id + '/file" alt="">';
      if (j.status === 'done' && j.kind !== 'image') html += '<br><video controls src="/api/jobs/' + j.id + '/file"></video>' +
        '<br><a href="/api/jobs/' + j.id + '/file" download>Download</a>';
      if (j.status === 'error') html += '<br><span class="err">' + (j.error || 'failed') + '</span>';
      if (j.status === 'done' || j.status === 'error') html +=
        '<br><button class="btn secondary" data-del="' + j.id + '">Delete</button>';
      if (j.status === 'queued' || j.status === 'running') active = true;
      div.innerHTML = html; box.appendChild(div);
    }
    if (active) setTimeout(loadJobs, 10000);
    announceJobStates(r.jobs);
    renderClipResults(r.jobs);
  } catch (e) { /* signed out */ }
}
function renderClipResults(jobs) {
  const box = document.getElementById('clip-results');
  if (!box) return;
  const clips = jobs.filter(j => j.kind === 'clip_output');
  const parents = jobs.filter(j => j.kind === 'clip_job' && (j.status === 'queued' || j.status === 'running'));
  if (!clips.length && !parents.length) {
    box.innerHTML = '<p class="muted">No clips yet. Paste a link above and your clips will show up here.</p>';
    return;
  }
  box.innerHTML = '';
  for (const j of parents) {
    const d = document.createElement('div'); d.className = 'card';
    d.innerHTML = '<strong>Clipping</strong> · ' + (j.stage || j.status);
    box.appendChild(d);
  }
  for (const j of clips) {
    const d = document.createElement('div'); d.className = 'card';
    let h = '<strong>' + (j.hook || 'Clip') + '</strong> · ' + (j.duration_seconds || '') + 's';
    if (j.score !== null && j.score !== undefined) h += ' · Score ' + j.score;
    if (j.has_thumb) h += '<br><img class="thumb" src="/api/jobs/' + j.id + '/thumb" alt="">';
    h += '<br><video controls src="/api/jobs/' + j.id + '/file"></video>' +
      '<br><a href="/api/jobs/' + j.id + '/file" download>Download</a>';
    d.innerHTML = h; box.appendChild(d);
  }
}
$('jobs').addEventListener('click', async e => {
  const b = e.target.closest('[data-del]');
  if (!b) return;
  if (!confirm('Delete this video? It is removed from your studio and from the gallery.')) return;
  try { await api('/api/jobs/' + b.dataset.del, { method: 'DELETE' }); loadJobs(); }
  catch (err) { alert(err.message); }
});

// ---- Clip tab ----
let clipUploadId = null;
let clipQuote = null;
function showTab(which) {
  // Try It always opens the composer (for a free account it is the
  // Draft-only Try It composer; the tab itself is hidden once an
  // account has paid). Create and Clip are the paid studio: a paid
  // account opens them directly, a free account gets the upgrade
  // prompt in their place.
  const paidSurface = isPaidSurface();
  const pane = which === 'tryit' ? 'create'
    : which === 'clip' ? (paidSurface ? 'clip' : 'upgrade')
    : (paidSurface ? 'create' : 'upgrade');
  $('create-pane').style.display = pane === 'create' ? 'block' : 'none';
  $('clip-pane').style.display = pane === 'clip' ? 'block' : 'none';
  $('upgrade-pane').style.display = pane === 'upgrade' ? 'block' : 'none';
  $('tab-tryit').className = which === 'tryit' ? 'btn' : 'btn secondary';
  $('tab-create').className = which === 'create' ? 'btn' : 'btn secondary';
  $('tab-clip').className = which === 'clip' ? 'btn' : 'btn secondary';
}
$('tab-tryit').onclick = () => showTab('tryit');
$('tab-create').onclick = () => showTab('create');
$('tab-clip').onclick = () => showTab('clip');
$('upgrade-creator').onclick = () => $('buy-creator').click();
$('upgrade-topup').onclick = () => {
  const b = document.querySelector('[data-topup="1000"]');
  if (b) b.click();
};
function clipChargeLine(q) {
  if (!q) { $('clip-charge').textContent = ''; return; }
  clipQuote = q;
  const m = q.source_minutes;
  if (q.method === 'included') {
    $('clip-charge').textContent = 'This video is ' + m + ' minutes. It uses ' + m + ' of your included clipping minutes.';
  } else {
    $('clip-charge').textContent = 'This video is ' + m + ' minutes. Clipping costs $' + (q.balance_charge_cents / 100).toFixed(2) + ' from your balance.';
  }
}
$('clip-url').addEventListener('change', async () => {
  const url = $('clip-url').value.trim();
  if (!url) return;
  clipUploadId = null;
  try {
    msg('clip-msg', 'Reading that link...');
    const q = await api('/api/clips/quote', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ url }) });
    clipChargeLine(q);
    msg('clip-msg', q.title ? 'Found it. ' + q.title : '', '');
  } catch (e) { msg('clip-msg', e.message, 'err'); $('clip-charge').textContent = ''; }
});
$('clip-file').addEventListener('change', async () => {
  const f = $('clip-file').files[0];
  if (!f) return;
  try {
    msg('clip-msg', 'Uploading your file...');
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch('/api/clips/upload', { method: 'POST', body: fd });
    const q = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(q.detail || ('HTTP ' + r.status));
    clipUploadId = q.upload_id;
    clipChargeLine(q);
    msg('clip-msg', 'File ready.', 'ok');
  } catch (e) { msg('clip-msg', e.message, 'err'); }
});
$('make-clips').onclick = async () => {
  try {
    msg('clip-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck3');
    const payload = { clip_count: Number($('clip-count').value), length: $('clip-length').value,
      caption_style: $('clip-style').value, layout: $('clip-layout').value, ...bot };
    if (clipUploadId) payload.upload_id = clipUploadId;
    else if ($('clip-url').value.trim()) payload.url = $('clip-url').value.trim();
    else { msg('clip-msg', 'Paste a link or upload a file first.', 'err'); return; }
    const r = await api('/api/clips', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload) });
    msg('clip-msg', 'Clipping started. First we get the video, then we listen and transcribe it, pick the moments, and cut your clips.', 'ok');
    loadJobs();
  } catch (e) { msg('clip-msg', e.message, 'err'); }
};

// ---- Title cards (the free template lane, folded into Create) ----
// The form lives in the Create card and shows when the Title
// cards entry is picked in the model picker. Submission goes
// through /api/templates, the same route the old Titles tab used,
// so the job kind, the Kaggle dispatch, and the pricing rules are
// the ones the lane has always had.
let tplUploadId = null;
function tplShowFields() {
  const t = $('tpl-template').value;
  for (const name of ['title', 'captions', 'stat', 'promo']) {
    $('tpl-fields-' + name).style.display = name === t ? '' : 'none';
  }
}
$('tpl-template').addEventListener('change', tplShowFields);
$('tpl-clip-file').addEventListener('change', async () => {
  const f = $('tpl-clip-file').files[0];
  if (!f) return;
  try {
    msg('tpl-msg', 'Uploading your clip...');
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch('/api/templates/upload', { method: 'POST', body: fd });
    const q = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(q.detail || ('HTTP ' + r.status));
    tplUploadId = q.upload_id;
    msg('tpl-msg', 'Clip ready. It runs ' + q.duration_seconds + ' seconds.', 'ok');
  } catch (e) { msg('tpl-msg', e.message, 'err'); }
});
async function makeTitleCard() {
  try {
    const t = $('tpl-template').value;
    const payload = { template: t, color: $('tpl-color').value };
    if (t === 'title') {
      payload.headline = $('tpl-title-headline').value;
      payload.subline = $('tpl-title-subline').value;
    } else if (t === 'captions') {
      if (!tplUploadId) { msg('tpl-msg', 'Upload your clip first.', 'err'); return; }
      payload.caption = $('tpl-caption').value;
      payload.upload_id = tplUploadId;
    } else if (t === 'stat') {
      payload.stat = $('tpl-stat').value;
      payload.label = $('tpl-stat-label').value;
    } else if (t === 'promo') {
      payload.headline = $('tpl-promo-headline').value;
      payload.subline = $('tpl-promo-subline').value;
      payload.cta = $('tpl-promo-cta').value;
    }
    msg('tpl-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck4');
    Object.assign(payload, bot);
    await api('/api/templates', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload) });
    msg('tpl-msg', 'Started. Free renders take a few minutes to start, and yours shows up in your videos below as soon as it is done.', 'ok');
    loadJobs();
  } catch (e) { msg('tpl-msg', e.message, 'err'); }
}
// ---- Shorts (free faceless shorts lane, a Create picker entry) ----
let shortMode = 'topic';
let shortCfg = null;
function shortShowMode() {
  $('short-topic-wrap').style.display = shortMode === 'topic' ? '' : 'none';
  $('short-script-wrap').style.display = shortMode === 'script' ? '' : 'none';
  $('short-mode-topic').className = shortMode === 'topic' ? 'btn' : 'btn secondary';
  $('short-mode-script').className = shortMode === 'script' ? 'btn secondary' : 'btn';
}
$('short-mode-topic').onclick = () => { shortMode = 'topic'; shortShowMode(); };
$('short-mode-script').onclick = () => { shortMode = 'script'; shortShowMode(); };
async function loadShortsConfig() {
  try {
    const r = await api('/api/shorts/config');
    shortCfg = r;
    if (!r.topic_available) {
      // Topic writing is dark until a text route is configured:
      // the form opens on the script box, which always works.
      shortMode = 'script';
      $('short-mode-topic').style.display = 'none';
    }
    if (!r.paid) {
      $('short-voice').disabled = true;
      $('short-voice-note').textContent = 'The free short uses the standard voice. Picking a voice is a paid feature.';
    } else {
      $('short-voice-note').textContent = 'The voice reads your script over the footage.';
    }
    shortShowMode();
  } catch (e) { /* signed out: the form keeps its default shape */ }
}
async function makeShort() {
  try {
    const payload = { voice: $('short-voice').value };
    if (shortMode === 'topic') {
      payload.topic = $('short-topic').value;
      if (!payload.topic.trim()) { msg('short-msg', 'Give the short a topic first.', 'err'); return; }
    } else {
      payload.script = $('short-script').value;
      if (!payload.script.trim()) { msg('short-msg', 'Paste your script first.', 'err'); return; }
    }
    msg('short-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck5');
    Object.assign(payload, bot);
    await api('/api/shorts', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload) });
    msg('short-msg', 'Started. Your short shows up in your videos below as soon as it is done.', 'ok');
    loadJobs();
  } catch (e) { msg('short-msg', e.message, 'err'); }
}
// Characters: saved once, reused across jobs. The picker lists
// the account's characters; the create form rides along in More
// options so the studio stays one calm card.
let characterData = [];
function renderCharacters() {
  const sel = $('character');
  const keep = sel.value;
  sel.innerHTML = '<option value="">None</option>';
  for (const c of characterData) {
    const o = document.createElement('option');
    o.value = c.id;
    o.textContent = c.name + ' (' + c.image_count + ' image' + (c.image_count === 1 ? '' : 's') + ')';
    sel.appendChild(o);
  }
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  updateCharacterUI();
}
function updateCharacterUI() {
  const c = characterData.find(x => x.id === $('character').value);
  const note = $('character-note');
  const train = $('character-train');
  train.style.display = 'none';
  if (!c) { note.textContent = 'Pick a saved character and it appears in this video. Its first image becomes the start image, and its images ride as references on models that take them.'; return; }
  if (c.train_status === 'training') note.textContent = c.name + ' is training. Training takes a while. The trained identity appears here when it is ready.';
  else if (c.train_status === 'ready' || c.has_lora) note.textContent = c.name + ' is trained. Its identity is locked in and reused on every job.';
  else if (c.train_status === 'failed') note.textContent = 'Training did not finish for ' + c.name + ' and the charge was refunded. You can train again.';
  else note.textContent = c.name + ' is saved and ready to use.';
  if (c.image_count >= 10 && c.train_status !== 'training' && !c.has_lora) train.style.display = '';
  else if (c.image_count < 10 && !c.has_lora) note.textContent += ' Add ' + (10 - c.image_count) + ' more images (10 total) to unlock training, which locks the identity in much harder.';
}
async function loadCharacters() {
  try { const r = await api('/api/characters'); characterData = r.characters || []; renderCharacters(); }
  catch (e) { /* signed out or free account: the picker stays empty */ }
}
$('character').addEventListener('change', updateCharacterUI);
$('character-toggle').onclick = () => {
  const box = $('character-new');
  box.style.display = box.style.display === 'none' ? '' : 'none';
};
$('character-save').onclick = async () => {
  try {
    const files = [...$('character-files').files].slice(0, 10);
    if (!files.length) { msg('job-msg', 'Pick at least one reference image for the character.', 'err'); return; }
    const images = [];
    for (const f of files) {
      const dataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
      images.push(String(dataUrl).split(',')[1]);
    }
    const c = await api('/api/characters', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: $('character-name').value, images }) });
    msg('job-msg', 'Your character ' + c.name + ' is saved.', 'ok');
    $('character-new').style.display = 'none';
    await loadCharacters();
    $('character').value = c.id; updateCharacterUI();
  } catch (e) { msg('job-msg', e.message, 'err'); }
};
$('character-train').onclick = async () => {
  try {
    const c = await api('/api/characters/' + $('character').value + '/train', { method: 'POST' });
    msg('job-msg', 'Training started for ' + c.name + '. It runs in the background.', 'ok');
    loadCharacters();
  } catch (e) { msg('job-msg', e.message, 'err'); }
};
// Image mode: a small curated shelf alongside video, with the
// same simplicity: a prompt, a model, a size, one button.
let imageMode = false;
let shelfImageJobId = null;
let editSourceDataUrl = null;
function imageLane() {
  return (cfg.image_models || []).find(m => m.id === $('image-model').value);
}
function buildImageModels() {
  const sel = $('image-model');
  const keep = sel.value;
  sel.innerHTML = '';
  for (const m of (cfg.image_models || [])) {
    const o = document.createElement('option');
    o.value = m.id; o.textContent = m.label; sel.appendChild(o);
  }
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  buildImageSizes();
}
function buildImageSizes() {
  const lane = imageLane();
  const sizes = lane ? lane.sizes : [];
  $('image-size-wrap').style.display = sizes.length ? '' : 'none';
  const sel = $('image-size');
  sel.innerHTML = '';
  for (const s of sizes) {
    const o = document.createElement('option');
    o.value = s;
    o.textContent = (lane.size_labels && lane.size_labels[s]) || s;
    sel.appendChild(o);
  }
  const isEdit = !!(lane && lane.edit);
  $('image-edit-wrap').style.display = isEdit ? '' : 'none';
  if (imageMode) $('prompt').placeholder = isEdit
    ? 'Describe the edit… e.g. change the jacket to black leather, keep the face, the pose and the room exactly the same'
    : 'Describe the image… e.g. a lighthouse at dusk, waves crashing, dramatic sky';
  refreshImageQuote();
}
async function refreshImageQuote() {
  if (!imageMode) return;
  const lane = imageLane();
  if (!lane) return;
  const btn = $('create');
  const body = lane.id === 'house'
    ? { prompt: $('prompt').value || 'image', aspect_ratio: $('image-size').value }
    : { prompt: $('prompt').value || 'image', image_model: lane.id, image_size: $('image-size').value };
  try {
    const q = await api('/api/images/quote', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body) });
    btn.textContent = 'Make image · $' + (q.price_cents / 100).toFixed(2);
    $('image-shelf-note').textContent = lane.id === 'house'
      ? 'Made by Moonfactory itself, at the size picked.'
      : lane.edit
        ? 'One edit of the image you uploaded, at its own size, charged only when it is delivered. If it fails, the charge comes back in full.'
        : 'One image at the size picked, charged only when it is delivered. If it fails, the charge comes back in full.';
  } catch (e) {
    btn.textContent = 'Make image';
    $('image-shelf-note').textContent = e.message;
  }
}
function setImageMode(on) {
  imageMode = on;
  if (on) { $('titlecard-fields').style.display = 'none'; $('shorts-fields').style.display = 'none'; }
  $('mode-video').className = 'btn' + (on ? ' secondary' : '');
  $('mode-image').className = 'btn' + (on ? '' : ' secondary');
  $('image-opts').style.display = on ? '' : 'none';
  const videoBits = [$('dropzone'), $('start-source'), $('start-upload-wrap'), $('start-gen-wrap'),
    document.querySelector('label[for="start-source"]'),
    $('model').closest('.row'), $('model-note'), document.querySelector('details.more'),
    $('api-opts'), $('house-price'), $('segments-note'), $('motion-fields')];
  for (const el of videoBits) if (el) el.style.display = on ? 'none' : '';
  $('prompt').placeholder = on
    ? 'Describe the image… e.g. a lighthouse at dusk, waves crashing, dramatic sky'
    : 'Describe what happens… e.g. a red hot air balloon rising over green hills, gentle forward camera movement, bright daylight';
  if (on) { buildImageModels(); }
  else { $('create').textContent = 'Create video'; updateModelUI(); }
}
$('mode-video').onclick = () => setImageMode(false);
$('mode-image').onclick = () => setImageMode(true);
$('image-model').addEventListener('change', buildImageSizes);
$('image-size').addEventListener('change', refreshImageQuote);
$('image-edit-file').addEventListener('change', async () => {
  const f = $('image-edit-file').files[0];
  editSourceDataUrl = null;
  $('image-edit-preview').innerHTML = '';
  if (!f) return;
  try {
    editSourceDataUrl = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });
    const img = document.createElement('img');
    img.src = editSourceDataUrl;
    img.style.maxWidth = '100%'; img.style.borderRadius = '12px';
    img.alt = 'The image to edit';
    $('image-edit-preview').appendChild(img);
  } catch (e) { msg('job-msg', 'That image could not be read.', 'err'); }
});
async function makeImage() {
  const lane = imageLane();
  if (!lane) { msg('job-msg', 'Pick an image model first.', 'err'); return; }
  if (lane.edit && !editSourceDataUrl) { msg('job-msg', 'Add the image you want edited first.', 'err'); return; }
  msg('job-msg', 'Working on the bot check...');
  const bot = await botFields('botcheck2');
  const body = lane.id === 'house'
    ? { prompt: $('prompt').value, aspect_ratio: $('image-size').value, ...bot }
    : { prompt: $('prompt').value, image_model: lane.id, image_size: $('image-size').value, ...bot };
  if (lane.edit) body.source_image = editSourceDataUrl;
  const r = await api('/api/images', { method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body) });
  shelfImageJobId = r.job_id;
  msg('job-msg', 'Making your image...', 'ok');
  $('image-result-wrap').style.display = 'none';
  for (let i = 0; i < 90; i++) {
    await new Promise(res => setTimeout(res, 4000));
    const j = await api('/api/jobs/' + r.job_id);
    if (j.status === 'done') {
      $('image-result').src = '/api/jobs/' + r.job_id + '/file';
      $('image-result-wrap').style.display = '';
      msg('job-msg', 'Your image is ready. It is also saved in your library below.', 'ok');
      loadJobs();
      return;
    }
    if (j.status === 'error') { msg('job-msg', j.error || 'The image could not be made.', 'err'); return; }
  }
  msg('job-msg', 'Still working. Your image will appear in your library below when it lands.', 'ok');
}
$('image-use-start').onclick = () => {
  if (!shelfImageJobId) return;
  generatedImageJobId = shelfImageJobId;
  setImageMode(false);
  $('start-source').value = 'generate';
  $('start-source').dispatchEvent(new Event('change'));
  msg('job-msg', 'Start image attached. Describe the video and create it.', 'ok');
};
fetch('/api/config').then(r => r.json()).then(c => { cfg = c; catalogCfg = c; renderBotChecks(); buildCatalog(); });
fetch('/api/pricing').then(r => r.json()).then(p => { pricingData = p; buildCatalog(); updateHousePrice(); }).catch(() => {});
const prefillPrompt = new URLSearchParams(location.search).get('prompt');
if (prefillPrompt) $('prompt').value = prefillPrompt;
buildSegmentsSeg();
loadPresets();
loadEffects();
if (new URLSearchParams(location.search).get('verified')) {
  msg('auth-msg', 'Email verified. You can sign in now.', 'ok');
}
refreshMe();
</script>
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
</script>
</body></html>"""
)


GALLERY_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory gallery</title>
<meta name="description" content="Short AI videos made with Moonfactory, shared by our creators under their handles only.">
<meta property="og:title" content="Moonfactory gallery">
<meta property="og:description" content="Short AI videos made with Moonfactory, shared by our creators.">
<meta property="og:type" content="website">"""
    + _CSS
    + """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0c0c0f">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Moonfactory">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
</head><body><main>
<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/app">Studio</a></nav>
</header>
<div style="max-width:880px;margin:56px auto 22px">
  <h2>Community gallery</h2>
  <p class="muted">Made with Moonfactory, shared by our creators under
  their handles only. Clips are public by default. Paying customers can
  opt out in the studio. If a clip should not be here, use its Report
  button and it will be reviewed.</p>
</div>
<div class="grid" id="items"><p class="muted">Loading...</p></div>
</main>
<footer><span>Moonfactory</span><span><a href="/app">Studio</a> &nbsp;<a href="/help">Help</a> &nbsp;<a href="/terms">Terms</a> &nbsp;<a href="/privacy">Privacy</a> &nbsp;<a href="/support">Support</a></span></footer>
<script>
async function loadGallery() {
  const box = document.getElementById('items');
  try {
    const r = await fetch('/api/gallery');
    const data = await r.json();
    box.innerHTML = '';
    if (!data.items.length) {
      box.innerHTML = '<p class="muted">No clips yet. The first ones are on their way.</p>';
      return;
    }
    for (const item of data.items) {
      const t = document.createElement('div'); t.className = 'tile';
      const f = document.createElement('div'); f.className = 'frame';
      if (item.kind === 'image') {
        const img = document.createElement('img');
        img.src = item.file_url; img.alt = ''; img.loading = 'lazy';
        f.appendChild(img);
      } else {
        const video = document.createElement('video');
        video.controls = true; video.src = item.file_url; video.preload = 'none';
        if (item.poster_url) video.poster = item.poster_url;
        video.setAttribute('playsinline', '');
        f.appendChild(video);
      }
      const h = document.createElement('div'); h.className = 'handle';
      h.textContent = item.handle;
      const report = document.createElement('button');
      report.className = 'report'; report.textContent = 'Report';
      report.onclick = async () => {
        const reason = window.prompt('Why are you reporting this clip? (optional)');
        if (reason === null) return;
        try {
          const resp = await fetch('/api/gallery/' + item.id + '/report', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ reason: reason || '' }) });
          if (!resp.ok) throw new Error('report failed');
          report.textContent = 'Reported, thank you';
          report.disabled = true;
        } catch (e) { report.textContent = 'Report failed, try again'; }
      };
      t.appendChild(f); t.appendChild(h); t.appendChild(report);
      box.appendChild(t);
    }
  } catch (e) {
    box.innerHTML = '<p class="err">The gallery could not load. Try again in a moment.</p>';
  }
}
loadGallery();
</script>
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
</script>
</body></html>"""
)
