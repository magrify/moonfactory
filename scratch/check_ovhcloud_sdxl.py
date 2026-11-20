"""Local checks for the OVHcloud AI Endpoints free image
route and the three-source Stock image search (2026-11-20).

Jack ordered two builds on top of the landed Cloudflare and
Stock image lanes: (1) OVHcloud's SDXL, priced at 0 EUR in
OVHcloud's own catalog, as another Free Models entry riding
its free route (2 requests a minute anonymously, 400 a
minute with a project key, the pace enforced by the
platform's own ledger in meta); (2) Unsplash and Pixabay
standing behind the Stock image entry alongside Pexels,
searched in that order, a source with no key stored yet
skipped silently. These checks pin the SDXL lane, its
picker class and price, the pace ledger's behavior (the
defs extracted from app.py and exec'd against a plain dict
standing in for meta, the way check_cloudflare_images
proves its cap), and the create flow's source order. No
Modal calls, no network, no renders.

Run:  modal-venv python scratch/check_ovhcloud_sdxl.py
"""
import ast
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
TREE = ast.parse(SRC)


def func_src(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(SRC, node) or ""
    return ""


# --- the lane ------------------------------------------------------
SDXL = pricing.IMAGE_LANES.get("sdxl_ovh") or {}
check(SDXL.get("provider") == "ovhcloud",
      "SDXL rides the ovhcloud provider")
check(SDXL.get("label") == "SDXL",
      "the picker label is the plain model name, SDXL")
check(SDXL.get("free") is True, "SDXL carries the free flag")
check(SDXL.get("picker_order") == 4,
      "SDXL follows the Cloudflare pair and the stock lane")
check(SDXL.get("raw_cents") == {"1024x1024": 0.0},
      "SDXL sells only 1024x1024 at $0, the one size its "
      "endpoint answers")
check((SDXL.get("endpoint") or "").endswith("/api/text2image"),
      "SDXL points at the model's own text2image endpoint")
check(not SDXL.get("uncensored"),
      "SDXL is a standard censored model, not uncensored")
check(not SDXL.get("edit"), "SDXL takes no source image")
raw, debit = pricing.image_lane_price("sdxl_ovh", "1024x1024")
check(raw == 0.0 and debit == 0.0,
      "SDXL prices at exactly $0 raw and $0 customer")
raw_o, debit_o = pricing.image_lane_price(
    "sdxl_ovh", "1024x1024", owner=True)
check(raw_o == 0.0 and debit_o == 0.0,
      "the SDXL owner price is $0 too")
check(pricing.image_size_label("sdxl_ovh", "1024x1024")
      == "1024 x 1024", "the picker labels the SDXL size plainly")

# --- the picker class ----------------------------------------------
check(app.image_picker_class("sdxl_ovh") == "free",
      "SDXL groups as free in the image picker")
check(app.image_picker_price_cents("sdxl_ovh") == 0.0,
      "SDXL sorts at a $0 picker price")
check(app.PROVIDER_KEY_ENV.get("ovhcloud") == "OVH_AI_API_KEY",
      "the ovhcloud key env name follows the provider key pattern")

# --- the pace constants ---------------------------------------------
for name, want in (("OVH_ANON_PER_MINUTE", "2"),
                   ("OVH_KEY_PER_MINUTE", "400")):
    m = re.search(rf"{name} = ({want})\b", SRC)
    check(bool(m), f"{name} is {want}")

# --- behavior: the extracted pace ledger ----------------------------
ns = {"meta": {}, "time": time, "os": os}
for const in ("OVH_ANON_PER_MINUTE", "OVH_KEY_PER_MINUTE"):
    m = re.search(rf"^    {const} = (.+)$", SRC, re.M)
    check(bool(m), f"{const} assignment found")
    ns[const] = eval(m.group(1), dict(ns))
for name in ("_ovh_per_minute", "_ovh_recent_calls",
             "_ovh_rate_allows", "_ovh_rate_reserve"):
    src = func_src(name)
    check(bool(src), f"{name} exists in app.py")
    exec(src, ns)

check("OVH_AI_API_KEY" not in os.environ,
      "the test env carries no OVHcloud key, so the pace "
      "under test is the anonymous one")
check(ns["_ovh_per_minute"]() == 2,
      "with no key stored the pace is the anonymous 2 a minute")
allows = ns["_ovh_rate_allows"]
reserve = ns["_ovh_rate_reserve"]
meta = ns["meta"]
check(allows(), "a fresh minute allows a call")
check(reserve() is True, "the first call takes a slot")
check(reserve() is True, "the second call takes the last slot")
check(not allows(), "at the pace the ledger refuses")
check(reserve() is False and len(meta["ovh_calls"]) == 2,
      "a refused reservation leaves the ledger untouched")
meta["ovh_calls"] = [time.time() - 120.0, time.time() - 90.0]
check(allows(), "calls older than a minute fall out of the ledger")
check(reserve() is True and len(meta["ovh_calls"]) == 1,
      "the pruned ledger is written back on a reservation")

# --- source pins: the create flow and the submit --------------------
create_src = func_src("create_image")
check('lane["provider"] == "ovhcloud"' in create_src
      and "_ovh_rate_allows" in create_src,
      "create_image checks the SDXL pace before creating")
check(create_src.index("_ovh_rate_allows")
      < create_src.index("jobs[job_id] = job"),
      "the pace check runs before any job exists, so a busy "
      "lane leaves nothing behind")
check("the free SDXL lane is busy right now" in create_src,
      "a busy SDXL lane fails with the plain busy message")
submit_src = func_src("_submit_provider_image")
check('provider == "ovhcloud"' in submit_src
      and "_submit_ovhcloud_image" in submit_src,
      "the provider submit dispatches ovhcloud to its own submit")
ovh_src = func_src("_submit_ovhcloud_image")
check("_ovh_rate_reserve()" in ovh_src,
      "the ovhcloud submit reserves a pace slot before the call")
check("OVH_AI_API_KEY" in ovh_src and "Bearer" in ovh_src,
      "the ovhcloud submit sends a stored key as its Bearer token")
check("negative_prompt" in ovh_src,
      "the ovhcloud submit posts the documented prompt shape")
check('raw.startswith(b"\\x89PNG")' in ovh_src,
      "the ovhcloud submit only settles a real image answer")

# --- the stock lane's three sources ---------------------------------
check(create_src.index("shorts.pexels_search_photos")
      < create_src.index("shorts.unsplash_search_photos")
      < create_src.index("shorts.pixabay_search_photos"),
      "the stock search order is Pexels, then Unsplash, "
      "then Pixabay")
check('shorts._env_value("UNSPLASH_ACCESS_KEY")' in create_src
      and 'shorts._env_value("PIXABAY_API_KEY")' in create_src,
      "each stock source reads its own stored key, and a "
      "missing one is skipped silently")
check('job["stock_source"] = stock_pick["provider"]'
      in create_src,
      "the winning stock source rides the job record")
check('job["stock_credit_url"]' in create_src
      and 'job["stock_track_url"]' in create_src,
      "an Unsplash win stores the photographer profile link "
      "and the download location on the job record")
stock_src = func_src("_submit_stock_image")
check("stock_track_url" in stock_src and "Client-ID" in stock_src,
      "the stock submit fires the Unsplash download "
      "tracking call at delivery")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_ovhcloud_sdxl")
