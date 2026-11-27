# Third-party notices

## yt-dlp (link ingest)

Video link downloads (Clip mode's fetch and the Edit/Clone
section's any-site ingest) run through yt-dlp
(https://github.com/yt-dlp/yt-dlp), released into the public
domain under the Unlicense. It is a runtime dependency of
the CPU media image; no yt-dlp code is vendored into this
repository.

## HunyuanVideo 1.5 (house video lane)

The HunyuanVideo 1.5 video lane runs Tencent's open weights
(Comfy-Org/Hunyuan-Video-1.5, the 480p FP8 distilled builds)
on this platform's own worker under the Tencent Hunyuan
Community License Agreement. The United States is inside
the license territory (the agreement excludes only the
European Union, the United Kingdom and South Korea), and
outputs generated with the model are owned by the user who
generated them under the agreement's terms.

## LongCat-Image (house image lane)

The LongCat-Image lane runs Meituan's open weights
(Comfy-Org/LongCat-Image) on this platform's own worker
under the Apache License 2.0.

## MotionForge (3D motion templates and photo presenter)

Moonfactory's 3D motion templates (product turntable, globe
arcs, city pin, house build, 3D bars) and its photo presenter
template adapt the template family from MotionForge
(https://github.com/aykutucesai/motionforge), under the MIT
License, copyright the MotionForge contributors. The scenes
were rebuilt for this platform's HyperFrames scene contract
in motion/src/forge3d.ts and motion/src/presenter.ts; the
mouth curve tool in motion/presenter/mouth_curve.py follows
the shape of MotionForge's mouth curve tool, with this
platform's own envelope math.

## Animation studio pass (diagram, particles, vector scene)

Three motion templates added 2026-11-27 are original
implementations written for this platform, each in the
spirit of an open source animation project whose shape it
follows: the diagram explainer follows Motion Canvas
(github.com/motion-canvas/motion-canvas, MIT), the particle
burst follows Proton (github.com/drawcall/Proton, MIT), and
the vector scene follows Two.js (github.com/jonobr1/two.js,
MIT). No code from those projects is vendored or adapted;
the compositions are plain HyperFrames pages on the wing's
existing GSAP shell, built to the same seek contract as
every other template. The GIF option beside them converts
on the worker with ffmpeg's palette pipeline, a runtime
tool the worker already carries; the browser encoder
gifenc was evaluated for it and not adopted, since the
worker is not a browser.

## PeakCut (clipping pipeline)

Moonfactory's Clip mode ports code from PeakCut
(https://github.com/haziqahmedqasmi/PeakCut), pinned at commit
b3e403e, under the MIT License, copyright PeakCut contributors.
The full license text ships with the ported code as
platform/PEAKCUT-LICENSE.txt.

Ported: moment picking (brain.py), ASS word-by-word captions and
ffmpeg layouts (render.py), the sherpa-onnx Parakeet transcription
wrapper (transcribe.py), and the allowlist-guarded fetch, probe and
loudness code (media.py). Not ported: the update checker
(updates.py, dropped entirely), PeakCut's server, auth and
accounts, and its plaintext key storage.

Caption fonts bundled with the port (Poppins ExtraBold, Anton,
Archivo Black) are licensed under the SIL Open Font License 1.1;
the license texts ship alongside the fonts in the upstream repo
(studio/fonts/OFL-*.txt).
