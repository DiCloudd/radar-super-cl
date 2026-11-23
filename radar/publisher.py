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

from .analysis import Finding, analyze
from .matcher import group_products
from .models import now_iso
from .storage import Storage
from .units import parse_content, unit_price

CORE_MAX = 3000
HIST_POINTS = 60
STORE_NAMES = {"lider": "Lider", "jumbo": "Jumbo", "santaisabel": "Santa Isabel", "acuenta": "Acuenta",
               "unimarc": "Unimarc", "alvi": "Alvi", "tottus": "Tottus",
               "super9": "Super9"}
STORE_NOTES = {
    "unimarc": "Bloquea las IPs de GitHub (Akamai). Se integra con el nodo opcional en Chile.",
    "alvi": "Mayorista SMU. Bloquea las IPs de GitHub (Akamai). Se integra con el nodo opcional en Chile.",
    "super9": "Lista; hoy su tienda online no publica productos (vende en locales de O'Higgins y Maule).",
    "tottus": "Integración lista; se activa cuando Tottus habilite la llave de socio en su Cloudflare.",
}


def _unit(r) -> tuple[float | None, str | None]:
    if r["unit_price"] and r["unit"]:
        return r["unit_price"], r["unit"]
    up = unit_price(r["price"], parse_content(r["net_content"] or r["name"]))
    return up if up else (None, None)


def small_img(url: str | None) -> str:
    """Miniatura de ~200 px (las de Lider llegan a 2000 px: pesadas para datos móviles)."""
    if not url:
        return ""
    if "walmartimages.cl/" in url and "odnWidth" not in url:
        return url + ("&" if "?" in url else "?") + "odnHeight=200&odnWidth=200&odnBg=FFFFFF"
    if "images.lider.cl/wmtcl" in url and "scale=" not in url:
        return url.replace("&sink", "&scale=size[200x200]&sink")
    return url


def _compact(r, gid: str | None) -> dict:
    up, un = _unit(r)
    d = {
        "k": r["key"], "s": r["store"], "n": r["name"], "b": r["brand"] or "", "e": r["ean"] or "",
        "p": r["price"], "l": r["list_price"], "c": r["card_price"], "u": round(up) if up else None, "un": un,
        "st": int(r["in_stock"] or 0), "img": small_img(r["image"]), "url": r["url"], "cat": r["category"] or "",
        "f": r["first_seen"][:10], "mk": int(r["marketplace"] or 0), "g": gid,
        "w": (r["net_content"] or "")[1:] if (r["net_content"] or "").startswith("≈") else None,
    }
    if r["promos"]:
        try:
            d["pr"] = [x.get("text") for x in json.loads(r["promos"]) if x.get("text")][:3]
        except json.JSONDecodeError:
            pass
    return {k: v for k, v in d.items() if v not in (None, "", [], 0) or k in ("st", "p")}


def _history(st: Storage, since: str) -> dict[str, list[tuple[str, float]]]:
    h: dict[str, list[tuple[str, float]]] = defaultdict(list)
    # precios de vendedores externos (marketplace) no sirven de referencia: se excluyen
    for row in st.db.execute("SELECT product_key, ts, price FROM prices WHERE ts >= ? "
                             "AND COALESCE(marketplace, 0) = 0 ORDER BY id", (since,)):
        h[row["product_key"]].append((row["ts"], row["price"]))
    return h


def _confirmed_by_others(x: dict, groups: dict, members: dict) -> bool:
    """El precio nuevo es coherente con el mismo producto en otra cadena (±50%)."""
    others = [o["price"] for o in members.get(groups.get(x["key"]), []) if o["store"] != x["store"]]
    return any(0.5 * o <= x["price"] <= 1.5 * o for o in others)


def _tg_status(st: Storage) -> dict:
    from .notifier import Telegram
    s = Telegram(st).status()
    return {"enabled": s["enabled"], "linked": s["linked"], "code": s["code"]}


def _record_errors(st: Storage, findings, by_key) -> None:
    """Tabla errors (🏆 salón de la fama): abre los nuevos, cierra los que desaparecieron."""
    now = now_iso()
    current = {f.key: f for f in findings if f.kind in ("error", "godzilla", "pack")}
    open_rows = {r["product_key"]: r for r in st.db.execute("SELECT * FROM errors WHERE ended IS NULL")}
    for k, f in current.items():
        if k not in open_rows:
            st.db.execute("INSERT INTO errors(product_key,detected,verdict,price,reference,rule,payload) "
                          "VALUES(?,?,?,?,?,?,?)", (k, now, "sin verificar", f.price, f.reference, f.kind,
                                                    json.dumps({"basis": f.basis}, ensure_ascii=False)))
            p = by_key.get(k) or {}
            st.db.execute("INSERT INTO alerts(ts, product_key, kind, payload) VALUES(?,?,?,?)", (now, k, f.kind,
                          json.dumps({"name": p.get("name"), "store": p.get("store"), "url": p.get("url"),
                                      "price": f.price, "why": f.basis, "image": p.get("image")}, ensure_ascii=False)))
    for k, row in open_rows.items():
        if k not in current:
            p = by_key.get(k)
            reason = ("agotado" if p and not p["in_stock"] else
                      "corregido" if p and p["price"] > row["price"] else "terminó")
            st.db.execute("UPDATE errors SET ended=?, end_reason=? WHERE id=?", (now, reason, row["id"]))
    st.commit()


