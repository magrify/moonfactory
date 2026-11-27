"""Local before/after render harness for the motion templates.

Builds one template's composition with the platform's own
build_hf_composition (source-sliced out of platform/app.py the
way the scratch checks do it), stages it beside the CURRENT
built scene bundle in motion/scene/, then drives headless
Chromium through hf-seek events at a fixed frame rate and
screenshots the canvas. Frames land in frames-<label>/ for
ffmpeg to encode.

Run: /tmp/motion-venv/bin/python render_sample.py <label> [template]
"""
import importlib.util
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
APP = (ROOT / "platform" / "app.py").read_text()

spec = importlib.util.spec_from_loader("hf_slice", loader=None)
mod = importlib.util.module_from_spec(spec)
mod.__dict__["re"] = re
code = APP[APP.index("HF_TEMPLATES = {"):APP.index("def hf_kaggle_push")]
exec(compile(code, "hf_slice", "exec"), mod.__dict__)

label = sys.argv[1] if len(sys.argv) > 1 else "before"
template = sys.argv[2] if len(sys.argv) > 2 else "turntable3d"
FPS = 12
SECONDS = 2.5

fields = mod.hf_clean_fields(template, {
    "headline": "The mug that sells itself",
    "subline": "Now in five colors",
    "title": "Sales", "data": "Jan, 10\nFeb, 20",
    "script": "Hello there",
})
if template == "presenter":
    fields["duration"] = 8.0
    fields["photo"] = "photo.jpg"
html = mod.build_hf_composition(template, fields, 0.0)

stage = HERE / f"stage-{label}"
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir(parents=True)
(stage / "index.html").write_text(html)
shutil.copy(HERE / "gsap.min.js", stage / "gsap.min.js")
for bundle in ("forge3d-scene.js", "presenter-scene.js", "motion-scene.js"):
    src = ROOT / "motion" / "scene" / bundle
    if src.exists():
        shutil.copy(src, stage / bundle)

from playwright.sync_api import sync_playwright

frames_dir = HERE / f"frames-{label}"
if frames_dir.exists():
    shutil.rmtree(frames_dir)
frames_dir.mkdir(parents=True)

import glob
chromium = sorted(glob.glob(
    str(Path.home() / ".cache/ms-playwright/chromium-*/chrome-linux64/chrome")))
exe = chromium[-1] if chromium else None

with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=exe,
        args=["--enable-unsafe-swiftshader", "--use-gl=angle",
              "--use-angle=swiftshader", "--disable-gpu-sandbox",
              "--no-sandbox"])
    page = browser.new_page(viewport={"width": 540, "height": 960})
    page.goto((stage / "index.html").as_uri())
    page.wait_for_function(
        "() => window.__hf && window.__hf.buildReady && "
        "window.__hf.buildReady['motion']", timeout=30000)
    page.wait_for_timeout(300)
    n = int(FPS * SECONDS)
    for i in range(n + 1):
        t = i / FPS
        page.evaluate(
            """(t) => {
              const ev = new CustomEvent('hf-seek', {detail: {
                time: t, waitUntil: (p) => { window.__lastSeek = p; }}});
              window.dispatchEvent(ev);
              return window.__lastSeek;
            }""", t)
        page.wait_for_timeout(60)
        el = page.query_selector("#gl")
        el.screenshot(path=str(frames_dir / f"f{i:03d}.png"))
    browser.close()
print(f"rendered {n + 1} frames to {frames_dir}")
