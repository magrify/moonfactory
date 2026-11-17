"""Fun Camera conditioning resolution — the ONE place a Fun Camera
preset becomes a (recipe, speed) pair for the worker.

Evidence (proof renders, 2026-11-16; proof-renders-run-2026-11-16.md):
the benchmark scene was rendered under every recipe/speed combination
and the camera drift measured frame by frame.

  - speed 1.0 NEVER pans, in any recipe: distilled, stock, and the
    mid recipe all measured zero drift at speed 1.0.
  - at speed 3.0 only the distilled recipe produces a real pan:
    -8.89% of frame width, monotonic, correct direction (3a).
  - stock at speed 3.0 measured trace noise only (+1.11% net,
    oscillating, centroid moving against the pan direction) (3c).

So a Fun Camera preset does not take a user-chosen speed or recipe at
all. Whatever the client sends, every Fun Camera job resolves here to
the single proven combination: distilled recipe, speed 3.0. The
platform's create_job is the only caller; the worker receives the
resolved pair explicitly in the job payload.
"""

FUN_CAMERA_RECIPE = "distilled"
FUN_CAMERA_SPEED = 3.0


def resolve_fun_camera(requested_speed=None):
    """Return the (camera_recipe, camera_speed) a Fun Camera job runs
    with. The requested speed is accepted for API compatibility and
    deliberately not honored: 3.0 on the distilled recipe is the only
    combination measured to move the camera."""
    return FUN_CAMERA_RECIPE, FUN_CAMERA_SPEED
