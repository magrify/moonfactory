"""Normalize the pulled catalogs and dump, per Moonfactory lane,
every candidate same-model entry on another provider with its
price. Human review decides what is a true same-model match."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "platform"))
import pricing  # noqa: E402

CANDS = []  # (provider, id, kind, unit, prices{res: usd}, status, note)


def add(provider, mid, kind, unit, prices, status="", note=""):
    CANDS.append({"provider": provider, "id": mid, "kind": kind,
                  "unit": unit, "prices": prices, "status": status,
                  "note": note})


# --- VidLab -------------------------------------------------------------
d = json.load(open(HERE / "vidlab.json"))["data"]["data"]
for e in d:
    add("vidlab", e["id"], e.get("kind"), e.get("unit"),
        {k: float(v) for k, v in (e.get("prices") or {}).items()
         if isinstance(v, (int, float))},
        e.get("status") or "", f"routes={e.get('routes')}")

# --- SpicyAPI -----------------------------------------------------------
raw = json.load(open(HERE / "spicyapi.json"))["data"]["data"]
items = raw["items"] if isinstance(raw, dict) else raw
for e in items:
    prices = {}
    for p in e.get("pricing") or []:
        try:
            prices[p.get("variant") or "*"] = float(p["price"])
        except Exception:
            pass
    add("spicyapi", e["model"], e.get("modality"),
        (e.get("pricing") or [{}])[0].get("unit", ""),
        prices, "available" if e.get("available") else "UNAVAILABLE",
        e.get("displayName") or "")

# --- Kie ----------------------------------------------------------------
raw = json.load(open(HERE / "kie.json"))["data"]["data"]["models"]
for e in raw:
    desc = e.get("pricingDesc") or ""
    prices = {}
    # "17.6 credits ($0.088) per second" style
    m = re.search(r"\(\$(\d+\.\d+)\)\s*per second", desc)
    if m:
        prices["*"] = float(m.group(1))
    else:
        m = re.search(r"\$(\d+\.\d+)\s*(?:per second|/second|/s)\b", desc)
        if m:
            prices["*"] = float(m.group(1))
    add("kie", e["model"], "video", "per_second?", prices, "",
        desc[:110])

# --- BeatAPI ------------------------------------------------------------
offers = json.load(open(HERE / "beatapi_offers.json"))
for slug, page in offers.items():
    prices = {}
    names = {}
    for o in page.get("offers") or []:
        res = next((r for r in ("480p", "720p", "1080p", "4K", "2K")
                    if r in o["name"]), "*")
        key = res
        if key in prices:  # mode variants: keep both visible
            key = f"{res} ({o['name'][:40]})"
        prices[key] = o["per_second"]
        names[key] = o["name"]
    add("beatapi", slug, "video", "per_second", prices, "",
        "; ".join(f"{k}={v}" for k, v in names.items())[:150])

# --- FrameHush ----------------------------------------------------------
d = json.load(open(HERE / "framehush.json"))["data"]["data"]
for e in d:
    add("framehush", e["id"], "video", "estimate-only", {}, "", "")

# --- CometAPI / Eachlabs / fal: ids only --------------------------------
d = json.load(open(HERE / "cometapi.json"))["data"]["data"]
for e in d:
    add("cometapi", e["id"], "?", "no-price-in-catalog", {}, "", "")
d = json.load(open(HERE / "eachlabs.json"))["data"]
for e in d:
    add("eachlabs", e.get("slug") or "", e.get("output_type"),
        "no-price-in-catalog", {}, "", e.get("name") or "")

# --- Lane alias tokens ----------------------------------------------------
LANE_TOKENS = {
    "wan3": ["wan-3.0", "wan-3", "wan3"],
    "wan3_prime": ["wan-3.0-prime", "wan-3-prime", "wan3-prime", "wan-3.0 prime"],
    "wan_motion": ["motion-control", "wan-motion", "motion_control"],
    "seedance": ["seedance-2-5", "seedance-2.5", "seedance_2.0", "sd2.5"],
    "veo3": ["veo-3.1", "veo3.1", "veo-3-1"],
    "veo3_fast": ["veo-3.1-fast", "veo3.1/fast", "veo-3.1 fast"],
    "veo3_lite": ["veo-3.1-lite", "veo3.1/lite", "veo-3.1 lite"],
    "kling3": ["kling-3", "kling3", "kling/3", "kling-v3"],
    "kling4": ["kling-4", "kling4", "kling/4", "kling-v4"],
    "kling3_omni": ["kling-3-omni", "kling-3.0-omni", "kling-omni"],
    "kling26_std": ["kling-2.6", "kling/2-6", "kling-2-6"],
    "kling_o1": ["kling-o1", "kling/2.6", "kling-2.6-motion"],
    "seedance2": ["seedance-2.0", "seedance-2", "seedance_2.0"],
    "seedance2_fast": ["seedance-2.0-fast", "seedance-2-fast"],
    "seedance2_mini": ["seedance-2-mini", "seedance-2.0-mini", "seedance-2.0 mini"],
    "omni12": ["omni-1.2", "omni-1-2", "gemini-omni-1.2", "omni12"],
    "omni11": ["omni-1.1", "omni-1-1", "gemini-omni-1.1", "omni11"],
    "hailuo23": ["hailuo-2.3", "hailuo-23", "hailuo2.3"],
    "hailuo23_fast": ["hailuo-2.3-fast", "hailuo-2.3 fast"],
    "minimax_h3": ["minimax-h3", "minimax_h3", "h3"],
    "minimax_h3_max": ["minimax-h3-max", "h3-max", "h3 max"],
    "grok": ["grok-imagine-video", "grok-imagine-1.0", "grok-video"],
    "grok15": ["grok-imagine-video-1.5", "grok-1.5", "grok-imagine-1.5"],
    "grok15_lite": ["grok-imagine-lite", "grok-lite", "grok-imagine-video-lite"],
    "luma_ray32": ["ray-3.2", "ray3.2", "luma-ray"],
    "ltx25": ["ltx-2.5", "ltx-2-5", "ltx2.5"],
    "ltx25_fast": ["ltx-2.5-fast", "ltx-2.5/fast", "ltx-fast"],
    "happyhorse": ["happyhorse"],
    "pika22": ["pika-2.2", "pika-v2.2", "pika-22"],
    "vidu_q3": ["vidu-q3", "vidu/q3", "viduq3"],
    "vidu_q4": ["vidu-q4", "vidu/q4"],
    "vidu_q3_pro": ["vidu-q3-pro", "vidu-q3 pro"],
    "vidu_q3_turbo": ["vidu-q3-turbo"],
    "vidu_q3_mix": ["vidu-q3-mix"],
    "runway_gen45": ["gen-4.5", "gen4.5", "runway-gen"],
    "vidra3": ["vidra-3", "vidra3"],
    "seedance25_boost": ["seedance-2.5-boost", "seedance-2.5 boost"],
    "seedance25_flash": ["seedance-2.5-flash", "seedance-2-5-flash", "sd2.5-flash"],
    "pixverse_v6": ["pixverse-v6", "pixverse-6", "pixverse_v6"],
    "pixverse_v6_max": ["pixverse-v6-max", "pixverse-v6 max"],
    "wan25": ["wan-2.5", "wan-25", "wan2.5"],
    "wan26": ["wan-2.6", "wan-26", "wan2.6"],
    "wan26_flash": ["wan-2.6-flash", "wan-2.6 flash"],
    "wan27": ["wan-2.7", "wan-27", "wan2.7"],
    "skyreels_v4_fast": ["skyreels-v4-fast", "skyreels-v4 fast"],
    "skyreels_v4_std": ["skyreels-v4", "skyreels_v4"],
    "seedance15pro": ["seedance-1.5-pro", "seedance-1-5-pro"],
    "seedance10pro_fast": ["seedance-1.0-pro-fast", "seedance-1.0"],
    "vidlab15": ["vidlab-1.5", "vidlab15"],
    "vidlab2": ["vidlab-2", "vidlab2"],
    "magi2": ["magi-2", "magi2"],
}

CURRENT = {}
import importlib  # noqa: E402
app_src = (HERE.parent.parent / "platform" / "app.py").read_text()
m = re.search(r"MODEL_PROVIDER = \{(.*?)\n\}", app_src, re.S)
for k, v in re.findall(r'"([a-z0-9_]+)":\s*"([a-z0-9_]+)"',
                       m.group(1)):
    CURRENT[k] = v

rows = []
for lane, tokens in LANE_TOKENS.items():
    cur = pricing.PROVIDER_RATES_CENTS_PER_SECOND.get(lane)
    if cur is None:
        continue
    hits = []
    for c in CANDS:
        cid = c["id"].lower()
        if c["provider"] == CURRENT.get(lane):
            continue
        if any(t in cid for t in tokens):
            hits.append(c)
    rows.append((lane, CURRENT.get(lane), cur, hits))

for lane, cur_provider, cur, hits in rows:
    print(f"\n### {lane}  (now {cur_provider}: "
          f"{ {k: v for k, v in cur.items()} } c/s)")
    for h in hits:
        p = {k: (round(v * 100, 2) if isinstance(v, float) else v)
             for k, v in h["prices"].items()}
        print(f"   {h['provider']:10s} {h['id'][:64]:64s} "
              f"{h['unit'] or '':14s} {p} {h['status']} {h['note'][:80]}")
