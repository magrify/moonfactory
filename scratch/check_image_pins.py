"""Pin for floating-runtime-clones-and-unpinned-installs,
platform half (security fix, 2026-11-27): every pip_install
in platform/app.py carries exact == versions."""
import re
import sys
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent
       / "platform" / "app.py").read_text()
bad = []
for m in re.finditer(r"\.pip_install\((.*?)\)", SRC, re.S):
    for pkg in re.findall(r'"([^"]+)"', m.group(1)):
        if "==" not in pkg:
            bad.append(pkg)
if bad:
    print(f"FAIL: unpinned platform installs: {bad}")
    sys.exit(1)
print("check_image_pins: every platform pip install is pinned")
