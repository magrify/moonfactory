# Motion wing sources

Source for the two assets the Kaggle worker stages from the
moonfactory-hf-toolchain dataset for story template jobs.

- `src/` builds `motion-scene.js`, the three.js town scene the
  story composition drives through HyperFrames hf-seek events.
  The world, cast, ink and grade code adapts the MIT template
  kit from mromidev/claude-animated-shorts (templates/bridge).
  `story.ts` holds the camera programs and scene timing,
  `world.ts` the town, `cast.ts` the townsfolk, `main.ts` the
  bootstrap and the post chain.
- `audio/` is the score kit. `motion_audio.py` writes the march
  and the scene change sounds for one story job from its
  duration and scene start times, using the vendored `dsp.py`
  and `instruments.py` from the same MIT kit.

Build the scene bundle with `node build.mjs` after
`npm install`. Ship `motion-scene.js` plus the three audio
files as loose files in a new version of the
magrify/moonfactory-hf-toolchain dataset. The worker stages
them next to the toolchain and fails story jobs clearly when
they are missing. Titles jobs never touch them.

## The 3D templates and the photo presenter (2026-11-25)

Two more asset families ride the same dataset route, lifted
from the MotionForge template kit
(github.com/aykutucesai/motionforge, MIT) and rebuilt to the
scene contract above: a frame is a pure function of the
hf-seek time, props ride in window.__motion, half scale, no
post chain.

- `src/forge3d.ts` builds `forge3d-scene.js`, one dispatcher
  bundle for the six 3D motion templates (product turntable,
  globe arcs, city pin, house build, 3D bars, product
  showcase). The claim payload flags these jobs `forge3d`
  and the worker stages the bundle beside the composition.
  The showcase loads the user's product photo as its card
  texture and its ready gate waits for the photo like the
  presenter's does. The logo draw-in template has no scene
  bundle at all: its composition page draws the Play Hollow
  mark as SVG line art on the plain GSAP shell.
- `src/presenter.ts` builds `presenter-scene.js`, the photo
  presenter: the user's photo as a 2.5D puppet whose mouth
  follows the voiceover. `presenter/mouth_curve.py` is the
  worker-side tool that turns the finished Kokoro voiceover
  wav into mouth-curve.js, one openness value per frame,
  which the scene script indexes as it seeks. The claim
  payload flags these jobs `presenter`.

Ship `forge3d-scene.js`, `presenter-scene.js` and
`mouth_curve.py` as loose files in the same toolchain
dataset version as the story assets. Jobs in each family
fail clearly when their assets are missing; every other
family keeps rendering.
