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
_ANY_PACK = re.compile(r"\b(pack|paquetes?|six|display|multipack|bipack|tripack|caja|latas|botellas|unidades)\b|"
                       r"\b\d+\s*x\s*\d|"
                       r"\b\d+\s*(?:un|und|unid|unidades|latas|botellas)\b")
# productos que se venden por CONTEO (64 toallitas, 30 pañales): "pack 64 un" no es un multipack
_COUNTABLE = re.compile(r"\b(toallitas?|toallas|panales?|servilletas?|panuelos?|bolsas?|tabletas?|sobres?|"
                        r"capsulas?|discos?|pastillas?|comprimidos?|rollos?|hojas?|cotones?|apositos?|filtros?)\b")
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|cc|l|lt|lts|litros?|g|gr|grs|kg)\b")
_TO_BASE = {"ml": 1, "cc": 1, "l": 1000, "lt": 1000, "lts": 1000, "litro": 1000, "litros": 1000,
            "g": 1, "gr": 1, "grs": 1, "kg": 1000}


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


def item_size(name: str) -> float | None:
    """Tamaño de CADA unidad (ml o g) según el nombre: "Pack 6 un. Néctar 200 ml" -> 200."""
    ms = list(_SIZE.finditer(norm(name).replace(" 5 l ", " 5 l ")))
    if not ms:
        return None
    m = ms[-1]
    return float(m.group(1).replace(",", ".")) * _TO_BASE[m.group(2)]


def pack_size(r: dict) -> tuple[int, float | None]:
    """(N unidades del pack, contenido por unidad en g/ml o None)."""
    # SOLO packs explícitos ("Pack 6", "6 x 350 cc", "6 latas"). "200 un" de servilletas o "500 cc"
    # son UN producto, no un pack (falso positivo detectado al calibrar).
    m = re.search(r"\b(?:pack|six\s*pack)\s*(?:de\s*)?(\d{1,2})\b|\b(\d{1,2})\s*x\s*\d|"
                  r"\b(\d{1,2})\s*(?:latas|botellas|unidades de)\b", norm(r["name"]))
    n = int(next(g for g in m.groups() if g)) if m else 1
    if n > 1 and (n > 24 or _COUNTABLE.search(norm(r["name"]))):
        n = 1        # 64 toallitas / 30 pañales: es un conteo, no un multipack
    return max(n, 1), item_size(r["name"])


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
        if n == 1 and (_ANY_PACK.search(norm(r["name"])) or _COUNTABLE.search(norm(r["name"]))):
            continue    # "Caja 24 un.", "Lata 6 Un", "pack familiar": no son una unidad suelta
        if n == 1 and per is None:
            continue    # sin tamaño declarado no se puede asegurar que sea UNA unidad comparable
        if n == 1:
            singles[(b, bn)].append((r, per))
        elif n >= MIN_PACK:
            packs.append((r, b, bn, n, per))
    out = []
    for r, b, bn, n, per in packs:
        cands = [(s, sp) for s, sp in singles.get((b, bn), [])
                 if per is None or abs(sp - per) <= 0.15 * max(sp, per)]
        if not cands:
            continue
        same = [s for s, _ in cands if s["store"] == r["store"]]
        ref = min(same or [s for s, _ in cands], key=lambda s: s["price"])
        # si el pack declara tamaño y coincide con el de la "suelta", puede ser el TOTAL del pack
        # ("Hamburguesa Pack 10 Un 900 g" vs "Hamburguesa 900 g"): entonces debe ser barato incluso
        # comparado 1 a 1 con la suelta para contar como error.
        if per is not None and r["price"] >= 0.6 * ref["price"]:
            continue
        if r["price"] < PACK_RATIO * n * ref["price"]:
            pct = round(100 * (1 - r["price"] / (n * ref["price"])), 1)
            basis = (f"pack de {n} a ${round(r['price']):,} y 1 unidad suelta vale ${round(ref['price']):,}"
                     .replace(",", ".") + f" ({pct}% menos que comprarlas sueltas)")
            out.append(Absurd(r["key"], r["price"], ref["key"], ref["price"], n, pct, basis))
    return sorted(out, key=lambda a: -a.pct)
