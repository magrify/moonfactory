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
]
MODEL_LABELS = {m["id"]: m["label"] for m in MODELS}
DEFAULT_MODEL = "wan2.2"
PER_SECOND_MODELS = ("wan3", "wan3_prime", "seedance", "veo3", "veo3_fast", "kling3")
# Which supplier serves each per-second model. Internal only: the
# picker, the pricing page, and every error show model names, never
# suppliers (Amendment 1). Seedance 2.5's primary supplier is the
# one whose per-second rate makes the lane undercut-capable under
# Amendment 3; its fal endpoint stays in FALLBACK notes in
# pricing.py, not wired, because fal charges over twice the rate
# and offers no 1080p tier for this model.
MODEL_PROVIDER = {
    "wan3": "fal",
    "wan3_prime": "fal",
    "veo3": "fal",
    "veo3_fast": "fal",
    "kling3": "fal",
    "seedance": "beatapi",
}
# Per-model duration bounds in seconds (the global form range is
# 3-30; these narrow it to what each model actually accepts).
MODEL_DURATION_BOUNDS = {
    "seedance": (4, 30),
    "veo3": (4, 8),
    "veo3_fast": (4, 8),
    "kling3": (3, 15),
}
# Veo generates only these clip lengths; anything else is refused
# at validation instead of being silently rounded by the provider.
MODEL_DURATION_STEPS = {
    "veo3": (4, 6, 8),
    "veo3_fast": (4, 6, 8),
}
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
}


def fal_endpoint_for(model: str, mode: str, resolution: str) -> str:
    entry = FAL_ENDPOINTS[(model, mode)]
    if isinstance(entry, dict):
        return entry[resolution]
    return entry


