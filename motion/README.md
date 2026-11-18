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
