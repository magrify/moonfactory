// Moonfactory 3D motion templates for HyperFrames: five small
// deterministic three.js scenes (product turntable, globe arcs,
// city pin, house build, 3D bars) lifted from the MotionForge
// template family (github.com/aykutucesai/motionforge, MIT) and
// rebuilt to this platform's scene contract: the frame is a pure
// function of the hf-seek time, props ride in window.__motion,
// and the scene renders at half scale on the free worker's
// software GL, with no post chain, so a frame stays cheap.
import * as THREE from "three";

type BarPoint = [string, number];
type ForgeProps = {
  template: string;
  color: string;
  duration: number;
  points?: BarPoint[];
};

const W = 1080, H = 1920;
const SCALE = 0.5;
const RW = Math.round(W * SCALE), RH = Math.round(H * SCALE);
const props = (window as unknown as { __motion: ForgeProps }).__motion;
const canvas = document.getElementById("gl") as HTMLCanvasElement;
const ACCENT = new THREE.Color(props.color || "#6366f1");
const INK = new THREE.Color("#17171d");
const PAPER = new THREE.Color("#e8e6df");
const SLATE = new THREE.Color("#4a4a55");

const renderer = new THREE.WebGLRenderer({
  canvas, antialias: false, stencil: false,
  powerPreference: "high-performance", preserveDrawingBuffer: true,
});
renderer.setPixelRatio(1);
renderer.setSize(RW, RH, false);
renderer.toneMapping = THREE.NoToneMapping;

const scene = new THREE.Scene();
scene.background = new THREE.Color("#0c0c0f");
const camera = new THREE.PerspectiveCamera(38, W / H, 0.2, 1500);

scene.add(new THREE.HemisphereLight(0xf5f2e8, 0x1a1a22, 1.15));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(6, 12, 8);
scene.add(key);
const rim = new THREE.DirectionalLight(0xbfd0ff, 0.9);
rim.position.set(-8, 5, -6);
scene.add(rim);

// Small deterministic helpers. Every animation below is a
// closed-form function of the seek time: no clocks, no state.
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));
const easeOut = (v: number) => 1 - Math.pow(1 - clamp01(v), 3);
const easeInOut = (v: number) =>
  (v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2);
const easeBack = (v: number) => {
  const c = 1.70158, u = clamp01(v);
  return 1 + (c + 1) * Math.pow(u - 1, 3) + c * Math.pow(u - 1, 2);
};
const stage = (t: number, a: number, b: number) => clamp01((t - a) / (b - a));
function mulberry(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let z = s;
    z = Math.imul(z ^ (z >>> 15), z | 1);
    z ^= z + Math.imul(z ^ (z >>> 7), z | 61);
    return ((z ^ (z >>> 14)) >>> 0) / 4294967296;
  };
}
const mat = (color: THREE.Color | string, rough = 0.85) =>
  new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: 0.05 });
function blobShadow(r: number, opacity = 0.35) {
  const m = new THREE.Mesh(
    new THREE.CircleGeometry(r, 40),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity }));
  m.rotation.x = -Math.PI / 2;
  return m;
}

// ---------------------------------------------------------------------
// Product turntable: a stylized product box on a pedestal disc,
// one slow full turn, the camera easing in as it goes.
function buildTurntable(root: THREE.Group) {
  camera.position.set(0, 3.4, 13.5);
  camera.lookAt(0, 1.6, 0);
  const spin = new THREE.Group();
  root.add(spin);
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(3.4, 3.7, 0.5, 48), mat(SLATE, 0.6));
  disc.position.y = -1.6;
  spin.add(disc);
  const trim = new THREE.Mesh(new THREE.TorusGeometry(3.4, 0.07, 10, 64), mat(ACCENT, 0.4));
  trim.rotation.x = Math.PI / 2;
  trim.position.y = -1.34;
  spin.add(trim);
  const shadow = blobShadow(3.1);
  shadow.position.y = -1.84;
  root.add(shadow);
  const product = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(3.1, 3.9, 3.1), mat(PAPER, 0.55));
  product.add(body);
  const lid = new THREE.Mesh(new THREE.BoxGeometry(3.3, 0.5, 3.3), mat(INK, 0.5));
  lid.position.y = 2.15;
  product.add(lid);
  const band = new THREE.Mesh(new THREE.BoxGeometry(3.16, 1.05, 3.16), mat(ACCENT, 0.45));
  band.position.y = 0.25;
  product.add(band);
  const knot = new THREE.Mesh(new THREE.TorusGeometry(0.55, 0.16, 10, 28), mat(ACCENT, 0.4));
  knot.position.y = 2.62;
  knot.rotation.x = 0.5;
  product.add(knot);
  product.position.y = 0.55;
  spin.add(product);
  const D = props.duration;
  return (t: number) => {
    const rise = easeBack(stage(t, 0.1, 0.9));
    product.position.y = 0.55 - (1 - clamp01(rise)) * 2.2;
    spin.rotation.y = easeInOut(stage(t, 0.4, D - 0.3)) * Math.PI * 2 + 0.5;
    camera.position.z = 13.5 - easeInOut(stage(t, 0, D)) * 1.6;
    camera.lookAt(0, 1.6, 0);
  };
}

