// Moonfactory story template bootstrap for HyperFrames: builds the town world and the shorts post
// chain (ink outlines, lens depth of field, bloom, neutral tone map, grade) and draws one frame per
// hf-seek event. The frame is a pure function of the seek time, as the HyperFrames seek protocol asks.
import * as THREE from "three";
import { EffectComposer, RenderPass, EffectPass, BloomEffect, ToneMappingEffect, ToneMappingMode } from "postprocessing";
import { InkPass } from "./ink";
import { LensBlur } from "./lensblur";
import { GradeEffect, gradeFor } from "./grade";
import { TownWorld } from "./world";
import { SceneSpan, sceneAt } from "./story";

type MotionProps = { title: string; lines: string[]; color: string; duration: number; scenes: SceneSpan[] };

const W = 1080, H = 1920;
const props = (window as unknown as { __motion: MotionProps }).__motion;
const canvas = document.getElementById("gl") as HTMLCanvasElement;

const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, stencil: false, powerPreference: "high-performance", preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(W, H, false);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.toneMapping = THREE.NoToneMapping;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, W / H, 0.2, 1500);
const world = new TownWorld(props.scenes);
scene.add(world.root);

const composer = new EffectComposer(renderer, new THREE.WebGLRenderTarget(W, H, { samples: 4, type: THREE.HalfFloatType }));
const lens = new LensBlur({ maxCoC: 24, nearScale: 1.2 });
const grade = new GradeEffect({ ky: [0, 0.1, 0.28, 0.6, 0.835, 0.945, 1], sat: [1, 1.05, 1], tintS: [0.01, 0.002], tintM: [0, 0], tintH: [0.006, 0], vig: [0.12, 0.45] });
// the scene colour render comes first: InkPass inks over the input buffer, it does not fill it
composer.addPass(new RenderPass(scene, camera));
composer.addPass(new InkPass(scene, camera));
composer.addPass(new EffectPass(camera, lens.pre));
composer.addPass(new EffectPass(camera,
  lens.main,
  new BloomEffect({ mipmapBlur: true, luminanceThreshold: 0.55, luminanceSmoothing: 0.3, intensity: 1.6, radius: 0.72 }),
  new ToneMappingEffect({ mode: ToneMappingMode.NEUTRAL }),
  grade));

(window as unknown as { __motionDebug: unknown }).__motionDebug = {
  renderer, scene, camera, composer, world, lens, grade,
  renderScene: () => renderer.render(scene, camera),
};
const viewPos = new THREE.Vector3();
function renderAt(t: number) {
  const idx = sceneAt(props.scenes, t);
  const info = world.update(idx, t, camera, scene);
  viewPos.copy(info.focus).applyMatrix4(camera.matrixWorldInverse);
  lens.focus = Math.max(0.5, -viewPos.z);
  lens.cInf = info.cInf;
  gradeFor(grade, info.light);
  composer.render();
}

let resolveReady: () => void = () => {};
const ready = new Promise<void>((r) => { resolveReady = r; });
// The HyperFrames runtime may already own window.__hf when this script runs (it does in the
// render pipeline, not in a plain page), and its object has no buildReady map. Create the map
// if it is missing; never assume the shape, and never let this registration kill the scene.
try {
  const hf = (window as unknown as { __hf?: { buildReady?: Record<string, Promise<void>> } }).__hf
    || ((window as unknown as { __hf: { buildReady?: Record<string, Promise<void>> } }).__hf = {});
  if (!hf.buildReady) hf.buildReady = {};
  hf.buildReady["motion"] = ready;
} catch { /* the seek listener below is the part that matters */ }

window.addEventListener("hf-seek", (e) => {
  const detail = (e as CustomEvent<{ time: number; waitUntil: (p: Promise<unknown>) => void }>).detail;
  renderAt(detail.time);
  detail.waitUntil(Promise.resolve());
});

// Warm up every shader and texture before the first captured frame, then open the gate.
// Synchronous on purpose: the seek protocol forbids leaning on rAF or timers during setup.
// Two frames only, one per light grade: the readiness gate on the render side is not
// patient, and the first captured seek re-poses the scene anyway.
renderAt(0);
renderAt(Math.max(0, props.scenes[props.scenes.length - 1].a + 0.5));
resolveReady();
