"""One-off stored-rate fix for the 2026-11-26 FrameHush reroute.

The live rate table in the moonfactory-meta Dict merges over
pricing.py's seed per cell, and its seedance cells still carry
the displaced routes' refreshed values (CometAPI 480p, a
BeatAPI page read at 1080p), which would shadow the FrameHush
seed at quote and settle time now that the refresh no longer
reads for this lane. This replaces the stored seedance cells
with the FrameHush rates and leaves every other model and the
table timestamp untouched.

Run:  ~/workspace/sinclip/modal-venv/bin/python scratch/fix_framehush_stored_rates.py
"""

import modal

FRAMEHUSH = {"480p": 3.5, "720p": 4.7, "1080p": 8.0}


def main() -> None:
    meta = modal.Dict.from_name("moonfactory-meta")
    table = meta.get("provider_rates")
    if not table or "rates" not in table:
        print("no stored table; the seed governs, nothing to fix")
        return
    rates = dict(table.get("rates") or {})
    before = rates.get("seedance")
    rates["seedance"] = dict(FRAMEHUSH)
    meta["provider_rates"] = {
        "rates": rates,
        "updated_at": table.get("updated_at"),
    }
    print(f"stored seedance rates: {before} -> {FRAMEHUSH}")


if __name__ == "__main__":
    main()
