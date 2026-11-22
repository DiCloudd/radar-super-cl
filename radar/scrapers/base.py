"""Interfaz común de extractores (patrón Strategy): uno por plataforma."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import Source
from ..models import Product


@dataclass
class PageResult:
    products: list[Product]
    total: int | None = None          # total de productos de la categoría, si la tienda lo dice
    page_size: int | None = None
    warnings: list[str] = field(default_factory=list)


class Scraper(ABC):
    store: str = ""
    base_url: str = ""
    cookies: dict | None = None

    @abstractmethod
    def page_url(self, source: Source, page: int) -> str: ...

    @abstractmethod
    def parse_listing(self, html: str, source: Source) -> PageResult: ...

    def referer(self, source: Source) -> str | None:
        return self.base_url + "/"

    def extra_headers(self) -> dict:
        """Headers adicionales (ej. llave de socio acordada con la tienda)."""
        return {}


def money(value) -> float | None:
    """'$1.290' | 1290 | '1.290' -> 1290.0 (pesos chilenos, sin decimales)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    digits = re.sub(r"[^\d]", "", str(value))
    return float(digits) if digits else None
