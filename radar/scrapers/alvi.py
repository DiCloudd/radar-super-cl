"""Alvi (SMU, mayorista): misma plataforma que Unimarc -> reutiliza su extractor.

Diferencias: el total viene en "resources", el nombre útil es "name" (nameComplete repite texto)
y trae "priceSteps": precio mayorista por cantidad (ej. 3+ unidades a $850 c/u).
Bloquea las IPs de GitHub (Akamai): solo nodo Chile.
"""
from __future__ import annotations

import json
import re

from ..config import Source
from ..models import Promo
from .base import PageResult, money
from .unimarc import UnimarcScraper

_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class AlviScraper(UnimarcScraper):
    store = "alvi"
    base_url = "https://www.alvi.cl"
    name_field = "name"

    def parse_listing(self, html: str, source: Source) -> PageResult:
        res = super().parse_listing(html, source)
        m = _ND.search(html)
        if not m:
            return res
        queries = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
        raw = {}
        for q in queries:
            data = (q.get("state") or {}).get("data")
            if isinstance(data, dict) and "availableProducts" in data:
                if res.total is None:
                    try:
                        res.total = int(data.get("resources") or 0) or None
                    except (TypeError, ValueError):
                        pass
                for p in (data.get("availableProducts") or []) + (data.get("notAvailableProducts") or []):
                    raw[str(p.get("itemId") or p.get("sku"))] = p
        for prod in res.products:
            p = raw.get(prod.sku) or {}
            # Alvi publica la ficha en /product/<slug> (el detailUrl "/<slug>/p" da 404)
            slug = (p.get("detailUrl") or "").strip("/")
            if slug.endswith("/p"):
                slug = slug[:-2]
            if slug:
                prod.url = f"{self.base_url}/product/{slug}"
            for step in p.get("priceSteps") or []:
                q, pp = int(step.get("minQuantity") or 0), money(step.get("promotionalPrice"))
                if q > 1 and pp:
                    prod.promos.append(Promo("mxn", f"{q}+ un a ${int(pp):,} c/u".replace(",", "."),
                                             min_qty=q, unit_price=pp))
        return res
