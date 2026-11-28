"""Local checks for the writer picker grouping (2026-11-28).

Jack's screenshot showed the Improve my prompt picker's
Uncensored Models group opening with four free Numes routes
above a bare $ separator, so free, paid, and uncensored were
not separated the way the Create video and image pickers
separate them. Jack: "idk why ur making model pickers
formatted differently from the create videos one. specify
free models, paid models, uncensored, etc."

The writer pickers now group exactly like the Create
pickers: three optgroups in the order Free Models, Regular
Models, Uncensored Models; every model in exactly one group
by its registry class; the $ tier separators appear only
inside the Regular and Uncensored groups; a route whose real
cost is $0 (every :free route, the four Numes uncensored
builds included) files under Free Models. This pin parses
each of the five studio selects and asserts that shape
against the registry, so a free route parked at the top of a
paid group fails here instead of in a screenshot.

Run:  modal-venv python scratch/check_writer_picker_groups.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import llm_providers  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


SRC = (ROOT / "platform" / "app.py").read_text()
BY_ID = {m["id"]: m for m in llm_providers.DRAFT_MODELS}
GROUP_OF = {"free": "Free Models", "regular": "Regular Models",
            "uncensored": "Uncensored Models"}

SELECT_RE = re.compile(
    r'<select id="((?:prompt|image|image-edit|short|drama)'
    r'-draft-model)">(.*?)</select>', re.S)
GROUP_RE = re.compile(
    r'<optgroup label="([^"]+)">(.*?)</optgroup>', re.S)
ROW_RE = re.compile(
    r'<option disabled data-tier-sep="1">(\$+)</option>'
    r'|<option value="([^"]+)"', re.S)

blocks = SELECT_RE.findall(SRC)
check(len(blocks) == 5,
      f"expected the five writer selects, found {len(blocks)}")

for sid, body in blocks:
    groups = GROUP_RE.findall(body)
    check([g for g, _ in groups]
          == ["Free Models", "Regular Models", "Uncensored Models"],
          f"{sid}: groups are Free Models, Regular Models, "
          f"Uncensored Models in that order")
    seen = []
    for gname, gbody in groups:
        rows = []
        for tok in ROW_RE.finditer(gbody):
            if tok.group(1) is not None:
                rows.append(("sep", tok.group(1)))
            else:
                rows.append(("opt", tok.group(2)))
        if gname == "Free Models":
            check(all(kind == "opt" for kind, _ in rows),
                  f"{sid}: no $ separator row sits inside "
                  f"Free Models")
        for kind, value in rows:
            if kind != "opt":
                continue
            seen.append(value)
            entry = BY_ID.get(value)
            if not check(entry is not None,
                         f"{sid}: {value} is a registered "
                         f"draft model"):
                continue
            check(GROUP_OF[entry["group"]] == gname,
                  f"{sid}: {value} sits in {gname}, its "
                  f"registry group is "
                  f"{GROUP_OF[entry['group']]}")
            if value.endswith(":free"):
                check(gname == "Free Models",
                      f"{sid}: the $0 route {value} must sit "
                      f"in Free Models, not {gname}")
            if gname == "Free Models":
                check(value.endswith(":free"),
                      f"{sid}: Free Models holds only $0 "
                      f"routes, found {value}")
    check(sorted(seen) == sorted(BY_ID),
          f"{sid}: every registered draft model appears "
          f"exactly once, nothing added, nothing dropped")
    check(len(seen) == len(set(seen)),
          f"{sid}: no option value repeats")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check_writer_picker_groups")