// ---------------------------------------------------------------------
// Globe arcs: a wire globe turning slowly while three routes draw
// themselves bead by bead, with a pulse riding each one.
function buildGlobe(root: THREE.Group) {
  camera.position.set(0, 1.2, 12.5);
  camera.lookAt(0, 0.6, 0);
  const globe = new THREE.Group();
  globe.position.y = 0.6;
  root.add(globe);
  const R = 3.6;
  const core = new THREE.Mesh(new THREE.SphereGeometry(R * 0.985, 40, 28), mat("#12121a", 0.95));
  globe.add(core);
  const wire = new THREE.Mesh(
    new THREE.SphereGeometry(R, 24, 16),
    new THREE.MeshBasicMaterial({ color: 0x8f96ad, wireframe: true, transparent: true, opacity: 0.4 }));
  globe.add(wire);
  const rand = mulberry(41);
  const routes: { pts: THREE.Vector3[]; beads: THREE.Mesh[]; pulse: THREE.Mesh }[] = [];
  const beadGeo = new THREE.SphereGeometry(0.11, 8, 8);
  for (let r = 0; r < 3; r++) {
    const lat1 = (rand() - 0.5) * 1.6, lon1 = rand() * Math.PI * 2;
    const lat2 = (rand() - 0.5) * 1.6, lon2 = lon1 + 1.6 + rand() * 1.8;
    const p = (lat: number, lon: number, lift: number) => new THREE.Vector3(
      (R + lift) * Math.cos(lat) * Math.cos(lon),
      (R + lift) * Math.sin(lat),
      (R + lift) * Math.cos(lat) * Math.sin(lon));
    const a = p(lat1, lon1, 0), b = p(lat2, lon2, 0);
    const mid = a.clone().add(b).multiplyScalar(0.5).normalize()
      .multiplyScalar(R + 1.5 + rand() * 0.7);
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    const pts = curve.getPoints(26);
    const beads: THREE.Mesh[] = [];
    for (const pt of pts) {
      const bead = new THREE.Mesh(beadGeo, mat(ACCENT, 0.4));
      bead.position.copy(pt);
      bead.visible = false;
      globe.add(bead);
      beads.push(bead);
    }
    for (const end of [a, b]) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.2, 10, 10), mat(PAPER, 0.4));
      dot.position.copy(end);
      globe.add(dot);
    }
    const pulse = new THREE.Mesh(new THREE.SphereGeometry(0.24, 10, 10),
      new THREE.MeshBasicMaterial({ color: ACCENT }));
    globe.add(pulse);
    routes.push({ pts, beads, pulse });
  }
  const D = props.duration;
  return (t: number) => {
    globe.rotation.y = 0.6 + t * 0.16;
    routes.forEach((route, i) => {
      const draw = stage(t, 0.7 + i * 0.8, 2.3 + i * 0.8);
      const lit = Math.floor(easeOut(draw) * route.beads.length);
      route.beads.forEach((bead, j) => { bead.visible = j < lit; });
      const ride = (t * 0.22 + i / 3) % 1;
      const idx = Math.min(route.pts.length - 1, Math.floor(ride * route.pts.length));
      route.pulse.position.copy(route.pts[idx]);
      route.pulse.visible = draw >= 1;
    });
  };
}

