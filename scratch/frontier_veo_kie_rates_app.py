"""Frontier coverage pass (2026-11-25): apply the Veo 3.1
and Veo 3.1 Fast lanes' new worst-case per-second ceiling
cells to the LIVE stored rate table in the moonfactory-meta
Dict, mirroring pricing.py.

Both lanes moved to Kie, which bills flat per clip (Quality
250 / 255 / 370 credits = $1.25 / $1.275 / $1.85 per clip at
720p / 1080p / 4K; Fast 60 / 65 / 180 credits = $0.30 /
$0.325 / $0.90). The stored table's contract is worst-case
cost per billed second, so each flat cost is carried at the
lane's shortest sold length (4 s): veo3 12.0 / 12.0 / 60.0
-> 31.25 / 31.875 / 46.25, veo3_fast 15.0 / 15.0 / 35.0 ->
7.5 / 8.125 / 22.5. The governing per-clip cost lives in
pricing.FLAT_CLIP_COST_CENTS; these cells only feed the
per-second displays and pause logic. updated_at is left
untouched, per the round-2 precedent.

Usage:
  modal run frontier_veo_kie_rates_app.py            (show)
  modal run frontier_veo_kie_rates_app.py::apply     (apply)
"""

import modal

app = modal.App("moonfactory-frontier-veo-kie-rates")

meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)

UPDATES = {
    "veo3": {"720p": 31.25, "1080p": 31.875, "4K": 46.25},
    "veo3_fast": {"720p": 7.5, "1080p": 8.125, "4K": 22.5},
}


@app.local_entrypoint()
def main():
    import json
    print(json.dumps(show.remote(), indent=1))


@app.function(timeout=120)
def show() -> dict:
    table = meta.get("provider_rates") or {}
    rates = table.get("rates") or {}
    return {"updated_at": table.get("updated_at"),
            "cells": {m: {r: (rates.get(m) or {}).get(r) for r in cells}
                      for m, cells in UPDATES.items()}}


@app.function(timeout=120)
def apply() -> dict:
    table = meta.get("provider_rates")
    if not table or not table.get("rates"):
        return {"ok": False, "error": "no stored provider_rates table"}
    rates = {m: dict(r) for m, r in table["rates"].items()}
    before = {}
    for model, cells in UPDATES.items():
        before[model] = {res: (rates.get(model) or {}).get(res)
                         for res in cells}
        merged = dict(rates.get(model) or {})
        merged.update(cells)
        rates[model] = merged
    meta["provider_rates"] = {"rates": rates,
                              "updated_at": table.get("updated_at")}
    back = meta.get("provider_rates")
    after = {m: {res: (back["rates"].get(m) or {}).get(res)
                 for res in cells}
             for m, cells in UPDATES.items()}
    return {"ok": True, "before": before, "after": after,
            "updated_at": back.get("updated_at")}
