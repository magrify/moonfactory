"""One-off stored-rate fix for the 2026-11-27 UnificAlly
catalog audit.

The live rate table in the moonfactory-meta Dict merges over
pricing.py's seeds per cell, and the swapped lanes' cells
still carry their old routes' values (VidLab catalog reads,
fal page reads, seeded Kie/BeatAPI/SpicyAPI rows), which
would shadow the new UnificAlly seeds at quote and settle
time. This replaces the fifteen swapped lanes' stored cells
with the new pricing.py seeds and leaves every other model
and the table timestamp untouched. Split lanes carry their
full new basis row: Seedance 2.0's 480p cell keeps its
BeatAPI value (it is in the row), and MiniMax H3 Max's
1080p cell keeps its VidLab value (the refresh keeps
tracking it there).

Run AFTER the deploy that carries the new routes:
  ~/workspace/sinclip/modal-venv/bin/python \
      scratch/fix_unifically_stored_rates.py
"""

import modal

NEW = {
    "seedance25_flash": {"480p": 4.16, "720p": 8.96},
    "seedance10pro_fast": {"480p": 0.7, "720p": 1.5, "1080p": 3.7},
    "seedance2": {"480p": 7.725, "720p": 12.9, "1080p": 31.0,
                  "4K": 74.1},
    "seedance2_fast": {"480p": 4.44, "720p": 9.18},
    "seedance2_mini": {"480p": 1.8, "720p": 3.8},
    "hailuo23_fast": {"720p": 2.88, "1080p": 4.95},
    "minimax_h3_max": {"480p": 4.5, "720p": 7.2, "1080p": 16.0},
    "omni12": {"720p": 4.28, "1080p": 4.75},
    "grok15_lite": {"480p": 1.0, "720p": 1.5, "1080p": 7.0},
    "veo3": {"720p": 30.0, "1080p": 30.625, "4K": 45.0},
    "kling4": {"720p": 12.6, "1080p": 18.9},
    "kling4_flash": {"720p": 7.56},
    "kling_o1": {"720p": 7.56, "1080p": 10.08},
    "kling26_std": {"720p": 3.78},
    "wan27": {"720p": 9.0, "1080p": 13.5},
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
    print("stored rates fixed for the fifteen UnificAlly lanes")


if __name__ == "__main__":
    main()
