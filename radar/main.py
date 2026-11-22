"""CLI del radar.

  python -m radar.main scan <fuente> [--page N]      lee una página y la muestra (sin guardar)
  python -m radar.main window --minutes 12            corre una ventana de monitoreo
  python -m radar.main stats                          resumen de la BD
  python -m radar.main encrypt|decrypt <src> <dst>    cifrado de la BD (DB_KEY)
"""
from __future__ import annotations

import argparse
import os
import asyncio
import logging
import sys
from pathlib import Path

from . import backup, config
from .config import load_sources
from .fetchers import Fetcher
from .monitor import Monitor
from .scrapers import SCRAPERS
from .storage import Storage


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


async def _scan(name: str, page: int) -> int:
    src = next((s for s in load_sources() if s.name == name), None)
    if not src:
        print(f"no existe la fuente {name}")
        return 2
    sc = SCRAPERS[src.store]
    f = Fetcher()
    try:
        r = await f.get(sc.page_url(src, page), referer=sc.referer(src), cookies=sc.cookies, headers=sc.extra_headers())
    finally:
        await f.close()
    res = sc.parse_listing(r.text, src)
    print(f"HTTP {r.status_code} · {len(res.products)} productos · total={res.total} · avisos={res.warnings[:3]}")
    for p in res.products[:15]:
        extra = f" (normal ${p.list_price:,.0f})" if p.list_price else ""
        card = f" 💳${p.card_price:,.0f}" if p.card_price else ""
        up = f" · ${p.unit_price:,.0f}/{p.unit}" if p.unit_price else ""
        print(f"  ${p.price:>8,.0f}{extra}{card}{up} · {p.name[:60]} · EAN {p.ean or '-'}".replace(",", "."))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("source")
    s.add_argument("--page", type=int, default=1)
    w = sub.add_parser("window")
    w.add_argument("--minutes", type=float, default=12)
    w.add_argument("--only", help="lista de tiendas separadas por coma")
    sub.add_parser("stats")
    pb = sub.add_parser("publish")
    pb.add_argument("out", type=Path)
    pb.add_argument("--no-seal", action="store_true", help="no cifrar (solo pruebas locales)")
    dv = sub.add_parser("device")
    dv.add_argument("action", choices=["aprobar", "bloquear", "desbloquear", "eliminar", "listar"])
    dv.add_argument("--code", default="")
    dv.add_argument("--name", default="")
    ne = sub.add_parser("node-export")
    ne.add_argument("out", type=Path)
    ne.add_argument("--stores", default="unimarc")
    ig = sub.add_parser("ingest")
    ig.add_argument("file", type=Path)
    for c in ("encrypt", "decrypt"):
        e = sub.add_parser(c)
        e.add_argument("src", type=Path)
        e.add_argument("dst", type=Path)
    a = ap.parse_args(argv)
    _setup_logging(a.verbose)

    if a.cmd == "scan":
        return asyncio.run(_scan(a.source, a.page))
    if a.cmd == "window":
        sources = load_sources(runner=config.MONITOR_RUNNER)
        if a.only:
            keep = set(a.only.split(","))
            sources = [x for x in sources if x.store in keep]
        st = Storage(config.DB_PATH)
        fetchers = {}
        if any(x.store == "tottus" for x in sources) and os.environ.get("TOTTUS_BROWSER") == "1" \
                and not os.environ.get("TOTTUS_PARTNER_TOKEN"):
            from .browser_fetch import BrowserFetcher
            fetchers["tottus"] = BrowserFetcher()
        mon = Monitor(st, sources, fetchers=fetchers)
        stats = asyncio.run(mon.run_window(a.minutes))
        print("\n══════ RESUMEN DE LA VENTANA ══════")
        print(f"peticiones={stats.requests} páginas={stats.pages} lecturas={stats.products} "
              f"nuevos={stats.new} cambios={stats.changes} EAN+={stats.eans}")
        for name, d in sorted(stats.per_source.items()):
            print(f"  {'✅' if d['ok'] and not d['err'] else '⚠️' if d['ok'] else '❌'} {name}: "
                  f"ok={d['ok']} err={d['err']} productos={d['products']}")
        for store, why in stats.blocked.items():
            print(f"  ⏸️ {store}: {why}")
        for store in sorted({x.store for x in sources}):
            print(f"  BD {store}: {st.count(store)} productos")
        st.close()
        return 0
    if a.cmd == "stats":
        st = Storage(config.DB_PATH)
        for row in st.db.execute("SELECT store, COUNT(*) n, SUM(ean IS NOT NULL AND ean<>'') e, "
                                 "MIN(price) mn, MAX(price) mx FROM products GROUP BY store"):
            print(dict(row))
        st.close()
        return 0
    if a.cmd == "publish":
        from . import access
        from .publisher import export
        st = Storage(config.DB_PATH)
        devices = [] if a.no_seal else access.load_devices()
        print("exportado:", export(st, a.out, extra={"devices": access.public_list(devices)}))
        st.close()
        if not a.no_seal:
            print("acceso:", access.seal(a.out, devices))
        return 0
    if a.cmd == "device":
        from . import access
        devices = access.load_devices()
        if a.action == "listar":
            for d in devices:
                print(d["id"], d.get("status"), d.get("name") or "-", d.get("desc") or "")
            return 0
        try:
            devices, msg = access.apply_action(devices, a.action, a.code, a.name)
        except ValueError as e:
            print("❌", e)
            return 2
        access.save_devices(devices)
        print(msg)
        return 0
    if a.cmd in ("node-export", "ingest"):
        from . import nodesync
        st = Storage(config.DB_PATH)
        if a.cmd == "node-export":
            print("exportados:", nodesync.export_node(st, a.stores.split(","), a.out))
        else:
            n, ch = nodesync.ingest(st, a.file)
            print(f"integrados del nodo: {n} productos, {ch} cambios de precio")
        st.close()
        return 0
    if a.cmd == "encrypt":
        print("bytes cifrados:", backup.encrypt(a.src, a.dst))
        return 0
    if a.cmd == "decrypt":
        print("bytes:", backup.decrypt(a.src, a.dst))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
