"""Finish-run rate corrections (2026-11-16): apply the four
Kie-catalog-verified seed corrections to the LIVE stored rate
table in the moonfactory-meta Dict, mirroring pricing.py.

Cells (cents/second), each from Kie's own authenticated catalog
API (api.kie.ai/api/v1/models pricingDesc, read this run):
  kling3_omni   720p 7.55 -> 10.1, 1080p 7.55 -> 12.6
                (with native audio; the submit sends sound=true)
  seedance2_fast 480p 3.4 -> 5.9, 720p 3.4 -> 12.4 (no-video rates)
  seedance2_mini 480p 1.2 -> 1.9, 720p 1.2 -> 4.1 (no-video rates)
  omni12        720p 2.0 -> 4.5, 1080p 2.0 -> 5.0
                (the old 2.0 was the catalog's 360P rate)
updated_at is left untouched, per the round-2 precedent.

Usage:
  modal run finish_rates_app.py            (show)
  modal run finish_rates_app.py::apply     (apply)
"""

import modal

app = modal.App("moonfactory-finish-rates")

meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)

UPDATES = {
    "kling3_omni": {"720p": 10.1, "1080p": 12.6},
    "seedance2_fast": {"480p": 5.9, "720p": 12.4},
    "seedance2_mini": {"480p": 1.9, "720p": 4.1},
    "omni12": {"720p": 4.5, "1080p": 5.0},
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
