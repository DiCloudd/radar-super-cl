"""📦 "Pack a precio de unidad": un pack de N (N >= 3) que cuesta casi lo mismo que UNA unidad del
mismo producto (misma marca y mismo nombre sin las palabras de pack). Ej.: "Cerveza X Pack 6 Un Lata"
a $1.090 cuando la lata suelta vale $990. Es una señal clara de precio mal cargado.

Por qué esta regla y no "barato para lo que es" en general: al calibrar con 13.160 productos reales,
comparar contra el "tipo" de producto daba falsos positivos (vino en caja vs vinos de botella, una
Gillette desechable vs máquinas premium...). Comparar el pack contra SU PROPIA unidad suelta no.

Condiciones: misma marca; mismo nombre base (sin pack/unidades/formato); la unidad suelta del mismo
tamaño cuando ambos lo declaran; precio del pack < PACK_RATIO × N × precio unitario suelto;
sin marketplace, sin peso variable; unidad suelta de la misma cadena o, si no hay, de otra.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .matcher import brand_key, norm
from .units import parse_content

PACK_RATIO = 0.40      # el pack sale a menos del 40% de comprar N sueltas
MIN_PACK = 3
_PACK_WORDS = re.compile(r"\b(pack|six|display|caja|unidades|unidad|und|un|uds|x|lata|latas|botella|botellas|"
                         r"desechable|retornable|de|con)\b")
_MEASURE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:ml|cc|l|lt|lts|g|gr|grs|kg|un|und|unidades|x)?\b")
_DEGREES = re.compile(r"\d+(?:[.,]\d+)?\s*°")
_ANY_PACK = re.compile(r"\b(pack|paquetes?|six|display|multipack|bipack|tripack)\b|\b\d+\s*x\s*\d")


@dataclass
class Absurd:
    key: str
    price: float
    single_key: str
    single_price: float
    pack: int
    pct: float
    basis: str


def base_name(name: str, brand: str | None) -> str:
    n = _DEGREES.sub(" ", norm(name.replace("°", "° ")))
    n = _MEASURE.sub(" ", n)
    n = _PACK_WORDS.sub(" ", n)
    words = [w for w in n.split() if w not in set(norm(brand or "").split())]
    return " ".join(words)


def pack_size(r: dict) -> tuple[int, float | None]:
    """(N unidades del pack, contenido por unidad en g/ml o None)."""
    c = parse_content(r.get("net_content")) if r.get("net_content") else None
    c = c or parse_content(r["name"])
    # SOLO packs explícitos ("Pack 6", "6 x 350 cc", "6 latas"). "200 un" de servilletas o "500 cc"
    # son UN producto, no un pack (falso positivo detectado al calibrar).
    m = re.search(r"\b(?:pack|six\s*pack)\s*(?:de\s*)?(\d{1,2})\b|\b(\d{1,2})\s*x\s*\d|"
                  r"\b(\d{1,2})\s*(?:latas|botellas|unidades de)\b", norm(r["name"]))
    n = int(next(g for g in m.groups() if g)) if m else 1
    per = (c.amount / max(c.pack, 1)) if c and c.base in ("g", "ml") else None
    return max(n, 1), per


def find_absurd(rows: list[dict]) -> list[Absurd]:
    singles: dict[tuple[str, str], list[tuple[dict, float | None]]] = defaultdict(list)
    packs = []
    for r in rows:
        if r.get("marketplace") or (r.get("net_content") or "").startswith("≈") or r["price"] <= 0:
            continue
        b = brand_key(r.get("brand"))
        bn = base_name(r["name"], r.get("brand"))
        if not b or len(bn.split()) < 1:
            continue
        n, per = pack_size(r)
        if n == 1 and _ANY_PACK.search(norm(r["name"])):
            continue    # "pack familiar 18 paquetes" no es una unidad suelta (falso positivo calibrado)
        if n == 1:
            singles[(b, bn)].append((r, per))
        elif n >= MIN_PACK:
            packs.append((r, b, bn, n, per))
    out = []
    for r, b, bn, n, per in packs:
        cands = [(s, sp) for s, sp in singles.get((b, bn), [])
                 if per is None or sp is None or abs(sp - per) <= 0.15 * max(sp, per)]
        if not cands:
            continue
        same = [s for s, _ in cands if s["store"] == r["store"]]
        ref = min(same or [s for s, _ in cands], key=lambda s: s["price"])
        if r["price"] < PACK_RATIO * n * ref["price"]:
            pct = round(100 * (1 - r["price"] / (n * ref["price"])), 1)
            basis = (f"pack de {n} a ${round(r['price']):,} y 1 unidad suelta vale ${round(ref['price']):,}"
                     .replace(",", ".") + f" ({pct}% menos que comprarlas sueltas)")
            out.append(Absurd(r["key"], r["price"], ref["key"], ref["price"], n, pct, basis))
    return sorted(out, key=lambda a: -a.pct)
