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
from pathlib import Path

import modal

sys.path.insert(0, "/platform")
import pricing  # noqa: E402
import clipping  # noqa: E402

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
    .add_local_file(str(Path(__file__).parent / "clipping.py"), "/platform/clipping.py")
    .add_local_file(
        str(
            Path(__file__).parent.parent
            / "workflows" / "camera-presets" / "presets-index.json"
        ),
        "/platform/presets-index.json",
    )
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
        if clipping.LINK_DEAD_MESSAGE.split(";")[0] in str(exc):
            # A dead link is the one failure the user can act on:
            # surface the working alternative, not the generic line.
            job["error"] = (clipping.LINK_DEAD_MESSAGE
                            + "; nothing was charged")
        job["internal_error"] = str(exc)[:300]
        jobs[job_id] = job
        _clip_release_hold(job)
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
SPEECH_VOICES = ("af_heart", "af_bella", "am_michael", "am_adam",
                 "bf_emma", "bm_george")
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
    {"id": "grok", "label": "Grok Imagine Video 1.5", "paid_only": True},
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
]
MODEL_LABELS = {m["id"]: m["label"] for m in MODELS}
DEFAULT_MODEL = "wan2.2"
PER_SECOND_MODELS = (
    "wan3", "wan3_prime", "seedance", "veo3", "veo3_fast", "kling3",
    "kling4", "kling3_omni", "seedance2", "seedance2_fast",
    "seedance2_mini", "veo3_lite", "omni12", "omni11", "hailuo23",
    "minimax_h3", "grok", "luma_ray32", "ltx25", "ltx25_fast",
    "happyhorse", "pika22", "vidu_q2", "vidu_q3", "vidu_q4",
    "seedance25_flash", "pixverse_v6", "runway_gen45",
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
    "wan3": {"native_speech": False, "native_audio": True,
             "note": "native audio is music/SFX-led; speech is not reliable"},
    "wan3_prime": {"native_speech": False, "native_audio": True,
                   "note": "native audio is music/SFX-led; speech is not reliable"},
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
}


def lane_audio(model: str) -> dict:
    return LANE_AUDIO.get(model, {
        "native_speech": False, "native_audio": False, "note": "",
    })


