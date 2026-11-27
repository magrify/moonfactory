"""The motion design principles pass (2026-11-26).

Pins the LottieFiles motion-design skill applied to the
motion wing: the forge3d easing kit carries the directional
curves (decelerate entrances, gravity falls, material-weight
follow-through, sine ambience), no spatial move runs on the
old one-curve-fits-all kit, the city pin falls under gravity
and squashes on landing, the house builds by material, the
bars hero takes the follow-through, the presenter enters
instead of simply being there, and the forge3d title
choreography runs anticipation, entrance, follow-through in
that order.

Run:  modal-venv python scratch/check_motion_principles.py
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
FORGE = (ROOT / "motion" / "src" / "forge3d.ts").read_text()
PRESENTER = (ROOT / "motion" / "src" / "presenter.ts").read_text()
APP = (ROOT / "platform" / "app.py").read_text()

# The easing kit: every directional curve the skill calls for.
for name in ("easeOut", "easeOutExpo", "easeInQuad", "easeInOut",
             "easeBack", "easeBackSoft", "breathe"):
    assert f"const {name}" in FORGE, name

# Turntable: anticipation crouch before the rise, expo
# entrance, camera arcing as it dollies.
assert "const anti = easeOut(stage(t, 0.02, 0.3))" in FORGE
assert "const rise = easeOutExpo(stage(t, 0.3, 1.1))" in FORGE
assert "camera.position.y = 3.4 - dolly * 0.45" in FORGE

# Globe: endpoint dots pop, the pulse breathes.
assert "route.dots.forEach" in FORGE
assert "breathe(t, 0.9, i / 3)" in FORGE

# City pin: gravity in, squash and hop on landing, rings only
# after the pin is down.
assert "const fall = easeInQuad(stage(t, D * 0.16, D * 0.40))" in FORGE
assert "pin.scale.set(1 + squash * 0.13, 1 - squash * 0.2" in FORGE
assert "const on = t > D * 0.42" in FORGE

# House: parts animate by material kind.
for fx in ('"rigid"', '"paper"', '"pop"', '"drop"'):
    assert fx in FORGE, fx
assert "yoff = p.drop * (1 - easeInQuad(u))" in FORGE

# Bars: the hero bar takes the soft follow-through, labels
# fade on an ease and pop their scale.
assert "b.hero" in FORGE and "easeBackSoft(stage(t, 0.35" in FORGE
assert "b.sprite.material.opacity = easeOut(popT)" in FORGE

# Presenter: an expo entrance, not a frame-zero puppet.
assert "const enter = easeOutExpo(stage(t, 0, 0.9))" in PRESENTER
assert "(1 - enter) * 1.5" in PRESENTER

# The forge3d title choreography: bar (anticipation) before
# headline (entrance) before subline (follow-through).
page = APP[APP.index("def _hf_forge3d_page"):APP.index("def _hf_presenter_page")]
assert 'id="forge3d-bar"' in page and 'id="forge3d-head"' in page
assert 'id="forge3d-sub"' in page
bar_at = page.index('"#forge3d-bar"')
head_at = page.index('"#forge3d-head"')
assert bar_at < head_at
# The subline's own timeline row is interpolated into the
# timeline after the headline row (the {sub_tl} slot).
assert head_at < page.index("{sub_tl}")

print("motion principles pins OK")
