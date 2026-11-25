"""Unimarc (SMU, VTEX + Next.js).  Fuente: __NEXT_DATA__ de /category/<slug>?orderBy=...&page=N.

Bloquea las IPs de GitHub (Akamai 403): solo corre en el nodo opcional de Chile.
Precios con etiqueta "Club Unimarc" se tratan como precio de club (aparte del público).
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config import Source
from ..models import Product, Promo
from .base import PageResult, Scraper, money

_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_WEIGHTED = re.compile(r"\b(granel|al vac[ií]o|aprox\.?|a granel|por kilo|x kg)\b", re.I)
_PPUM = re.compile(r"\$\s*([\d.]+)\s*x\s*(kg|lt|l|un|ml|g)", re.I)


class UnimarcScraper(Scraper):
    store = "unimarc"
    base_url = "https://www.unimarc.cl"
    name_field = "nameComplete"

    def page_url(self, source: Source, page: int) -> str:
        u = urlparse(source.url)
        q = dict(parse_qsl(u.query))
        if page > 1:
            q["page"] = str(page)
        else:
            q.pop("page", None)
        return urlunparse(u._replace(query=urlencode(q)))

    def parse_listing(self, html: str, source: Source) -> PageResult:
        m = _ND.search(html)
        res = PageResult(products=[], page_size=50)
        if not m:
            res.warnings.append("sin __NEXT_DATA__")
            return res
        pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        queries = (pp.get("dehydratedState") or {}).get("queries") or []
        data = next((q["state"]["data"] for q in queries
                     if isinstance(q.get("state", {}).get("data"), dict)
                     and "availableProducts" in q["state"]["data"]), None)
        if not data:
            res.warnings.append("sin productos en dehydratedState")
            return res
        try:
            res.total = int(data.get("resource") or 0) or None
        except ValueError:
            pass
        for avail, items in ((True, data.get("availableProducts") or []),
                             (False, data.get("notAvailableProducts") or [])):
            for p in items:
                prod = self._product(p, avail, source)
                if prod:
                    res.products.append(prod)
        return res

    def _product(self, p: dict, avail: bool, source: Source) -> Product | None:
        s = (p.get("sellers") or [{}])[0]
        price, lp = money(s.get("price")), money(s.get("listPrice"))
        if not price:
            return None
        pd = p.get("priceDetail") or {}
        tag = ((pd.get("promotionalTag") or {}).get("text") or "")
        promos, card_price = [], None
        public_price = price
        if "club" in tag.lower() and lp and lp > price:
            # precio exclusivo de Club Unimarc: el público paga el normal
            card_price, public_price = price, lp
            promos.append(Promo("card", tag, unit_price=price, card_only=True))
        req = pd.get("itemsRequiredForPromotion")
        if req and int(req) > 1:
            promos.append(Promo("mxn", pd.get("promotionMessage") or f"{req} unidades",
                                min_qty=int(req)))
        ppum_txt = s.get("ppumListPrice") if card_price else s.get("ppum")
        unit_price = unit = None
        mm = _PPUM.search(ppum_txt or "")
        if mm:
            unit_price = money(mm.group(1))
            unit = {"lt": "l", "l": "l", "kg": "kg", "un": "un"}.get(mm.group(2).lower())
        imgs = p.get("images") or []
        stock_q = s.get("availableQuantity")
        name = p.get(self.name_field) or p.get("name", "")
        net = p.get("netContent") or None
        url_path = p.get("detailUrl") or p.get("slug") or ""
        # Peso variable (carne "al vacío", pollo "granel", "aprox"): el precio depende del peso de cada
        # pieza y cambia entre lecturas -> falsos Godzilla. Se marca "≈" para excluirlo de alertas.
        if _WEIGHTED.search(name) or re.search(r"-kg/p$|granel|aprox", url_path):
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\b", name, re.I)
            net = (f"≈{float(m.group(1).replace(',', '.')) / (1000 if m.group(2).lower() == 'g' else 1):g} kg"
                   if m else "≈ peso variable")
        return Product(
            store=self.store, sku=str(p.get("itemId") or p.get("sku")), name=name,
            price=public_price, list_price=lp if lp and lp > public_price else None,
            card_price=card_price, unit_price=unit_price, unit=unit, net_content=net,
            ean=(p.get("ean") or None), brand=p.get("brand"), image=imgs[0] if imgs else None,
            url=self.base_url + (p.get("detailUrl") or p.get("slug") or ""), category=source.category,
            in_stock=avail and (stock_q is None or stock_q > 0), promos=promos,
        )
