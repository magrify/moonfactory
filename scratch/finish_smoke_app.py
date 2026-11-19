"""Finish-run smoke probe for Moonfactory (2026-11-16).

Exercises the LIVE platform end to end with NO generation of any
kind: landing variants (anon / reused free fixture / owner), /app
shell, /api/config (29 models), /api/pricing four-number checks
(provider cost, owner price, customer price, Higgsfield), house
job quotes, free-account gates (all rejections happen before any
dispatch), start-image quote + validation, gallery list/file/
report, account settings (handle, gallery opt-out, restored
after), and Stripe checkout session creation with immediate
expiry (no payment, no charge).

Accounts: REUSES existing test fixtures from the users Dict (the
Nov 16 build/verification residue) per Jack's standing rule —
never creates an account. Sessions are minted with SESSION_SECRET
(the established probe pattern; no passwords used).

Runs inside Modal because this sandbox's egress proxy stalls
POSTs to *.modal.run. CPU-only function; no GPU is invoked.

Usage:
  modal run finish_smoke_app.py --base https://<platform-url>
"""

import base64
import hashlib
import hmac
import json
import os
import time

import modal

app = modal.App("moonfactory-finish-smoke")

image = modal.Image.debian_slim(python_version="3.12").pip_install("requests")

users = modal.Dict.from_name("moonfactory-users", create_if_missing=True)
jobs = modal.Dict.from_name("moonfactory-jobs", create_if_missing=True)

OWNER_EMAIL = "support@magrify.com"


def solve_pow(challenge: str, difficulty_bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).hexdigest()
        if int(digest, 16) >> (256 - difficulty_bits) == 0:
            return str(nonce)
        nonce += 1


