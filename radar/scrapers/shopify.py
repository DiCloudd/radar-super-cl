"""Supermercados hechos en Shopify (ej. Super9 / Supermercados el 9).

Fuente: /products.json?limit=250&page=N (catálogo público que Shopify expone para cada tienda;
robots.txt lo permite). No se usa sort_by (robots.txt lo prohíbe). Shopify responde 429 si se
consulta muy seguido: se trata como bloqueo -> pausa con backoff.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config import Source
from ..models import Product
from .base import PageResult, Scraper


def money(v) -> float | None:
    """Shopify entrega "1690.00" (con decimales): no sirve el parser de pesos con punto de miles."""
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(f) if f > 0 else None


class ShopifyScraper(Scraper):
    store = ""
    base_url = ""

    def __init__(self, store: str, base_url: str):
        self.store, self.base_url = store, base_url

    def page_url(self, source: Source, page: int) -> str:
        u = urlparse(source.url)
        q = dict(parse_qsl(u.query))
        q.setdefault("limit", "250")
        if page > 1:
            q["page"] = str(page)
        else:
            q.pop("page", None)
        return urlunparse(u._replace(query=urlencode(q)))

    def parse_listing(self, html: str, source: Source) -> PageResult:
        res = PageResult(products=[], page_size=250)
        try:
            items = json.loads(html).get("products") or []
        except (json.JSONDecodeError, AttributeError):
            res.warnings.append("respuesta no es JSON de Shopify")
            return res
        for p in items:
            variants = [v for v in p.get("variants") or [] if money(v.get("price"))]
            if not variants:
                continue
            v = min(variants, key=lambda x: money(x["price"]))     # variante más barata
            price = money(v["price"])
            cmp = money(v.get("compare_at_price"))
            imgs = p.get("images") or []
            barcode = (v.get("barcode") or "").strip()
            name = p.get("title") or ""
            if len(variants) > 1 and v.get("title") and v["title"] != "Default Title":
                name = f"{name} {v['title']}"
            res.products.append(Product(
                store=self.store, sku=str(v.get("id") or p.get("id")), name=name, price=price,
                list_price=cmp if cmp and cmp > price else None,
                ean=barcode if barcode.isdigit() and 8 <= len(barcode) <= 14 else None,
                brand=p.get("vendor") or None, image=(imgs[0].get("src") if imgs else None),
                url=f"{self.base_url}/products/{p.get('handle')}", category=source.category,
                in_stock=bool(v.get("available", True)),
            ))
        return res
