"""Local proof for the two new picker entries (2026-11-26).

Renders the real compositions build_hf_composition emits:
the product showcase at an exploded moment and assembled,
and the logo draw-in mid-draw and filled, through headless
Chromium with the same seek contract the worker uses.

Run:  /tmp/motion-venv python render_new.py
"""
import asyncio
import base64
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "platform"))
import glob as _glob  # noqa: E402
for _sp in _glob.glob(str(Path.home() / "workspace" / "sinclip"
                         / "modal-venv" / "lib" / "python*"
                         / "site-packages")):
    sys.path.insert(0, _sp)
import app as platform_app  # noqa: E402

from PIL import Image, ImageDraw  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

GSAP = (ROOT / "scratch" / "motion-principles-sample" / "gsap.min.js")


def product_photo() -> Path:
    """A simple product shot stand-in: a jar on a backdrop."""
    img = Image.new("RGB", (600, 760), (226, 222, 214))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([190, 200, 410, 620], 36, fill=(38, 92, 140))
    d.rounded_rectangle([215, 140, 385, 210], 18, fill=(24, 24, 28))
    d.rounded_rectangle([225, 330, 375, 470], 10, fill=(240, 238, 230))
    p = HERE / "photo.jpg"
    img.save(p, quality=88)
    return p


async def shoot(page, name):
    await page.screenshot(path=str(HERE / name))
    print("shot", name)


async def main():
    photo = product_photo()
    showcase_html = platform_app.build_hf_composition(
        "showcase3d",
        {"headline": "The jar, assembled", "subline": "piece by piece",
         "photo": "photo.jpg", "color": "#6366f1"}, 0.0)
    logodraw_html = platform_app.build_hf_composition(
        "logodraw", {"headline": "Moonfactory"}, 0.0)
    (HERE / "showcase.html").write_text(showcase_html)
    (HERE / "logodraw.html").write_text(logodraw_html)
    async with async_playwright() as pw:
        import glob as _g
        _exe = _g.glob(str(Path.home() / ".cache" / "ms-playwright"
                           / "chromium-*" / "chrome-linux*" / "chrome"))[0]
        browser = await pw.chromium.launch(executable_path=_exe, args=[
            "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
            "--no-sandbox"])
        # Showcase: stage the bundle and photo beside the page
        # the way the worker does.
        page = await browser.new_page(viewport={"width": 540, "height": 960})
        await page.route("**/forge3d-scene.js", lambda r: r.fulfill(
            path=str(ROOT / "motion" / "scene" / "forge3d-scene.js")))
        await page.route("**/three.min.js", lambda r: r.fulfill(
            path=str(ROOT / "scratch" / "motion-principles-sample" / "three.min.js")))
        await page.route("**/gsap.min.js", lambda r: r.fulfill(path=str(GSAP)))
        await page.route("**/photo.jpg", lambda r: r.fulfill(path=str(photo)))
        await page.goto("file://" + str(HERE / "showcase.html"))
        await page.wait_for_function(
            "() => window.__hf && window.__hf.buildReady && "
            "window.__hf.buildReady['motion']", timeout=30000)
        # The gate promise itself: the showcase waits for its
        # photo before it resolves.
        await page.evaluate(
            "() => Promise.race([window.__hf.buildReady['motion'],"
            " new Promise(r => setTimeout(r, 8000))])")
        for t, name in ((0.3, "showcase-exploded.png"),
                        (3.0, "showcase-assembled.png")):
            await page.evaluate(
                """(t) => {
                  const ev = new CustomEvent('hf-seek', {detail: {
                    time: t, waitUntil: (p) => { window.__lastSeek = p; }}});
                  window.dispatchEvent(ev);
                  return window.__lastSeek;
                }""", t)
            await page.wait_for_timeout(400)
            el = await page.query_selector("#gl")
            await el.screenshot(path=str(HERE / name))
            print("shot", name)
        await page.close()
        # Logo draw-in: pure GSAP page, timeline seek.
        page = await browser.new_page(viewport={"width": 1080, "height": 1920})
        await page.route("**/gsap.min.js", lambda r: r.fulfill(path=str(GSAP)))
        await page.goto("file://" + str(HERE / "logodraw.html"))
        await page.wait_for_timeout(300)
        for t, name in ((0.9, "logodraw-mid.png"), (2.6, "logodraw-done.png")):
            await page.evaluate(
                "(t) => { const tl = window.__timelines['main']; tl.pause(t); }", t)
            await page.wait_for_timeout(250)
            await shoot(page, name)
        await browser.close()
    print("RENDER DONE")


asyncio.run(main())
