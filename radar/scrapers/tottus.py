"""Tottus (Falabella). Fuente: __NEXT_DATA__ de /tottus-cl/lista/<CATG>/<nombre>?sortBy=...&page=N (48/pág).

Acceso: Tottus protege su web con Cloudflare. Este extractor NO evade esa protección: funciona
con la autorización del socio (regla en el Cloudflare de Tottus que deja pasar las peticiones
que traen la llave acordada en el header X-Radar-Partner). La llave vive en el GitHub Secret
TOTTUS_PARTNER_TOKEN; sin ella las fuentes de Tottus no se activan. Ver docs/TOTTUS_INTEGRACION.md.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config import Source
from ..models import Product, Promo
from .base import PageResult, Scraper, money

_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
PARTNER_ENV = "TOTTUS_PARTNER_TOKEN"
PARTNER_HEADER = "X-Radar-Partner"
_PUM_UNIT = {"KG": "kg", "LT": "l", "L": "l", "UN": "un", "100 GR": None, "100 ML": None}


def _first_price(p: dict) -> float | None:
    vals = p.get("price") or []
    return money(vals[0]) if vals else None


class TottusScraper(Scraper):
    store = "tottus"
    base_url = "https://www.tottus.cl"

    def extra_headers(self) -> dict:
        token = os.environ.get(PARTNER_ENV, "").strip()
        return {PARTNER_HEADER: token} if token else {}

    def referer(self, source: Source) -> str | None:
        return self.base_url + "/tottus-cl"

    def page_url(self, source: Source, page: int) -> str:
        u = urlparse(source.url)
        q = dict(parse_qsl(u.query))
        if page > 1:
            q["page"] = str(page)
        else:
            q.pop("page", None)
        return urlunparse(u._replace(query=urlencode(q)))

    def parse_listing(self, html: str, source: Source) -> PageResult:
        res = PageResult(products=[], page_size=48)
        m = _ND.search(html)
        if not m:
            res.warnings.append("sin __NEXT_DATA__")
            return res
        pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        pag = pp.get("pagination") or {}
        res.total = pag.get("count")
        res.page_size = pag.get("perPage") or 48
        for x in pp.get("results") or []:
            p = self._product(x, source)
            if p:
                res.products.append(p)
        return res

    def _product(self, x: dict, source: Source) -> Product | None:
        prices = {p.get("type"): p for p in x.get("prices") or []}
        cur = prices.get("internetPrice") or prices.get("eventPrice") or prices.get("normalPrice")
        price = _first_price(cur) if cur else None
        if not price:
            return None
        normal = _first_price(prices["normalPrice"]) if "normalPrice" in prices and prices.get("internetPrice") else None
        card = None
        promos = []
        for t, p in prices.items():
            if t and "cmr" in t.lower():
                card = _first_price(p)
                if card and card < price:
                    promos.append(Promo("card", "Precio CMR", unit_price=card, card_only=True))
                else:
                    card = None
        unit_price = unit = None
        pum = (cur or {}).get("pum") or {}
        if pum.get("price"):
            label = (pum.get("label") or "").upper().strip()
            unit = _PUM_UNIT.get(label, None)
            unit_price = money(pum["price"][0]) if unit else None
        meas = x.get("measurements") or {}
        net = meas.get("format") or None
        if (meas.get("unit") or "").upper() == "KG" and net and not re.search(r"\d", net or ""):
            net = "≈ peso variable"
        for pr in x.get("promotions") or []:
            txt = pr.get("text") or pr.get("title") if isinstance(pr, dict) else None
            if txt:
                promos.append(Promo("other", str(txt)[:60]))
        imgs = x.get("mediaUrls") or []
        return Product(
            store=self.store, sku=str(x.get("skuId") or x.get("productId")), name=x.get("displayName") or "",
            price=price, list_price=normal if normal and normal > price else None, card_price=card,
            unit_price=unit_price, unit=unit, net_content=net, brand=(x.get("brand") or "").title() or None,
            image=imgs[0] if imgs else None, url=x.get("url") or self.base_url, category=source.category,
            in_stock=True, marketplace=(x.get("sellerId") or "TOTTUS_CHILE") != "TOTTUS_CHILE",
            promos=promos,
        )
