"""Pins for the settlement code fixes (2026-11-27): the
legacy worker auth fails closed, the top-up credit is
clamped to the charged amount, provider result downloads
are https-only at ensure_processed, and model-written hook
text loses its angle brackets in clipping._text."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "platform" / "app.py").read_text()
WORKER = (ROOT / "modal-worker" / "app.py").read_text()
sys.path.insert(0, str(ROOT / "platform"))
import clipping  # noqa: E402


def check(label, cond):
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)
    print(f"ok: {label}")


check("legacy worker auth fails closed",
      "if not expected or x_api_key != expected:" in WORKER)
check("topup credit clamps to the charged amount",
      "credit > int(obj[\"amount_total\"])" in APP)
seg = APP.split("def ensure_processed", 1)[1].split(
    "def ", 1)[0]
check("ensure_processed fetches results over https only",
      'str(job["result_url"]).startswith("https://")' in seg)
out = clipping._text("<IMG SRC=X ONERROR=ALERT(1)>", 60)
check("model hook text loses its angle brackets",
      "<" not in out and ">" not in out)
check("ordinary hook text is untouched",
      clipping._text("a cat dances at dawn", 60)
      == "a cat dances at dawn")
print("check_settlement_fixes: all pins hold")
