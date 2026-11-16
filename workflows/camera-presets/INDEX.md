# Moonfactory camera presets

The camera layer for the Moonfactory studio: pick a move (and optionally a
film look) and the preset steers the render. Built 2026-11-16 by porting
Freefield's Cinema Studio system (neuregex/freefield, MIT) into Moonfactory's
own terms, plus the UX patterns mined from open-higgsfield
(wide-trace/open-higgsfield) for the platform pass that renders this picker.

## Files

- `presets-index.json` — the whole library as data: composition grammar, the
  look vocabulary, and all 42 presets. This is the file the platform renders
  the picker from.
- `<id>.json` — one file per preset, same object as its index entry.
- `../fun_camera_i2v.json` — the ported Wan 2.2 Fun Camera workflow the
  `fun-*` presets patch.

## The three mechanisms

1. **Prompt recipes (33 presets, work today on both lanes).** Freefield's
   Cinema Studio runs its camera moves on LTX-2.5, but the moves themselves
   are prompt text: exact, tested phrases ("dolly-zooms, moving in while
   zooming out so the background stretches behind the subject"). Those
   phrases port directly to our Wan 2.2 lanes. Fidelity is honestly
   "approximate": prompt-guided, not a baked-in control. One move holds
   best; Freefield's lab found the model does not always keep the order
   when two or three are chained.
2. **Keyframe recipes (13 of the prompt presets carry one).** A
   deterministic crop-window transform of the start frame, made locally,
   becomes the end keyframe for the firstlast chain workflow, which steers
   the move harder than text. Spec'd in each preset's
   `keyframe_enhancement`; the API dispatcher does not expose the firstlast
   lane yet, so these are recipes, not wired features.
3. **Fun Camera conditioning (9 presets, live 2026-11-16, Pass 4).**
   Wan 2.2 Fun Camera takes the move as a camera embedding
   (`WanCameraEmbedding.camera_pose`: Static, Pan Up/Down/Left/Right,
   Zoom In/Out, Anti Clockwise (ACW), ClockWise (CW), plus a speed value).
   This is true camera control, the closest open-weights route to
   Higgsfield's Camera Controls. The two Fun Camera UNET files
   (15.3 GB each) are staged in the sinclip-models Volume, the
   worker takes `camera_pose` / `camera_speed` on the production
   lane, and the picker serves all nine presets as available. A
   Fun Camera preset is a single conditioned move: it cannot chain
   with other moves, and it runs one segment at a time with no end
   image (the platform refuses those combinations with a 422). The
   lane renders with the LightX2V distilled patch (4 steps, cfg
   1.0), so a segment costs what a Production segment costs.
   Open item, stated plainly: the Pass 4 proof render (Pan Left)
   completed at that cost but showed no measurable pan under the
   distilled recipe; whether the stock 20-step recipe carries the
   conditioning better is unverified (see modal-worker/ENDPOINT.md).

## Composition grammar (from Freefield's Cinema Studio)

Motion prompt:

```
{moves}{scene}.{tempo} The look of a film{genre}{era}: {camera_body}; {lens}. Lighting: {lighting}. Color: {palette}.
```

- `{moves}` = "The camera " + up to 3 move fragments joined with ", then "
  + ". " — ordered, in the order the user picked them.
- Every look slot is optional; an empty slot contributes nothing. The full
  vocabulary (tempo, genre, era, camera body, lens, focal length,
  aperture, lighting, palette) lives in `presets-index.json` under `looks`,
  with the exact phrase each choice contributes.
- Defaults mirror Freefield's Cinema Studio: Full-Frame Cine Digital body,
  Classic Anamorphic lens, 35 mm, f/1.4, Auto lighting and palette.

Still prompt (for the future start-image feature — Freefield draws the
first frame with this template, then animates it):

```
A cinematic film still{genre}{era}: {scene}. The look: {camera_body}; {lens}; {focal}; {aperture}. Lighting: {lighting}. Color: {palette}. High dynamic range, ultra-detailed
```

## Platform pass: how to render this (patterns from open-higgsfield)

open-higgsfield (wide-trace/open-higgsfield, TypeScript/Next.js) does not
port as code, but its studio architecture is the right shape for
Moonfactory's composer. Patterns worth adopting, in its own terms:

1. **Catalog as data, one entry per model.** Each model is a declarative
   entry: `id`, `surface` (image|video), `label`, the media `roles` it
   accepts with counts (`start`, `end`, `reference`, `video`, `audio`),
   a `settings` schema, and submit paths per mode. The UI renders any
   model from its declaration — no per-model UI code. Moonfactory's
   `presets-index.json` follows the same rule for presets: the picker is
   rendered from the data file, never hand-built per preset.
2. **One composer for everything.** A single prompt bar serves both
   surfaces; prompt text is kept per surface. A media tray holds
   role-tagged inputs (start frame, end frame, references) and the
   model's declaration decides which roles are enabled and how many.
3. **Settings as pills generated from the schema.** Each declared setting
   (enum, range, or boolean, each with a default) becomes a pill under
   the prompt bar that opens a small popover: a narrow list for enums, a
   slider for ranges. Stored settings are kept per model and validated
   against the declaration (`parseSettings`): unknown keys dropped,
   missing keys defaulted. For Moonfactory: camera preset = an enum pill
   (the 33 moves), look slots = enum pills, Fun Camera speed = a range
   pill. Paid-only features (model switching, custom audio) are just
   settings the plan gate hides.
4. **"Results per press" is one control.** If the model declares a native
   count setting the control writes it; otherwise the studio submits once
   per result. The user sees one batch control either way.
5. **The gallery is the history log, and history is an input.** Finished
   runs render in the gallery grid under the floating composer, and the
   asset picker reads the same log — any finished run can be attached as
   an input to the next generation (this is what makes chaining feel
   native: pick a finished clip, "use as start", keep going).
6. **In-flight runs are tiles, not locks.** A generating run holds its own
   tile in the grid; the composer stays usable.

What not to copy: open-higgsfield is a bring-your-own-key client for
someone else's hosted platform API. Moonfactory owns its worker, its
accounts, and its billing, so the credential and platform-mapping layers
have no counterpart here. Only the studio/composer patterns transfer.

## Provenance

- Move phrases, look vocabulary, and composition grammar: Freefield's
  Cinema Studio (cinema-video / cinema-studio apps), MIT licensed.
  Freefield credits the camera and lens vocabulary to open-higgsfield-ai
  (MIT).
- Fun Camera workflow: Freefield's camera-moves app, MIT; adapted to
  Moonfactory conventions (placeholder prompts, VHS output node,
  720x1280 canvas).
- UX patterns above: observed in wide-trace/open-higgsfield. That repo
  ships no license file; Jack was told and approved using it anyway
  (2026-11-16). Patterns and notes only — no code was copied.