# Second supplier (Seedance 2.5): an async task API. Create returns
# a task id; the platform polls the task on the same cadence as the
# fal lanes until it succeeds or fails, then downloads the result
# from the task's output. The supplier also offers webhooks, but a
# task stopped by a processing timeout sends no event, so polling
# is the contract's reliable path and the only one wired.
BEATAPI_BASE = "https://api.beatapi.io"
BEATAPI_MODEL_IDS = {"seedance": "seedance-2.5"}
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
# (mechanism "workflow_patch", status "needs_model") wait on a model
# download: the picker shows them as coming soon and the server
# refuses them, never silently drops them. Presets are not a paid
# feature; free accounts may use them on their Draft lane.
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
    """Selectable today: a prompt recipe that does not wait on a
    model. Everything else shows in the picker as coming soon."""
    return (
        preset.get("mechanism") == "prompt"
        and preset.get("status") != "needs_model"
    )


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
        key_env = "BEATAPI_KEY" if provider == "beatapi" else "FAL_KEY"
        configured = bool(os.environ.get(key_env))
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
    def landing():
        return HTMLResponse(INDEX_HTML)

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
            "models": [
                {
                    "id": m["id"],
                    "label": m["label"],
                    "paid_only": m["paid_only"],
                    "available": (
                        True if m["id"] == DEFAULT_MODEL
                        else not api_lane_state(m["id"])["paused"]
                    ),
                }
                for m in MODELS
            ],
        }

    @web.get("/api/pricing")
    def get_pricing():
        data = pricing.public_pricing()
        table = rate_table()
        lanes = {}
        for model in PER_SECOND_MODELS:
            state = api_lane_state(model)
            frontier = {
                res: pricing.frontier_lane(model, res, table["rates"])
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
                "paused": state["paused"],
                "rates_updated_at": state["rates_updated_at"],
            }
        data["clipping"] = {
            "included_cents_per_source_minute": pricing.CLIP_INCLUDED_CENTS_PER_MINUTE,
            "balance_cents_per_source_minute": pricing.CLIP_BALANCE_CENTS_PER_MINUTE,
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
                # never above the hold the user saw.
                debit = pricing.per_second_balance_debit_cents(
                    job["model"], job["resolution"], billed, table["rates"]
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

    def mirror_status(job: dict) -> dict:
        if job.get("provider") == "clip":
            return job
        if job.get("provider") == "fal":
            return mirror_fal_status(job)
        if job.get("provider") == "beatapi":
            return mirror_beatapi_status(job)
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
                            per_seg = pricing.JOB_PRICE_CENTS[job["lane"]]
                            refund = per_seg * missing
                            owner = users.get(f"id:{job['user_id']}")
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
                "Clip" if job.get("kind") in ("clip_job", "clip_output")
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
            "status": job["status"],
            "error": job.get("error"),
            "created_at": job["created_at"],
            "charged_cents": job.get("charged_cents", 0),
            "has_file": job["status"] == "done",
            "has_custom_audio": bool(job.get("music_prompt")),
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
        if model in ("veo3", "veo3_fast") and (ref_videos or ref_audios):
            raise HTTPException(
                status_code=422,
                detail="this model takes image references only",
            )
        if ref_images or ref_videos or ref_audios:
            mode = "reference"
        elif start_image:
            mode = "image"
        else:
            mode = "text"
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
        est = pricing.per_second_estimate(
            model, params["resolution"], params["duration"],
            params["reference_video_seconds"], rate_table()["rates"],
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
            "job_price_cents": pricing.JOB_PRICE_CENTS,
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
        if model in ("veo3", "veo3_fast"):
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
        elif model == "kling3":
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
        """Create one task on the Seedance supplier. The price is
        reserved from the supplier balance at creation; a failed
        task is refunded there in full, and any refusal here (a
        $0 balance answers 402) fails the job and releases the
        platform hold, exactly like the fal lanes."""
        payload = {
            "model": BEATAPI_MODEL_IDS[job["model"]],
            "prompt": prompt,
            "duration": params["duration"],
            "resolution": params["resolution"],
            "aspect_ratio": "9:16",
            "generate_audio": True,
        }
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

    def create_api_job(user: dict, body: dict, model: str, base: str,
                       prompt: str, music_prompt: str, camera: dict = None):
        """Quote -> hold -> submit, per design s.3. Any failure at or
        before submission leaves an error job with the clean public
        message and the hold fully released: no charge, nothing
        stuck."""
        params = _api_job_params(body, model)
        state = api_lane_state(model)
        if state["paused"]:
            raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)
        est = pricing.per_second_estimate(
            model, params["resolution"], params["duration"],
            params["reference_video_seconds"], rate_table()["rates"],
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
            "provider": MODEL_PROVIDER.get(model, "fal"),
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
        if job["provider"] == "beatapi":
            submit_beatapi_job(job, body, params, prompt)
        else:
            submit_fal_job(job, body, params, prompt, base)
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
        # Camera presets (available to every plan; model switching
        # stays the paid gate, presets are not one). The final
        # prompt sent to any lane is the composed one; the record
        # keeps the user's original prompt and the preset names.
        try:
            camera_ids, camera_look = validate_camera_selection(body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
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
        try:
            _validate_image_b64(start_image, "start image")
            _validate_image_b64(end_image, "end image")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
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
                camera=camera,
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
            if user["plan"] == "creator" and included_budget_remaining(user) >= pricing.MEASURED_COST_CENTS["production"] * segments:
                used_included = SEGMENT_SECONDS * segments
                used_budget = float(pricing.MEASURED_COST_CENTS["production"]) * segments
            else:
                price = pricing.JOB_PRICE_CENTS[lane] * segments
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
            "music_prompt": parent.get("music_prompt"),
            "model": parent.get("model") or DEFAULT_MODEL,
            "duration_seconds": parent.get("duration_seconds"),
            "resolution": parent.get("resolution"),
            "reference_video_seconds": parent.get("reference_video_seconds"),
            "reference_image_urls": parent.get("reference_image_urls"),
            "reference_video_urls": parent.get("reference_video_urls"),
            "reference_audio_urls": parent.get("reference_audio_urls"),
            "segments": parent.get("segments_total") or 1,
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
                if job.get("provider") in ("fal", "beatapi"):
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
            if job.get("provider") in ("fal", "beatapi"):
                bed = _bed_for_job(job) if job.get("music_prompt") else None
                try:
                    _process_api_file(raw_path, out_path, bed_path=bed)
                except Exception:
                    # Delivery never breaks on the finishing stage:
                    # fall back to the standard mux with a bed.
                    job["audio_fallback"] = True
                    jobs[job["id"]] = job
                    _process_file(raw_path, out_path, watermark=False,
                                  bed_path=_bed_for_job(job))
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
                              bed_path=_bed_for_job(job))
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
        q = pricing.clip_quote(duration_seconds)
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
                raise HTTPException(status_code=502, detail="that link could not be read")
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
                raise HTTPException(status_code=502, detail="that link could not be read")
            duration = float(info.get("duration") or 0)
            source_title = info.get("title") or ""
            if duration <= 0:
                raise HTTPException(status_code=422, detail="that link has no readable duration")
        else:
            raise HTTPException(status_code=422, detail="paste a link or upload a file")
        if duration > pricing.CLIP_MAX_SOURCE_SECONDS:
            raise HTTPException(status_code=422, detail="this video is longer than the 3 hour clipping limit")
        q = pricing.clip_quote(duration)
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
                else MODEL_LABELS.get(job.get("model") or DEFAULT_MODEL, "Wan 2.2")
            ),
            "prompt": (job.get("prompt") or "")[:200],
            "camera_presets": job.get("camera_preset_names") or [],
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


def _process_file(raw_path: Path, out_path: Path, watermark: bool, bed_path=None):
    """Mux a music bed onto the video (CPU): the job's custom bed
    when one is passed, else the house bed. Free-tier files also get
    the watermark burned in, which needs a re-encode."""
    if bed_path is None:
        bed_path = BED_PATH
    bed = bed_path if bed_path and Path(bed_path).exists() else None
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


def _process_api_file(raw_path: Path, out_path: Path, bed_path=None):
    """Finishing for per-second-lane outputs (CPU). The model's own
    sound is the delivered audio, loudness-normalized to the house
    standard (about -14 LUFS / -1 dBTP); nothing replaces it. When
    the job ordered a custom bed, the bed is mixed under the model's
    sound. Paid lanes are never watermarked. The optional GPU
    upscale pass is not part of this lane yet (design s.3 step 6
    lists it as optional); the file ships at the resolution the
    user picked."""
    if bed_path and Path(bed_path).exists():
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_path), "-i", str(bed_path),
            "-filter_complex",
            "[0:a]loudnorm=I=-14:TP=-1:LRA=11[na];"
            "[1:a]volume=0.35[bed];"
            "[na][bed]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-shortest", str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_path),
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-af", "loudnorm=I=-14:TP=-1:LRA=11",
            "-shortest", str(out_path),
        ]
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
    two Wan pages and the Seedance page on its supplier's site.
    The Veo and Kling pages are read opportunistically (their
    prices update when the pages parse, and the weekly margin
    report is the backstop until they do). Runs on CPU; no key
    needed, the pages are public."""
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

    fal_pages = {
        "wan3": [("alibaba/wan-3.0/text-to-video", pricing.RESOLUTIONS)],
        "wan3_prime": [("alibaba/wan-3.0-prime/text-to-video", pricing.RESOLUTIONS)],
        "veo3": [("fal-ai/veo3.1", ("720p", "1080p"))],
        "veo3_fast": [("fal-ai/veo3.1/fast", ("720p", "1080p"))],
        "kling3": [
            ("fal-ai/kling-video/v3/standard/text-to-video", ("720p",)),
            ("fal-ai/kling-video/v3/pro/text-to-video", ("1080p",)),
        ],
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

    # Seedance 2.5's supplier publishes per-clip prices for 8-second
    # clips on the model's own page; per-second is price / 8. The
    # rows used are the no-video-input ones, the same basis as the
    # seeded table. The page carries them as structured-data offers
    # ("name": "... 8 sec · 720p · no video input", "price": 1.5633)
    # and as a rendered table; try both shapes.
    try:
        resp = _requests.get(
            "https://beatapi.io/seedance-2.5-api",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        if resp.status_code == 200:
            text = resp.text.replace("\\u00b7", "·").replace("\\", "")
            pairs = {}
            for res, price in _re.findall(
                r"8 sec · (480p|720p|1080p) · no video input\",\"url\":\"[^\"]*\",\"price\":(\d+\.\d+)",
                text,
            ):
                pairs[res] = round(float(price) / 8 * 100, 3)
            if len(pairs) < 3:
                pairs = {}
                for res, price in _re.findall(
                    r"8 sec · (480p|720p|1080p) · no video input[^$\d]{0,80}\$(\d+\.\d+)",
                    text,
                ):
                    pairs[res] = round(float(price) / 8 * 100, 3)
            if len(pairs) == 3:
                found["seedance"] = {
                    res: pairs[res] for res in pricing.RESOLUTIONS
                }
    except Exception:
        pass

    required = {"wan3", "wan3_prime", "seedance"}
    if not required.issubset(found):
        return {"verified": sorted(found), "updated": False}
    table = meta.get("provider_rates") or {
        "rates": pricing.PROVIDER_RATES_CENTS_PER_SECOND,
        "updated_at": _now(),
    }
    rates = dict(table.get("rates") or {})
    changed = [m for m, r in found.items() if rates.get(m) != r]
    rates.update(found)
    meta["provider_rates"] = {"rates": rates, "updated_at": _now()}
    return {"verified": sorted(found), "updated": True, "changed": changed}


# ---------------------------------------------------------------------------
# Pages. Plain copy, prices always loaded from /api/pricing.
# ---------------------------------------------------------------------------

_CSS = """
<style>
  body { font-family: system-ui, sans-serif; margin: 0; color: #1a1a1a; background: #fafafa; }
  main { max-width: 880px; margin: 0 auto; padding: 24px 16px 64px; }
  header { display: flex; justify-content: space-between; align-items: baseline; }
  h1 { font-size: 28px; margin: 8px 0; } h2 { font-size: 20px; margin-top: 36px; }
  a { color: #6a1fd0; } .btn { display: inline-block; background: #6a1fd0; color: #fff;
    padding: 10px 18px; border-radius: 8px; text-decoration: none; border: 0; cursor: pointer; font-size: 15px; }
  .btn.secondary { background: #e6e0f5; color: #33135e; }
  .card { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin: 12px 0; }
  input, textarea, select { width: 100%; box-sizing: border-box; padding: 9px; margin: 6px 0 12px;
    border: 1px solid #bbb; border-radius: 6px; font-size: 15px; }
  label { font-weight: 600; font-size: 14px; }
  .muted { color: #555; font-size: 14px; } .err { color: #b00020; } .ok { color: #0a6a2a; }
  video { width: 100%; max-width: 360px; border-radius: 8px; background: #000; }
  table { border-collapse: collapse; } td, th { border: 1px solid #ddd; padding: 8px 10px; text-align: left; }
  .chip { display: inline-block; border: 1px solid #bbb; background: #fff; color: #1a1a1a;
    border-radius: 999px; padding: 5px 12px; cursor: pointer; font-size: 13px; margin: 0 6px 6px 0; width: auto; }
  .chip.on { background: #6a1fd0; color: #fff; border-color: #6a1fd0; }
  .chip:disabled { opacity: .45; cursor: default; }
  #looks { display: flex; flex-wrap: wrap; gap: 8px 14px; margin-bottom: 12px; }
  #looks label { font-weight: 400; font-size: 13px; }
  #looks select { width: auto; margin: 2px 0 0; display: block; }
</style>
"""

INDEX_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory: AI video generation for creators</title>"""
    + _CSS
    + """</head><body><main>
<header><strong>Moonfactory</strong><span><a href="/gallery">Gallery</a> &nbsp; <a href="/app">Open the studio</a></span></header>
<h1>Turn a still image into a short video, with music.</h1>
<p>Moonfactory takes a start image and a prompt and renders a vertical
short video with a music track included. It is built for TikTok, Reels,
and X: 9:16, captioned by you, ready to post.</p>
<p><a class="btn" href="/app">Start creating</a></p>

<h2>How it works</h2>
<ol>
  <li>Bring a start image, or just describe the scene for a text-only video on the Draft lane.</li>
  <li>Write the prompt: what moves, where the camera goes, the mood.</li>
  <li>Pick a model and render. Every account starts on Wan 2.2;
      paying customers can switch models in the studio.</li>
</ol>

<h2>Models</h2>
<table>
  <tr><th>Model</th><th>What you get</th><th>Who can use it</th></tr>
  <tr><td>Wan 2.2</td><td>Draft previews and Production renders, about 5 seconds per segment, in 9:16, 16:9 or 1:1. Paying customers can join up to 12 segments into one video of about a minute and steer Production renders to a chosen end frame.</td><td>Everyone; Production is Creator plan or balance</td></tr>
  <tr><td>Wan 3.0</td><td>Sharper motion and detail, sound made with the picture, up to 30 seconds in one pass</td><td>Any paying customer</td></tr>
  <tr><td>Wan 3.0 Prime</td><td>The flagship Wan tier: the best motion and detail of the Wan family</td><td>Any paying customer</td></tr>
  <tr><td>Seedance 2.5</td><td>Long takes up to 30 seconds with sound made with the picture, at up to 1080p</td><td>Any paying customer</td></tr>
  <tr><td>Veo 3.1</td><td>Cinematic shots with sound, 4 to 8 seconds, at 720p or 1080p</td><td>Any paying customer</td></tr>
  <tr><td>Veo 3.1 Fast</td><td>The quick Veo tier: same shots, faster and cheaper, 4 to 8 seconds</td><td>Any paying customer</td></tr>
  <tr><td>Kling 3.0</td><td>Cinematic motion with sound, up to 15 seconds, at 720p or 1080p</td><td>Any paying customer</td></tr>
</table>
<p class="muted">Every model beyond Wan 2.2 is priced per second.
The exact price for your video is always shown before it runs.
Creator members can pay from their included monthly budget;
everyone else pays per video from prepaid balance. Nothing ever
runs at a price you have not seen first.</p>

<h2>Pricing</h2>
<p>Two ways to pay, side by side. <strong>Pay per video:</strong>
top up a prepaid balance and pay per clip, no subscription.
<strong>Creator:</strong> a monthly plan with an included
generation budget, plus top-ups whenever you want more. Any
payment, either way, unlocks every model, custom music, and
hiding your clips from the gallery.</p>
<div id="pricing"><p class="muted">Loading prices...</p></div>
<p class="muted">Wan 2.2 prices come from one formula: worst-case
measured generation cost, times three, with card fees included.
The per-second models are priced against what the
big credit platforms effectively charge for the same clip, and
never below our own cost plus a margin, so no video ever runs at
a loss. If our costs drop, the included time goes up on its own.</p>

<h2>Free plan, honestly</h2>
<p>The free plan is small on purpose: <span id="free-limit">5</span> Draft
clips per account, total. Not per month and never resetting: five,
ever, watermarked, lowest queue priority. Each free clip costs real
GPU time, so the whole platform also has a hard total budget for free
work; when it is used up, free jobs pause. Paid work is never crowded
out by free work.</p>

<h2>Gallery</h2>
<p>Finished clips show in the <a href="/gallery">public gallery</a>
under the creator's handle, never an email address. Paying
customers can opt out in the studio; clips from accounts that have
never paid are always listed. Every
gallery item has a report button, and anything reported gets
reviewed and removed when it should not be there.</p>

<h2>FAQ</h2>
<p><strong>Does every video have sound?</strong> Yes. A music track is
mixed onto every finished video. Silent output is not a thing here.
Paying customers can also describe the music they want and have it
generated for that video; other clips use the house track.</p>
<p><strong>Can I use the videos commercially?</strong> Yes, on either
paid path: the Creator plan or pay per video. Free clips are for
trying things out.</p>
<p><strong>What if a render fails?</strong> If the failure is on our
side, the charge goes back to your balance automatically and you can
re-run it.</p>
<p><strong>Do unused plan minutes roll over?</strong> No. Included time
resets each month, which is part of how the price stays at $20.</p>

<p style="margin-top:40px"><a class="btn" href="/app">Open the studio</a></p>
</main>
<script>
fetch('/api/pricing').then(r => r.json()).then(p => {
  const secs = p.creator_included_production_seconds;
  let perSecond = '';
  if (p.api_lanes) {
    const order = ['wan3', 'wan3_prime', 'seedance', 'veo3', 'veo3_fast', 'kling3'];
    const parts = [];
    for (const id of order) {
      const lane = p.api_lanes[id];
      if (lane && lane.balance_debit_cents_per_second['720p'] != null) {
        parts.push(lane.label + ' $' +
          (lane.balance_debit_cents_per_second['720p']/100).toFixed(2));
      }
    }
    if (parts.length) {
      perSecond = ' Per-second models at 720p, per second: ' + parts.join(', ') +
        '. The exact price for your length and size is shown before the job runs.';
    }
  }
  let html =
    '<div class="card"><strong>Pay per video</strong>: no subscription. Top up $5 / $10 / $20 of prepaid balance and pay per clip. ' +
    'A Draft clip costs $' + (p.job_price_cents.draft/100).toFixed(2) +
    ' and a Production render $' + (p.job_price_cents.production/100).toFixed(2) + '.' +
    perSecond + '</div>' +
    '<div class="card"><strong>Creator</strong>: $' + (p.creator_price_cents/100).toFixed(2) +
    '/month. An included monthly budget, about ' + secs +
    ' seconds of Production time at current generation costs, and the same budget also pays for seconds on the per-second models. ' +
    'Top-ups work on top of the plan whenever you want more.</div>' +
    '<div class="card"><strong>Free</strong>: $0. Wan 2.2 Draft lane, 5 clips per account total, watermarked.</div>';
  document.getElementById('pricing').innerHTML = html;
});
</script>
</body></html>"""
)

APP_HTML = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moonfactory studio</title>"""
    + _CSS
    + """<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</head><body><main>
<header><strong><a href="/" style="color:#1a1a1a;text-decoration:none">Moonfactory</a></strong>
<span id="who"></span></header>

<div id="auth" class="card">
  <h2 style="margin-top:0">Sign in or create an account</h2>
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
  <div style="margin-bottom:14px">
    <button class="btn" id="tab-create">Create</button>
    <button class="btn secondary" id="tab-clip">Clip</button>
  </div>
  <div class="card">
    <h2 style="margin-top:0">Your account</h2>
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

  <div id="create-pane">
  <div class="card">
    <h2 style="margin-top:0">New video</h2>
    <label>Model</label>
    <select id="model">
      <option value="wan2.2">Wan 2.2</option>
      <option value="wan3">Wan 3.0</option>
      <option value="wan3_prime">Wan 3.0 Prime</option>
      <option value="seedance">Seedance 2.5</option>
      <option value="veo3">Veo 3.1</option>
      <option value="veo3_fast">Veo 3.1 Fast</option>
      <option value="kling3">Kling 3.0</option>
    </select>
    <p class="muted" id="model-note"></p>
    <div id="lane-wrap">
    <label>Lane</label>
    <select id="lane">
      <option value="draft">Draft: fast preview, about 5 seconds</option>
      <option value="production">Production: 720x1280, Creator plan or balance</option>
    </select>
    <p class="muted" id="lane-note"></p>
    </div>
    <div id="api-opts" style="display:none">
    <label>Length</label>
    <select id="duration">
      <option value="5">5 seconds</option>
      <option value="10">10 seconds</option>
      <option value="15">15 seconds</option>
      <option value="20">20 seconds</option>
      <option value="30">30 seconds</option>
    </select>
    <label>Resolution</label>
    <select id="resolution">
      <option value="480p">480p</option>
      <option value="720p" selected>720p</option>
      <option value="1080p">1080p</option>
    </select>
    <p class="muted" id="quote"></p>
    </div>
    <label>Prompt</label>
    <textarea id="prompt" rows="3" placeholder="a red hot air balloon rising over green hills, gentle forward camera movement, bright daylight"></textarea>
    <label>Camera moves (optional, up to 3, applied in the order you pick them)</label>
    <div id="presets"><p class="muted">Loading camera presets...</p></div>
    <p class="muted" id="preset-chain"></p>
    <label>Film look (optional)</label>
    <div id="looks"></div>
    <label>Music prompt (optional)</label>
    <textarea id="music" rows="2" maxlength="300" placeholder="lo-fi piano with soft rain, slow and dreamy"></textarea>
    <p class="muted" id="music-note"></p>
    <div id="house-opts">
    <label>Aspect ratio</label>
    <select id="aspect">
      <option value="9:16" selected>9:16 vertical</option>
      <option value="16:9">16:9 wide</option>
      <option value="1:1">1:1 square</option>
    </select>
    <label>Length (segments of about 5 seconds, joined into one video)</label>
    <select id="segments">
      <option value="1" selected>1 segment, about 5 seconds</option>
      <option value="2">2 segments, about 10 seconds</option>
      <option value="3">3 segments, about 15 seconds</option>
      <option value="4">4 segments, about 20 seconds</option>
      <option value="6">6 segments, about 30 seconds</option>
      <option value="8">8 segments, about 40 seconds</option>
      <option value="12">12 segments, about 60 seconds</option>
    </select>
    <p class="muted" id="segments-note"></p>
    <label>End image (PNG, optional, Production lane: the video steers to this final frame)</label>
    <input id="endimg" type="file" accept="image/png">
    </div>
    <label>Start image (PNG, optional on Draft: leave empty for a text-only video)</label>
    <input id="startimg" type="file" accept="image/png">
    <div id="botcheck2"></div>
    <button class="btn" id="create">Create video</button>
    <p id="job-msg"></p>
  </div>

  </div>
  </div>

  <div id="clip-pane" style="display:none">
  <div class="card">
    <h2 style="margin-top:0">Turn one long video into clips that are ready to post.</h2>
    <p class="muted">Paste a YouTube, Twitch or Kick link, or upload a file. You get vertical clips with captions and a hook on each one.</p>
    <label>Video link</label>
    <input id="clip-url" type="url" placeholder="https://www.youtube.com/watch?v=...">
    <label>Or upload a video file</label>
    <input id="clip-file" type="file" accept="video/*">
    <label>How many clips</label>
    <select id="clip-count">
      <option value="1">1</option>
      <option value="2">2</option>
      <option value="3" selected>3</option>
      <option value="4">4</option>
      <option value="5">5</option>
      <option value="6">6</option>
    </select>
    <label>Clip length</label>
    <select id="clip-length">
      <option value="short">Short</option>
      <option value="medium" selected>Medium</option>
      <option value="long">Long</option>
    </select>
    <label>Caption style</label>
    <select id="clip-style">
      <option value="bounce">Bounce</option>
      <option value="hype">Hype</option>
      <option value="clean">Clean</option>
    </select>
    <label>Layout</label>
    <select id="clip-layout">
      <option value="blur">Fill with blur</option>
      <option value="zoom">Zoom</option>
      <option value="crop">Crop</option>
    </select>
    <p id="clip-charge" class="muted"></p>
    <div id="botcheck3"></div>
    <button class="btn" id="make-clips">Make clips</button>
    <p id="clip-msg"></p>
    <p class="muted">Clip only content you own or have permission to use. Clipping is a paid feature: Creator plans use included clipping minutes, or pay 5 cents per source minute from your balance.</p>
  </div>
  <div id="clip-results"><p class="muted">No clips yet. Paste a link above and your clips will show up here.</p></div>
  </div>

  <h2>Your videos</h2>
  <div id="jobs"><p class="muted">Loading...</p></div>
</div>
</main>
<script>
const $ = id => document.getElementById(id);
let cfg = { pow_required: true, pow_difficulty_bits: 18, turnstile_site_key: null };
let startImageB64 = null;
let endImageB64 = null;

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

$('startimg').addEventListener('change', e => {
  const f = e.target.files[0]; if (!f) { startImageB64 = null; return; }
  const reader = new FileReader();
  reader.onload = () => { startImageB64 = String(reader.result).split(',')[1]; };
  reader.readAsDataURL(f);
});
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
    ? 'Camera: ' + selectedPresets.map(id => names[id] || id).join(' \\u2192 ')
    : 'No camera move picked: the camera follows your prompt.';
  $('preset-chain').className = 'muted';
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
function showStudio(u) {
  currentUser = u;
  $('auth').style.display = 'none'; $('studio').style.display = 'block';
  $('who').textContent = u.email;
  const inc = u.plan === 'creator'
    ? ('Included production time: ' + Math.max(0, u.included_seconds_total - u.included_seconds_used).toFixed(0) + 's left of ' + u.included_seconds_total + 's. ')
    : (u.paid_customer
      ? 'Pay per video: you pay per clip from your balance, and every paid feature is unlocked. '
      : ('Free clips: ' + u.free_jobs_used + ' of ' + u.free_jobs_limit + ' used, total. The free plan never refills. '));
  $('account').textContent = 'Plan: ' + u.plan + '. ' + inc +
    'Prepaid balance: $' + (u.balance_cents / 100).toFixed(2) + '.' +
    (u.verified ? '' : ' Your email is not verified yet, so creating is off.');
  $('handle').value = u.handle || '';
  const opt = $('gallery-optout');
  const prodOption = document.querySelector('#lane option[value="production"]');
  const paidModels = document.querySelectorAll('#model option:not([value="wan2.2"])');
  if (u.paid_customer) {
    opt.disabled = false; opt.checked = !!u.gallery_opt_out;
    $('gallery-note').textContent = '';
    prodOption.disabled = false;
    $('lane-note').textContent = '';
    paidModels.forEach(o => { o.disabled = false; });
    $('model-note').textContent = '';
    $('music').disabled = false;
    $('music-note').textContent = 'Describe the music and it is generated for this video.';
  } else {
    opt.disabled = true; opt.checked = false;
    $('gallery-note').textContent = 'Clips from accounts that have never paid always show in the gallery. Hiding them unlocks with any payment.';
    prodOption.disabled = true;
    $('lane-note').textContent = '';
    paidModels.forEach(o => { o.disabled = true; });
    $('model').value = 'wan2.2';
    $('model-note').textContent = 'Model switching unlocks with any payment. Accounts that have never paid render on Wan 2.2 Draft.';
    $('music').disabled = true; $('music').value = '';
    $('music-note').textContent = 'Custom audio unlocks with any payment. Free clips get the house music track.';
  }
  const segSel = $('segments');
  if (u.paid_customer) {
    segSel.disabled = false;
    $('segments-note').textContent = 'Longer videos are priced per segment, exactly like separate clips, and the segments join into one finished video.';
  } else {
    segSel.disabled = true; segSel.value = '1';
    $('segments-note').textContent = 'Multi-segment videos unlock with any payment. Free accounts make single Draft clips.';
  }
  updateModelUI();
  loadJobs();
}
async function fetchQuote() {
  const q = $('quote');
  if (!currentUser || !currentUser.paid_customer) { q.textContent = ''; return; }
  try {
    const r = await api('/api/jobs/quote', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ model: $('model').value,
        duration_seconds: Number($('duration').value), resolution: $('resolution').value }) });
    if (r.lane_paused) { q.textContent = 'This model is temporarily unavailable.'; return; }
    if (!r.ceiling_ok) { q.textContent = 'Exact price: $' + (r.balance_debit_cents / 100).toFixed(2) + '. This job would exceed the total generation your payments cover.'; return; }
    const p = r.payment || {};
    if (p.method === 'included') {
      q.textContent = 'Exact price: $' + (p.included_debit_cents / 100).toFixed(2) +
        ' of your included budget ($' + (p.included_budget_remaining_cents / 100).toFixed(2) + ' left). Nothing is charged to your balance.';
    } else if (p.method === 'balance') {
      q.textContent = 'Exact price: $' + (p.charge_cents / 100).toFixed(2) + ' from your prepaid balance.';
    } else {
      q.textContent = 'Exact price: $' + ((p.charge_cents || 0) / 100).toFixed(2) +
        '. Your included budget is used up and your balance is $' + ((p.balance_cents || 0) / 100).toFixed(2) + ', so top up to run this.';
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
  kling3: { durations: [5, 10, 15], resolutions: ['720p', '1080p'] }
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
function updateModelUI() {
  const m = $('model').value;
  const api = m !== 'wan2.2';
  $('lane-wrap').style.display = api ? 'none' : 'block';
  $('house-opts').style.display = api ? 'none' : 'block';
  $('api-opts').style.display = api ? 'block' : 'none';
  if (api) {
    const opts = MODEL_OPTS[m];
    if (opts) {
      fillSelect($('duration'), opts.durations, v => v + ' seconds');
      fillSelect($('resolution'), opts.resolutions, v => v);
    }
    fetchQuote();
  }
}
$('model').addEventListener('change', updateModelUI);
$('duration').addEventListener('change', fetchQuote);
$('resolution').addEventListener('change', fetchQuote);
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
    const jobBody = model === 'wan2.2'
      ? { model: model, lane: $('lane').value, prompt: $('prompt').value,
          music_prompt: $('music').disabled ? '' : $('music').value,
          start_image_png_b64: startImageB64, end_image_png_b64: endImageB64,
          aspect_ratio: $('aspect').value, segments: Number($('segments').value),
          ...camera, ...bot }
      : { model: model, prompt: $('prompt').value,
          music_prompt: $('music').disabled ? '' : $('music').value,
          duration_seconds: Number($('duration').value), resolution: $('resolution').value,
          start_image_png_b64: startImageB64, ...camera, ...bot };
    const r = await api('/api/jobs', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(jobBody) });
    msg('job-msg', model === 'wan2.2'
      ? 'Job queued. Draft renders take roughly 6 to 13 minutes; production about 25.'
      : 'Job queued. This model usually finishes in a few minutes.', 'ok');
    loadJobs();
  } catch (e) { msg('job-msg', e.message, 'err'); }
};
async function loadJobs() {
  try {
    const r = await api('/api/jobs');
    const box = $('jobs');
    if (!r.jobs.length) { box.innerHTML = '<p class="muted">No videos yet.</p>'; return; }
    box.innerHTML = '';
    let active = false;
    for (const j of r.jobs) {
      const div = document.createElement('div'); div.className = 'card';
      const isClipOut = j.kind === 'clip_output';
      const isClipJob = j.kind === 'clip_job';
      const title = isClipOut ? (j.hook || 'Clip') : ((j.model || 'Wan 2.2') + (j.model_id === 'wan2.2' ? ' · ' + j.lane : ''));
      const spec = j.duration_seconds ? ' · ' + j.duration_seconds + 's' + (j.resolution ? ' · ' + j.resolution : '') : '';
      let html = '<strong>' + title + '</strong>' + spec + ' · ' + j.status + '<br><span class="muted">' +
        j.prompt.slice(0, 120) + '</span>';
      if (j.aspect_ratio && j.model_id === 'wan2.2') html += '<br><span class="muted">Aspect: ' + j.aspect_ratio + '</span>';
      if (j.segments_total > 1) html += '<br><span class="muted">Segment ' + Math.min(j.segments_done + (j.status === 'done' ? 0 : 1), j.segments_total) + ' of ' + j.segments_total + (j.status === 'done' ? ' joined into one video' : '') + '</span>';
      if ((j.status === 'queued' || j.status === 'running') && j.queue_position) html += '<br><span class="muted">In flight: #' + j.queue_position + ' of your running videos</span>';
      if (j.partial) html += '<br><span class="muted">Finished shorter than planned: the later segments could not render, and you were charged only for the segments in this file.</span>';
      if (isClipOut && j.score !== null && j.score !== undefined) html += '<br><span class="muted">Score: ' + j.score + '</span>';
      if (isClipOut && j.has_thumb) html += '<br><img src="/api/jobs/' + j.id + '/thumb" style="width:120px;border-radius:8px" alt="">';
      if (isClipJob && j.stage && (j.status === 'queued' || j.status === 'running')) html += '<br><span class="muted">' + j.stage + '</span>';
      if (j.camera_presets && j.camera_presets.length) html +=
        '<br><span class="muted">Camera: ' + j.camera_presets.join(' \\u2192 ') + '</span>';
      if (j.status === 'done') html += '<br><video controls src="/api/jobs/' + j.id + '/file"></video>' +
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
    if (j.has_thumb) h += '<br><img src="/api/jobs/' + j.id + '/thumb" style="width:140px;border-radius:8px" alt="">';
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
  $('create-pane').style.display = which === 'create' ? 'block' : 'none';
  $('clip-pane').style.display = which === 'clip' ? 'block' : 'none';
  $('tab-create').className = which === 'create' ? 'btn' : 'btn secondary';
  $('tab-clip').className = which === 'clip' ? 'btn' : 'btn secondary';
}
$('tab-create').onclick = () => showTab('create');
$('tab-clip').onclick = () => showTab('clip');
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
fetch('/api/config').then(r => r.json()).then(c => { cfg = c; renderBotChecks(); });
loadPresets();
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
    + """</head><body><main>
<header><strong><a href="/" style="color:#1a1a1a;text-decoration:none">Moonfactory</a></strong>
<a href="/app">Open the studio</a></header>
<h1>Community gallery</h1>
<p class="muted">Finished clips from Moonfactory creators, shown under
the creator's handle only. Clips are public by default; paying
customers can opt out in the studio. If a clip should not be here, use
its Report button and it will be reviewed.</p>
<div id="items"><p class="muted">Loading...</p></div>
</main>
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
      const div = document.createElement('div'); div.className = 'card';
      const head = document.createElement('div');
      const who = document.createElement('strong'); who.textContent = item.handle;
      const lane = document.createElement('span'); lane.className = 'muted';
      lane.textContent = ' · ' + (item.model || item.lane);
      head.appendChild(who); head.appendChild(lane);
      if (item.camera_presets && item.camera_presets.length) {
        const cam = document.createElement('span'); cam.className = 'muted';
        cam.textContent = ' · Camera: ' + item.camera_presets.join(', ');
        head.appendChild(cam);
      }
      const desc = document.createElement('p'); desc.className = 'muted';
      desc.textContent = item.prompt;
      const video = document.createElement('video');
      video.controls = true; video.src = item.file_url; video.preload = 'metadata';
      const report = document.createElement('button');
      report.className = 'btn secondary'; report.textContent = 'Report';
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
      div.appendChild(head); div.appendChild(desc);
      div.appendChild(video); div.appendChild(document.createElement('br'));
      div.appendChild(report);
      box.appendChild(div);
    }
  } catch (e) {
    box.innerHTML = '<p class="err">The gallery could not load. Try again in a moment.</p>';
  }
}
loadGallery();
</script>
</body></html>"""
)
