import { build } from "esbuild";

const shared = {
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  logLevel: "info",
};

await build({
  ...shared,
  entryPoints: ["src/main.ts"],
  outfile: "scene/motion-scene.js",
});
console.log("bundled scene/motion-scene.js");

// The 3D motion templates (2026-11-25): five small three.js
// scenes behind one dispatcher bundle.
await build({
  ...shared,
  entryPoints: ["src/forge3d.ts"],
  outfile: "scene/forge3d-scene.js",
});
console.log("bundled scene/forge3d-scene.js");

// The photo presenter (2026-11-25): the talking photo puppet.
await build({
  ...shared,
  entryPoints: ["src/presenter.ts"],
  outfile: "scene/presenter-scene.js",
});
console.log("bundled scene/presenter-scene.js");
