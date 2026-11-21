"""Cliente HTTP cortés: robots.txt, 1 petición a la vez por dominio (≥4 s + jitter),
reintentos ante errores transitorios y detección de bloqueos (→ BlockedError).

Nunca intenta esquivar protecciones: si una tienda bloquea, se lanza BlockedError y el
monitor pausa esa tienda con backoff exponencial.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import ssl
import time
from collections import defaultdict, deque
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import config

log = logging.getLogger("radar.fetch")

BROWSER_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.6",
}

BLOCK_MARKERS = [
    ("perimeterx", re.compile(r"px-captcha|Robot or human\?", re.I)),
    ("cloudflare", re.compile(r"<title>Just a moment\.\.\.</title>|cf_chl_opt|challenge-platform", re.I)),
    ("akamai", re.compile(r"<TITLE>Access Denied</TITLE>", re.I)),
    ("captcha", re.compile(r"g-recaptcha\"|hcaptcha\.com/1/api|captcha-delivery\.com", re.I)),
]


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    def __init__(self, domain: str, reason: str, status: int | None = None):
        super().__init__(f"{domain} bloqueó: {reason} (HTTP {status})")
        self.domain, self.reason, self.status = domain, reason, status


class RobotsDisallowed(FetchError):
    pass


def detect_block(status: int, text: str) -> str | None:
    head = text[:60_000]
    for name, rx in BLOCK_MARKERS:
        if rx.search(head):
            return name
    if status in (403, 429):
        return f"http{status}"
    return None


class Fetcher:
    def __init__(self, min_spacing: float = config.MIN_SPACING_S, jitter: float = config.JITTER_S):
        self.min_spacing, self.jitter = min_spacing, jitter
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._ssl: dict[str, ssl.SSLContext] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        self.minute_log: dict[str, deque] = defaultdict(deque)
        self.counts: dict[str, int] = defaultdict(int)

    # ---------- infraestructura ----------
    def _client(self, host: str) -> httpx.AsyncClient:
        if host not in self._clients:
            verify: ssl.SSLContext | bool = self._ssl.get(host, True)
            self._clients[host] = httpx.AsyncClient(
                http2=True, follow_redirects=True, timeout=config.REQUEST_TIMEOUT_S,
                headers=BROWSER_HEADERS, verify=verify,
            )
        return self._clients[host]

    async def _fix_tls(self, host: str) -> None:
        """Servidor con cadena incompleta: completar intermedio vía AIA (verificación intacta)."""
        from .tls import ssl_context_for
        self._ssl[host] = await asyncio.to_thread(ssl_context_for, host)
        old = self._clients.pop(host, None)
        if old:
            await old.aclose()
        log.info("TLS de %s completado vía AIA", host)

    async def close(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    async def _wait_turn(self, host: str) -> None:
        last = self._last.get(host, 0.0)
        wait = last + self.min_spacing + random.uniform(0, self.jitter) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last[host] = time.monotonic()
        dq = self.minute_log[host]
        now = time.time()
        dq.append(now)
        while dq and dq[0] < now - 60:
            dq.popleft()
        self.counts[host] += 1
        if len(dq) > config.BUDGET_WARN_PER_MIN:
            log.warning("presupuesto: %s lleva %d peticiones/min", host, len(dq))

    def per_minute(self, host: str) -> int:
        return len(self.minute_log[host])

    # ---------- robots.txt ----------
    async def allowed(self, url: str) -> bool:
        p = urlparse(url)
        host = p.hostname or ""
        if host not in self._robots:
            rp = RobotFileParser()
            try:
                r = await self._raw_get(f"{p.scheme}://{host}/robots.txt", host)
                if r.status_code == 200 and "text/plain" in r.headers.get("content-type", ""):
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403) and detect_block(r.status_code, r.text):
                    raise BlockedError(host, detect_block(r.status_code, r.text) or "robots", r.status_code)
                else:
                    rp.parse([])  # sin robots.txt (404) -> todo permitido
            except BlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("robots.txt de %s no disponible (%s); se asume permitido", host, e)
                rp.parse([])
            self._robots[host] = rp
        rp = self._robots[host]
        return rp is None or rp.can_fetch(config.USER_AGENT, url)

    # ---------- GET ----------
    async def _raw_get(self, url: str, host: str, headers: dict | None = None,
                       cookies: dict | None = None) -> httpx.Response:
        async with self._locks[host]:
            await self._wait_turn(host)
            try:
                return await self._client(host).get(url, headers=headers, cookies=cookies)
            except httpx.ConnectError as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e) and host not in self._ssl:
                    await self._fix_tls(host)
                    return await self._client(host).get(url, headers=headers, cookies=cookies)
                raise

    async def get(self, url: str, *, referer: str | None = None,
                  cookies: dict | None = None) -> httpx.Response:
        host = urlparse(url).hostname or ""
        if not await self.allowed(url):
            raise RobotsDisallowed(f"robots.txt no permite {url}")
        headers = {"Referer": referer} if referer else None
        last_exc: Exception | None = None
        for attempt in range(config.MAX_RETRIES + 1):
            try:
                r = await self._raw_get(url, host, headers, cookies)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                last_exc = e
                await asyncio.sleep(3 * (attempt + 1))
                continue
            reason = detect_block(r.status_code, r.text)
            if reason:
                raise BlockedError(host, reason, r.status_code)
            if r.status_code >= 500:
                last_exc = FetchError(f"HTTP {r.status_code} en {url}")
                await asyncio.sleep(5 * (attempt + 1))
                continue
            return r
        raise FetchError(f"falló {url}: {last_exc}")