// ---------------------------------------------------------------------
// City pin: a little city grid, then the pin drops in and its
// rings walk outward across the ground.
function buildCityPin(root: THREE.Group) {
  camera.position.set(7.5, 6.4, 10.5);
  camera.lookAt(0, 0.8, 0);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(15, 15), mat("#101016", 1));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.01;
  root.add(ground);
  const rand = mulberry(7);
  const city = new THREE.Group();
  root.add(city);
  for (let gx = -3; gx <= 3; gx++) {
    for (let gz = -3; gz <= 3; gz++) {
      if (Math.abs(gx) <= 0 && Math.abs(gz) <= 0) continue;
      if (rand() < 0.25) continue;
      const h = 0.5 + rand() * 2.3;
      const b = new THREE.Mesh(new THREE.BoxGeometry(1.15, h, 1.15),
        mat(rand() < 0.2 ? SLATE : INK, 0.9));
      b.position.set(gx * 1.75 + (rand() - 0.5) * 0.3, h / 2, gz * 1.75 + (rand() - 0.5) * 0.3);
      city.add(b);
    }
  }
  const pin = new THREE.Group();
  const head = new THREE.Mesh(new THREE.SphereGeometry(1.05, 24, 18), mat(ACCENT, 0.35));
  head.position.y = 1.5;
  pin.add(head);
  const hole = new THREE.Mesh(new THREE.SphereGeometry(0.42, 14, 12), mat("#0c0c0f", 0.6));
  hole.position.y = 1.5;
  pin.add(hole);
  const tip = new THREE.Mesh(new THREE.ConeGeometry(0.62, 1.7, 24), mat(ACCENT, 0.35));
  tip.rotation.x = Math.PI;
  tip.position.y = 0.35;
  pin.add(tip);
  pin.position.set(0, 9, 0);
  root.add(pin);
  const rings: THREE.Mesh[] = [];
  for (let i = 0; i < 2; i++) {
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.92, 1.0, 48),
      new THREE.MeshBasicMaterial({ color: ACCENT, transparent: true, opacity: 0, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.03;
    root.add(ring);
    rings.push(ring);
  }
  const D = props.duration;
  return (t: number) => {
    const drop = easeOut(stage(t, D * 0.16, D * 0.42));
    pin.position.y = 9 - drop * 9 + Math.abs(Math.sin(stage(t, D * 0.42, D * 0.62) * Math.PI)) * 0.55 * (drop >= 1 ? 1 : 0);
    pin.rotation.y = t * 0.25;
    rings.forEach((ring, i) => {
      const ph = ((t * 0.45 + i * 0.5) % 1);
      const on = t > D * 0.45;
      const s = 0.6 + ph * 4.6;
      ring.scale.set(s, s, s);
      (ring.material as THREE.MeshBasicMaterial).opacity = on ? (1 - ph) * 0.65 : 0;
    });
    camera.position.x = 7.5 - Math.sin(t * 0.11) * 1.1;
    camera.lookAt(0, 0.8, 0);
  };
}

// ---------------------------------------------------------------------
// House build: the house assembles itself piece by piece, slab,
// walls, roof, door, lit windows, chimney, in that order.
function buildHouse(root: THREE.Group) {
  camera.position.set(8.6, 5.4, 11.5);
  camera.lookAt(0, 1.7, 0);
  const shadow = blobShadow(4.4);
  shadow.position.y = 0.02;
  root.add(shadow);
  const parts: { obj: THREE.Object3D; a: number; b: number; y: number; drop?: number }[] = [];
  const add = (obj: THREE.Object3D, a: number, b: number, drop = 0) => {
    obj.position.y += 0;
    root.add(obj);
    parts.push({ obj, a, b, y: obj.position.y, drop });
  };
  const slab = new THREE.Mesh(new THREE.BoxGeometry(7.4, 0.5, 6.2), mat(SLATE, 0.9));
  slab.position.y = 0.25;
  add(slab, 0.05, 0.2);
  const walls = new THREE.Mesh(new THREE.BoxGeometry(6.2, 3.1, 5.0), mat(PAPER, 0.8));
  walls.position.y = 2.05;
  add(walls, 0.18, 0.4);
  const roof = new THREE.Mesh(new THREE.ConeGeometry(5.1, 2.5, 4), mat(ACCENT, 0.6));
  roof.rotation.y = Math.PI / 4;
  roof.scale.z = 0.82;
  roof.position.y = 4.85;
  add(roof, 0.4, 0.58, 2.6);
  const door = new THREE.Mesh(new THREE.BoxGeometry(1.1, 2.0, 0.12), mat(INK, 0.7));
  door.position.set(0, 1.5, 2.53);
  add(door, 0.58, 0.7);
  const winMat = new THREE.MeshStandardMaterial({
    color: 0xffca7a, emissive: 0xff9d2e, emissiveIntensity: 1.4, roughness: 0.4 });
  for (const x of [-1.9, 1.9]) {
    const win = new THREE.Mesh(new THREE.BoxGeometry(1.15, 1.15, 0.1), winMat);
    win.position.set(x, 2.3, 2.53);
    add(win, 0.66, 0.78);
  }
  const chimney = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.7, 0.9), mat(INK, 0.8));
  chimney.position.set(1.8, 5.6, 0);
  add(chimney, 0.74, 0.86, 1.8);
  const sun = new THREE.Mesh(new THREE.SphereGeometry(0.55, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0xffca7a }));
  sun.position.set(-6.4, 6.8, -3);
  root.add(sun);
  const D = props.duration;
  return (t: number) => {
    for (const p of parts) {
      const s = easeBack(stage(t, D * p.a, D * p.b));
      const u = clamp01(s);
      p.obj.scale.set(Math.max(0.001, u), Math.max(0.001, u), Math.max(0.001, u));
      p.obj.position.y = p.y + (p.drop || 0) * (1 - u);
    }
    camera.position.x = 8.6 - Math.sin(stage(t, 0, D) * Math.PI) * 1.6;
    camera.lookAt(0, 1.7, 0);
  };
}

