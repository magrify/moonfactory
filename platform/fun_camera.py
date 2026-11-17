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

Policy (Jack's correction, 2026-11-16): camera speed is the USER's
choice. The finish pass locked every Fun Camera job to speed 3.0
because the old silent default, 1.0, is a measured dead camera — the
studio never sent a speed at all, so 1.0 applied to every job. Jack
overruled the lock: "I don't want every video set at three. I want
people to be able to choose." So this resolver HONORS the requested
speed, clamped to the platform's supported range, and falls back to
3.0 only when the request carries no speed. 3.0 stays the default
because it is the measured-working speed; the choice is the user's.

The recipe stays "distilled" for every Fun Camera job: it is the
only recipe ever measured to pan, and recipe was an internal test
parameter, never a user-facing choice. The platform's create_job is
the only caller; the worker receives the resolved pair explicitly
in the job payload.
"""

FUN_CAMERA_RECIPE = "distilled"
FUN_CAMERA_DEFAULT_SPEED = 3.0
FUN_CAMERA_MIN_SPEED = 0.1
FUN_CAMERA_MAX_SPEED = 3.0


def resolve_fun_camera(requested_speed=None):
    """Return the (camera_recipe, camera_speed) a Fun Camera job runs
    with. The user's requested speed is honored, clamped to the
    supported range [0.1, 3.0]; no speed requested (None) means the
    default, 3.0."""
    if requested_speed is None:
        return FUN_CAMERA_RECIPE, FUN_CAMERA_DEFAULT_SPEED
    speed = float(requested_speed)
    speed = min(max(speed, FUN_CAMERA_MIN_SPEED), FUN_CAMERA_MAX_SPEED)
    return FUN_CAMERA_RECIPE, speed
