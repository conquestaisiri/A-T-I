"""Headless-browser verification of all dashboard views: console + screenshots."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT_DIR = Path(r"C:\Users\USER\Desktop\A-T-I\Trading-Intelligence")


async def main() -> int:
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="msedge", headless=True)
        except Exception:
            browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 900})

        errors: list[str] = []
        page.on(
            "console",
            lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: errors.append(f"[pageerror] {exc}"))

        await page.goto("http://127.0.0.1:8000/", wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(OUT_DIR / "shot_trading.png"))

        # Candle count sanity: chart canvas should exist and be non-zero sized
        canvas = await page.query_selector(".tv-lightweight-charts canvas")
        canvas_info = None
        if canvas:
            box = await canvas.bounding_box()
            canvas_info = f"canvas {box['width']:.0f}x{box['height']:.0f}" if box else "no box"
        else:
            canvas_info = "NO CANVAS FOUND"

        for view, label in [
            ("system", "shot_system.png"),
            ("portfolio", "shot_portfolio.png"),
            ("forex", "shot_forex.png"),
        ]:
            items = await page.query_selector_all(".nav-i")
            idx = {"system": 5, "portfolio": 2, "forex": 4}[view]
            if idx < len(items):
                await items[idx].click()
                await page.wait_for_timeout(2500)
                await page.screenshot(path=str(OUT_DIR / label))

        await browser.close()

    print("CANVAS:", canvas_info)
    print("--- CONSOLE ERRORS ---")
    for e in errors[:15] or ["(none)"]:
        print(e)
    for f in ("shot_trading.png", "shot_system.png", "shot_portfolio.png", "shot_forex.png"):
        print(f, (OUT_DIR / f).exists())
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
