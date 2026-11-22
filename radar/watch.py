"""🚨 Vigilancia de reposición: ofertas buenas que están AGOTADAS se vigilan; si vuelven a tener
stock al mismo precio (o menos, tolerancia +5%) se crea una alerta.

- La lista se arma en cada publicación (publisher) y se guarda en meta["watch"].
- La reposición se detecta en dos vías: (1) cualquier lectura normal del producto (listado del
  monitor o datos del nodo de Chile) y (2) revisión activa de la ficha cada pocos minutos.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .models import now_iso
from .storage import Storage

WATCH_DAYS = 14          # se vigila hasta 14 días
PRICE_TOLERANCE = 1.05   # avisa si vuelve a <= precio vigilado +5%
MIN_DISCOUNT = 30        # descuento declarado mínimo para vigilar (si no es ganga/error/bajada)
PDP_STORES = {"lider", "jumbo", "santaisabel", "acuenta", "unimarc"}   # ficha legible (Alvi: 403)


def build_watchlist(st: Storage, rows: list[dict], flagged: dict[str, str]) -> dict:
    """flagged: key -> motivo (error/godzilla/ganga/bajada). Devuelve la lista vigente."""
    now = datetime.now(timezone.utc)
    watch = st.get_meta("watch", {}) or {}
    by_key = {r["key"]: r for r in rows}
    for r in rows:
        if r["in_stock"] or r["marketplace"] or (r.get("net_content") or "").startswith("≈"):
            continue
        reason = flagged.get(r["key"])
        if not reason and r["list_price"] and r["list_price"] > r["price"]:
            pct = 100 * (1 - r["price"] / r["list_price"])
            if pct >= MIN_DISCOUNT:
                reason = f"-{pct:.0f}%"
        if reason and r["key"] not in watch:
            watch[r["key"]] = {"price": r["price"], "store": r["store"], "url": r["url"], "name": r["name"],
                               "reason": reason, "since": now_iso()}
    # limpiar: vencidos, o que ya tienen stock (se avisó o volvieron caros)
    for k in list(watch):
        w = watch[k]
        r = by_key.get(k)
        if datetime.fromisoformat(w["since"]) < now - timedelta(days=WATCH_DAYS) or (r and r["in_stock"]):
            watch.pop(k)
    st.set_meta("watch", watch)
    return watch


def on_reading(st: Storage, key: str, name: str, store: str, url: str, price: float, in_stock: bool,
               image: str | None = None) -> bool:
    """Llamar con cada lectura. Si un producto vigilado vuelve con stock y buen precio -> alerta."""
    watch = st.get_meta("watch", {}) or {}
    w = watch.get(key)
    if not w or not in_stock or not price:
        return False
    watch.pop(key)
    st.set_meta("watch", watch)
    if price > w["price"] * PRICE_TOLERANCE:
        return False   # volvió, pero más caro: no es la oferta
    payload = {"name": name, "store": store, "url": url, "price": price, "was_price": w["price"],
               "reason": w.get("reason"), "since": w["since"], "image": image}
    st.db.execute("INSERT INTO alerts(ts, product_key, kind, payload) VALUES(?,?,?,?)",
                  (now_iso(), key, "restock", json.dumps(payload, ensure_ascii=False)))
    st.commit()
    return True
