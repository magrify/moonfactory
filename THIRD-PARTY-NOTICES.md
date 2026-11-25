# Third-party notices

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
