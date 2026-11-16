# Reference Video Recreation Method

Saved 2026-11-15 from Jack, adapted: Muse does the analysis step (in place of the original workflow's model), and generation runs on Wan 3.0 Prime via API, exactly as the original workflow specifies.

Correction logged 2026-11-15: Wan 3.0 is real. Alibaba released Tongyi Wanxiang 3.0 in August 2026 (public beta Aug 6, general availability Aug 24), and Wan 3.0 Prime (model ID wan3.0-video-prime) is its official high-speed variant. Muse initially said it could not verify the model existed; Jack was right and that statement was wrong. The catch for Sinclip's architecture: Wan 3.0 is API-only with NO open weights, so it cannot run inside our self-hosted ComfyUI workers. Wan 2.2 remains the last open-weights Wan and stays the self-hosted engine. Wan 3.0 enters the picture as a hosted API lane (Alibaba Cloud Model Studio, also carried by aggregators such as fal), which suits this recreation method particularly well: one model handles text/image/reference-to-video, takes up to 10 reference images (the character sheet), generates up to 30 seconds in a single pass (most shorts need no chaining), and produces native audio in the same pass. List pricing on Model Studio: $0.05/s at 480p, $0.10/s at 720p, $0.20/s at 1080p; Prime runs roughly 1.4x that. At those rates a 30-second 720p short costs about $3.00 standard or $4.20 Prime in raw generation, so this lane fits per-use or premium pricing, not the Creator plan's included minutes; the self-hosted 2.2 pipeline remains the margin engine for included usage.

Purpose: reverse engineer a reference video into a ready-to-use generation prompt, then recreate it with our own character and a custom script. Fits Sinclip two ways: as an internal production method for making shorts fast, and as a candidate product feature later ("recreate this format with your character").

## The workflow

1. **Get the reference video.** Download the video to recreate (yt-dlp is the tool in our stack). Keep the source URL noted with the file.
2. **Send the video to Muse** with the analysis prompt below and the custom script filled in. Muse analyzes it frame by frame and returns ONE copy-paste-ready generation prompt.
3. **Generate.** Send that prompt plus a character sheet (the character's reference images, passed as Wan 3.0's reference inputs) to Wan 3.0 Prime via API. Native audio comes back in the same pass; if a talking-head shot needs a tighter lip-sync pass, MuseTalk or LatentSync can still run over the result in our own post chain. Self-hosted alternative when API cost matters more than fidelity: the same prompt and character sheet through the Wan 2.2 A14B production workflow, with the normal post chain (interpolation, upscale, audio) finishing it.

## The analysis prompt (use verbatim, fill in the script)

Analyze this reference video frame by frame and generate a detailed, ready-to-use prompt to recreate it as closely as possible (1:1) using a provided character sheet.

Custom script: {script}

Include:
- Output & references: format, duration, character identity and consistency.
- Scene & background: environment, objects, colors, textures, depth and positioning.
- Acting & expression map: gestures, posture, facial expressions, eye movements and micro-expressions.
- Timeline: timestamped breakdown of movements, dialogue, expressions and camera changes, adapted to the custom script.
- Camera rules: framing, angles, handheld motion, shake, focus and perspective.
- Lighting & realism: shadows, exposure, skin texture and authentic smartphone quality.
- Dialogue & voice: use the custom script EXACTLY, word for word, with natural pacing, accurate lip sync and a new voice (never clone the original).
- Audio mix: ambience, background noise and microphone characteristics.
- Negatives: no AI artifacts, identity drift, unnatural movements or unwanted text.

Preserve the original video's timing, composition, acting and imperfections while adapting speech and lip movements to the custom script. Be extremely specific, avoid assumptions, and output ONLY the final copy-paste-ready generation prompt.

## Notes and caution

- The method is strongest on short-form formats: talking-head clips, product demos, trend formats, UGC-style ads. That is exactly Sinclip's launch lane.
- The original post claimed about $1.40 per influencer video. Against verified Wan 3.0 Prime pricing (about $0.14/s at 720p) that checks out for a short of roughly 10 seconds; longer shorts scale linearly from there.
- Caution (Jack's own note on saving this): recreating formats and trends is normal creative practice, but a 1:1 recreation of someone else's specific video can carry copyright and likeness risk if published commercially, especially with a recognizable person or a distinctive original performance. Use it for formats, styles, and our own or licensed references; do not clone a real person's identity. The prompt already specifies a new voice, never the original's.
