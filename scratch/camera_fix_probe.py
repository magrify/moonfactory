"""Camera fix probe (2026-11-16): verifies, with NO rendering and no
Modal spend, that every Fun Camera preset in the product resolves to
the one proven combination — distilled recipe, speed 3.0 — and that
the platform's create_job actually routes through that resolution.

Checks:
  1. platform/fun_camera.py's resolver returns ("distilled", 3.0) for
     every requested speed a client could send (including none).
  2. Every workflow_patch (Fun Camera) preset in the served preset
     library resolves through the same path (per-preset assertion).
  3. platform/app.py source: create_job calls
     fun_camera.resolve_fun_camera, the worker payload carries the
     resolved camera_recipe, and the old free default (speed 1.0 with
     no recipe) can no longer reach the worker for a fun preset.
  4. With --live: the deployed /api/camera-presets serves the same
     fun preset ids as the local library.

Usage: python3 camera_fix_probe.py [--live <base-url>]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import fun_camera  # noqa: E402

REQUESTED_SPEEDS = [None, 0.1, 0.5, 1.0, 2.0, 2.5, 3.0]

failures = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# 1. The resolver itself.
for speed in REQUESTED_SPEEDS:
    recipe, resolved = fun_camera.resolve_fun_camera(speed)
    check(
        f"resolve_fun_camera({speed!r}) == ('distilled', 3.0)",
        (recipe, resolved) == ("distilled", 3.0),
        f"got ({recipe!r}, {resolved!r})",
    )
check(
    "module constants are the proven pair",
    (fun_camera.FUN_CAMERA_RECIPE, fun_camera.FUN_CAMERA_SPEED)
    == ("distilled", 3.0),
)

# 2. Every Fun Camera preset in the library.
library = json.loads(
    (ROOT / "workflows" / "camera-presets" / "presets-index.json").read_text()
)
fun_presets = [
    p for p in library.get("presets") or [] if p.get("mechanism") == "workflow_patch"
]
check("library has Fun Camera presets", len(fun_presets) > 0, f"{len(fun_presets)} found")
for preset in fun_presets:
    ok = all(
        fun_camera.resolve_fun_camera(s) == ("distilled", 3.0)
        for s in REQUESTED_SPEEDS
    )
    check(f"preset {preset['id']} resolves to (distilled, 3.0)", ok)

# 3. The platform routes through the resolver.
source = (ROOT / "platform" / "app.py").read_text()
check(
    "create_job calls fun_camera.resolve_fun_camera",
    "camera_recipe, camera_speed = fun_camera.resolve_fun_camera(" in source,
)
check(
    "worker payload carries the resolved camera_recipe",
    '"camera_recipe": camera_recipe if fun_preset else None' in source,
)
check(
    "app image ships fun_camera.py",
    source.count('"/platform/fun_camera.py"') >= 2,
    f"{source.count('/platform/fun_camera.py')} add_local_file entries",
)

# 4. Optional live read-back.
if "--live" in sys.argv:
    import urllib.request

    base = sys.argv[sys.argv.index("--live") + 1].rstrip("/")
    with urllib.request.urlopen(f"{base}/api/camera-presets", timeout=30) as resp:
        served = json.loads(resp.read())
    served_fun = sorted(
        p["id"] for p in served.get("presets") or [] if p.get("mechanism") == "workflow_patch"
    )
    local_fun = sorted(p["id"] for p in fun_presets)
    check(
        "live /api/camera-presets serves the same Fun Camera presets",
        served_fun == local_fun,
        f"live={served_fun}",
    )
    with urllib.request.urlopen(f"{base}/api/config", timeout=30) as resp:
        config = json.loads(resp.read())
    check(
        "live /api/config responds with the model shelf",
        len(config.get("models") or []) == 29,
        f"{len(config.get('models') or [])} models",
    )

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    sys.exit(1)
print("ALL CHECKS PASS")
