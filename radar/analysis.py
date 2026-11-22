"""Reglas de errores de precio, Godzilla y gangas. Diseñadas para CERO falsos positivos:
ante la duda, no se marca.

Capas de defensa aplicadas a todo candidato:
 1) unidad/pack: solo se compara dentro de grupos del matcher (EAN o marca+contenido exacto);
 2) promos por cantidad: se usa el precio de 1 unidad (nunca el precio "llevando N");
 3) precio con tarjeta/club: se ignora (se usa el público);
 4) marketplace/vendedor externo: excluido;
 5) formatos muestra/mini/viaje: excluidos;
 6) si el "precio normal" de la propia tienda ya es bajo, no es un error de etiqueta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

from .units import is_small_format

ERROR_CROSS_RATIO = 0.45       # < 45% de la mediana de otras cadenas
ERROR_CROSS_RATIO_SINGLE = 0.35  # con una sola referencia, más estricto
ERROR_OWN_DROP = 0.55          # además debe haber BAJADO: precio < 55% de su propio precio anterior
ERROR_ONLY_OWN_RATIO = 0.35    # sin otras cadenas: < 35% de su propio precio anterior...
ERROR_ONLY_OWN_MIN = 3000      # ...y precio anterior >= $3.000
GODZILLA_DROP = 0.40
GODZILLA_MIN_PRICE = 5000
GANGA_RATIO = 0.70
MIN_PRICE = 300                # nada bajo $300 (ruido)
ARTIFACT_RATIO = 0.15          # caer a <15% del precio anterior sin otra cadena que lo confirme
                               # = casi siempre cambio de vendedor/pack, no error de etiqueta
OWN_MIN_HELD_H = 12            # el precio anterior debió regir al menos 12 h


@dataclass
class Finding:
    key: str
    kind: str          # "error" | "godzilla" | "ganga"
    price: float
    reference: float   # precio de referencia (otras cadenas o propio anterior)
    basis: str         # texto explicando la regla
    pct: float         # % bajo la referencia


def _eligible(r: dict) -> bool:
    return (bool(r["in_stock"]) and not r["marketplace"] and r["price"] >= MIN_PRICE
            and not is_small_format(r["name"])
            and not (r.get("net_content") or "").startswith("≈"))   # peso variable: excluido


def analyze(rows: list[dict], groups: dict[str, str], history: dict[str, list[tuple[str, float]]],
            now: datetime | None = None) -> list[Finding]:
    """rows: productos; groups: key -> id de grupo; history: key -> [(ts, price)] ordenado."""
    now = now or datetime.now(timezone.utc)
    by_group: dict[str, list[dict]] = {}
    for r in rows:
        if r["key"] in groups:
            by_group.setdefault(groups[r["key"]], []).append(r)
    out: list[Finding] = []
    for r in rows:
        if not _eligible(r):
            continue
        price = r["price"]
        hist = history.get(r["key"], [])
        prev = _previous_price(hist, price)
        recent = _changed_since(hist, now - timedelta(hours=48))
        others = [o["price"] for o in by_group.get(groups.get(r["key"], ""), [])
                  if o["store"] != r["store"] and not o["marketplace"] and o["price"] > 0]
        ref = median(others) if others else None
        lp = r.get("list_price")

        # capa 6: el "normal" de la tienda ya es bajo frente a otras cadenas -> es su precio, no un error
        normal_low = bool(ref and lp and lp < 0.6 * ref)
        dropped = bool(prev and recent and price < ERROR_OWN_DROP * prev)

        if dropped and not normal_low:
            if ref is not None:
                lim = ERROR_CROSS_RATIO if len(others) >= 2 else ERROR_CROSS_RATIO_SINGLE
                if price < lim * ref:
                    out.append(Finding(r["key"], "error", price, ref,
                                       f"{_pct(price, ref)}% bajo la mediana de {len(others)} cadena(s) "
                                       f"y bajó desde ${prev:,.0f}".replace(",", "."), _pct(price, ref)))
                    continue
            elif (prev >= ERROR_ONLY_OWN_MIN and ARTIFACT_RATIO * prev <= price < ERROR_ONLY_OWN_RATIO * prev
                  and _held_hours(hist, prev) >= OWN_MIN_HELD_H):
                out.append(Finding(r["key"], "error", price, prev,
                                   f"bajó {_pct(price, prev)}% desde ${prev:,.0f} (sin otras cadenas)".replace(",", "."),
                                   _pct(price, prev)))
                continue
        # Godzilla: caída fuerte en 24 h sobre un precio alto
        p24 = prev
        if p24 and ref is None and price < ARTIFACT_RATIO * p24:
            continue   # caída sospechosa de dato (vendedor/pack): ni error ni Godzilla
        if p24 and p24 >= GODZILLA_MIN_PRICE and _held_hours(hist, p24) >= OWN_MIN_HELD_H and price <= (1 - GODZILLA_DROP) * p24 and \
                _changed_since(hist, now - timedelta(hours=24)):
            if ref is None or price < ref:   # si en otras cadenas es aún más barato, no es noticia
                out.append(Finding(r["key"], "godzilla", price, p24,
                                   f"cayó {_pct(price, p24)}% en 24 h".replace(",", "."), _pct(price, p24)))
                continue
        if ref is not None and len(others) >= 1 and price <= GANGA_RATIO * ref:
            out.append(Finding(r["key"], "ganga", price, ref,
                               f"{_pct(price, ref)}% más barato que en otras cadenas", _pct(price, ref)))
    return out


def _pct(price: float, ref: float) -> float:
    return round(100 * (1 - price / ref), 1) if ref else 0.0


def _previous_price(hist: list[tuple[str, float]], current: float) -> float | None:
    """Último precio distinto del actual en el historial propio."""
    for _ts, p in reversed(hist):
        if p and p != current:
            return p
    return None


def _held_hours(hist: list[tuple[str, float]], price: float) -> float:
    """Horas que rigió `price` (desde que apareció hasta el cambio siguiente)."""
    for i in range(len(hist) - 1, 0, -1):
        if hist[i - 1][1] == price:
            j = i - 1
            while j > 0 and hist[j - 1][1] == price:
                j -= 1
            return (_dt(hist[i][0]) - _dt(hist[j][0])).total_seconds() / 3600
    return 0.0


def _changed_since(hist: list[tuple[str, float]], since: datetime) -> bool:
    return bool(len(hist) >= 2 and _dt(hist[-1][0]) >= since)


def _dt(ts: str) -> datetime:
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
