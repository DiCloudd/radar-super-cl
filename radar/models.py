"""Modelo de producto normalizado (común a todas las cadenas)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Promo:
    kind: str            # "nominal" | "mxn" (lleva M paga N / M x $X) | "percent" | "card" | "other"
    text: str            # texto legible, ej. "2x$1.940"
    min_qty: int = 1     # unidades necesarias para que aplique
    unit_price: float | None = None   # precio por unidad si aplica la promo
    card_only: bool = False


@dataclass
class Product:
    store: str                 # "santaisabel" | "acuenta" | "unimarc"
    sku: str                   # id estable dentro de la cadena
    name: str
    price: float               # precio público que paga cualquiera hoy (1 unidad)
    url: str
    list_price: float | None = None      # "precio normal" declarado por la tienda
    card_price: float | None = None      # precio exclusivo con tarjeta/club (aparte)
    unit_price: float | None = None      # $ por unidad de medida (según la tienda)
    unit: str | None = None              # "kg" | "l" | "un"
    net_content: str | None = None       # texto original, ej. "1,5 L"
    ean: str | None = None
    brand: str | None = None
    image: str | None = None
    category: str | None = None
    in_stock: bool = True
    marketplace: bool = False
    promos: list[Promo] = field(default_factory=list)
    found_at: str = field(default_factory=now_iso)

    @property
    def key(self) -> str:
        return f"{self.store}:{self.sku}"

    @property
    def discount_pct(self) -> float:
        if self.list_price and self.list_price > self.price > 0:
            return round(100 * (1 - self.price / self.list_price), 1)
        return 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d
