"""Local proof for the animation studio pass (2026-11-27).

Renders the real compositions build_hf_composition emits
for the three new picker entries through headless Chromium
with the same seek contract the worker uses, and proves
the worker kernel's GIF conversion on frames captured from
the particle burst: frames to an MP4 with ffmpeg, then the
kernel's own write_gif turns it into the GIF.

Run:  /tmp/motion-venv python render_proof.py
"""
import asyncio
import glob as _glob
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "platform"))
for _sp in _glob.glob(str(Path.home() / "workspace" / "sinclip"
                          / "modal-venv" / "lib" / "python*"
                          / "site-packages")):
    sys.path.insert(0, _sp)
import app as platform_app  # noqa: E402

from playwright.async_api import async_playwright  # noqa: E402

GSAP = ROOT / "scratch" / "motion-principles-sample" / "gsap.min.js"

class _Kernel:
    pass


kernel = _Kernel()
# The kernel's top level resolves its Kaggle toolchain mount
# at import, so the whole module cannot load off Kaggle.
# Lift write_gif's own AST out of the shipped file and exec
# exactly that source, which is the code the worker runs.
import ast as _ast  # noqa: E402

_tree = _ast.parse(
    (ROOT / "worker-kaggle" / "hf_worker_kernel.py").read_text())
_fn = next(n for n in _tree.body
           if isinstance(n, _ast.FunctionDef) and n.name == "write_gif")
_ns = {"subprocess": subprocess, "Path": Path}
exec(compile(_ast.Module(body=[_fn], type_ignores=[]),
             "hf_worker_kernel.write_gif", "exec"), _ns)
kernel.write_gif = _ns["write_gif"]


async def shoot_at(page, t, name):
    await page.evaluate(
        "(t) => { const tl = window.__timelines['main']; tl.pause(t); }", t)
    await page.wait_for_timeout(250)
    await page.screenshot(path=str(HERE / name))
    print("shot", name)


async def main():
    pages = {
        "diagram": platform_app.build_hf_composition(
            "diagram", platform_app.hf_clean_fields("diagram", {
                "headline": "How booking works",
                "points": ("Guest finds you\nGuest books direct\n"
                           "You keep the fee"),
                "color": "#6366f1"})),
        "particles": platform_app.build_hf_composition(
            "particles", platform_app.hf_clean_fields("particles", {
                "headline": "Ten thousand guests",
                "subline": "Thank you to every one of you",
                "color": "#6366f1", "gif": True})),
        "vectorscene": platform_app.build_hf_composition(
            "vectorscene", platform_app.hf_clean_fields("vectorscene", {
                "headline": "Morning at the lake house",
                "subline": "Coffee is on at seven",
                "color": "#6366f1"})),
    }
    for name, html in pages.items():
        (HERE / f"{name}.html").write_text(html)
    async with async_playwright() as pw:
        _exe = _glob.glob(str(Path.home() / ".cache" / "ms-playwright"
                              / "chromium-*" / "chrome-linux*"
                              / "chrome"))[0]
        browser = await pw.chromium.launch(executable_path=_exe, args=[
            "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
            "--no-sandbox"])

        async def open_page(name, width=1080, height=1920):
            page = await browser.new_page(
                viewport={"width": width, "height": height})
            await page.route("**/gsap.min.js", lambda r: r.fulfill(
                path=str(GSAP)))
            await page.goto("file://" + str(HERE / f"{name}.html"))
            await page.wait_for_timeout(350)
            return page

        page = await open_page("diagram")
        await shoot_at(page, 2.6, "diagram-pulse.png")
        await shoot_at(page, 6.5, "diagram-lit.png")
        await page.close()

        page = await open_page("vectorscene")
        await shoot_at(page, 1.6, "vectorscene-rising.png")
        await shoot_at(page, 5.5, "vectorscene-day.png")
        await page.close()

        # The particle burst at full size for the stills, then
        # a half scale frame run for the GIF proof.
        page = await open_page("particles")
        await shoot_at(page, 1.1, "particles-burst.png")
        await shoot_at(page, 3.6, "particles-echo.png")
        await page.close()

        page = await open_page("particles", 540, 960)
        await page.add_style_tag(content=(
            "html { transform: scale(0.5); transform-origin: top left; }"))
        frames = HERE / "frames"
        frames.mkdir(exist_ok=True)
        for i in range(36):
            t = i / 12.0
            await page.evaluate(
                "(t) => { const tl = window.__timelines['main'];"
                " tl.pause(t); }", t)
            await page.wait_for_timeout(60)
            await page.screenshot(path=str(frames / f"f_{i:03d}.png"))
        await page.close()
        await browser.close()

    mp4 = HERE / "particles-proof.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", "12", "-i",
         str(frames / "f_%03d.png"), "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(mp4)],
        check=True, capture_output=True, text=True)
    gif = kernel.write_gif(mp4, HERE / "particles-proof.gif")
    assert gif is not None and gif.exists(), "write_gif failed"
    head = gif.read_bytes()[:6]
    assert head in (b"GIF87a", b"GIF89a"), head
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames",
         "-of", "csv=p=0", str(gif)],
        check=True, capture_output=True, text=True).stdout.strip()
    print("gif", gif.name, gif.stat().st_size, "bytes;", probe)
    print("RENDER DONE")


asyncio.run(main())
