// Moonfactory story template: shared constants, the scene plan shape (computed by the platform and
// embedded in the composition) and the camera programs. The bridge specific exports below exist only
// so the copied cast.ts module links unchanged; the town staging never calls them.
export const FPS = 30;
/** bank-top height: the town ground plane (the river surface is y = 0) */
export const D0 = 6.0;
export type V3 = [number, number, number];

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
/** smootherstep, the shorts' standard glide curve */
export const smooth = (u: number) => { u = clamp01(u); return u * u * u * (u * (u * 6 - 15) + 10); };
export const easeOut = (u: number) => 1 - (1 - clamp01(u)) ** 4;

// ---------------- scene plan (single source of truth: the platform's hf_story_plan) ----------------
export type SceneSpan = {
  a: number; b: number;                       // seconds from the start of the film
  kind: "title" | "line" | "outro";
  line: number;                               // index into props.lines for kind "line", else -1
  light: "day" | "golden";
  program: number;                            // index into PROGRAMS
};

// ---------------- camera programs ----------------
// One gentle smootherstep glide per scene between two keys, in the bridge grammar (glides and slow
// push ins, small rolls, cuts only on scene changes). All framings sit over the river or the quay and
// look at the near bank town (houses at z = -40.5 face the river, the quay runs along z = -31..-36.5).
export type CamKey = { pos: V3; look: V3 };
export type Program = { keys: [CamKey, CamKey]; roll: number; cInf: number };
export const PROGRAMS: Program[] = [
  // 0 title wide over the river
  { keys: [{ pos: [17, 14.5, -2], look: [-2, 8, -40] }, { pos: [14.5, 13.2, -7], look: [-3, 8, -40] }], roll: 0, cInf: 9 },
  // 1 quay track, gliding along the strollers
  { keys: [{ pos: [-27, 8.6, -20.5], look: [-20, 7.4, -36] }, { pos: [-5, 8.6, -20.5], look: [2, 7.4, -36] }], roll: 0, cInf: 13 },
  // 2 street eye level, slow dolly forward
  { keys: [{ pos: [2.4, 7.8, -24.5], look: [4.5, 7.3, -38] }, { pos: [2.4, 7.8, -29.5], look: [4.5, 7.3, -38] }], roll: 0, cInf: 16 },
  // 3 low over the water
  { keys: [{ pos: [-13, 1.7, -3], look: [3, 8.5, -40] }, { pos: [-9.5, 1.9, -5.5], look: [4, 8.2, -40] }], roll: 0.07, cInf: 8 },
  // 4 market close by the stalls
  { keys: [{ pos: [-11.8, 8.1, -26.8], look: [-18.5, 7.2, -34.5] }, { pos: [-13.8, 7.9, -28.6], look: [-19.5, 7.1, -34.8] }], roll: 0, cInf: 18 },
  // 5 high glide
  { keys: [{ pos: [25, 11.5, -13], look: [-3, 7.6, -37] }, { pos: [11, 10.4, -15.5], look: [-5, 7.6, -37] }], roll: -0.05, cInf: 10 },
  // 6 outro wide in golden light
  { keys: [{ pos: [15.5, 12, -10], look: [-2, 8, -39] }, { pos: [12.8, 11, -13], look: [-3, 8, -39] }], roll: 0, cInf: 9 },
];

/** camera pose of a program at scene local time lt (seconds into a scene of the given length) */
export const poseScene = (p: Program, dur: number, lt: number) => {
  const u = smooth(dur > 0 ? lt / dur : 0);
  const mix = (a: V3, b: V3): V3 => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
  return { pos: mix(p.keys[0].pos, p.keys[1].pos), look: mix(p.keys[0].look, p.keys[1].look), roll: p.roll };
};

/** which scene a film time falls in */
export const sceneAt = (scenes: SceneSpan[], t: number) => {
  for (let i = 0; i < scenes.length; i++) if (t < scenes[i].b || i === scenes.length - 1) return i;
  return scenes.length - 1;
};

// ---------------- bridge compatibility exports (dead code paths in cast.ts only) ----------------
export const FILES = [-1.35, -0.45, 0.45, 1.35];
export const ROW_GAP = 1.05;
export const COUNT = 24;
export const PARK: V3 = [0, D0, 0];
export const SHOTS: { a: number; b: number; light: "day" | "golden"; tau: (t: number) => number }[] = [];
export const SNAP = 0;
export const DROP0 = 0;
export const DROP1 = 1;
export const DROP_H = 0;
export const front = (_tau: number): V3 => [0, 0, 0];
export const marchPhase = (_tau: number) => 0;
export const stomp = (_tau: number) => 1;
export const deckY = (_z: number, _b?: unknown) => D0;
export const dropW = (_z: number) => 0;
export const dropU = (_u: number) => 0;
export const shapeZ = (_z: number) => 0;
export type BridgeState = { bounce: number; drop: number; snap: number };
