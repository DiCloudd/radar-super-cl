"""Lectura con un navegador REAL (Microsoft Edge instalado) para tiendas que solo atienden
navegadores, como Tottus. Solo para el nodo en Chile (el PC del usuario); nunca en Actions.

Sin evasión: es Edge normal y visible (minimizado), con Playwright estándar. No se ocultan
señales de automatización, no se falsifican huellas, no se usan proxies ni se resuelven captchas.
Si la tienda muestra un desafío que no se resuelve solo en unos segundos, se lanza BlockedError
y el monitor pausa esa tienda con backoff exponencial.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from pathlib import Path

from . import config
from .fetchers import BlockedError, FetchError

log = logging.getLogger("radar.browser")
_CHALLENGE = re.compile(r"Just a moment|Un momento|Robot or human|Attention Required", re.I)
PROFILE_DIR = config.DATA_DIR / "edge-profile"   # perfil propio (cookies normales de sesión)


class BrowserResponse:
    def __init__(self, text: str, status: int):
        self.text, self.status_code = text, status


class BrowserFetcher:
    def __init__(self, min_spacing: float | None = None, channel: str = "msedge"):
        if min_spacing is None:
            import os
            min_spacing = float(os.environ.get("BROWSER_SPACING_S", "75"))
        self.min_spacing, self.channel = min_spacing, channel
        self._pw = self._ctx = self._page = None
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def _ensure(self):
        if self._page:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        self._ctx = await self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel=self.channel, headless=False, locale="es-CL",
            args=["--start-minimized"], viewport={"width": 1280, "height": 900},
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

    async def get(self, url: str, *, referer: str | None = None, cookies: dict | None = None,
                  headers: dict | None = None) -> BrowserResponse:
        async with self._lock:
            await self._ensure()
            wait = self._last + self.min_spacing + random.uniform(0, 2) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as e:  # noqa: BLE001
                raise FetchError(f"navegador: {e}") from e
            for _ in range(10):   # un desafío JS normal se resuelve solo en un navegador real
                if not _CHALLENGE.search(await self._page.title()):
                    break
                await self._page.wait_for_timeout(2000)
            html = await self._page.content()
            if _CHALLENGE.search(await self._page.title()) or "cf-turnstile" in html:
                raise BlockedError(url.split("/")[2], "desafío en navegador", resp.status if resp else None)
            return BrowserResponse(html, resp.status if resp else 200)

    async def close(self):
        try:
            if self._ctx:
                await self._ctx.close()
            if self._pw:
                await self._pw.stop()
        finally:
            self._pw = self._ctx = self._page = None
