"""One-off stored-rate fix for the 2026-11-26 route audit.

The live rate table in the moonfactory-meta Dict merges over
pricing.py's seeds per cell, and the swapped lanes' cells still
carry their old routes' values (BeatAPI page reads for Wan 3.0,
fal page reads for LTX-2.5, VidLab catalog reads for Seedance
2.0 Fast and Vidu Q4), which would shadow the new seeds at
quote and settle time. This replaces the five lanes' stored
cells with the new pricing.py seeds and leaves every other
model and the table timestamp untouched.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/fix_route_audit_stored_rates.py
"""

import modal

NEW = {
    "wan3": {"480p": 3.0, "720p": 6.0, "1080p": 9.7},
    "minimax_h3": {"480p": 1.5, "720p": 1.5, "1080p": 1.5},
    "ltx25": {"720p": 9.0, "1080p": 13.0, "4K": 30.0},
    "seedance2_fast": {"480p": 4.5, "720p": 9.68},
    "vidu_q4": {"480p": 3.6, "720p": 7.6, "1080p": 9.6},
}


def main() -> None:
    meta = modal.Dict.from_name("moonfactory-meta")
    table = meta.get("provider_rates")
    if not table or "rates" not in table:
        print("no stored table; the seeds govern, nothing to fix")
        return
    rates = dict(table.get("rates") or {})
    for lane, cells in NEW.items():
        print(lane, "before:", rates.get(lane))
        rates[lane] = dict(cells)
    meta["provider_rates"] = {
        "rates": rates,
        "updated_at": table.get("updated_at"),
    }
    back = (meta.get("provider_rates") or {}).get("rates") or {}
    for lane, cells in NEW.items():
        assert back.get(lane) == cells, (lane, back.get(lane))
        print(lane, "after:", back[lane])
    print("stored rates fixed for the five audit lanes")


if __name__ == "__main__":
    main()