# Suppliers whose finished files arrive as a result URL for the
# platform to download (the house worker is the only other source).
URL_RESULT_PROVIDERS = (
    "fal", "beatapi", "kie", "cometapi", "piapi", "openrouter",
    "novita", "wavespeed", "eachlabs", "runway", "vidu", "minimax",
)
# Suppliers polled through mirror_api_status (fal and BeatAPI keep
# their original mirror functions).
API_POLL_PROVIDERS = (
    "kie", "cometapi", "piapi", "openrouter", "novita",
    "wavespeed", "eachlabs", "runway", "vidu", "minimax",
)
# Per-model duration bounds in seconds (the global form range is
# 3-30; these narrow it to what each model actually accepts).
MODEL_DURATION_BOUNDS = {
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
    "grok": (3, 15),
    "happyhorse": (3, 15),
    "ltx25": (6, 20),
    "ltx25_fast": (6, 20),
    "vidu_q3": (3, 16),
    "vidu_q4": (3, 16),
    "seedance25_flash": (4, 30),
    "pixverse_v6": (3, 15),
    "runway_gen45": (3, 10),
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
}
# Models whose supplier endpoint has no reference mode at all:
# they take a prompt or a start image, nothing else. Validation
# refuses reference inputs for them with a clean 422.
TEXT_IMAGE_ONLY_MODELS = (
    "veo3_lite", "hailuo23", "luma_ray32", "ltx25", "ltx25_fast",
    "pika22", "vidu_q2", "vidu_q3", "runway_gen45", "pixverse_v6",
)
# Models that accept references but only still images: their
# supplier schemas have no reference video or audio inputs.
IMAGE_REFS_ONLY_MODELS = ("grok", "happyhorse")
FAL_BASE = "https://queue.fal.run"
FAL_ENDPOINTS = {
    ("wan3", "text"): "alibaba/wan-3.0/text-to-video",
    ("wan3", "image"): "alibaba/wan-3.0/image-to-video",
    ("wan3", "reference"): "alibaba/wan-3.0/reference-to-video",
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
    "aperture", "lighting", "palette",
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
            return bool(resp.json().get("success"))
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
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

    web = FastAPI(title="Moonfactory")

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
        cost, so nothing ever starts that would cross the wall."""
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

    @web.get("/healthz")
    def healthz():
        return {"ok": True}

    @web.get("/api/config")
    def config():
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
            "models": [
                {
                    "id": m["id"],
                    "label": m["label"],
                    "paid_only": m["paid_only"],
                    "audio": {
                        "native_speech": lane_audio(m["id"])["native_speech"],
                        "native_audio": lane_audio(m["id"])["native_audio"],
                    },
                    "available": (
                        True if m["id"] == DEFAULT_MODEL
                        else not api_lane_state(m["id"])["paused"]
                    ),
                }
                for m in MODELS
            ],
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
            for m in MODELS
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
            "Verify your email to start creating:\n"
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
                detail="handle must be 3-20 characters: letters, numbers, _ or -",
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
                detail="gallery opt-out is a paid feature",
            )
        user["gallery_opt_out"] = opt_out
        save_user(user)
        return {"gallery_opt_out": opt_out}

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
            float(job.get("result_duration") or job.get("duration_seconds") or 0)
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
                    if status == "IN_PROGRESS":
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
                                job["result_url"] = video["url"]
                                job["result_duration"] = payload.get("duration")
                                job["bytes"] = video.get("file_size")
                                job["status"] = "done"
                                jobs[job["id"]] = job
                                settle_fal_job(job)
                            else:
                                # Result fetch hiccup right after
                                # completion: retry on the next poll,
                                # fail only after many attempts.
                                job["poll_errors"] = int(job.get("poll_errors") or 0) + 1
                                if job["poll_errors"] > 30:
                                    fail_fal_job(job)
                                else:
                                    jobs[job["id"]] = job
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
                    if status == "processing":
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
                            job["result_url"] = video["url"]
                            if usage.get("billable_duration_seconds"):
                                job["result_duration"] = float(
                                    usage["billable_duration_seconds"]
                                )
                            if usage.get("credits_settled") is not None:
                                job["provider_cost_cents"] = round(
                                    float(usage["credits_settled"]) * 100, 2
                                )
                            job["status"] = "done"
                            jobs[job["id"]] = job
                            settle_fal_job(job)
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
                        owner = users.get(f"id:{job['user_id']}")
                        if owner:
                            if job.get("charged_cents") and not job.get("refunded"):
                                owner["balance_cents"] = (
                                    owner.get("balance_cents", 0)
                                    + job["charged_cents"])
                                job["refunded"] = True
                            if job.get("included_cost_cents") and not job.get("included_released"):
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
            except Exception:
                pass
            job["last_poll"] = _now()
            jobs[job["id"]] = job
        return job

    def mirror_status(job: dict) -> dict:
        if job.get("provider") == "clip":
            return job
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
        low, high = MODEL_DURATION_BOUNDS.get(model, (3, 30))
        if duration < low or duration > high:
            raise HTTPException(
                status_code=422,
                detail=f"duration for this model must be between {low} and {high} seconds",
            )
        steps = MODEL_DURATION_STEPS.get(model)
        if steps and duration not in steps:
            raise HTTPException(
                status_code=422,
                detail="this model generates clips of "
                + ", ".join(f"{s} seconds" for s in steps),
            )
        resolution = body.get("resolution") or "720p"
        allowed_res = pricing.MODEL_RESOLUTIONS.get(model, pricing.RESOLUTIONS)
        if resolution not in allowed_res:
            raise HTTPException(
                status_code=422,
                detail="resolution for this model must be one of: "
                + ", ".join(allowed_res),
            )
        ref_images = body.get("reference_image_urls") or []
        ref_videos = body.get("reference_video_urls") or []
        ref_audios = body.get("reference_audio_urls") or []
        if not all(isinstance(x, list) for x in (ref_images, ref_videos, ref_audios)):
            raise HTTPException(status_code=422, detail="reference inputs must be lists of URLs")
        if len(ref_images) > 10 or len(ref_videos) > 5 or len(ref_audios) > 5:
            raise HTTPException(status_code=422, detail="too many reference inputs")
        ref_video_seconds = 0.0
        if ref_videos:
            try:
                ref_video_seconds = float(body.get("reference_video_seconds") or 0)
            except (TypeError, ValueError):
                ref_video_seconds = 0.0
            if ref_video_seconds <= 0 or ref_video_seconds > 15:
                raise HTTPException(
                    status_code=422,
                    detail="reference_video_seconds (the total length of the reference videos, up to 15) is required: it is part of the price",
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
        return {
            "duration": duration,
            "resolution": resolution,
            "mode": mode,
            "reference_image_urls": ref_images,
            "reference_video_urls": ref_videos,
            "reference_audio_urls": ref_audios,
            "reference_video_seconds": ref_video_seconds,
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
                "duration_seconds": params["duration"],
                "resolution": params["resolution"],
                "billed_seconds": float(params["duration"]),
                "raw_cost_cents": 0.0,
                "balance_debit_cents": 0,
                "cap_cents": 0.0,
                "lane_paused": True,
                "ceiling_ok": False,
                "audio": lane_audio(model),
                "payment": {"method": "unavailable"},
            }
        est = pricing.per_second_estimate(
            model, params["resolution"], params["duration"],
            params["reference_video_seconds"], rate_table()["rates"],
            owner=is_owner(user),
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
        elif int(user.get("balance_cents", 0)) >= est["cap_balance_cents"]:
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
            "duration_seconds": params["duration"],
            "resolution": params["resolution"],
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
        if model in ("veo3", "veo3_fast", "veo3_lite"):
            # Veo's fal schema: clip length as a string, audio as
            # generate_audio, frames as image_url / end_image_url,
            # references as image_urls (images only; validation
            # already refused video and audio references).
            payload = {
                "prompt": prompt,
                "aspect_ratio": "9:16",
                "duration": f"{params['duration']}s",
                "resolution": params["resolution"],
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
                "aspect_ratio": "9:16",
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
        elif model in ("luma_ray32", "ltx25", "ltx25_fast", "pika22", "vidu_q2"):
            # Minimal text/image payloads in the shared fal shape.
            # These lanes are dark (the supplier account is
            # unfunded), so a schema mismatch surfaces as the
            # clean unavailable sentence with the hold released,
            # never a charge; the payloads get their funded
            # verification pass before any of these lanes lights.
            payload = {
                "prompt": prompt,
                "aspect_ratio": "9:16",
                "duration": params["duration"],
                "resolution": params["resolution"],
            }
            if params["mode"] == "image":
                payload["image_url"] = f"data:image/png;base64,{params['start_image']}"
        else:
            payload = {
                "prompt": prompt,
                "resolution": params["resolution"],
                "aspect_ratio": "9:16",
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
            payload["aspect_ratio"] = "9:16"
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

    def _poll_done(job: dict, result_url: str, cost_cents=None,
                   duration=None):
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
            ("hailuo23", "text"): "hailuo-2.3/text-to-video",
            ("hailuo23", "image"): "hailuo-2.3/image-to-video",
        }
        slug = slugs.get((model, mode))
        if not slug:
            _submit_failed(job, f"no kie slug for {model}/{mode}")
        inp = {"prompt": prompt, "duration": params["duration"],
               "aspect_ratio": "9:16"}
        if model in ("kling3", "kling4", "kling3_omni"):
            inp["sound"] = True
        elif model == "seedance2_fast":
            inp["generate_audio"] = True
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
        size = "720x1280"
        if job["model"] == "seedance" and \
                params.get("resolution") == "480p":
            # CometAPI's Seedance 2.5 catalog lists the 480p tier
            # as 854x480 landscape; the platform is vertical, so
            # the 9:16 raster is 480x854. The 480p tier routes
            # here since pricing round 2 (2026-11-16).
            size = "480x854"
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
                    url = (data.get("video_url") or data.get("url")
                           or (data.get("output") or {}).get("url")
                           if isinstance(data.get("output"), dict)
                           else data.get("output"))
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "error", "cancelled"):
                    _poll_fail(job, str(data.get("error"))[:200])
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
               "aspect_ratio": "9:16", "generate_audio": False}
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
                if status in ("completed", "success", "succeeded"):
                    output = data.get("output") or {}
                    url = output.get("video") if isinstance(
                        output, dict) else output
                    if isinstance(url, dict):
                        url = url.get("url")
                    if url:
                        _poll_done(job, url)
                    else:
                        _poll_retry_or_fail(job)
                elif status in ("failed", "error", "cancelled"):
                    _poll_fail(
                        job, str(data.get("error")
                                 or data.get("message"))[:200])
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
                   "aspect_ratio": "9:16"}
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
            "aspect_ratio": "9:16",
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
                   "aspect_ratio": "9:16"}
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
               "aspect_ratio": "9:16"}
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
                if status == "success":
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
        if params["mode"] == "image":
            payload = {"model": "gen4.5",
                       "promptImage": _data_uri(params["start_image"]),
                       "promptText": prompt, "ratio": "720:1280",
                       "duration": params["duration"]}
            url = f"{RUNWAY_BASE}/v1/image_to_video"
        else:
            payload = {"model": "gen4.5", "promptText": prompt,
                       "ratio": "720:1280",
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
            "aspect_ratio": "9:16",
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
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
                if status in ("Success", "succeeded", "success") and url:
                    _poll_done(job, url)
                elif status in ("Fail", "Failed", "failed", "error"):
                    _poll_fail(job, str(payload.get("error"))[:200])
                else:
                    job["status"] = "running"
                    jobs[job["id"]] = job
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
        return job


    def create_api_job(user: dict, body: dict, model: str, base: str,
                       prompt: str, music_prompt: str, camera: dict = None,
                       speech_script: str = "", speech_voice: str = ""):
        """Quote -> hold -> submit, per design s.3. Any failure at or
        before submission leaves an error job with the clean public
        message and the hold fully released: no charge, nothing
        stuck."""
        params = _api_job_params(body, model)
        state = api_lane_state(model)
        if state["paused"]:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        prov = provider_for(model, params["resolution"])
        if not os.environ.get(PROVIDER_KEY_ENV.get(prov, "FAL_KEY")):
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        est = pricing.per_second_estimate(
            model, params["resolution"], params["duration"],
            params["reference_video_seconds"], rate_table()["rates"],
            owner=is_owner(user),
        )
        if not ceiling_allows(user, est["cap_raw_cents"]):
            raise HTTPException(
                status_code=402,
                detail="this job would exceed the total generation your payments cover",
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
        else:
            raise HTTPException(
                status_code=402,
                detail=f"included budget used up; this job costs {est['balance_debit_cents']} cents of prepaid balance",
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
            "plan_at_job": user["plan"],
            "retry_of": body.get("retry_of"),
            "last_poll": 0,
            "duration_seconds": params["duration"],
            "resolution": params["resolution"],
            "reference_video_seconds": params["reference_video_seconds"],
            "reference_image_urls": params["reference_image_urls"],
            "reference_video_urls": params["reference_video_urls"],
            "reference_audio_urls": params["reference_audio_urls"],
            "hold_kind": hold_kind,
            "hold_amount": hold_amount,
            "cap_raw_cents": est["cap_raw_cents"],
        }
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
                detail="aspect_ratio must be one of: " + ", ".join(HOUSE_ASPECTS))
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
        camera_speed = 1.0
        if body.get("camera_speed") is not None:
            try:
                camera_speed = float(body["camera_speed"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="camera_speed must be a number")
        if fun_preset is not None:
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
                    detail="effects need a start image: pick an effect and upload the photo it acts on")
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
        if model in PER_SECOND_MODELS:
            # The per-second lanes take single-pass clips at 9:16 in
            # this form; chaining and other aspects are house-lane
            # shapes, refused here rather than silently reshaped.
            if segments > 1:
                raise HTTPException(
                    status_code=422,
                    detail="multi-segment chaining runs on Wan 2.2; this model makes single-pass clips")
            if aspect != "9:16":
                raise HTTPException(
                    status_code=422,
                    detail="this model is offered at 9:16 in this form; other aspects run on Wan 2.2")
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
                    detail="Production needs a start image; text-only videos run on the Draft lane")
            if segments > 1 and not is_paid(user):
                raise HTTPException(
                    status_code=403,
                    detail="multi-segment videos are a paid feature; free accounts make single Draft clips")
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
                detail=f"you already have {in_flight} videos in flight (your plan allows {limit} at a time); one needs to finish before the next starts")
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
                speech_voice=speech_voice,
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
                    detail=f"free clip limit reached: the free plan is {FREE_CLIPS_TOTAL} clips total",
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
            if not ceiling_allows(user, job_cost):
                raise HTTPException(
                    status_code=402,
                    detail="this job would exceed the total generation your payments cover",
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
                if user.get("balance_cents", 0) < price:
                    detail = (
                        f"included time used up; this job costs {price} cents of prepaid balance"
                        if user["plan"] == "creator"
                        else f"this job costs {price} cents of prepaid balance"
                    )
                    raise HTTPException(status_code=402, detail=detail)
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
            "model": DEFAULT_MODEL,
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
                detail="aspect_ratio must be one of: " + ", ".join(HOUSE_ASPECTS))
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
        if int(user.get("balance_cents", 0)) >= charge:
            return {"method": "balance", "charge_cents": charge}
        return {"method": "insufficient", "charge_cents": charge,
                "balance_cents": int(user.get("balance_cents", 0))}

    @web.post("/api/images/quote")
    async def quote_image(request: Request, mf_session: str = Cookie(default="")):
        user = require_user(request, mf_session)
        body = await request.json()
        _image_params(body)
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
                detail=f"you already have {in_flight} jobs in flight (your plan allows {limit} at a time); one needs to finish before the next starts")
        u = usage(user)
        charged = 0
        used_budget = 0.0
        job_cost_cents = 0.0
        is_free_job = False
        if user["plan"] == "free" and not is_paid(user):
            if free_jobs_total(user) >= FREE_CLIPS_TOTAL:
                raise HTTPException(
                    status_code=403,
                    detail=f"free clip limit reached: the free plan is {FREE_CLIPS_TOTAL} clips total",
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
                    detail=f"included budget used up; a first frame costs {payment['charge_cents']} cents of prepaid balance")
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
                script, job.get("speech_voice") or "af_heart")
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
        if not out_path.exists():
            raw_path = FILES_DIR / f"{job_id}.raw.mp4"
            if not raw_path.exists():
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
                        jobs[job["id"]] = job
                else:
                    voice = _voiceover_for_job(job)
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
            data_volume.commit()
        return out_path

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
        elif int(user.get("balance_cents", 0)) >= q["balance_charge_cents"]:
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
                detail="caption style must be one of: "
                + ", ".join(clipping.STYLE_NAMES.values()))
        layout = body.get("layout") or "blur"
        if layout not in clipping.LAYOUTS:
            raise HTTPException(
                status_code=422,
                detail="layout must be one of: "
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
        if not ceiling_allows(user, q["worst_case_cost_cents"]):
            raise HTTPException(
                status_code=402,
                detail="this job would exceed the total generation your payments cover")
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
        else:
            raise HTTPException(
                status_code=402,
                detail=f"included budget used up; clipping this video costs {q['balance_charge_cents']} cents of prepaid balance")
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
    # accounts that have never paid always list.

    def gallery_visible(job: dict) -> bool:
        if job.get("kind") == "image":
            # Start images are ingredients, not finished clips: they
            # never list in the gallery.
            return False
        if job.get("status") != "done" or job.get("removed"):
            return False
        owner = users.get(f"id:{job.get('user_id')}")
        if not owner:
            return False
        if is_paid(owner) and owner.get("gallery_opt_out"):
            return False
        return True

    def public_gallery_item(job: dict) -> dict:
        owner = users.get(f"id:{job.get('user_id')}") or {}
        return {
            "id": job["id"],
            "handle": display_handle(owner),
            "lane": job["lane"],
            "model": (
                "Clip" if job.get("kind") in ("clip_job", "clip_output")
                else "Effects" if job.get("effect")
                else MODEL_LABELS.get(job.get("model") or DEFAULT_MODEL, "Wan 2.2")
            ),
            "prompt": (job.get("prompt") or "")[:200],
            "camera_presets": job.get("camera_preset_names") or [],
            "camera_look": job.get("camera_look") or {},
            "effect": job.get("effect_name"),
            "duration_seconds": job.get("duration_seconds"),
            "resolution": job.get("resolution"),
            "aspect_ratio": job.get("aspect_ratio") or "9:16",
            "segments_total": int(job.get("segments_total") or 1),
            "cost_cents": (
                None if job.get("kind") in ("clip_job", "clip_output")
                else int(job.get("charged_cents") or 0)
                + float(job.get("included_cost_cents")
                        or job.get("budget_cost_cents") or 0)
            ),
            "created_at": job["created_at"],
            "file_url": f"/gallery/{job['id']}/file",
        }

    @web.get("/gallery", response_class=HTMLResponse)
    def gallery_page():
        return HTMLResponse(GALLERY_HTML)

    @web.get("/api/gallery")
    def gallery_list():
        items = []
        for key in list(jobs.keys()):
            job = jobs.get(key)
            if job and gallery_visible(job):
                items.append(public_gallery_item(job))
        items.sort(key=lambda i: i["created_at"], reverse=True)
        return {"items": items[:100]}

    @web.get("/gallery/{job_id}/file")
    def gallery_file(job_id: str):
        job = jobs.get(job_id)
        if not job or not gallery_visible(job):
            raise HTTPException(status_code=404, detail="not in the gallery")
        out_path = ensure_processed(job)
        return FileResponse(out_path, media_type="video/mp4",
                            filename=f"moonfactory-{job_id}.mp4")

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
            customer_email=user["email"],
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
            raise HTTPException(status_code=422, detail="unknown top-up pack")
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
            customer_email=user["email"],
            client_reference_id=user["id"],
            metadata={"user_id": user["id"], "credit_cents": str(pack)},
            success_url=f"{base}/app?checkout=success",
            cancel_url=f"{base}/app?checkout=cancel",
        )
        return {"url": session.url}

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

        def add_net_paid(user: dict, gross_cents) -> None:
            """Amendment 2 accounting: cumulative payments, net of
            card fees, are the account's cost ceiling."""
            if gross_cents:
                user["net_paid_cents"] = (
                    float(user.get("net_paid_cents") or 0.0)
                    + pricing.net_of(int(gross_cents))
                )

        if etype == "checkout.session.completed":
            uid = (obj.get("metadata") or {}).get("user_id") or obj.get("client_reference_id")
            user = users.get(f"id:{uid}") if uid else None
            if user:
                if obj.get("mode") == "subscription":
                    user["plan"] = "creator"
                    user["past_due"] = False
                    user["stripe_customer_id"] = obj.get("customer")
                    user["stripe_subscription_id"] = obj.get("subscription")
                    u = user.get("usage") or {}
                    u["included_seconds"] = 0.0
                    u["included_cost_cents"] = 0.0
                    user["usage"] = u
                    # The money itself is counted on invoice.paid,
                    # which fires for the first subscription payment
                    # too; counting it here as well would double it.
                elif obj.get("mode") == "payment":
                    credit = int((obj.get("metadata") or {}).get("credit_cents") or 0)
                    user["balance_cents"] = user.get("balance_cents", 0) + credit
                    add_net_paid(user, obj.get("amount_total") or credit)
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
                job["result_url"] = video["url"]
                job["result_duration"] = payload.get("duration")
                job["bytes"] = video.get("file_size")
                job["status"] = "done"
                jobs[job["id"]] = job
                settle_fal_job(job)
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
  main{max-width:960px;margin:0 auto;padding:26px 24px 90px}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .logo{font-weight:800;font-size:19px;letter-spacing:-.02em;text-decoration:none;color:var(--ink);display:flex;align-items:center;gap:9px}
  .logo svg{flex:none;display:block}
  .logo em{font-style:normal;color:var(--accent-bright)}
  header nav{display:flex;gap:26px;align-items:center}
  header nav a{text-decoration:none;color:var(--gray);font-size:15px}
  header nav a:hover{color:var(--ink)}
  header nav .who{color:var(--faint);font-size:14px}
  h1{font-size:46px;line-height:1.12;letter-spacing:-.03em;font-weight:800;margin:0}
  h2{font-size:26px;letter-spacing:-.02em;margin:0 0 6px}
  a{color:var(--accent-bright)}
  .btn{display:inline-block;background:var(--accent);color:#fff;padding:11px 20px;border-radius:12px;text-decoration:none;border:0;cursor:pointer;font:inherit;font-size:15px;font-weight:600;transition:.15s}
  .btn:hover{background:var(--accent-bright)}
  .btn.secondary{background:var(--seg-on);color:var(--ink)}
  .btn.secondary:hover{background:#3a3a46}
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
  .badge{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#a5b4fc;background:#1e1e38;border-radius:999px;padding:5px 11px}
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
  .tile .frame video{width:100%;height:100%;object-fit:cover;border-radius:0}
  .tile .handle{margin-top:9px;font-size:14px;color:var(--gray)}
  .tile .report{margin-top:6px;background:none;border:0;color:var(--faint);font:inherit;font-size:13px;cursor:pointer;padding:0}
  .tile .report:hover{color:var(--ink)}
  .tile .report:disabled{color:var(--faint);cursor:default}
  .job video{max-width:360px}
  .job img.thumb{width:140px;border-radius:10px}
  footer{max-width:960px;margin:0 auto;padding:0 24px 44px;color:var(--faint);font-size:13.5px;display:flex;justify-content:space-between}
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
  with sound: music, and a voice whenever someone is talking.
  Your first 5 clips are free.</p>
</div>
<div class="hero-entry">
  <div class="row2">
    <input id="hero-prompt" type="text" maxlength="500"
      placeholder="Describe your video: a red hot air balloon rising over green hills">
    <button class="btn" id="hero-go" type="button">Try It free</button>
  </div>
  <p class="under" style="margin-top:12px">Trying anything takes an account:
  sign up free, verify your email, and Try It gives you 5 clips, total.
  No card.</p>
</div>"""

_LANDING_TOP_MEMBER = """<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/gallery">Gallery</a><a href="/app" style="color:#f4f4f1;font-weight:600">Open Create</a></nav>
</header>
<div class="hero">
  <h1>Turn one image into<br>a short video.</h1>
  <p>Welcome back. Create is your studio: every model, custom sound,
  the full shelf. It is one click away.</p>
</div>
<div class="hero-entry">
  <div class="row2">
    <input id="hero-prompt" type="text" maxlength="500"
      placeholder="Describe your video: a red hot air balloon rising over green hills">
    <button class="btn" id="hero-go" type="button">Open Create</button>
  </div>
  <p class="under" style="margin-top:12px">Members open straight into Create.
  The free trial is for people who have not paid yet, so it is not in your way.</p>
</div>"""

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found — Moonfactory</title>
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
</body></html>"""

INDEX_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory: AI video generation for creators</title>"""
    + _CSS
    + """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
</head><body><main>
__LANDING_TOP__

<div class="strip-label" id="strip-label" style="display:none">Fresh from the community</div>
<div class="strip" id="strip"></div>

<p class="pricing-line"><b>Free</b> — 5 clips, no card. &nbsp;<b>Creator</b> —
<span id="creator-price">$20</span>/mo with an included budget. &nbsp;<b>Pay per clip</b> —
the exact price is on the button before anything runs. No credits, no conversion math.</p>
</main>
<footer><span>Moonfactory</span><span><a href="/gallery">Gallery</a></span></footer>
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
    const v = document.createElement('video');
    v.src = item.file_url; v.preload = 'metadata'; v.muted = true;
    v.setAttribute('playsinline', '');
    f.appendChild(v);
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
</body></html>"""
)


APP_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory studio</title>"""
    + _CSS
    + """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</head><body><main>
<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/gallery">Gallery</a><span class="who" id="who"></span></nav>
</header>

<div id="auth" class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
  <h2>Sign in or create an account</h2>
  <label>Email</label><input id="email" type="email" autocomplete="email">
  <label>Password (8+ characters)</label><input id="password" type="password" autocomplete="current-password">
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
      <span class="badge" id="composer-badge" style="display:none">Members · Creator</span>
    </div>
    <p class="muted" id="composer-note"></p>

    <div class="drop" id="dropzone" tabindex="0" role="button" aria-label="Add a start image" style="margin-top:14px">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#9c9ca6" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5-9 9"/></svg>
      <div class="big">Add a start image</div>
      <div class="small">Drop it here, or click to browse. Optional on the Draft lane: text-only works too.</div>
    </div>
    <label class="flabel" for="start-source" style="margin-top:14px">Start image</label>
    <select id="start-source">
      <option value="none" selected>None: text only (Draft lane)</option>
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
      <p class="muted" id="image-note">We make the opening frame at the aspect ratio picked for the video. Check it here, and it attaches to your video with one click. Every frame we make is charged like a small render; the exact price shows before it runs. Not happy with it? Make another.</p>
      <div id="image-preview"></div>
    </div>

    <label class="flabel" for="prompt" style="margin-top:6px">Prompt</label>
    <textarea id="prompt" rows="4" class="composer-prompt" placeholder="Describe what happens… e.g. a red hot air balloon rising over green hills, gentle forward camera movement, bright daylight"></textarea>

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
      </div>
    </div>
    <p class="muted" id="model-note"></p>
    <p class="muted" id="segments-note"></p>

    <details class="more">
      <summary>More options</summary>

      <div id="lane-wrap">
        <label>Lane</label>
        <select id="lane">
          <option value="draft">Draft: fast preview, about 5 seconds</option>
          <option value="production">Production: 720x1280, Creator plan or balance</option>
        </select>
        <p class="muted" id="lane-note"></p>
      </div>

      <div id="resolution-wrap" style="display:none">
        <label>Resolution</label>
        <select id="resolution">
          <option value="480p">480p</option>
          <option value="720p" selected>720p</option>
          <option value="1080p">1080p</option>
        </select>
      </div>

      <div id="house-more">
        <label>Aspect ratio</label>
        <select id="aspect">
          <option value="9:16" selected>9:16 vertical</option>
          <option value="16:9">16:9 wide</option>
          <option value="1:1">1:1 square</option>
        </select>
        <label>End image (PNG, optional, Production lane: the video steers to this final frame)</label>
        <input id="endimg" type="file" accept="image/png">
      </div>

      <p class="group-label">Camera and look</p>
      <label>Camera moves (optional, up to 3, applied in the order you pick them)</label>
      <div id="presets"><p class="muted">Loading camera presets...</p></div>
      <p class="muted" id="preset-chain"></p>
      <label>Film look (optional)</label>
      <div id="looks"></div>
      <label>Effect (optional, one per video)</label>
      <div id="effects"><p class="muted">Loading effects...</p></div>
      <p class="muted" id="effect-note"></p>

      <p class="group-label">Sound</p>
      <label>Music prompt (optional)</label>
      <textarea id="music" rows="2" maxlength="300" placeholder="lo-fi piano with soft rain, slow and dreamy"></textarea>
      <p class="muted" id="music-note"></p>
      <label>Spoken line (optional)</label>
      <textarea id="speech" rows="2" maxlength="1000" placeholder="the words a voice should say in this video"></textarea>
      <p class="muted" id="speech-note"></p>

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
      <span class="badge">Members · Creator</span>
    </div>
    <div class="paywall">
      <p>Try It is the free plan: Wan 2.2 Draft clips, 5 per account total,
      watermarked. Nothing on Create is free: every model beyond Wan 2.2,
      the Production lane, custom music and voiceover, effects,
      multi-segment videos, clipping, and hiding your clips from the
      gallery all live here, and the server keeps them locked until an
      account has paid.</p>
      <p>Any payment opens Create, two ways: Creator, a monthly plan with
      an included generation budget, or a one-time top-up and pay per
      video, no subscription. Once you have paid, Try It disappears and
      you open straight into Create.</p>
    </div>
    <p>
      <button class="btn" id="upgrade-creator">Get Creator, $20/month</button>
      <button class="btn secondary" id="upgrade-topup">Top up $10</button>
    </p>
    <p class="muted">Both buttons use the same checkout as the account
    card below.</p>
  </div>
  </div>

  <div id="clip-pane" style="display:none">
  <div class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
    <div class="create-head">
      <h2>Clip</h2>
      <span class="badge">Members · Creator</span>
    </div>
    <p class="muted">Paste a YouTube, Twitch or Kick link, or upload a file. You get vertical clips with captions and a hook on each one. If a link ever refuses to read, upload the file instead: uploads always work.</p>
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
    <p class="muted">Clip only content you own or have permission to use. Clipping is a paid feature: Creator plans use included clipping minutes, or pay 5 cents per source minute from your balance.</p>
  </div>
  <h2 style="max-width:640px;margin:26px auto 6px">Your clips</h2>
  <div id="clip-results" style="max-width:640px;margin:0 auto"><p class="muted">No clips yet. Paste a link above and your clips will show up here.</p></div>
  </div>

  <h2 style="max-width:640px;margin:30px auto 6px">Your videos</h2>
  <div id="jobs" style="max-width:640px;margin:0 auto"><p class="muted">Loading...</p></div>

  <div class="card" style="max-width:640px;margin-left:auto;margin-right:auto">
    <h2>Your account</h2>
    <p id="account"></p>
    <label>Public handle (the only name the gallery ever shows)</label>
    <input id="handle" maxlength="20">
    <button class="btn secondary" id="save-handle">Save handle</button>
    <label style="font-weight:400;display:block;margin-top:12px">
      <input type="checkbox" id="gallery-optout" style="width:auto;margin:0 6px 0 0">
      Hide my clips from the public gallery</label>
    <p class="muted" id="gallery-note"></p>
    <p id="profile-msg"></p>
    <p>
      <button class="btn secondary" id="buy-creator">Get Creator, $20/month</button>
      <button class="btn secondary" data-topup="500">Top up $5</button>
      <button class="btn secondary" data-topup="1000">Top up $10</button>
      <button class="btn secondary" data-topup="2000">Top up $20</button>
      <button class="btn secondary" id="logout">Sign out</button>
    </p>
    <p id="pay-msg"></p>
    <p class="muted">Two ways to pay: top up and pay per video with
    no subscription, or get Creator for a monthly included budget
    plus top-ups. Any payment unlocks every model, custom music,
    and hiding your clips from the gallery.</p>
  </div>
</div>
</main>
<footer><span>Moonfactory</span><span><a href="/gallery">Gallery</a></span></footer>
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
async function botFields(containerId) {
  if (cfg.turnstile_site_key) {
    const el = document.querySelector('#' + containerId + ' input[name="cf-turnstile-response"]');
    return { turnstile_token: el ? el.value : '' };
  }
  return await solvePow();
}
function renderBotChecks() {
  if (cfg.turnstile_site_key && window.turnstile) {
    turnstile.render('#botcheck', { sitekey: cfg.turnstile_site_key });
    turnstile.render('#botcheck2', { sitekey: cfg.turnstile_site_key });
    if (document.getElementById('botcheck3')) turnstile.render('#botcheck3', { sitekey: cfg.turnstile_site_key });
  }
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || ('HTTP ' + r.status));
  return body;
}
function msg(id, text, cls) { const el = $(id); el.textContent = text; el.className = cls || ''; }

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
      '<div><div class="fname">' + (f ? f.name : 'start-image') + '</div>' +
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
      '<div class="small">Drop it here, or click to browse. Optional on the Draft lane: text-only works too.</div>';
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
    msg('image-note', 'Making your first frame: ' + priceText + '...');
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
    ? 'Camera: ' + selectedPresets.map(id => names[id] || id).join(' \u2192 ')
    : 'No camera move picked: the camera follows your prompt.';
  $('preset-chain').className = 'muted';
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
    ? 'This effect acts on your start image: pick one in the Start image section above. Your prompt above is who or what is in the photo. Effects price as a Production segment and render at 480p class, delivered with the usual music bed.'
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
    msg('auth-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck');
    const r = await api('/api/signup', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email: $('email').value, password: $('password').value, ...bot }) });
    msg('auth-msg', 'Account created. Open the verification link in your email, then sign in.', 'ok');
  } catch (e) { msg('auth-msg', e.message, 'err'); }
};
$('login').onclick = async () => {
  try {
    const r = await api('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email: $('email').value, password: $('password').value }) });
    showStudio(r.user);
  } catch (e) { msg('auth-msg', e.message, 'err'); }
};
$('logout').onclick = async () => { await api('/api/logout', { method: 'POST' }); location.reload(); };

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
  $('who').textContent = u.email;
  const paidSurface = isPaidSurface();
  $('tab-tryit').style.display = paidSurface ? 'none' : '';
  $('composer-heading').textContent = paidSurface ? 'Create' : 'Try It';
  $('composer-badge').style.display = paidSurface ? '' : 'none';
  $('composer-note').textContent = paidSurface
    ? 'The full studio — every model, custom sound, longer clips. Nothing in here is free.'
    : 'Try It is the free plan: Wan 2.2, Draft lane, watermarked. Create, the full studio, opens with any payment.';
  genLabel(null);
  const inc = u.plan === 'creator'
    ? ('Included production time: ' + Math.max(0, u.included_seconds_total - u.included_seconds_used).toFixed(0) + 's left of ' + u.included_seconds_total + 's. ')
    : (u.paid_customer
      ? 'Pay per video: you pay per clip from your balance, and every paid feature is unlocked. '
      : ('Free clips: ' + u.free_jobs_used + ' of ' + u.free_jobs_limit + ' used, total. The free plan never refills. '));
  $('account').textContent = 'Plan: ' + u.plan + ' · ' + (paidSurface ? 'Create' : 'Try It') + '. ' + inc +
    'Prepaid balance: $' + (u.balance_cents / 100).toFixed(2) + '.' +
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
    $('segments-note').textContent = 'Multi-segment videos unlock with any payment. Free accounts make single Draft clips.';
  }
  buildSegmentsSeg();
  updateModelUI();
  showTab(paidSurface ? 'create' : 'tryit');
  loadJobs();
}
async function fetchQuote() {
  const q = $('quote');
  if (!currentUser || !currentUser.paid_customer) { q.textContent = ''; return; }
  try {
    const r = await api('/api/jobs/quote', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model: $('model').value,
        duration_seconds: Number($('duration').value), resolution: $('resolution').value }) });
    if (r.lane_paused) { q.textContent = 'This model is temporarily unavailable.'; genLabel(null); return; }
    if (!r.ceiling_ok) { q.textContent = 'Exact price: $' + (r.balance_debit_cents / 100).toFixed(2) + '. This job would exceed the total generation your payments cover.'; genLabel(r.balance_debit_cents); return; }
    const p = r.payment || {};
    if (p.method === 'included') {
      q.textContent = 'Exact price: $' + (p.included_debit_cents / 100).toFixed(2) +
        ' of your included budget ($' + (p.included_budget_remaining_cents / 100).toFixed(2) + ' left). Nothing is charged to your balance.';
      genLabel(p.included_debit_cents);
    } else if (p.method === 'balance') {
      q.textContent = 'Exact price: $' + (p.charge_cents / 100).toFixed(2) + ' from your prepaid balance.';
      genLabel(p.charge_cents);
    } else {
      q.textContent = 'Exact price: $' + ((p.charge_cents || 0) / 100).toFixed(2) +
        '. Your included budget is used up and your balance is $' + ((p.balance_cents || 0) / 100).toFixed(2) + ', so top up to run this.';
      genLabel(p.charge_cents || null);
    }
  } catch (e) { q.textContent = ''; }
}
// Per-model length and resolution choices, mirroring what the
// server accepts for each model (it validates again on quote and
// create, so this only keeps the form honest).
const MODEL_OPTS = {
  wan3: { durations: [5, 10, 15, 20, 30], resolutions: ['480p', '720p', '1080p'] },
  wan3_prime: { durations: [5, 10, 15, 20, 30], resolutions: ['480p', '720p', '1080p'] },
  seedance: { durations: [5, 10, 15, 20, 30], resolutions: ['480p', '720p', '1080p'] },
  veo3: { durations: [4, 6, 8], resolutions: ['720p', '1080p'] },
  veo3_fast: { durations: [4, 6, 8], resolutions: ['720p', '1080p'] },
  kling3: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] },
  kling4: { durations: [5, 10, 15, 20, 30], resolutions: ['720p', '1080p'] },
  kling3_omni: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] },
  seedance2: { durations: [5, 10, 15], resolutions: ['480p', '720p', '1080p'] },
  seedance2_fast: { durations: [5, 10, 15], resolutions: ['480p', '720p'] },
  seedance2_mini: { durations: [5, 10, 15], resolutions: ['480p', '720p'] },
  veo3_lite: { durations: [4, 6, 8], resolutions: ['720p', '1080p'] },
  omni12: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] },
  omni11: { durations: [5, 10], resolutions: ['720p', '1080p'] },
  hailuo23: { durations: [6, 10], resolutions: ['720p'] },
  minimax_h3: { durations: [5, 10, 15], resolutions: ['480p', '720p', '1080p'] },
  grok: { durations: [5, 10, 15], resolutions: ['480p', '720p'] },
  luma_ray32: { durations: [5, 10], resolutions: ['480p', '720p', '1080p'] },
  ltx25: { durations: [6, 10, 15, 20], resolutions: ['720p', '1080p'] },
  ltx25_fast: { durations: [6, 10, 15, 20], resolutions: ['720p', '1080p'] },
  happyhorse: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] },
  pika22: { durations: [5, 10], resolutions: ['720p', '1080p'] },
  vidu_q2: { durations: [4, 6, 8], resolutions: ['720p', '1080p'] },
  vidu_q3: { durations: [5, 10, 15], resolutions: ['480p', '720p'] },
  vidu_q4: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] },
  seedance25_flash: { durations: [5, 10, 15, 20, 30], resolutions: ['480p', '720p'] },
  pixverse_v6: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] },
  runway_gen45: { durations: [5, 10], resolutions: ['720p', '1080p'] }
};
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
  buildSegButtons($('segments-seg'), $('segments'), [1, 2, 3, 4, 6, 8, 12],
    v => (v * 5) + 's', updateHousePrice);
}
// The model dropdown is fed from /api/config at runtime (names,
// order, availability), never a hand-written subset: the config is
// the shelf. Picking a model drives the same hidden state the form
// has always read; /api/pricing supplies the lengths and sizes.
let catalogCfg = null;
let pricingData = null;
function moneyC(c) { return '$' + (c / 100).toFixed(2); }
function buildCatalog() {
  const sel = $('model');
  if (!sel || !catalogCfg) return;
  const keep = sel.value || 'wan2.2';
  sel.innerHTML = '';
  for (const m of catalogCfg.models) {
    const o = document.createElement('option');
    o.value = m.id;
    o.textContent = m.label + (m.available ? '' : ' (unavailable)');
    sel.appendChild(o);
  }
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
  applyModelGates();
  updateModelUI();
}
function applyModelGates() {
  const sel = $('model');
  const paid = !!(currentUser && currentUser.paid_customer);
  [...sel.options].forEach(o => { if (o.value !== 'wan2.2') o.disabled = !paid; });
  if (!paid && sel.value !== 'wan2.2' && [...sel.options].some(o => o.value === 'wan2.2')) sel.value = 'wan2.2';
}
// The price of the house-lane job being composed, from the served
// pricing data, shown under the Create button before anything runs.
function updateHousePrice() {
  const el = $('house-price');
  if (!el) return;
  if (!pricingData || !currentUser || $('model').value !== 'wan2.2') { el.textContent = ''; return; }
  const jp = pricingData.job_price_cents || {};
  if (!currentUser.paid_customer) {
    el.textContent = 'Free plan: a Draft video uses one of your free clips.';
    genLabel(null);
    return;
  }
  if (selectedEffect) {
    el.textContent = 'Price: ' + moneyC(jp.effects || 0) +
      ' from your balance for this effect video. Creator members pay from their included budget instead.';
    genLabel(jp.effects || null);
    return;
  }
  const segs = Number($('segments').value) || 1;
  const per = $('lane').value === 'production' ? (jp.production || 0) : (jp.draft || 0);
  el.textContent = 'Price: ' + moneyC(per * segs) + ' from your balance for ' +
    segs + (segs === 1 ? ' segment' : ' segments') + ' at ' + moneyC(per) +
    ' per segment. Creator members pay from their included budget instead.';
  genLabel(per * segs);
}
function updateModelUI() {
  const m = $('model').value;
  const api = m !== 'wan2.2';
  $('length-api').style.display = api ? '' : 'none';
  $('length-house').style.display = api ? 'none' : '';
  $('lane-wrap').style.display = api ? 'none' : '';
  $('house-more').style.display = api ? 'none' : '';
  $('resolution-wrap').style.display = api ? '' : 'none';
  $('api-opts').style.display = api ? '' : 'none';
  if (api) {
    const opts = MODEL_OPTS[m];
    if (opts) {
      fillSelect($('duration'), opts.durations, v => v + ' seconds');
      buildSegButtons($('duration-seg'), $('duration'), opts.durations, v => v + 's', fetchQuote);
      const lane = pricingData ? (pricingData.api_lanes || {})[m] : null;
      const res = lane ? Object.keys(lane.balance_debit_cents_per_second) : opts.resolutions;
      fillSelect($('resolution'), res.length ? res : opts.resolutions, v => v);
    }
    fetchQuote();
  }
  applyModelGates();
  updateHousePrice();
}
$('model').addEventListener('change', updateModelUI);
$('duration').addEventListener('change', fetchQuote);
$('resolution').addEventListener('change', fetchQuote);
$('lane').addEventListener('change', updateHousePrice);
$('segments').addEventListener('change', updateHousePrice);
$('save-handle').onclick = async () => {
  try {
    const r = await api('/api/me/handle', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ handle: $('handle').value }) });
    msg('profile-msg', 'Handle saved: ' + r.handle, 'ok');
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
document.querySelectorAll('[data-topup]').forEach(b => b.onclick = async () => {
  try {
    const r = await api('/api/checkout/topup', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ pack_cents: Number(b.dataset.topup) }) });
    location.href = r.url;
  } catch (e) { msg('pay-msg', e.message, 'err'); }
});
$('create').onclick = async () => {
  try {
    msg('job-msg', 'Working on the bot check...');
    const bot = await botFields('botcheck2');
    const model = $('model').value;
    const camera = { camera_presets: selectedPresets, camera_look: selectedLook() };
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
    const jobBody = model === 'wan2.2'
      ? { model: model, lane: selectedEffect ? 'effects' : $('lane').value, prompt: $('prompt').value,
          music_prompt: $('music').disabled ? '' : $('music').value,
          speech_script: $('speech').disabled ? '' : $('speech').value,
          effect: selectedEffect || undefined,
          ...startFields, end_image_png_b64: selectedEffect ? null : endImageB64,
          aspect_ratio: $('aspect').value, segments: selectedEffect ? 1 : Number($('segments').value),
          ...(selectedEffect ? {} : camera), ...detailFields, ...bot }
      : { model: model, prompt: $('prompt').value,
          music_prompt: $('music').disabled ? '' : $('music').value,
          speech_script: $('speech').disabled ? '' : $('speech').value,
          duration_seconds: Number($('duration').value), resolution: $('resolution').value,
          ...startFields, ...camera, ...detailFields, ...bot };
    const r = await api('/api/jobs', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(jobBody) });
    msg('job-msg', model === 'wan2.2'
      ? 'Job queued. Draft renders take roughly 6 to 13 minutes; production about 25.'
      : 'Job queued. This model usually finishes in a few minutes.', 'ok');
    loadJobs();
  } catch (e) { msg('job-msg', e.message, 'err'); }
};
function costLine(j) {
  if (j.kind === 'clip_output' || j.status === 'error') return '';
  const bal = j.charged_cents || 0;
  const inc = j.included_cost_cents || 0;
  if (bal > 0) return '<br><span class="muted">Cost: $' + (bal / 100).toFixed(2) + ' from your balance</span>';
  if (inc > 0) return '<br><span class="muted">Cost: $' + (inc / 100).toFixed(2) + ' of your included budget</span>';
  if (j.free_tier) return '<br><span class="muted">Cost: free clip</span>';
  return '';
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
      if (j.aspect_ratio && j.model_id === 'wan2.2') html += '<br><span class="muted">Aspect: ' + j.aspect_ratio + '</span>';
      if (j.segments_total > 1) html += '<br><span class="muted">Segment ' + Math.min(j.segments_done + (j.status === 'done' ? 0 : 1), j.segments_total) + ' of ' + j.segments_total + (j.status === 'done' ? ' joined into one video' : '') + '</span>';
      if ((j.status === 'queued' || j.status === 'running') && j.queue_position) html += '<br><span class="muted">In flight: #' + j.queue_position + ' of your running videos</span>';
      if (j.partial) html += '<br><span class="muted">Finished shorter than planned: the later segments could not render, and you were charged only for the segments in this file.</span>';
      if (isClipOut && j.score !== null && j.score !== undefined) html += '<br><span class="muted">Score: ' + j.score + '</span>';
      if (isClipOut && j.has_thumb) html += '<br><img class="thumb" src="/api/jobs/' + j.id + '/thumb" alt="">';
      if (isClipJob && j.stage && (j.status === 'queued' || j.status === 'running')) html += '<br><span class="muted">' + j.stage + '</span>';
      if (j.camera_presets && j.camera_presets.length) html +=
        '<br><span class="muted">Camera: ' + j.camera_presets.join(' \u2192 ') + '</span>';
      if (j.effect) html +=
        '<br><span class="muted">Effect: ' + j.effect + '</span>';
      if (j.camera_look && Object.keys(j.camera_look).length) html +=
        '<br><span class="muted">Look: ' + Object.values(j.camera_look).join(' · ') + '</span>';
      if (j.has_custom_audio) html +=
        '<br><span class="muted">Music: made for this video from its music prompt</span>';
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
    if (j.score !== null && j.score !== undefined) h += ' · Score: ' + j.score;
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
    msg('clip-msg', q.title ? 'Found: ' + q.title : '', '');
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
    msg('clip-msg', 'Clipping started. Stages: Getting the video, Listening and transcribing, Picking the moments, Cutting your clips.', 'ok');
    loadJobs();
  } catch (e) { msg('clip-msg', e.message, 'err'); }
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
</body></html>"""
)


GALLERY_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory gallery</title>"""
    + _CSS
    + """<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23818cf8' d='M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z'/%3E%3Cpath fill='%23f5d67a' stroke='%23f5d67a' stroke-width='1.6' stroke-linejoin='round' d='M24 18.5L24 27.5L31.5 23Z'/%3E%3C/svg%3E">
</head><body><main>
<header>
  <a class="logo" href="/"><svg width="27" height="27" viewBox="0 0 48 48" aria-hidden="true"><path fill="#818cf8" d="M23.3 8A16 16 0 1 0 32.6 37.5A16.5 16.5 0 0 1 23.3 8Z"/><path fill="#f5d67a" stroke="#f5d67a" stroke-width="1.6" stroke-linejoin="round" d="M24 18.5L24 27.5L31.5 23Z"/></svg><span>Moon<em>factory</em></span></a>
  <nav><a href="/app">Studio</a></nav>
</header>
<div style="max-width:880px;margin:56px auto 22px">
  <h2>Community gallery</h2>
  <p class="muted">Made with Moonfactory, shared by their creators under
  their handles only. Clips are public by default; paying customers can
  opt out in the studio. If a clip should not be here, use its Report
  button and it will be reviewed.</p>
</div>
<div class="grid" id="items"><p class="muted">Loading...</p></div>
</main>
<footer><span>Moonfactory</span><span><a href="/app">Studio</a></span></footer>
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
      const video = document.createElement('video');
      video.controls = true; video.src = item.file_url; video.preload = 'metadata';
      f.appendChild(video);
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
</body></html>"""
)