// ---------------------------------------------------------------------
// Bars 3D: the data video's rows as real bars in space, rising in
// a stagger while the camera orbits a little. Values ride as
// canvas sprites so they stay exact at every seek.
function textSprite(text: string, size = 54, color = "#f4f4f1") {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 128;
  const g = c.getContext("2d")!;
  g.font = `800 ${size}px sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = color;
  g.fillText(text, 128, 66);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true }));
  sp.scale.set(2.3, 1.15, 1);
  return sp;
}
function buildBars(root: THREE.Group) {
  camera.position.set(6.8, 5.2, 11.8);
  camera.lookAt(0, 1.6, 0);
  const points = (props.points || []).slice(0, 8);
  const maxv = Math.max(...points.map((p) => p[1]), 1);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(14, 7), mat("#101016", 1));
  floor.rotation.x = -Math.PI / 2;
  root.add(floor);
  const grid = new THREE.GridHelper(14, 14, 0x2c2c36, 0x22222c);
  grid.position.y = 0.01;
  (grid.material as THREE.Material).transparent = true;
  (grid.material as THREE.Material).opacity = 0.6;
  root.add(grid);
  const n = points.length;
  const slot = Math.min(2.2, 11 / Math.max(1, n));
  const bars: { mesh: THREE.Mesh; sprite: THREE.Sprite; h: number; i: number }[] = [];
  points.forEach(([label, value], i) => {
    const h = 0.25 + (value / maxv) * 4.6;
    const isMax = value === maxv;
    const bar = new THREE.Mesh(new THREE.BoxGeometry(slot * 0.58, 1, 1.5),
      mat(isMax ? ACCENT : SLATE, 0.55));
    bar.position.set((i - (n - 1) / 2) * slot, 0, 0);
    bar.scale.y = 0.001;
    root.add(bar);
    const sprite = textSprite(label + " " + (Number.isInteger(value) ? String(value) : value.toFixed(1)));
    sprite.position.set(bar.position.x, h + 0.85, 0);
    sprite.material.opacity = 0;
    root.add(sprite);
    bars.push({ mesh: bar, sprite, h, i });
  });
  const D = props.duration;
  return (t: number) => {
    for (const b of bars) {
      const s = easeOut(stage(t, 0.35 + b.i * 0.22, 1.35 + b.i * 0.22));
      b.mesh.scale.y = Math.max(0.001, b.h * s);
      b.mesh.position.y = (b.h * s) / 2;
      b.sprite.material.opacity = stage(t, 1.0 + b.i * 0.22, 1.5 + b.i * 0.22);
    }
    const orbit = Math.sin(stage(t, 0, D) * Math.PI) * 0.35;
    camera.position.x = 6.8 * Math.cos(orbit) + 11.8 * Math.sin(orbit) * 0.4;
    camera.lookAt(0, 1.6, 0);
  };
}

// ---------------------------------------------------------------------
const builders: Record<string, (root: THREE.Group) => (t: number) => void> = {
  turntable3d: buildTurntable,
  globe3d: buildGlobe,
  citypin3d: buildCityPin,
  house3d: buildHouse,
  bars3d: buildBars,
};
const root = new THREE.Group();
scene.add(root);
const pose = (builders[props.template] || buildTurntable)(root);

function renderAt(t: number) {
  pose(t);
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
  renderAt(detail.time);
  detail.waitUntil(Promise.resolve());
});

renderAt(0);
renderAt(Math.min(1.2, props.duration / 2));
resolveReady();
