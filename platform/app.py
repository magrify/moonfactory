"""Moonfactory platform: the public web app and API.

One FastAPI service on Modal that serves the landing page and studio,
handles accounts, enforces the business rules (pricing.py + config
below), dispatches generation jobs to the private worker endpoint, and
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

State: Modal Dicts (users, jobs, meta, outbox). Finished files are
cached on the moonfactory-data Volume.
"""

import hashlib
import hmac
import json
import os
import re
import secrets as pysecrets
import subprocess
import sys
import time
from pathlib import Path

import modal

sys.path.insert(0, "/platform")
import pricing  # noqa: E402

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
)

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
# Creator-plan feature gates (Jack, 2026-11-16): custom audio and
# model/lane switching are paid-only, enforced server-side in
# create_job and the settings endpoints below, not just in the UI.
CUSTOM_AUDIO_MAX_CHARS = 300
HANDLE_RE = re.compile(r"[A-Za-z0-9_-]{3,20}")

BED_PATH = Path("/worker-models/audio/music_bed_first.wav")
FILES_DIR = Path("/data/files")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


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
    ],
    volumes={"/worker-models": worker_models, "/data": data_volume},
    timeout=1200,
    max_containers=1,
    scaledown_window=240,
)
@modal.asgi_app()
def api():
    import requests
    from fastapi import Cookie, FastAPI, Header, HTTPException, Request
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
        # included seconds. Free clips are counted on the account's
        # lifetime counter (free_jobs_total) and never reset.
        period = _period()
        u = user.get("usage") or {}
        if u.get("period") != period:
            u = {"period": period, "free_jobs": 0, "included_seconds": 0.0}
            user["usage"] = u
            save_user(user)
        return u

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
        u = usage(user)
        return {
            "email": user["email"],
            "verified": user["verified"],
            "plan": user["plan"],
            "handle": display_handle(user),
            "gallery_opt_out": bool(user.get("gallery_opt_out")),
            "past_due": user.get("past_due", False),
            "balance_cents": user.get("balance_cents", 0),
            "free_jobs_used": free_jobs_total(user),
            "free_jobs_limit": FREE_CLIPS_TOTAL,
            "included_seconds_used": round(u["included_seconds"], 2),
            "included_seconds_total": pricing.creator_included_production_seconds(),
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
        }

    @web.get("/api/pricing")
    def get_pricing():
        return pricing.public_pricing()

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
            "free_jobs_total": 0,
            "usage": {"period": _period(), "free_jobs": 0, "included_seconds": 0.0},
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
        if opt_out and user["plan"] == "free":
            raise HTTPException(
                status_code=403,
                detail="gallery opt-out is a Creator plan feature",
            )
        user["gallery_opt_out"] = opt_out
        save_user(user)
        return {"gallery_opt_out": opt_out}

    # -- jobs ---------------------------------------------------------------

    def mirror_status(job: dict) -> dict:
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
                    if job["status"] == "error" and job.get("charged_cents"):
                        # Rule 8: a failed generation restores the balance.
                        owner = users.get(f"id:{job['user_id']}")
                        if owner and not job.get("refunded"):
                            owner["balance_cents"] = owner.get("balance_cents", 0) + job["charged_cents"]
                            job["refunded"] = True
                            save_user(owner)
            except Exception:
                pass
            job["last_poll"] = _now()
            jobs[job["id"]] = job
        return job

    def public_job(job: dict) -> dict:
        return {
            "id": job["id"],
            "lane": job["lane"],
            "prompt": job["prompt"],
            "status": job["status"],
            "error": job.get("error"),
            "created_at": job["created_at"],
            "charged_cents": job.get("charged_cents", 0),
            "has_file": job["status"] == "done",
            "has_custom_audio": bool(job.get("music_prompt")),
        }

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
        lane = body.get("lane", "draft")
        if lane not in pricing.MEASURED_COST_CENTS:
            raise HTTPException(status_code=422, detail="lane must be draft or production")
        if pricing.lane_paused(lane):
            raise HTTPException(status_code=503, detail="this lane is paused while its costs are reviewed")
        prompt = (body.get("prompt") or "").strip()
        if not prompt or len(prompt) > 2000:
            raise HTTPException(status_code=422, detail="a prompt of up to 2000 characters is required")
        # Custom audio is a Creator-plan feature (Jack, 2026-11-16),
        # enforced here on the server: a free account that submits a
        # music prompt gets a clean rejection before any dispatch.
        music_prompt = (body.get("music_prompt") or "").strip()
        if len(music_prompt) > CUSTOM_AUDIO_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"music prompt must be {CUSTOM_AUDIO_MAX_CHARS} characters or fewer",
            )
        if music_prompt and user["plan"] == "free":
            raise HTTPException(
                status_code=403, detail="custom audio is a Creator plan feature"
            )
        u = usage(user)
        charged = 0
        used_included = 0.0
        is_free_job = False
        if user["plan"] == "free":
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
        else:  # creator
            allowance = pricing.creator_included_production_seconds()
            remaining = allowance - u["included_seconds"]
            if remaining >= SEGMENT_SECONDS:
                used_included = SEGMENT_SECONDS
            else:
                price = pricing.JOB_PRICE_CENTS[lane]
                if user.get("balance_cents", 0) < price:
                    raise HTTPException(
                        status_code=402,
                        detail=f"included time used up; this job costs {price} cents of prepaid balance",
                    )
                charged = price
        try:
            resp = requests.post(
                f"{WORKER_BASE}/jobs",
                headers=_worker_headers(),
                json={
                    "lane": lane,
                    "prompt": prompt,
                    "negative_prompt": body.get("negative_prompt") or None,
                    "seed": body.get("seed"),
                    "start_image_png_b64": body.get("start_image_png_b64") or None,
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
        if charged:
            user["balance_cents"] = user.get("balance_cents", 0) - charged
        user["usage"] = u
        save_user(user)
        job = {
            "id": pysecrets.token_hex(8),
            "user_id": user["id"],
            "worker_job_id": worker_job_id,
            "lane": lane,
            "prompt": prompt,
            "status": "queued",
            "error": None,
            "bytes": None,
            "created_at": _now(),
            "charged_cents": charged,
            "included_seconds": used_included,
            "music_prompt": music_prompt or None,
            "plan_at_job": user["plan"],
            "retry_of": body.get("retry_of"),
            "last_poll": 0,
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
        return {"jobs": [public_job(j) for j in mine[:50]]}

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
            "prompt": parent["prompt"],
            "music_prompt": parent.get("music_prompt"),
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
        prompt get the house bed. Jobs with one (Creator only, gated
        at creation) get an ACE-Step bed generated from the prompt,
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
        """Fetch the worker's silent file if needed and produce the
        delivered file (music muxed, watermark for free-tier jobs)."""
        job_id = job["id"]
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FILES_DIR / f"{job_id}.mp4"
        if not out_path.exists():
            raw_path = FILES_DIR / f"{job_id}.raw.mp4"
            if not raw_path.exists():
                resp = requests.get(
                    f"{WORKER_BASE}/jobs/{job['worker_job_id']}/file",
                    headers=_worker_headers(), timeout=120,
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=502, detail="finished file is not available yet")
                raw_path.write_bytes(resp.content)
            _process_file(raw_path, out_path,
                          watermark=(job.get("plan_at_job") == "free"),
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

    # -- public gallery -----------------------------------------------------
    # Every finished clip is listed by default (Jack, 2026-11-16).
    # Safety shape: only the owner's handle is shown (never an email
    # or real name), no identifying job metadata, every item carries
    # a report control, owners can delete their clips at any time
    # (which removes them here), and an admin can remove any item.
    # Opting out is a Creator-plan feature; free clips always list.

    def gallery_visible(job: dict) -> bool:
        if job.get("status") != "done" or job.get("removed"):
            return False
        owner = users.get(f"id:{job.get('user_id')}")
        if not owner:
            return False
        if owner.get("plan") != "free" and owner.get("gallery_opt_out"):
            return False
        return True

    def public_gallery_item(job: dict) -> dict:
        owner = users.get(f"id:{job.get('user_id')}") or {}
        return {
            "id": job["id"],
            "handle": display_handle(owner),
            "lane": job["lane"],
            "prompt": (job.get("prompt") or "")[:200],
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
        """Which Stripe mode the configured key belongs to."""
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        if key.startswith("sk_live_"):
            return "live"
        if key.startswith("sk_test_"):
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
                if obj.get("mode") == "subscription":
                    user["plan"] = "creator"
                    user["past_due"] = False
                    user["stripe_customer_id"] = obj.get("customer")
                    user["stripe_subscription_id"] = obj.get("subscription")
                    u = user.get("usage") or {}
                    u["included_seconds"] = 0.0
                    user["usage"] = u
                elif obj.get("mode") == "payment":
                    credit = int((obj.get("metadata") or {}).get("credit_cents") or 0)
                    user["balance_cents"] = user.get("balance_cents", 0) + credit
                save_user(user)
        elif etype == "invoice.paid":
            customer = obj.get("customer")
            for key in list(users.keys()):
                if not key.startswith("id:"):
                    continue
                user = users.get(key)
                if user and user.get("stripe_customer_id") == customer:
                    u = user.get("usage") or {}
                    u["included_seconds"] = 0.0
                    user["usage"] = u
                    user["past_due"] = False
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
    audio_in = ["-i", str(bed)] if bed else []
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
  <li>Bring a start image, or use the built-in one.</li>
  <li>Write the prompt: what moves, where the camera goes, the mood.</li>
  <li>Pick a lane and render. Draft is fast and cheap. Production is
      the full quality render at 720p.</li>
</ol>

<h2>Lanes</h2>
<table>
  <tr><th>Lane</th><th>What you get</th><th>Status</th></tr>
  <tr><td>Draft</td><td>Fast preview renders, about 5 seconds each</td><td>Live</td></tr>
  <tr><td>Production</td><td>720x1280 renders for finished posts</td><td>Live, Creator plan</td></tr>
  <tr><td>Prime (Wan 3.0)</td><td>Hosted flagship model, up to 30 seconds in one pass</td><td>Coming later</td></tr>
</table>
<p class="muted">Prime is not switched on yet. When it is, it will be
sold per use at a price shown before the job runs, never bundled.</p>

<h2>Pricing</h2>
<div id="pricing"><p class="muted">Loading prices...</p></div>
<p class="muted">Every price on this page comes from one formula:
worst-case measured generation cost, times three, with card fees
included. If our costs drop, the included time goes up on its own.</p>

<h2>Free plan, honestly</h2>
<p>The free plan is small on purpose: <span id="free-limit">5</span> Draft
clips per account, total. Not per month and never resetting: five,
ever, watermarked, lowest queue priority. Each free clip costs real
GPU time, so the whole platform also has a hard total budget for free
work; when it is used up, free jobs pause. Paid work is never crowded
out by free work.</p>

<h2>Gallery</h2>
<p>Finished clips show in the <a href="/gallery">public gallery</a>
under the creator's handle, never an email address. Creator members
can opt out in the studio; free clips are always listed. Every
gallery item has a report button, and anything reported gets
reviewed and removed when it should not be there.</p>

<h2>FAQ</h2>
<p><strong>Does every video have sound?</strong> Yes. A music track is
mixed onto every finished video. Silent output is not a thing here.
Creator members can also describe the music they want and have it
generated for that video; free clips use the house track.</p>
<p><strong>Can I use the videos commercially?</strong> Yes, on the
Creator plan. Free clips are for trying things out.</p>
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
  document.getElementById('pricing').innerHTML =
    '<div class="card"><strong>Free</strong>: $0. Draft lane, watermarked.</div>' +
    '<div class="card"><strong>Creator</strong>: $' + (p.creator_price_cents/100).toFixed(2) +
    '/month. Production lane, no watermark, and about ' + secs +
    ' seconds of included production time per month at current generation costs.</div>' +
    '<div class="card"><strong>Top-ups</strong>: prepaid credit, $5 / $10 / $20. ' +
    'A Draft clip costs $' + (p.job_price_cents.draft/100).toFixed(2) +
    ' of balance past the included time; a Production render costs $' +
    (p.job_price_cents.production/100).toFixed(2) + '.</div>';
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
  </div>

  <div class="card">
    <h2 style="margin-top:0">New video</h2>
    <label>Lane</label>
    <select id="lane">
      <option value="draft">Draft: fast preview, about 5 seconds</option>
      <option value="production">Production: 720x1280, Creator plan or balance</option>
    </select>
    <p class="muted" id="lane-note"></p>
    <label>Prompt</label>
    <textarea id="prompt" rows="3" placeholder="a red hot air balloon rising over green hills, clouds drifting, gentle forward camera movement"></textarea>
    <label>Music prompt (optional)</label>
    <textarea id="music" rows="2" maxlength="300" placeholder="lo-fi piano with soft rain, slow and dreamy"></textarea>
    <p class="muted" id="music-note"></p>
    <label>Start image (PNG, optional)</label>
    <input id="startimg" type="file" accept="image/png">
    <div id="botcheck2"></div>
    <button class="btn" id="create">Create video</button>
    <p id="job-msg"></p>
  </div>

  <h2>Your videos</h2>
  <div id="jobs"><p class="muted">Loading...</p></div>
</div>
</main>
<script>
const $ = id => document.getElementById(id);
let cfg = { pow_required: true, pow_difficulty_bits: 18, turnstile_site_key: null };
let startImageB64 = null;

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

function showStudio(u) {
  $('auth').style.display = 'none'; $('studio').style.display = 'block';
  $('who').textContent = u.email;
  const inc = u.plan === 'creator'
    ? ('Included production time: ' + Math.max(0, u.included_seconds_total - u.included_seconds_used).toFixed(0) + 's left of ' + u.included_seconds_total + 's. ')
    : ('Free clips: ' + u.free_jobs_used + ' of ' + u.free_jobs_limit + ' used, total. The free plan never refills. ');
  $('account').textContent = 'Plan: ' + u.plan + '. ' + inc +
    'Prepaid balance: $' + (u.balance_cents / 100).toFixed(2) + '.' +
    (u.verified ? '' : ' Your email is not verified yet, so creating is off.');
  $('handle').value = u.handle || '';
  const opt = $('gallery-optout');
  const prodOption = document.querySelector('#lane option[value="production"]');
  if (u.plan === 'creator') {
    opt.disabled = false; opt.checked = !!u.gallery_opt_out;
    $('gallery-note').textContent = '';
    prodOption.disabled = false;
    $('lane-note').textContent = '';
    $('music').disabled = false;
    $('music-note').textContent = 'Describe the music and it is generated for this video.';
  } else {
    opt.disabled = true; opt.checked = false;
    $('gallery-note').textContent = 'Free clips always show in the gallery. Hiding them is a Creator feature.';
    prodOption.disabled = true;
    $('lane-note').textContent = 'Model switching is a Creator feature. Free accounts render on the Draft lane.';
    $('music').disabled = true; $('music').value = '';
    $('music-note').textContent = 'Custom audio is a Creator feature. Free clips get the house music track.';
  }
  loadJobs();
}
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
    const r = await api('/api/jobs', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ lane: $('lane').value, prompt: $('prompt').value,
        music_prompt: $('music').disabled ? '' : $('music').value,
        start_image_png_b64: startImageB64, ...bot }) });
    msg('job-msg', 'Job queued. Draft renders take roughly 6 to 13 minutes; production about 25.', 'ok');
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
      let html = '<strong>' + j.lane + '</strong> · ' + j.status + '<br><span class="muted">' +
        j.prompt.slice(0, 120) + '</span>';
      if (j.status === 'done') html += '<br><video controls src="/api/jobs/' + j.id + '/file"></video>' +
        '<br><a href="/api/jobs/' + j.id + '/file" download>Download</a>';
      if (j.status === 'error') html += '<br><span class="err">' + (j.error || 'failed') + '</span>';
      if (j.status === 'done' || j.status === 'error') html +=
        '<br><button class="btn secondary" data-del="' + j.id + '">Delete</button>';
      if (j.status === 'queued' || j.status === 'running') active = true;
      div.innerHTML = html; box.appendChild(div);
    }
    if (active) setTimeout(loadJobs, 10000);
  } catch (e) { /* signed out */ }
}
$('jobs').addEventListener('click', async e => {
  const b = e.target.closest('[data-del]');
  if (!b) return;
  if (!confirm('Delete this video? It is removed from your studio and from the gallery.')) return;
  try { await api('/api/jobs/' + b.dataset.del, { method: 'DELETE' }); loadJobs(); }
  catch (err) { alert(err.message); }
});
fetch('/api/config').then(r => r.json()).then(c => { cfg = c; renderBotChecks(); });
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
the creator's handle only. Clips are public by default; Creator
members can opt out in the studio. If a clip should not be here, use
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
      lane.textContent = ' · ' + item.lane;
      head.appendChild(who); head.appendChild(lane);
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
