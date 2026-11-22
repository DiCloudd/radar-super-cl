"""Ventana de monitoreo: trampas rápidas ⚡ + rastreador completo, hasta un deadline.

- Trampas: página 1 ordenada de menor a mayor precio, cada ~2 min (rotación fast_every/offset).
- Rastreador: un trabajador por tienda, round-robin entre categorías, cursor en la BD;
  cede el paso a las trampas. Fin de categoría: página vacía, total alcanzado o ≥80% repetidos.
- Bloqueo: pausa la tienda con backoff exponencial (persistido) y sigue con las demás.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config
from .config import Source
from .fetchers import BlockedError, Fetcher, FetchError, RobotsDisallowed
from .models import Product, now_iso
from .scrapers import SCRAPERS
from .scrapers.santaisabel import parse_ean_from_pdp
from .storage import Storage

log = logging.getLogger("radar.monitor")


@dataclass
class Change:
    product: Product
    prev: dict
    source: str
    fast: bool


@dataclass
class WindowStats:
    requests: int = 0
    pages: int = 0
    products: int = 0
    changes: int = 0
    new: int = 0
    eans: int = 0
    blocked: dict[str, str] = field(default_factory=dict)
    per_source: dict[str, dict] = field(default_factory=lambda: defaultdict(lambda: {"ok": 0, "err": 0, "products": 0}))


def _utc() -> datetime:
    return datetime.now(timezone.utc)


class Monitor:
    def __init__(self, storage: Storage, sources: list[Source], runner: str = config.MONITOR_RUNNER,
                 fetcher: Fetcher | None = None, on_changes=None, fetchers: dict | None = None):
        self.st, self.sources, self.runner = storage, sources, runner
        self.fetcher = fetcher or Fetcher()
        self.fetchers = fetchers or {}      # tienda -> fetcher especial (ej. navegador real para Tottus)
        self.stats = WindowStats()
        self.on_changes = on_changes          # callback(list[Change]) -> fase 4 (análisis/alertas)
        self.fast_idle = asyncio.Event()
        self.fast_idle.set()
        self.changes: list[Change] = []
        self.last_repeated = 0
        self.last_page_size: int | None = None

    # ---------- circuit breaker ----------
    def store_paused(self, store: str) -> bool:
        b = self.st.get_meta(f"block:{store}")
        return bool(b and datetime.fromisoformat(b["until"]) > _utc())

    def mark_blocked(self, store: str, reason: str) -> None:
        b = self.st.get_meta(f"block:{store}") or {"streak": 0}
        streak = b.get("streak", 0) + 1
        secs = min(config.BLOCK_BACKOFF_BASE_S * 2 ** (streak - 1), config.BLOCK_BACKOFF_MAX_S)
        until = (_utc() + timedelta(seconds=secs)).isoformat()
        self.st.set_meta(f"block:{store}", {"until": until, "streak": streak, "reason": reason, "at": now_iso()})
        self.stats.blocked[store] = reason
        log.warning("⏸️  %s bloqueó (%s): pausa %d min (racha %d)", store, reason, secs // 60, streak)

    def mark_ok(self, store: str) -> None:
        if self.st.get_meta(f"block:{store}"):
            self.st.set_meta(f"block:{store}", None)

    # ---------- lectura de una página ----------
    def _seen_since(self, keys: list[str], since: str) -> int:
        if not keys:
            return 0
        q = f"SELECT COUNT(*) FROM products WHERE key IN ({','.join('?' * len(keys))}) AND last_seen >= ?"
        return self.st.db.execute(q, (*keys, since)).fetchone()[0]

    async def read_page(self, src: Source, page: int, fast: bool,
                        sweep_start: str | None = None) -> list[Product] | None:
        sc = SCRAPERS[src.store]
        if self.store_paused(src.store):
            return None
        url = sc.page_url(src, page)
        t0 = time.monotonic()
        fetcher = self.fetchers.get(src.store, self.fetcher)
        try:
            if fetcher is not self.fetcher and not await self.fetcher.allowed(url):
                raise RobotsDisallowed(f"robots.txt no permite {url}")   # robots.txt también con navegador
            r = await fetcher.get(url, referer=sc.referer(src), cookies=sc.cookies,
                                  headers=sc.extra_headers())
            self.stats.requests += 1
        except BlockedError as e:
            self.stats.requests += 1
            self.mark_blocked(src.store, e.reason)
            self.st.source_error(src.name, str(e))
            return None
        except (FetchError, RobotsDisallowed) as e:
            self.stats.per_source[src.name]["err"] += 1
            self.st.source_error(src.name, str(e))
            log.error("❌ %s p%d: %s", src.name, page, e)
            return None
        if r.status_code == 404 and page > 1:
            return []        # pasada la última página (Santa Isabel responde 404): fin de categoría
        if r.status_code == 404:
            self.st.source_error(src.name, "HTTP 404")
            log.error("❌ %s p%d: HTTP 404 (¿cambió la URL?)", src.name, page)
            return None
        res = sc.parse_listing(r.text, src)
        self.mark_ok(src.store)
        self.stats.pages += 1
        ps = self.stats.per_source[src.name]
        ps["ok"] += 1
        ps["products"] += len(res.products)
        if page == 1:
            prev_avg = self.st.source_ok(src.name, len(res.products))
            if prev_avg and len(res.products) < 0.5 * prev_avg:
                log.warning("🧩 %s trajo %d productos (habitual ~%.0f): ¿cambio de diseño?",
                            src.name, len(res.products), prev_avg)
        prices = [p.price for p in res.products]
        log.info("%s %s p%d: %d productos%s (%.1fs)%s", "⚡" if fast else "🕷️", src.name, page,
                 len(res.products),
                 f", ${min(prices):,.0f}–${max(prices):,.0f}".replace(",", ".") if prices else "",
                 time.monotonic() - t0, f" ⚠️ {res.warnings[:2]}" if res.warnings else "")
        # repetidos dentro del barrido actual (se mide ANTES de guardar)
        self.last_repeated = (self._seen_since([p.key for p in res.products], sweep_start)
                              if sweep_start and page > 1 else 0)
        self.last_page_size = res.page_size
        self._store(res.products, src, fast)
        if res.total is not None:
            self.st.set_meta(f"total:{src.name}", res.total)
        return res.products

    def _store(self, products: list[Product], src: Source, fast: bool) -> None:
        batch = []
        for p in products:
            changed, prev = self.st.upsert(p)
            self.stats.products += 1
            if prev is None:
                self.stats.new += 1
            elif changed:
                self.stats.changes += 1
                batch.append(Change(p, prev, src.name, fast))
        self.st.commit()
        if batch:
            self.changes.extend(batch)
            if self.on_changes:
                self.on_changes(batch)

    # ---------- trampas rápidas ----------
    async def fast_loop(self, deadline: float) -> None:
        cycle = self.st.get_meta("fast_cycle", 0)
        while time.monotonic() < deadline - 20:
            t0 = time.monotonic()
            due = [s for s in self.sources if s.fast and cycle % max(1, s.fast_every) == s.fast_offset % max(1, s.fast_every)]
            self.fast_idle.clear()
            try:
                by_store: dict[str, list[Source]] = defaultdict(list)
                for s in due:
                    by_store[s.store].append(s)

                async def run_store(lst: list[Source]):
                    for s in lst:
                        if time.monotonic() > deadline - 15:
                            return
                        await self.read_page(s, 1, fast=True)
                await asyncio.gather(*(run_store(lst) for lst in by_store.values()))
            finally:
                self.fast_idle.set()
            cycle += 1
            self.st.set_meta("fast_cycle", cycle)
            rest = config.FAST_CYCLE_S - (time.monotonic() - t0)
            if rest > 0:
                await asyncio.sleep(min(rest, max(0, deadline - time.monotonic() - 5)))

    # ---------- rastreador ----------
    async def crawl_store(self, store: str, deadline: float) -> None:
        srcs = [s for s in self.sources if s.store == store]
        if not srcs:
            return
        rr = self.st.get_meta(f"rr:{store}", 0)
        steps = 0
        while time.monotonic() < deadline - 25:
            await self.fast_idle.wait()           # ceder el paso a las trampas
            if self.store_paused(store):
                return
            ready = [s for s in srcs if not self.in_cooldown(s)]
            if not ready:
                await asyncio.sleep(min(30, max(0, deadline - time.monotonic() - 25)))
                continue
            src = ready[rr % len(ready)]
            rr += 1
            self.st.set_meta(f"rr:{store}", rr)
            await self.crawl_step(src)
            steps += 1
            if store in ("santaisabel", "jumbo") and steps % 2 == 0:
                await self.enrich_ean(store, 1)

    def in_cooldown(self, src: Source) -> bool:
        cur = self.st.get_meta(f"cursor:{src.name}")
        nxt = cur and cur.get("page") == 1 and cur.get("not_before")
        return bool(nxt and datetime.fromisoformat(nxt) > _utc())

    async def crawl_step(self, src: Source) -> None:
        cur = self.st.get_meta(f"cursor:{src.name}") or {"page": 1, "sweep_start": now_iso(), "sweeps": 0}
        page = cur["page"]
        products = await self.read_page(src, page, fast=False, sweep_start=cur["sweep_start"])
        if products is None:
            return
        n = len(products)
        repeated = self.last_repeated
        total = self.st.get_meta(f"total:{src.name}")
        size = self.last_page_size
        end = (not products or page >= src.max_pages
               or (n and repeated / n >= config.END_REPEAT_RATIO)
               or bool(total and size and page * size >= total))
        if repeated:
            log.debug("%s p%d: %d/%d repetidos", src.name, page, repeated, n)
        if end:
            log.info("🏁 %s: barrido completo en %d páginas", src.name, page)
            cur = {"page": 1, "sweep_start": now_iso(), "sweeps": cur.get("sweeps", 0) + 1,
                   "last_sweep_pages": page,
                   "not_before": (_utc() + timedelta(seconds=config.SWEEP_COOLDOWN_S)).isoformat()}
        else:
            cur["page"] = page + 1
        self.st.set_meta(f"cursor:{src.name}", cur)

    async def enrich_ean(self, store: str, n: int) -> None:
        for row in self.st.missing_ean(store, n):
            if self.store_paused(store):
                return
            try:
                r = await self.fetcher.get(row["url"], referer=SCRAPERS[store].base_url + "/")
                self.stats.requests += 1
            except BlockedError as e:
                self.mark_blocked(store, e.reason)
                return
            except FetchError:
                continue
            ean = parse_ean_from_pdp(r.text) if r.status_code == 200 else None
            self.st.set_ean(row["key"], ean or "")
            if ean:
                self.stats.eans += 1
            self.st.commit()

    # ---------- ventana ----------
    async def run_window(self, minutes: float) -> WindowStats:
        deadline = time.monotonic() + minutes * 60
        run_id = self.st.start_run(self.runner)
        stores = sorted({s.store for s in self.sources})
        log.info("▶️ ventana %.0f min · runner=%s · %d fuentes · tiendas: %s",
                 minutes, self.runner, len(self.sources), ", ".join(stores))
        try:
            await asyncio.gather(self.fast_loop(deadline), *(self.crawl_store(s, deadline) for s in stores))
        finally:
            await self.fetcher.close()
            for f in self.fetchers.values():
                await f.close()
            self.st.end_run(run_id, requests=self.stats.requests, products=self.stats.products,
                            changes=self.stats.changes, blocked=",".join(self.stats.blocked) or None)
        return self.stats
