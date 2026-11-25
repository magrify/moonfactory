// Moonfactory photo presenter for HyperFrames, lifted from the
// MotionForge photo-avatar template family
// (github.com/aykutucesai/motionforge, MIT) and rebuilt to this
// platform's scene contract: a photo or illustration stands as a
// 2.5D puppet in a small studio set, and its mouth follows the
// voiceover through the mouth curve the worker computes from the
// finished voice track (mouth_curve.py writes mouth-curve.js
// beside this composition before the render). The frame is a
// pure function of the hf-seek time: the curve is indexed by
// frame, the sway is closed-form, nothing reads a clock.
import * as THREE from "three";

type PresenterProps = {
  color: string;
  duration: number;
  photo: string;
  mouthX: number; // percent across the photo, 0..100
  mouthY: number; // percent down the photo, 0..100
};

const W = 1080, H = 1920;
const SCALE = 0.5;
const RW = Math.round(W * SCALE), RH = Math.round(H * SCALE);
const FPS = 24;
const props = (window as unknown as { __motion: PresenterProps }).__motion;
const curve = (window as unknown as { __mouth?: number[] }).__mouth || [];
const canvas = document.getElementById("gl") as HTMLCanvasElement;
const ACCENT = new THREE.Color(props.color || "#6366f1");

const renderer = new THREE.WebGLRenderer({
  canvas, antialias: false, stencil: false,
  powerPreference: "high-performance", preserveDrawingBuffer: true,
});
renderer.setPixelRatio(1);
renderer.setSize(RW, RH, false);
renderer.toneMapping = THREE.NoToneMapping;

const scene = new THREE.Scene();
scene.background = new THREE.Color("#0c0c0f");
const camera = new THREE.PerspectiveCamera(38, W / H, 0.2, 200);
camera.position.set(0, 0, 14.2);
camera.lookAt(0, 0, 0);

scene.add(new THREE.HemisphereLight(0xf5f2e8, 0x1a1a22, 1.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 1.6);
keyLight.position.set(5, 8, 9);
scene.add(keyLight);

// The set: a soft backdrop glow in the accent color and a floor
// shadow, so the portrait stands in a place instead of floating.
const backdrop = new THREE.Mesh(
  new THREE.CircleGeometry(7.6, 40),
  new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity: 0.16 }));
backdrop.position.set(0, 1.2, -3.2);
scene.add(backdrop);
const shadow = new THREE.Mesh(
  new THREE.CircleGeometry(3.6, 40),
  new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.4 }));
shadow.rotation.x = -Math.PI / 2;
shadow.position.set(0, -5.45, -0.5);
shadow.scale.set(1, 0.42, 1);
scene.add(shadow);

// The puppet: a framed plane carrying the photo, and a mouth
// ellipse riding on it at the mouth position the user picked.
// The frame group sways with the speech energy, so a talking
// photo moves like a puppet and a silent one settles.
const puppet = new THREE.Group();
scene.add(puppet);
const frame = new THREE.Mesh(
  new THREE.BoxGeometry(6.7, 10.1, 0.22),
  new THREE.MeshStandardMaterial({ color: 0x17171d, roughness: 0.5, metalness: 0.1 }));
puppet.add(frame);
const portrait = new THREE.Mesh(
  new THREE.PlaneGeometry(6.3, 9.7),
  new THREE.MeshBasicMaterial({ color: 0x23232c }));
portrait.position.z = 0.13;
puppet.add(portrait);
const mouth = new THREE.Mesh(
  new THREE.CircleGeometry(0.30, 24),
  new THREE.MeshBasicMaterial({ color: 0x4a2320, transparent: true, opacity: 0.88 }));
mouth.position.z = 0.16;
puppet.add(mouth);

let photoReady = false;
new THREE.TextureLoader().load(props.photo, (tex) => {
  tex.colorSpace = THREE.SRGBColorSpace;
  const img = tex.image as HTMLImageElement;
  // Cover-fit the photo into the portrait: crop the longer axis
  // through the texture transform so nothing stretches.
  const target = 6.3 / 9.7;
  const aspect = img.width / img.height;
  if (aspect > target) {
    tex.repeat.set(target / aspect, 1);
    tex.offset.set((1 - target / aspect) / 2, 0);
  } else {
    tex.repeat.set(1, aspect / target);
    tex.offset.set(0, (1 - aspect / target) / 2);
  }
  tex.needsUpdate = true;
  (portrait.material as THREE.MeshBasicMaterial).map = tex;
  (portrait.material as THREE.MeshBasicMaterial).color.set(0xffffff);
  (portrait.material as THREE.MeshBasicMaterial).needsUpdate = true;
  // The mouth rides in photo space: x across, y down from the
  // top, both as the percents the user picked in the form.
  mouth.position.x = ((props.mouthX || 50) / 100 - 0.5) * 6.3;
  mouth.position.y = (0.5 - (props.mouthY || 68) / 100) * 9.7;
  photoReady = true;
  renderAt(0);
  resolveReady();
});

function mouthAt(t: number) {
  if (!curve.length) return 0;
  const i = Math.max(0, Math.min(curve.length - 1, Math.floor(t * FPS)));
  return curve[i];
}

function renderAt(t: number) {
  const talk = mouthAt(t);
  // Mouth: opens with the curve, a resting slit between words.
  const open = 0.22 + talk * 1.15;
  mouth.scale.set(1 - talk * 0.18, open, 1);
  (mouth.material as THREE.MeshBasicMaterial).opacity = 0.55 + talk * 0.4;
  // Puppet sway: a slow idle drift plus a nod that follows the
  // speech energy, so phrases move and pauses settle.
  puppet.rotation.z = Math.sin(t * 0.7) * 0.008 + Math.sin(t * 2.1) * 0.006 * talk;
  puppet.rotation.y = Math.sin(t * 0.45) * 0.05;
  puppet.position.y = Math.sin(t * 1.7) * 0.05 * talk + Math.sin(t * 0.9) * 0.03;
  backdrop.material.opacity = 0.13 + talk * 0.07;
  renderer.render(scene, camera);
}

let resolveReady: () => void = () => {};
const ready = new Promise<void>((r) => { resolveReady = r; });
try {
  const hf = (window as unknown as { __hf?: { buildReady?: Record<string, Promise<void>> } }).__hf
    || ((window as unknown as { __hf: { buildReady?: Record<string, Promise<void>> } }).__hf = {});
  if (!hf.buildReady) hf.buildReady = {};
  hf.buildReady["motion"] = ready;
} catch { /* the seek listener below is the part that matters */ }

window.addEventListener("hf-seek", (e) => {
  const detail = (e as CustomEvent<{ time: number; waitUntil: (p: Promise<unknown>) => void }>).detail;
  if (photoReady) renderAt(detail.time);
  detail.waitUntil(Promise.resolve());
});

// If the photo cannot load, open the gate anyway: the render
// then fails on a portrait-less frame set instead of hanging
// the job past its bound.
setTimeout(() => { if (!photoReady) resolveReady(); }, 4000);
renderAt(0);
