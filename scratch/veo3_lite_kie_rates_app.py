"""Veo 3.1 Lite Kie reroute (2026-11-25): apply the lane's new
worst-case per-second ceiling cells to the LIVE stored rate
table in the moonfactory-meta Dict, mirroring pricing.py.

Kie bills Veo 3.1 Lite flat per clip (30 credits / $0.15 per
720p video, 35 credits / $0.175 per 1080p video, from Kie's
own authenticated catalog API, read 2026-11-25). The stored
table's contract is worst-case cost per billed second, so
the flat cost is carried at the lane's shortest sold length
(4 s): 720p 3.0 -> 3.75, 1080p 8.0 -> 4.375. The governing
per-clip cost lives in pricing.FLAT_CLIP_COST_CENTS; these
cells only feed the per-second displays and pause logic.
updated_at is left untouched, per the round-2 precedent.

Usage:
  modal run veo3_lite_kie_rates_app.py            (show)
  modal run veo3_lite_kie_rates_app.py::apply     (apply)
"""

import modal

app = modal.App("moonfactory-veo3-lite-kie-rates")

meta = modal.Dict.from_name("moonfactory-meta", create_if_missing=True)

UPDATES = {
    "veo3_lite": {"720p": 3.75, "1080p": 4.375},
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
