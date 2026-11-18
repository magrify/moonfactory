import { build } from "esbuild";

await build({
  entryPoints: ["src/main.ts"],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  outfile: "scene/motion-scene.js",
  logLevel: "info",
});
console.log("bundled scene/motion-scene.js");
