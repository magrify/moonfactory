"""Camera choice probe (2026-11-16): verifies, with NO rendering and
no Modal spend, that Fun Camera speed is once again the USER's choice
(Jack's correction to the finish-pass lock: "I don't want every video
set at three. I want people to be able to choose.").

Checks:
  1. fun_camera.resolve_fun_camera HONORS distinct requested speeds,
     clamps out-of-range values to [0.1, 3.0], falls back to the 3.0
     default only for None, and always returns the distilled recipe.
  2. platform/app.py source: create_job tracks "no speed sent" as
     None (not the old silent 1.0), routes Fun Camera jobs through
     the resolver, and the worker payload + job record carry the
     resolved camera_speed.
  3. Studio source: the /app markup carries the Camera speed control
     (shown only for a Fun Camera selection), and the create handler
     sends camera_speed only when a Fun Camera preset is selected.
  4. With --live: read-backs of /api/config (29 models),
     /api/camera-presets (the Fun Camera ids), and the served /app
     markup containing the new control.

Usage: python3 camera_choice_probe.py [--live <base-url>]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "platform"))

import fun_camera  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# 1. The resolver honors the user's speed.
HONORED = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
for speed in HONORED:
    recipe, resolved = fun_camera.resolve_fun_camera(speed)
    check(
        f"resolve_fun_camera({speed}) honors the requested speed",
        (recipe, resolved) == ("distilled", speed),
        f"got ({recipe!r}, {resolved!r})",
    )
CLAMPED = [(0.0, 0.1), (0.05, 0.1), (3.5, 3.0), (99.0, 3.0)]
for raw, want in CLAMPED:
    recipe, resolved = fun_camera.resolve_fun_camera(raw)
    check(
        f"resolve_fun_camera({raw}) clamps to {want}",
        (recipe, resolved) == ("distilled", want),
        f"got ({recipe!r}, {resolved!r})",
    )
recipe, resolved = fun_camera.resolve_fun_camera(None)
check(
    "resolve_fun_camera(None) falls back to the 3.0 default",
    (recipe, resolved) == ("distilled", 3.0),
    f"got ({recipe!r}, {resolved!r})",
)
check(
    "module constants: distilled recipe, default 3.0, range [0.1, 3.0]",
    (fun_camera.FUN_CAMERA_RECIPE, fun_camera.FUN_CAMERA_DEFAULT_SPEED,
     fun_camera.FUN_CAMERA_MIN_SPEED, fun_camera.FUN_CAMERA_MAX_SPEED)
    == ("distilled", 3.0, 0.1, 3.0),
)

# 2. The platform routes through the resolver, with None = no speed.
source = (ROOT / "platform" / "app.py").read_text()
check(
    "create_job tracks 'no speed sent' as None (no silent 1.0)",
    "camera_speed = None" in source and "camera_speed = 1.0" not in source,
)
check(
    "create_job calls fun_camera.resolve_fun_camera",
    "camera_recipe, camera_speed = fun_camera.resolve_fun_camera(" in source,
)
check(
    "worker payload carries the resolved camera_speed",
    '"camera_speed": camera_speed if fun_preset else None' in source,
)
check(
    "app image ships fun_camera.py",
    source.count('"/platform/fun_camera.py"') >= 2,
    f"{source.count('/platform/fun_camera.py')} add_local_file entries",
)

# 3. The studio control.
check(
    "/app markup carries the Camera speed control",
    'id="camspeed-wrap"' in source
    and 'id="camera-speed"' in source
    and "Camera speed" in source,
)
check(
    "control defaults to 3.0",
    '<option value="3" selected>3</option>' in source,
)
check(
    "control shows only for a Fun Camera selection",
    "$('camspeed-wrap').style.display = selectedFunPreset() ? '' : 'none';"
    in source,
)
check(
    "create handler sends camera_speed only for a Fun Camera selection",
    "if (selectedFunPreset()) camera.camera_speed = "
    "Number($('camera-speed').value);" in source,
)

# 4. Optional live read-back.
if "--live" in sys.argv:
    import urllib.request

    base = sys.argv[sys.argv.index("--live") + 1].rstrip("/")

    def get(path):
        with urllib.request.urlopen(base + path, timeout=30) as resp:
            return resp.read().decode()

    config = json.loads(get("/api/config"))
    check(
        "live /api/config responds with the model shelf",
        len(config.get("models") or []) == 29,
        f"{len(config.get('models') or [])} models",
    )
    served = json.loads(get("/api/camera-presets"))
    library = json.loads(
        (ROOT / "workflows" / "camera-presets" / "presets-index.json").read_text()
    )
    local_fun = sorted(
        p["id"] for p in library.get("presets") or []
        if p.get("mechanism") == "workflow_patch"
    )
    served_fun = sorted(
        p["id"] for p in served.get("presets") or []
        if p.get("mechanism") == "workflow_patch"
    )
    check(
        "live /api/camera-presets serves the same Fun Camera presets",
        served_fun == local_fun,
        f"live={served_fun}",
    )
    app_html = get("/app")
    check(
        "live /app markup contains the Camera speed control",
        'id="camera-speed"' in app_html and "Camera speed" in app_html,
    )

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    sys.exit(1)
print("ALL CHECKS PASS")
