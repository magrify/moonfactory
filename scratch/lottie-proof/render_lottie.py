"""Local proof for the library empty-state Lottie
(2026-11-26): the vendored player and the hand-authored
Play Hollow animation, screenshotted mid-draw and settled
through headless Chromium. No external calls: both files
resolve from platform/lottie/ on disk.

Run:  /tmp/motion-venv python render_lottie.py
"""
import asyncio
import glob
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
LOTTIE = ROOT / "platform" / "lottie"

PAGE = """<!doctype html><html><body style="background:#0c0c0f">
<div id="box" style="width:200px;height:200px"></div>
<script src="lottie.min.js"></script>
<script>
  window.__anim = lottie.loadAnimation({ container: document.getElementById('box'),
    renderer: 'svg', loop: true, autoplay: false, path: 'play-hollow.json' });
</script></body></html>"""


async def main():
    from playwright.async_api import async_playwright
    (HERE / "index.html").write_text(PAGE)
    exe = glob.glob(str(Path.home() / ".cache" / "ms-playwright"
                        / "chromium-*" / "chrome-linux*" / "chrome"))[0]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=exe,
                                           args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 220, "height": 220})
        await page.route("**/lottie.min.js", lambda r: r.fulfill(
            path=str(LOTTIE / "lottie.min.js")))
        await page.route("**/play-hollow.json", lambda r: r.fulfill(
            path=str(LOTTIE / "play-hollow.json")))
        await page.goto("file://" + str(HERE / "index.html"))
        await page.wait_for_function(
            "() => window.__anim && window.__anim.isLoaded", timeout=15000)
        for frame, name in ((10, "lottie-mid.png"), (60, "lottie-done.png")):
            await page.evaluate(
                "(f) => { window.__anim.goToAndStop(f, true); }", frame)
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(HERE / name))
            print("shot", name)
        await browser.close()
    print("LOTTIE PROOF DONE")


asyncio.run(main())
