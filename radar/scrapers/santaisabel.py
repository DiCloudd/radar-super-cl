"""Santa Isabel (Cencosud; misma plataforma que Jumbo).

Fuente: payload RSC de la página de categoría /<categoria>?page=N&orderBy=...
- El grid real = ItemList del JSON-LD (40 por página); los demás productos del payload
  son carruseles y se ignoran.
- El EAN solo está en la ficha (/<slug>/p, JSON-LD "gtin"): se completa aparte.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

from ..config import Source
from ..models import Product, Promo
from . import rsc
from .base import PageResult, Scraper, money

_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def _grid_slugs(html: str) -> list[str]:
    for raw in _LD.findall(html):
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(j, dict) and j.get("@type") == "ItemList":
            out = []
            for e in j.get("itemListElement", []):
                path = urlparse(e.get("url", "")).path.strip("/").split("/")
                if len(path) >= 2 and path[-1] == "p":
                    out.append(path[-2])
            return out
    return []


def _promos(item: dict) -> tuple[list[Promo], float | None]:
    promos, card_price = [], None
    for p in item.get("promotions") or []:
        card = (p.get("paymentMethods") or "ALL") != "ALL"
        members = (p.get("userProperties") or "ALL") != "ALL"
        m = int(p.get("mQuantity") or 1)
        unit = money(p.get("unitPrice"))
        if p.get("type") == "mx" and m > 1:
            promos.append(Promo("mxn", f"{m}x${int(money(p.get('value')) or 0):,}".replace(",", "."),
                                min_qty=m, unit_price=unit, card_only=card or members))
        elif card or members:
            if m <= 1 and unit:
                card_price = unit if card_price is None else min(card_price, unit)
            promos.append(Promo("card", "Tarjeta Cencosud" if card else "Exclusivo socios",
                                min_qty=m, unit_price=unit, card_only=True))
    return promos, card_price


class SantaIsabelScraper(Scraper):
    store = "santaisabel"
    base_url = "https://www.santaisabel.cl"
    sorts = {"price_asc": "OrderByPriceASC", "discount": "OrderByBestDiscountDESC"}

    def page_url(self, source: Source, page: int) -> str:
        u = urlparse(source.url)
        q = dict(parse_qsl(u.query))
        if page > 1:
            q["page"] = str(page)
        else:
            q.pop("page", None)
        return urlunparse(u._replace(query=urlencode(q)))

    def parse_listing(self, html: str, source: Source) -> PageResult:
        slugs = _grid_slugs(html)
        rows = rsc.parse_rows(rsc.payload_text(html))
        by_slug: dict[str, dict] = {}
        for r in rows.values():
            for d in rsc.walk_dicts(r):
                if "productId" in d and "items" in d and "slug" in d and d["slug"] not in by_slug:
                    by_slug[d["slug"]] = d
        res = PageResult(products=[], page_size=40)
        for slug in slugs:
            d = by_slug.get(slug)
            if not d or not d.get("items"):
                res.warnings.append(f"sin datos para {slug}")
                continue
            it = d["items"][0]
            price = money(it.get("price"))
            if not price:
                continue
            promos, card_price = _promos(it)
            lp = money(it.get("listPrice"))
            ppum_unit = (it.get("ppumMeasurementUnit") or "").lower() or None
            img = (it.get("images") or [None])[0]
            res.products.append(Product(
                store=self.store, sku=str(it.get("skuId") or d["productId"]), name=it.get("name", ""),
                price=price, list_price=lp if lp and lp > price else None, card_price=card_price,
                unit_price=money(it.get("ppumPrice")),
                unit={"lt": "l", "l": "l", "kg": "kg", "un": "un"}.get(ppum_unit or "", ppum_unit),
                url=f"{self.base_url}/{slug}/p", brand=d.get("brand"), image=img,
                category=source.category, in_stock=bool(it.get("stock", True)),
                marketplace=bool(d.get("isMarketplace")), promos=promos,
            ))
        return res


def parse_ean_from_pdp(html: str) -> str | None:
    for raw in _LD.findall(html):
        try:
            j = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = j if isinstance(j, list) else [j]
        for x in items:
            if isinstance(x, dict) and x.get("@type") == "Product" and x.get("gtin"):
                g = re.sub(r"\D", "", str(x["gtin"]))
                return g if 8 <= len(g) <= 14 else None
    return None
