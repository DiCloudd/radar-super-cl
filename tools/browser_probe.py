"""Prueba si una tienda deja pasar un navegador REAL normal (sin stealth, sin resolver captchas).

Uso: python tools/browser_probe.py URL [--headed]
Reporta título, si quedó en desafío, y si hay __NEXT_DATA__ con productos.
"""
from __future__ import annotations

import asyncio
import re
import sys

from playwright.async_api import async_playwright


async def main(url: str, headed: bool) -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=not headed)
        page = await b.new_page(locale="es-CL")
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        print("status inicial:", resp.status if resp else None)
        for i in range(12):  # hasta 24 s: un desafío JS normal se resuelve solo en un navegador real
            title = await page.title()
            if not re.search(r"Just a moment|Un momento|Robot or human", title):
                break
            await page.wait_for_timeout(2000)
        html = await page.content()
        title = await page.title()
        print("título:", title)
        print("desafío pendiente:", bool(re.search(r"Just a moment|Robot or human|cf-turnstile", html)))
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        print("__NEXT_DATA__:", bool(m), "| productos (displayName):", html.count('"displayName"'))
        await b.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--headed" in sys.argv))
