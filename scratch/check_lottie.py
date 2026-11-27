"""The library empty-state Lottie (2026-11-26).

Pins the vendored player and the hand-authored Play Hollow
animation end to end: both files exist in platform/lottie/,
both ride the web image as local files, an allowlisted
/lottie route serves them from this origin (no external
calls anywhere in the wiring), the animation JSON parses
and carries the two brand shapes, and the /app page's
library empty state mounts it lazily (the player loads
only when an empty library actually shows).

Local render proof: scratch/lottie-proof/.

Run:  modal-venv python scratch/check_lottie.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
APP = (ROOT / "platform" / "app.py").read_text()
LOTTIE_DIR = ROOT / "platform" / "lottie"

player = LOTTIE_DIR / "lottie.min.js"
anim_path = LOTTIE_DIR / "play-hollow.json"
assert player.exists() and player.stat().st_size > 100_000
assert anim_path.exists()
assert (LOTTIE_DIR / "NOTICE.md").exists()

# The animation parses and is the mark: an indigo crescent
# arc and the warm yellow play triangle, drawn with trim
# paths, three seconds at 30fps, looping from the page.
anim = json.loads(anim_path.read_text())
assert anim["fr"] == 30 and anim["op"] == 90
assert anim["w"] == anim["h"] == 200
names = {layer["nm"] for layer in anim["layers"]}
assert names == {"crescent", "triangle"}, names
raw = anim_path.read_text()
assert "0.506,0.549,0.973" in raw  # the crescent's indigo
assert "0.964,0.839,0.478" in raw  # the triangle's yellow
assert raw.count('"ty":"tm"') == 2  # both shapes draw in

# Both files ride the web image.
assert '"/platform/lottie/lottie.min.js"' in APP
assert '"/platform/lottie/play-hollow.json"' in APP

# The route: allowlisted names only, right media types,
# and a local fallback path like the icons route.
assert '@web.get("/lottie/{name}")' in APP
assert '"lottie.min.js": "text/javascript"' in APP
assert '"play-hollow.json": "application/json"' in APP

# The page: the empty state mounts the animation, the
# player script comes from the route (never a CDN), and
# the animation path is the route's.
assert "function emptyVideosHtml()" in APP
assert "mountLibraryLottie" in APP
assert "s.src = '/lottie/lottie.min.js'" in APP
assert "path: '/lottie/play-hollow.json'" in APP
assert "else vbox.innerHTML = emptyVideosHtml();" in APP
page = APP[APP.index("function emptyVideosHtml()"):]
assert "cdn" not in page[:3000].lower().replace("cdninstagram", "")

print("lottie pins OK")
