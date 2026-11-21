"""Exporta los datos que consume la app web (GitHub Pages).

- data.json: liviano (productos "core": errores, gangas, ofertas, bajadas, comparables) + historial + salud.
- products.json: catálogo completo con id de grupo (para el buscador con comparación).

Comparación: matcher (EAN primero; nombre+marca+contenido exacto después). Grupos cuyo más caro
cuesta >2,5× el más barato se ocultan (y >1,8× si el grupo se armó por nombre).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .analysis import analyze
from .matcher import group_products
from .models import now_iso
from .storage import Storage
from .units import parse_content, unit_price

CORE_MAX = 3000
HIST_POINTS = 60
STORE_NAMES = {"lider": "Lider", "santaisabel": "Santa Isabel", "acuenta": "Acuenta",
               "unimarc": "Unimarc", "tottus": "Tottus"}
STORE_NOTES = {
    "unimarc": "Bloquea las IPs de GitHub (Akamai). Se integra con el nodo opcional en Chile.",
    "tottus": "Cloudflare bloquea a todo navegador automatizado; integrarla exigiría evadir protecciones.",
}


def _unit(r) -> tuple[float | None, str | None]:
    if r["unit_price"] and r["unit"]:
        return r["unit_price"], r["unit"]
    up = unit_price(r["price"], parse_content(r["net_content"] or r["name"]))
    return up if up else (None, None)


def _compact(r, gid: str | None) -> dict:
    up, un = _unit(r)
    d = {
        "k": r["key"], "s": r["store"], "n": r["name"], "b": r["brand"] or "", "e": r["ean"] or "",
        "p": r["price"], "l": r["list_price"], "c": r["card_price"], "u": round(up) if up else None, "un": un,
        "st": int(r["in_stock"] or 0), "img": r["image"] or "", "url": r["url"], "cat": r["category"] or "",
        "f": r["first_seen"][:10], "mk": int(r["marketplace"] or 0), "g": gid,
    }
    if r["promos"]:
        try:
            d["pr"] = [x.get("text") for x in json.loads(r["promos"]) if x.get("text")][:3]
        except json.JSONDecodeError:
            pass
    return {k: v for k, v in d.items() if v not in (None, "", [], 0) or k in ("st", "p")}


def _history(st: Storage, since: str) -> dict[str, list[tuple[str, float]]]:
    h: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in st.db.execute("SELECT product_key, ts, price FROM prices WHERE ts >= ? ORDER BY id", (since,)):
        h[row["product_key"]].append((row["ts"], row["price"]))
    return h


def _record_errors(st: Storage, findings, by_key) -> None:
    """Tabla errors (🏆 salón de la fama): abre los nuevos, cierra los que desaparecieron."""
    now = now_iso()
    current = {f.key: f for f in findings if f.kind in ("error", "godzilla")}
    open_rows = {r["product_key"]: r for r in st.db.execute("SELECT * FROM errors WHERE ended IS NULL")}
    for k, f in current.items():
        if k not in open_rows:
            st.db.execute("INSERT INTO errors(product_key,detected,verdict,price,reference,rule,payload) "
                          "VALUES(?,?,?,?,?,?,?)", (k, now, "sin verificar", f.price, f.reference, f.kind,
                                                    json.dumps({"basis": f.basis}, ensure_ascii=False)))
    for k, row in open_rows.items():
        if k not in current:
            p = by_key.get(k)
            reason = ("agotado" if p and not p["in_stock"] else
                      "corregido" if p and p["price"] > row["price"] else "terminó")
            st.db.execute("UPDATE errors SET ended=?, end_reason=? WHERE id=?", (now, reason, row["id"]))
    st.commit()


def export(st: Storage, out: Path, active_days: int = 3) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=active_days)).isoformat()
    rows = [dict(r) for r in st.db.execute("SELECT * FROM products WHERE last_seen >= ?", (since,))]
    by_key = {r["key"]: r for r in rows}

    groups, method = group_products(rows)
    # guarda de seguridad por grupo
    members: dict[str, list[dict]] = defaultdict(list)
    for k, g in groups.items():
        members[g].append(by_key[k])
    for g, lst in list(members.items()):
        prices = [x["price"] for x in lst if x["price"]]
        limit = 1.8 if method[g] == "nombre" else 2.5
        if len(prices) < 2 or max(prices) > limit * min(prices):
            for x in lst:
                groups.pop(x["key"], None)
            members.pop(g)

    history = _history(st, (now - timedelta(days=30)).isoformat())
    findings = analyze(rows, groups, history, now)
    _record_errors(st, findings, by_key)

    # comparables ordenados por ahorro
    compare = []
    for g, lst in members.items():
        best: dict[str, dict] = {}
        for x in lst:
            if x["store"] not in best or x["price"] < best[x["store"]]["price"]:
                best[x["store"]] = x
        ms = sorted(best.values(), key=lambda x: x["price"])
        compare.append({"g": g, "keys": [x["key"] for x in ms], "m": method[g],
                        "save": ms[-1]["price"] - ms[0]["price"],
                        "pct": round(100 * (1 - ms[0]["price"] / ms[-1]["price"]), 1)})
    compare.sort(key=lambda c: -c["save"])

    def disc(x):
        return (1 - x["price"] / x["list_price"]) * 100 if x["list_price"] and x["list_price"] > x["price"] else 0
    offers = sorted((x for x in rows if disc(x) >= 15 and not x["marketplace"] and x["in_stock"]),
                    key=disc, reverse=True)

    drops = []
    for k, h in history.items():
        x = by_key.get(k)
        if x and len(h) >= 2 and x["in_stock"] and h[-1][1] == x["price"] and h[-2][1] > x["price"] \
                and h[-1][0] >= (now - timedelta(hours=48)).isoformat():
            drops.append({"k": k, "old": h[-2][1], "new": x["price"], "ts": h[-1][0],
                          "pct": round(100 * (1 - x["price"] / h[-2][1]), 1)})
    drops.sort(key=lambda d: -d["pct"])

    fl = {kind: sorted([f for f in findings if f.kind == kind], key=lambda f: -f.pct)
          for kind in ("error", "godzilla", "ganga")}
    hof = [dict(r) for r in st.db.execute(
        "SELECT product_key k, detected, ended, end_reason, price, reference, rule FROM errors "
        "ORDER BY id DESC LIMIT 100")]

    core_keys, seen = [], set()
    for k in ([f.key for f in fl["error"]] + [f.key for f in fl["godzilla"]] + [h["k"] for h in hof]
              + [d["k"] for d in drops[:300]] + [f.key for f in fl["ganga"][:600]]
              + [k for c in compare[:600] for k in c["keys"]] + [x["key"] for x in offers[:800]]):
        if k not in seen and k in by_key:
            seen.add(k)
            core_keys.append(k)
        if len(core_keys) >= CORE_MAX:
            break
    # los demás miembros de cada grupo "core" también (para mostrar la comparación)
    for k in list(core_keys):
        g = groups.get(k)
        for x in members.get(g, []):
            if x["key"] not in seen:
                seen.add(x["key"])
                core_keys.append(x["key"])

    core = []
    for k in core_keys:
        d = _compact(by_key[k], groups.get(k))
        h = history.get(k, [])[-HIST_POINTS:]
        if len(h) > 1:
            d["h"] = [[t[:16], p] for t, p in h]
        core.append(d)

    health = {}
    for store, name in STORE_NAMES.items():
        n = sum(1 for x in rows if x["store"] == store)
        prefix = {"santaisabel": "sisa"}.get(store, store)
        srcs = [dict(x) for x in st.db.execute(
            "SELECT source, last_ok, last_count, last_error FROM source_stats WHERE source LIKE ?", (prefix + "-%",))]
        health[store] = {"name": name, "products": n, "blocked": st.get_meta(f"block:{store}"),
                         "sources": srcs, "with_ean": sum(1 for x in rows if x["store"] == store and x["ean"]),
                         "note": STORE_NOTES.get(store, "")}
    runs = [dict(x) for x in st.db.execute(
        "SELECT started, ended, runner, requests, products, changes, blocked FROM runs ORDER BY id DESC LIMIT 12")]

    fdict = lambda f: {"k": f.key, "price": f.price, "ref": f.reference, "why": f.basis, "pct": f.pct}  # noqa: E731
    data = {
        "generated": now_iso(), "health": health, "runs": runs,
        "stores": {s: n for s, n in STORE_NAMES.items() if health[s]["products"]},
        "counts": {"products": len(rows), "compare": len(compare), "offers": len(offers), "drops": len(drops),
                   "errors": len(fl["error"]), "godzilla": len(fl["godzilla"]), "gangas": len(fl["ganga"]),
                   "by_name": sum(1 for c in compare if c["m"] == "nombre")},
        "core": core, "compare": compare[:1500], "offers": [x["key"] for x in offers[:800]],
        "drops": drops[:300], "errors": [fdict(f) for f in fl["error"]],
        "godzilla": [fdict(f) for f in fl["godzilla"]], "gangas": [fdict(f) for f in fl["ganga"][:600]],
        "hall": hof,
    }
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    allp = [_compact(x, groups.get(x["key"])) for x in rows]
    (out / "products.json").write_text(json.dumps({"generated": data["generated"], "products": allp},
                                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data["counts"] | {"core": len(core)}