def export(st: Storage, out: Path, active_days: int = 3, extra: dict | None = None) -> dict:
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
    # 📦 pack a precio de unidad (ej. 6 cervezas a $1.090): con stock -> error con aviso; agotado -> vigilancia
    from .absurd import find_absurd
    absurd = find_absurd(rows)
    findings += [Finding(a.key, "pack", a.price, a.single_price, a.basis, a.pct)
                 for a in absurd if by_key[a.key]["in_stock"]]
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
        if x and len(h) >= 2 and x["in_stock"] and not x["marketplace"] \
                and not (x["net_content"] or "").startswith("≈") \
                and h[-1][1] == x["price"] and h[-2][1] > x["price"] \
                and h[-1][0] >= (now - timedelta(hours=48)).isoformat() \
                and (x["price"] >= 0.4 * h[-2][1] or _confirmed_by_others(x, groups, members)):
            # caídas >60% sin respaldo de otra cadena suelen ser cambio de vendedor/pack: no se muestran
            drops.append({"k": k, "old": h[-2][1], "new": x["price"], "ts": h[-1][0],
                          "pct": round(100 * (1 - x["price"] / h[-2][1]), 1)})
    drops.sort(key=lambda d: -d["pct"])

    fl = {kind: sorted([f for f in findings if f.kind == kind], key=lambda f: -f.pct)
          for kind in ("error", "godzilla", "ganga")}
    from . import watch as watchmod
    flagged = {f.key: {"error": "💥 error", "godzilla": "🦖 Godzilla", "ganga": "🔥 ganga", "pack": "📦 pack a precio de unidad"}[f.kind]
               for f in findings}
    flagged.update({a.key: "📦 pack a precio de unidad" for a in absurd})
    flagged.update({d["k"]: f"⚡ bajó {d['pct']}%" for d in drops if d["k"] not in flagged})
    # las bajadas solo se calculan con stock; para vigilar, también las que quedaron agotadas tras bajar
    for k, h in history.items():
        x = by_key.get(k)
        if x and not x["in_stock"] and len(h) >= 2 and h[-2][1] > x["price"] >= 0.4 * h[-2][1] and k not in flagged:
            flagged[k] = f"⚡ bajó {round(100 * (1 - x['price'] / h[-2][1]))}%"
    watchlist = watchmod.build_watchlist(st, rows, flagged)
    hof = [dict(r) for r in st.db.execute(
        "SELECT product_key k, detected, ended, end_reason, price, reference, rule FROM errors "
        "ORDER BY id DESC LIMIT 100")]

    core_keys, seen = [], set()
    for k in (list(watchlist) + [a.key for a in absurd] + [a.single_key for a in absurd] + [f.key for f in fl["error"]] + [f.key for f in fl["godzilla"]] + [h["k"] for h in hof]
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
                   "errors": len(fl["error"]), "absurd": len(absurd), "godzilla": len(fl["godzilla"]), "gangas": len(fl["ganga"]),
                   "by_name": sum(1 for c in compare if c["m"] == "nombre")},
        "core": core, "compare": compare[:1500], "offers": [x["key"] for x in offers[:800]],
        "drops": drops[:300], "errors": [fdict(f) for f in fl["error"]],
        "absurd": [{"k": a.key, "price": a.price, "ref": a.single_price, "why": a.basis, "pct": a.pct,
                    "single": a.single_key} for a in absurd],
        "godzilla": [fdict(f) for f in fl["godzilla"]], "gangas": [fdict(f) for f in fl["ganga"][:600]],
        "hall": hof, **(extra or {}),
        "watch": [{"k": k, "price": w["price"], "reason": w.get("reason"), "since": w["since"][:16],
                   "checked": (w.get("checked") or "")[:16]} for k, w in watchlist.items()],
        "telegram": _tg_status(st),
    }
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    allp = [_compact(x, groups.get(x["key"])) for x in rows]
    (out / "products.json").write_text(json.dumps({"generated": data["generated"], "products": allp},
                                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return data["counts"] | {"core": len(core)}