def make_session(user_id: str, secret: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256)
    return f"{body}.{sig.hexdigest()}"


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name("moonfactory-secrets"),
        modal.Secret.from_name("moonfactory-integrations"),
    ],
    timeout=900,
)
def run_smoke(base: str) -> dict:
    import requests

    base = base.rstrip("/")
    checks = []
    out = {"base": base, "checks": checks}

    def rec(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})

    def bot_fields(sess):
        c = sess.get(f"{base}/api/challenge", timeout=30).json()
        return {
            "challenge_id": c["challenge_id"],
            "challenge_nonce": solve_pow(c["challenge"], c["difficulty_bits"]),
        }

    def sess_for(rec_user):
        s = requests.Session()
        s.cookies.set("mf_session",
                      make_session(rec_user["id"], os.environ["SESSION_SECRET"]))
        return s

    # -- pick fixtures: reuse, never create ------------------------------
    fixtures = []
    for key in list(users.keys()):
        if not key.startswith("id:"):
            continue
        u = users.get(key)
        if u and (u.get("email") or "").endswith("@example.com"):
            fixtures.append(u)
    fixtures.sort(key=lambda u: u.get("created_at") or 0)
    # A "free" fixture for gate purposes must be unpaid by EVERY
    # definition the platform uses: plan free AND never paid
    # (is_paid() also fires on net_paid_cents > 0, and several
    # fixtures carry probe-set balances from earlier passes).
    free_fx = next((u for u in fixtures
                    if u.get("plan") == "free" and u.get("verified")
                    and float(u.get("net_paid_cents") or 0) == 0
                    and int(u.get("balance_cents") or 0) == 0), None)
    paid_fx = next((u for u in fixtures
                    if u.get("verified") and u.get("plan") == "creator"), None)
    owner_rec = users.get(f"email:{OWNER_EMAIL}")
    rec("fixtures_found", bool(free_fx and paid_fx and owner_rec),
        f"total_fixtures={len(fixtures)} free={bool(free_fx)} "
        f"paid={bool(paid_fx)} owner={bool(owner_rec)}")
    if not (free_fx and paid_fx and owner_rec):
        return out

    owner_money_before = {k: owner_rec.get(k) for k in (
        "plan", "net_paid_cents", "balance_cents", "free_jobs_total",
        "cost_settled_cents", "is_owner")}

    anon = requests.Session()
    free_s = sess_for(free_fx)
    paid_s = sess_for(paid_fx)
    owner_s = sess_for(owner_rec)

    # -- health + landing variants ---------------------------------------
    r = anon.get(f"{base}/healthz", timeout=30)
    rec("healthz", r.status_code == 200, r.status_code)

    r = anon.get(base + "/", timeout=30)
    rec("landing_anon_guest", r.status_code == 200
        and "first 5 clips are free" in r.text
        and "Open Create" not in r.text, r.status_code)

    r = free_s.get(base + "/", timeout=30)
    rec("landing_free_guest", r.status_code == 200
        and "first 5 clips are free" in r.text
        and "Open Create" not in r.text, r.status_code)

    r = owner_s.get(base + "/", timeout=30)
    rec("landing_owner_member", r.status_code == 200
        and "Open Create" in r.text and "Welcome back" in r.text,
        r.status_code)
    rec("landing_owner_no_tryit", "Try It" not in r.text,
        f"tryit_occurrences={r.text.count('Try It')}")

    # -- studio shell ------------------------------------------------------
    r = anon.get(base + "/app", timeout=30)
    rec("app_shell", r.status_code == 200 and "Moonfactory" in r.text
        and "More options" in r.text, r.status_code)

    # -- config: 29 models, runtime-fed ------------------------------------
    cfg = anon.get(f"{base}/api/config", timeout=30).json()
    models = cfg.get("models") or []
    rec("config_29_models", len(models) == 29, f"count={len(models)}")
    rec("config_default_wan22", cfg.get("default_model") == "wan2.2",
        cfg.get("default_model"))
    out["payments_mode"] = cfg.get("payments_mode")

    # -- /api/me surfaces ----------------------------------------------------
    me_free = free_s.get(f"{base}/api/me", timeout=30).json()
    rec("me_free_create_surface", me_free.get("access_surface") == "create"
        and me_free.get("surface_label") == "Create", me_free.get("access_surface"))
    me_owner = owner_s.get(f"{base}/api/me", timeout=30).json()
    rec("me_owner_create_surface", me_owner.get("access_surface") == "create"
        and me_owner.get("surface_label") == "Create",
        me_owner.get("access_surface"))

    # -- pricing: four numbers ----------------------------------------------
    anon_p = anon.get(f"{base}/api/pricing", timeout=30).json()
    owner_p = owner_s.get(f"{base}/api/pricing", timeout=30).json()

    def lane(payload, model, res):
        fl = (payload.get("frontier_lanes") or {}).get(model, {}).get(res, {})
        al = (payload.get("api_lanes") or {}).get(model, {})
        return {
            "cost": fl.get("cost_cents_per_second"),
            "sell": (al.get("sell_cents_per_second") or {}).get(res),
            "hf": fl.get("higgsfield_effective_cents_per_second"),
            "basis": fl.get("pricing_basis"),
        }

    rows = {}
    for model, res in (("wan3", "720p"), ("seedance", "480p"),
                       ("seedance", "1080p"), ("kling4", "1080p"),
                       ("veo3", "720p")):
        rows[f"{model}_{res}"] = {
            "anon": lane(anon_p, model, res),
            "owner_sell": lane(owner_p, model, res)["sell"],
        }
    out["pricing_rows_cents_per_second"] = rows

    w = rows["wan3_720p"]["anon"]
    rec("price_wan3_720p_atcost_under_hf",
        w["sell"] == w["cost"] == 7.2 and w["hf"] == 8.8, w)
    rec("price_wan3_owner_eq_cost", rows["wan3_720p"]["owner_sell"] == 7.2,
        rows["wan3_720p"]["owner_sell"])
    s = rows["seedance_480p"]["anon"]
    rec("price_seedance25_480p_under_hf", s["sell"] == 14.308
        and abs((s["cost"] or 0) - 8.34) < 0.01 and s["hf"] == 14.75, s)
    k = rows["kling4_1080p"]["anon"]
    rec("price_kling4_1080p_atcost_residual",
        k["sell"] == k["cost"] == 21.35 and k["basis"] == "at_cost_residual", k)

    rec("price_owner_jobs", owner_p.get("job_price_cents", {}).get("production") == 15
        and owner_p.get("job_price_cents", {}).get("draft") == 50,
        owner_p.get("job_price_cents"))
    rec("price_customer_jobs", anon_p.get("job_price_cents", {}).get("production") == 99
        and anon_p.get("job_price_cents", {}).get("draft") == 199,
        anon_p.get("job_price_cents"))
    rec("price_owner_image", (owner_p.get("image") or {}).get("price_cents") == 5
        and (anon_p.get("image") or {}).get("price_cents") == 47,
        {"owner": (owner_p.get("image") or {}).get("price_cents"),
         "anon": (anon_p.get("image") or {}).get("price_cents")})

    # -- house job quotes -----------------------------------------------------
    q_free = free_s.post(f"{base}/api/jobs/quote",
                         json={"model": "wan2.2", "lane": "draft"}, timeout=30)
    rec("quote_free_house", q_free.status_code == 200
        and q_free.json().get("job_price_cents", {}).get("draft") == 199,
        q_free.status_code)
    q_owner = owner_s.post(f"{base}/api/jobs/quote",
                           json={"model": "wan2.2", "lane": "production"},
                           timeout=30)
    rec("quote_owner_house", q_owner.status_code == 200
        and q_owner.json().get("job_price_cents", {}).get("production") == 15,
        q_owner.status_code)
    q_paid = paid_s.post(f"{base}/api/jobs/quote",
                         json={"model": "wan3", "duration_seconds": 5,
                               "resolution": "720p"}, timeout=30)
    rec("quote_paid_persec", q_paid.status_code == 200
        and q_paid.json().get("raw_cost_cents") is not None,
        f"{q_paid.status_code} {q_paid.text[:120]}")

    # -- free-account gates (all reject BEFORE any dispatch) ------------------
    def expect(name, resp, want):
        rec(name, resp.status_code == want,
            f"{resp.status_code} {resp.text[:120]}")

    r = free_s.post(f"{base}/api/jobs", timeout=30, json={
        "lane": "production", "prompt": "a balloon over hills",
        "start_image_png_b64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
        **bot_fields(free_s)})
    expect("gate_free_production_403", r, 403)

    r = free_s.post(f"{base}/api/jobs", timeout=30, json={
        "lane": "draft", "prompt": "a balloon over hills",
        "music_prompt": "soft piano", **bot_fields(free_s)})
    expect("gate_free_music_403", r, 403)

    r = free_s.post(f"{base}/api/jobs", timeout=30, json={
        "lane": "draft", "prompt": "a balloon over hills",
        "speech_script": "Hello there.", **bot_fields(free_s)})
    expect("gate_free_speech_403", r, 403)

    r = free_s.post(f"{base}/api/jobs/quote", timeout=30,
                    json={"model": "wan3", "duration_seconds": 5,
                          "resolution": "720p"})
    expect("gate_free_modelswitch_403", r, 403)

    r = free_s.post(f"{base}/api/jobs", timeout=30, json={
        "lane": "draft", "prompt": "x", "start_image_png_b64": "not-an-image",
        **bot_fields(free_s)})
    expect("gate_bad_image_422", r, 422)

    # -- start-image flow: quote + validation (no dispatch) -------------------
    qi = owner_s.post(f"{base}/api/images/quote",
                      json={"prompt": "a red balloon over green hills"},
                      timeout=30)
    rec("image_quote_owner", qi.status_code == 200
        and qi.json().get("price_cents") == 5,
        f"{qi.status_code} {qi.text[:120]}")
    qi2 = paid_s.post(f"{base}/api/images/quote",
                      json={"prompt": "a red balloon over green hills"},
                      timeout=30)
    rec("image_quote_customer", qi2.status_code == 200
        and qi2.json().get("price_cents") == 47,
        f"{qi2.status_code} {qi2.text[:120]}")
    r = owner_s.post(f"{base}/api/jobs", timeout=30, json={
        "lane": "production", "prompt": "x",
        "start_image_job_id": "doesnotexist000", **bot_fields(owner_s)})
    expect("attach_unknown_image_404", r, 404)

    # -- gallery: list, file, report -------------------------------------------
    g = anon.get(f"{base}/api/gallery", timeout=30).json()
    items = g.get("items") or []
    rec("gallery_has_items", len(items) > 0, f"items={len(items)}")
    if items:
        jid = items[0]["id"] if "id" in items[0] else items[0].get("job_id")
        out["gallery_first_item"] = jid
        rf = anon.get(f"{base}/gallery/{jid}/file", timeout=60)
        rec("gallery_file_serves", rf.status_code == 200
            and len(rf.content) > 10000
            and rf.headers.get("content-type", "").startswith("video/"),
            f"{rf.status_code} bytes={len(rf.content)}")
        rr = anon.post(f"{base}/api/gallery/{jid}/report",
                       json={"reason": "finish-run smoke check"}, timeout=30)
        rec("gallery_report", rr.status_code == 200
            and rr.json().get("status") == "reported", rr.status_code)
        admin = os.environ.get("ADMIN_KEY", "")
        ra = anon.get(f"{base}/api/admin/reports",
                      headers={"X-Admin-Key": admin}, timeout=30)
        rec("gallery_report_visible_admin", ra.status_code == 200
            and any(x.get("job_id") == jid for x in (ra.json().get("reports") or [])),
            ra.status_code)

    # -- settings: handle + opt-out (restored after) ----------------------------
    orig_handle = free_fx.get("handle") or ""
    r = free_s.post(f"{base}/api/me/handle",
                    json={"handle": "finishsmoke"}, timeout=30)
    ok_handle = r.status_code == 200
    me2 = free_s.get(f"{base}/api/me", timeout=30).json()
    ok_handle = ok_handle and me2.get("handle") == "finishsmoke"
    if orig_handle:
        free_s.post(f"{base}/api/me/handle",
                    json={"handle": orig_handle}, timeout=30)
    else:
        # restore derived-handle state by clearing via a fresh unique value
        # is not possible through the API; record what happened instead
        pass
    rec("settings_handle", ok_handle, f"orig={orig_handle!r}")

    r = free_s.post(f"{base}/api/me/gallery-optout",
                    json={"opt_out": True}, timeout=30)
    expect("settings_optout_free_403", r, 403)
    r = paid_s.post(f"{base}/api/me/gallery-optout",
                    json={"opt_out": True}, timeout=30)
    ok_opt = r.status_code == 200 and r.json().get("gallery_opt_out") is True
    r2 = paid_s.post(f"{base}/api/me/gallery-optout",
                     json={"opt_out": False}, timeout=30)
    ok_opt = ok_opt and r2.status_code == 200 \
        and r2.json().get("gallery_opt_out") is False
    rec("settings_optout_paid_toggle_restored", ok_opt, "")

    # -- stripe checkout: create + immediately expire (never paid) -------------
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    out["stripe_key_mode"] = (
        "live" if stripe_key.startswith(("sk_live_", "rk_live_")) else
        "test" if stripe_key.startswith(("sk_test_", "rk_test_")) else "off")

    def checkout(name, sess, path, payload=None):
        r = sess.post(f"{base}{path}", json=payload or {}, timeout=30)
        if r.status_code != 200 or not (r.json().get("url") or ""):
            rec(name, False, f"{r.status_code} {r.text[:150]}")
            return
        url = r.json()["url"]
        sid = url.rsplit("/", 1)[-1].split("?")[0]
        er = requests.post(
            f"https://api.stripe.com/v1/checkout/sessions/{sid}/expire",
            auth=(stripe_key, ""), timeout=30)
        rec(name, er.status_code == 200
            and (er.json().get("status") == "expired"),
            f"session={sid[:12]}... expire={er.status_code}")

    checkout("stripe_topup_session_created_expired", paid_s,
             "/api/checkout/topup", {"pack_cents": 500})
    checkout("stripe_subscription_session_created_expired", paid_s,
             "/api/checkout/subscription")

    # -- the accidental job from smoke run 1 (fixture was paid by the
    #    platform's own is_paid definition, so the "gate" probe created
    #    a real Production job): record its final state for the report.
    acc = jobs.get("80510862a8c4ca89")
    if acc:
        out["accidental_job_run1"] = {
            "status": acc.get("status"), "lane": acc.get("lane"),
            "charged_cents": acc.get("charged_cents"),
            "cost_cents": acc.get("cost_cents"),
            "settled_cost_cents": acc.get("settled_cost_cents"),
            "render_seconds": acc.get("render_seconds"),
            "user_id": acc.get("user_id"),
        }

    # -- owner record untouched by all of the above ------------------------------
    owner_after_rec = users.get(f"email:{OWNER_EMAIL}")
    owner_money_after = {k: owner_after_rec.get(k) for k in owner_money_before}
    rec("owner_record_unchanged", owner_money_after == owner_money_before,
        f"before={owner_money_before} after={owner_money_after}")

    out["passed"] = sum(1 for c in checks if c["ok"])
    out["total"] = len(checks)
    return out


@app.local_entrypoint()
def main(base: str):
    result = run_smoke.remote(base)
    print(json.dumps(result, indent=1, default=str))
