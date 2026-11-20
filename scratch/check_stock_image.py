"""Local checks for the Stock image lane (2026-11-20).

Jack ordered a "Stock image" entry in the image picker's
Free Models group: instead of generating, the lane returns
a real Pexels photo matching the description, through the
same image delivery path as a generated image. Pexels
costs the platform nothing inside its free quota (the key
the shorts stock footage lane already rides), so the lane
is honestly free at raw cost $0. These checks pin the
lane, its picker class and price, the photo search's
parsing against a stubbed Pexels response (no network),
and the create flow's shape: the search runs before any
job exists, so a description with no match fails clean
with no job, no count and no charge. No Modal calls, no
network, no renders.

Run:  modal-venv python scratch/check_stock_image.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import app  # noqa: E402
import pricing  # noqa: E402
import shorts  # noqa: E402

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
STOCK = pricing.IMAGE_LANES.get("stock") or {}
check(STOCK.get("provider") == "pexels",
      "the stock lane rides the pexels provider")
check(STOCK.get("label") == "Stock image",
      "the picker label is Jack's name for it, Stock image")
check(STOCK.get("free") is True, "the stock lane carries the free flag")
check(STOCK.get("picker_order") == 3,
      "the stock lane follows the two Cloudflare free lanes")
check(STOCK.get("raw_cents") == {"large": 0.0},
      "the stock lane sells one size at $0, the size Pexels serves")
check(not STOCK.get("uncensored"),
      "the stock lane is a standard lane, not uncensored")
check(not STOCK.get("edit"), "the stock lane takes no source image")
raw, debit = pricing.image_lane_price("stock", "large")
check(raw == 0.0 and debit == 0.0,
      "the stock lane prices at exactly $0 raw and $0 customer")
raw_o, debit_o = pricing.image_lane_price("stock", "large", owner=True)
check(raw_o == 0.0 and debit_o == 0.0, "the stock owner price is $0 too")
check(pricing.image_size_label("stock", "large") == "Large",
      "the picker calls the stock size Large, no invented pixels")

# --- the picker class ----------------------------------------------
check(app.image_picker_class("stock") == "free",
      "the stock lane groups as free in the image picker")
check(app.image_picker_price_cents("stock") == 0.0,
      "the stock lane sorts at a $0 picker price")

# --- behavior: the photo search against a stubbed API ---------------
seen = {}


def fake_get_json(url, headers=None, params=None):
    seen["url"] = url
    seen["headers"] = headers or {}
    return {"photos": [
        {"src": {"large2x": "https://images.pexels.com/a2x.jpg",
                 "large": "https://images.pexels.com/a.jpg",
                 "original": "https://images.pexels.com/ao.jpg"},
         "width": 6000, "height": 4000,
         "url": "https://www.pexels.com/photo/1/",
         "photographer": "Jane Doe", "alt": "a lighthouse at sunset"},
        {"src": {"large": "https://images.pexels.com/b.jpg"},
         "width": 5000, "height": 3333,
         "url": "https://www.pexels.com/photo/2/",
         "photographer": "John Roe", "alt": "another lighthouse"},
        {"src": {}, "url": "https://www.pexels.com/photo/3/",
         "photographer": "No Rendition"},
    ]}


real_get_json = shorts._get_json
shorts._get_json = fake_get_json
try:
    found = shorts.pexels_search_photos("a lighthouse at sunset",
                                        "test-key")
finally:
    shorts._get_json = real_get_json
check("/search?" in seen["url"] and "query=a+lighthouse+at+sunset"
      in seen["url"], "the search asks Pexels photos for the description")
check(seen["headers"].get("Authorization") == "test-key",
      "the search carries the platform's Pexels key")
check(len(found) == 2,
      "photos with no usable rendition are skipped")
check(found[0]["download_url"] == "https://images.pexels.com/a2x.jpg",
      "the top photo downloads at its large2x rendition")
check(found[1]["download_url"] == "https://images.pexels.com/b.jpg",
      "a photo without large2x falls back to its large rendition")
check(found[0]["provider"] == "pexels"
      and found[0]["credit"] == "Jane Doe"
      and found[0]["page_url"] == "https://www.pexels.com/photo/1/",
      "each candidate carries its credit trail, photographer and page")


def empty_get_json(url, headers=None, params=None):
    return {"photos": []}


shorts._get_json = empty_get_json
try:
    none_found = shorts.pexels_search_photos("zxqw vkjh", "test-key")
finally:
    shorts._get_json = real_get_json
check(none_found == [], "no match parses to an empty candidate list")

# --- behavior: the two sources behind the lane (2026-11-20) ---------
# Unsplash and Pixabay stand behind the same Stock image
# entry, searched after Pexels. Their searches parse to the
# same candidate shape; an Unsplash candidate also carries
# the photographer profile link and the download location
# its API terms ride on.
useen = {}


def fake_unsplash_get_json(url, headers=None, params=None):
    useen["url"] = url
    useen["headers"] = headers or {}
    return {"results": [
        {"urls": {"raw": "https://images.unsplash.com/raw.jpg",
                  "full": "https://images.unsplash.com/full.jpg"},
         "width": 6000, "height": 4000,
         "links": {"html": "https://unsplash.com/photos/abc",
                   "download_location":
                       "https://api.unsplash.com/photos/abc/download"},
         "user": {"name": "Una Splash",
                  "links": {"html": "https://unsplash.com/@una"}},
         "alt_description": "a lighthouse at sunset"},
        {"urls": {}, "links": {}, "user": {},
         "alt_description": "no rendition"},
    ]}


shorts._get_json = fake_unsplash_get_json
try:
    ufound = shorts.unsplash_search_photos(
        "a lighthouse at sunset", "test-key")
finally:
    shorts._get_json = real_get_json
check("api.unsplash.com/search/photos" in useen["url"]
      and "query=a+lighthouse+at+sunset" in useen["url"],
      "the Unsplash search asks its photos API for the description")
check(useen["headers"].get("Authorization") == "Client-ID test-key",
      "the Unsplash search carries the access key as a Client-ID")
check(len(ufound) == 1,
      "Unsplash photos with no usable rendition are skipped")
check(ufound[0]["provider"] == "unsplash"
      and ufound[0]["download_url"]
      == "https://images.unsplash.com/raw.jpg",
      "the top Unsplash photo downloads at its raw rendition")
check(ufound[0]["credit"] == "Una Splash"
      and ufound[0]["credit_url"] == "https://unsplash.com/@una",
      "an Unsplash candidate carries the photographer name "
      "and profile link its terms ask for")
check(ufound[0]["track_url"]
      == "https://api.unsplash.com/photos/abc/download",
      "an Unsplash candidate carries its download location "
      "for the tracking call at delivery")

pseen = {}


def fake_pixabay_get_json(url, headers=None, params=None):
    pseen["url"] = url
    return {"hits": [
        {"largeImageURL": "https://cdn.pixabay.com/large.jpg",
         "webformatURL": "https://cdn.pixabay.com/web.jpg",
         "imageWidth": 6000, "imageHeight": 4000,
         "pageURL": "https://pixabay.com/photos/1/",
         "user": "Pixa Bay", "tags": "lighthouse, sunset"},
        {"pageURL": "https://pixabay.com/photos/2/",
         "user": "No Rendition", "tags": ""},
    ]}


shorts._get_json = fake_pixabay_get_json
try:
    pfound = shorts.pixabay_search_photos(
        "a lighthouse at sunset", "test-key")
finally:
    shorts._get_json = real_get_json
check("pixabay.com/api/" in pseen["url"]
      and "key=test-key" in pseen["url"]
      and "q=a+lighthouse+at+sunset" in pseen["url"],
      "the Pixabay search asks its API for the description "
      "with the stored key")
check(len(pfound) == 1,
      "Pixabay photos with no usable rendition are skipped")
check(pfound[0]["provider"] == "pixabay"
      and pfound[0]["download_url"]
      == "https://cdn.pixabay.com/large.jpg"
      and pfound[0]["credit"] == "Pixa Bay",
      "the top Pixabay photo downloads at its large "
      "rendition with its credit trail")

# --- source pins: the create flow and the submit --------------------
create_src = func_src("create_image")
check("shorts.pexels_search_photos" in create_src,
      "create_image searches Pexels through the shorts stock code")
check(create_src.index("shorts.pexels_search_photos")
      < create_src.index("jobs[job_id] = job"),
      "the search runs before any job exists, so no match leaves "
      "nothing behind")
check("no stock photo matches that description. "
      "Try a few different words" in create_src,
      "a no match create fails with the plain no match message")
stock_block = create_src[
    create_src.index('if lane_id == "stock":'):
    create_src.index("stock_pick = stock_found[0]")]
check("status_code=400" in stock_block
      and "status_code=404" not in stock_block,
      "the no match answer is a 400, never a 404, because the "
      "platform's 404 handler would rewrite the message away")
check('job["stock_download_url"] = stock_pick["download_url"]'
      in create_src and 'job["stock_credit"] = stock_pick["credit"]'
      in create_src,
      "the winning photo and its credit ride the job record")
check("count_generation(user, free_job=served_free)" in create_src,
      "a stock creation counts through the shared shelf counting, "
      "a free tier job when a free account is served")

submit_src = func_src("_submit_provider_image")
check('provider == "pexels"' in submit_src
      and "_submit_stock_image" in submit_src,
      "the provider submit dispatches pexels to its own submit")
stock_src = func_src("_submit_stock_image")
check("_settle_provider_image(job, url)" in stock_src
      and 'job.get("stock_download_url")' in stock_src,
      "the stock submit settles the picked photo through the "
      "shared settle path")

check("lane.id === 'stock'" in SRC
      and "picked from the stock library" in SRC,
      "the studio shows the stock lane with no price and its "
      "own note")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_stock_image")
