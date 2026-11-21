"""Exporta los datos que consume la app web (GitHub Pages).

- data.json: liviano (~2.500 productos "core": ofertas, comparables entre cadenas) + historial + salud.
- products.json: catálogo completo (sin historial), solo se descarga al buscar.

Comparación: SOLO por EAN (incluye EAN alternativos). Sin match no se compara.
Guarda: si en un grupo el más caro cuesta >2,5× el más barato, el grupo se oculta.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import now_iso
from .storage import Storage
from .units import parse_content, unit_price

CORE_MAX = 2500
GROUP_MAX_RATIO = 2.5
HIST_POINTS = 60
STORE_NAMES = {"santaisabel": "Santa Isabel", "acuenta": "Acuenta", "unimarc": "Unimarc"}


def _unit(row) -> tuple[float | None, str | None]:
    if row["unit_price"] and row["unit"]:
        return row["unit_price"], row["unit"]
    up = unit_price(row["price"], parse_content(row["net_content"] or row["name"]))
    return up if up else (None, None)


def _compact(row) -> dict:
    up, un = _unit(row)
    d = {
        "k": row["key"], "s": row["store"], "n": row["name"], "b": row["brand"] or "",
        "e": row["ean"] or "", "p": row["price"], "l": row["list_price"], "c": row["card_price"],
        "u": up, "un": un, "st": int(row["in_stock"] or 0), "img": row["image"] or "",
        "url": row["url"], "cat": row["category"] or "", "f": row["first_seen"][:10],
        "mk": int(row["marketplace"] or 0),
    }
    if row["promos"]:
        try:
            d["pr"] = [x.get("text") for x in json.loads(row["promos"]) if x.get("text")][:3]
        except json.JSONDecodeError:
            pass
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _groups(rows) -> dict[str, list[str]]:
    """Agrupa claves de producto que comparten algún EAN (union-find)."""
    parent: dict[str, str] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_ean: dict[str, str] = {}
    for r in rows:
        eans = {r["ean"]} | set((r["alt_eans"] or "").split(","))
        for e in (e for e in eans if e and len(e) >= 8):
            if e in by_ean:
                parent[find(r["key"])] = find(by_ean[e])
            else:
                by_ean[e] = r["key"]
                find(r["key"])
    groups: dict[str, list[str]] = defaultdict(list)
    for k in parent:
        groups[find(k)].append(k)
    return groups


def export(st: Storage, out: Path, active_days: int = 3) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    since = (datetime.now(timezone.utc) - timedelta(days=active_days)).isoformat()
    rows = st.db.execute("SELECT * FROM products WHERE last_seen >= ?", (since,)).fetchall()
    by_key = {r["key"]: r for r in rows}

    # --- grupos comparables (mismo EAN en ≥2 cadenas) ---
    compare = []
    for keys in _groups(rows).values():
        stores = {by_key[k]["store"] for k in keys}
        if len(stores) < 2:
            continue
        # una entrada por cadena: la más barata de esa cadena
        best: dict[str, any] = {}
        for k in keys:
            r = by_key[k]
            if r["marketplace"]:
                continue
            if r["store"] not in best or r["price"] < best[r["store"]]["price"]:
                best[r["store"]] = r
        if len(best) < 2:
            continue
        prices = sorted(r["price"] for r in best.values())
        if prices[-1] > GROUP_MAX_RATIO * prices[0]:
            continue  # grupo sospechoso (pack vs unidad, EAN reutilizado...): mejor no comparar
        members = sorted(best.values(), key=lambda r: r["price"])
        compare.append({"keys": [r["key"] for r in members],
                        "save": members[-1]["price"] - members[0]["price"],
                        "pct": round(100 * (1 - members[0]["price"] / members[-1]["price"]), 1)})
    compare.sort(key=lambda g: -g["save"])

    # --- ofertas: descuento declarado (prefiere productos con historial) ---
    def disc(r):
        return (1 - r["price"] / r["list_price"]) * 100 if r["list_price"] and r["list_price"] > r["price"] else 0

    offers = sorted((r for r in rows if disc(r) >= 15 and not r["marketplace"]), key=disc, reverse=True)

    # --- bajadas reales (historial propio) en las últimas 48 h ---
    drops = []
    since48 = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    for r in st.db.execute(
        """SELECT p.product_key k, p.price new, (SELECT price FROM prices q WHERE q.product_key=p.product_key
                 AND q.id < p.id ORDER BY q.id DESC LIMIT 1) old, p.ts
           FROM prices p WHERE p.ts >= ?""", (since48,)):
        cur = by_key.get(r["k"])
        # solo bajadas VIGENTES (el precio actual sigue siendo el bajado) y una por producto
        if (r["old"] and r["new"] and r["new"] < r["old"] and cur and cur["price"] == r["new"]
                and cur["in_stock"]):
            drops = [d for d in drops if d["k"] != r["k"]]
            drops.append({"k": r["k"], "old": r["old"], "new": r["new"], "ts": r["ts"],
                          "pct": round(100 * (1 - r["new"] / r["old"]), 1)})
    drops.sort(key=lambda d: -d["pct"])

    core_keys: list[str] = []
    seen = set()
    for k in ([d["k"] for d in drops[:300]] + [k for g in compare[:900] for k in g["keys"]]
              + [r["key"] for r in offers]):
        if k not in seen and k in by_key:
            seen.add(k)
            core_keys.append(k)
        if len(core_keys) >= CORE_MAX:
            break

    core = []
    for k in core_keys:
        d = _compact(by_key[k])
        hist = st.db.execute(
            "SELECT ts, price FROM (SELECT ts, price, id FROM prices WHERE product_key=? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (k, HIST_POINTS)).fetchall()
        if len(hist) > 1:
            d["h"] = [[h["ts"][:16], h["price"]] for h in hist]
        core.append(d)

    # --- salud de tiendas ---
    health = {}
    for store, name in STORE_NAMES.items():
        n = sum(1 for r in rows if r["store"] == store)
        b = st.get_meta(f"block:{store}")
        srcs = [dict(x) for x in st.db.execute(
            "SELECT source, last_ok, last_count, last_error FROM source_stats WHERE source LIKE ?",
            (("sisa" if store == "santaisabel" else store) + "-%",))]
        health[store] = {"name": name, "products": n, "blocked": b, "sources": srcs,
                         "with_ean": sum(1 for r in rows if r["store"] == store and r["ean"])}
    runs = [dict(r) for r in st.db.execute(
        "SELECT started, ended, runner, requests, products, changes, blocked FROM runs ORDER BY id DESC LIMIT 12")]

    data = {
        "generated": now_iso(), "health": health, "runs": runs,
        "stores": {s: n for s, n in STORE_NAMES.items() if health[s]["products"]},
        "counts": {"products": len(rows), "compare": len(compare), "offers": len(offers), "drops": len(drops)},
        "core": core, "compare": compare[:900], "offers": [r["key"] for r in offers[:800]],
        "drops": drops[:300],
    }
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    allp = [_compact(r) for r in rows]
    (out / "products.json").write_text(json.dumps({"generated": data["generated"], "products": allp},
                                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data["counts"] | {"core": len(core)}
