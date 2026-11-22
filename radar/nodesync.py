"""Sincronización del nodo opcional en Chile con la nube.

El nodo (PC viejo / Android con Termux) corre el monitor para tiendas que bloquean a GitHub
(Unimarc) y exporta lo leído a un archivo .json.gz que sube a la rama `node-data`.
Cada ventana de la nube lo descarga e integra (upsert -> historial de precios incluido).
Solo contiene datos públicos de productos; nada personal.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from .models import Product, Promo
from .storage import Storage

FIELDS = ("store", "sku", "name", "price", "url", "list_price", "card_price", "unit_price", "unit",
          "net_content", "ean", "brand", "image", "category", "in_stock", "marketplace")


def export_node(st: Storage, stores: list[str], out: Path, since: str | None = None) -> int:
    q = f"SELECT * FROM products WHERE store IN ({','.join('?' * len(stores))})"
    args: list = list(stores)
    if since:
        q += " AND last_seen >= ?"
        args.append(since)
    items = []
    for r in st.db.execute(q, args):
        d = {f: r[f] for f in FIELDS}
        d["found_at"] = r["last_seen"]
        d["promos"] = json.loads(r["promos"]) if r["promos"] else []
        d["alt_eans"] = [e for e in (r["alt_eans"] or "").split(",") if e]
        items.append(d)
    out.write_bytes(gzip.compress(json.dumps({"items": items}, ensure_ascii=False).encode()))
    return len(items)


def ingest(st: Storage, path: Path) -> tuple[int, int]:
    if path.is_dir():
        tot = ch = 0
        for f in sorted(path.glob("*.json.gz")):
            a, b = ingest(st, f)
            tot, ch = tot + a, ch + b
        return tot, ch
    return _ingest_file(st, path)


def _ingest_file(st: Storage, path: Path) -> tuple[int, int]:
    """Integra el archivo del nodo. Devuelve (productos, cambios). Idempotente por found_at."""
    data = json.loads(gzip.decompress(path.read_bytes()))
    n = changes = 0
    for d in data.get("items", []):
        prev = st.db.execute("SELECT last_seen FROM products WHERE key=?", (f"{d['store']}:{d['sku']}",)).fetchone()
        if prev and prev["last_seen"] >= d["found_at"]:
            continue  # ya integrado
        promos = [Promo(**p) for p in d.pop("promos", [])]
        p = Product(**{k: d[k] for k in FIELDS}, promos=promos, alt_eans=d.get("alt_eans", []),
                    found_at=d["found_at"])
        p.in_stock, p.marketplace = bool(p.in_stock), bool(p.marketplace)
        changed, _ = st.upsert(p)
        n += 1
        changes += int(changed)
    st.commit()
    return n, changes
