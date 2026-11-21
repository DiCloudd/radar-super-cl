"""Normalización de contenido neto: "1,5 L", "6 x 350 cc", "pack 12 un", "900 g", "x kg".

Devuelve cantidades en unidades base: g (masa), ml (volumen), un (unidades).
La causa n.º 1 de falsos positivos en supermercados es leer mal el pack o la unidad,
así que ante la duda se devuelve None (mejor no comparar que comparar mal).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UNIT_MAP = {
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramos": ("g", 1), "grams": ("g", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
    "mg": ("g", 0.001),
    "ml": ("ml", 1), "cc": ("ml", 1), "cm3": ("ml", 1), "mls": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "litro": ("ml", 1000),
    "litros": ("ml", 1000), "ltr": ("ml", 1000),
    "un": ("un", 1), "u": ("un", 1), "und": ("un", 1), "unid": ("un", 1), "unidad": ("un", 1),
    "unidades": ("un", 1), "uds": ("un", 1),
}
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNITS = "|".join(sorted(map(re.escape, _UNIT_MAP), key=len, reverse=True))
_RX_PACK = re.compile(rf"(\d+)\s*(?:x|X|×)\s*{_NUM}\s*({_UNITS})\b", re.I)
_RX_PACK_WORD = re.compile(r"\b(?:pack|caja|display|six\s*pack)\s*(?:de\s*)?(\d+)\b", re.I)
_RX_SIMPLE = re.compile(rf"{_NUM}\s*({_UNITS})\b", re.I)
_RX_BULK = re.compile(r"(?:\bx\s*kg\b|\bpor\s+kilo\b|\bgranel\b|\ba\s+granel\b)", re.I)
_SMALL_FORMAT = re.compile(r"\b(muestra|mini|viaje|travel|sachet|monodosis)\b", re.I)


@dataclass(frozen=True)
class Content:
    amount: float       # cantidad TOTAL en unidad base (g, ml, un)
    base: str           # "g" | "ml" | "un"
    pack: int = 1       # unidades en el pack
    bulk: bool = False  # se vende por kilo (granel)

    @property
    def per_unit_label(self) -> str:
        return {"g": "kg", "ml": "l", "un": "un"}[self.base]

    @property
    def amount_in_label_units(self) -> float:
        """Cantidad expresada en kg / l / un (para $/kg, $/l, $/un)."""
        return self.amount / 1000 if self.base in ("g", "ml") else self.amount


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_content(text: str | None) -> Content | None:
    if not text:
        return None
    t = " " + text.replace(" ", " ") + " "
    if _RX_BULK.search(t):
        return Content(amount=1000, base="g", pack=1, bulk=True)
    m = _RX_PACK.search(t)
    if m:
        n, qty, unit = int(m.group(1)), _num(m.group(2)), m.group(3).lower()
        base, mult = _UNIT_MAP[unit]
        if n <= 0 or qty <= 0 or n > 200:
            return None
        return Content(amount=n * qty * mult, base=base, pack=n)
    matches = list(_RX_SIMPLE.finditer(t))
    if not matches:
        mw = _RX_PACK_WORD.search(t)
        if mw:
            return Content(amount=int(mw.group(1)), base="un", pack=int(mw.group(1)))
        return None
    # "Jurel 300 g - Neto 425 g": hay varias medidas -> preferir la que acompaña a "neto"
    chosen = matches[-1] if re.search(r"neto", t, re.I) and len(matches) > 1 else matches[0]
    qty, unit = _num(chosen.group(1)), chosen.group(2).lower()
    base, mult = _UNIT_MAP[unit]
    if qty <= 0:
        return None
    pack = 1
    mw = _RX_PACK_WORD.search(t)
    if mw and base != "un":
        pack = int(mw.group(1))
    amount = qty * mult * pack
    if base == "un":
        pack = int(qty)
    return Content(amount=amount, base=base, pack=pack)


def is_small_format(text: str | None) -> bool:
    return bool(text and _SMALL_FORMAT.search(text))


def unit_price(price: float, content: Content | None) -> tuple[float, str] | None:
    """$ por kg / l / un. None si no se puede calcular con seguridad."""
    if not content or price <= 0:
        return None
    if content.bulk:
        return price, "kg"
    q = content.amount_in_label_units
    if q <= 0:
        return None
    return round(price / q, 2), content.per_unit_label
