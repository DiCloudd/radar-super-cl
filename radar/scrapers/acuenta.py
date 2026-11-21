"""Acuenta / SuperBodega aCuenta (Walmart Chile, plataforma Instaleap).

Fuente: payload RSC de /ca/<slug>/<id>?sort=2&currentPage=N (50 por página).
- sort=2: precio de menor a mayor.  La tienda se fija con cookies _IL-storeId / _IL-storeReference.
- El certificado TLS del servidor viene sin intermedio: el Fetcher lo completa vía AIA.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config import Source
from ..models import Product, Promo
from . import rsc
from .base import PageResult, Scraper, money

# "10GR" "1KG" "100ML" "1LT" "1UN"
_SUBUNIT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(GR|G|KG|ML|CC|LT|L|UN|U)\s*$", re.I)
_TO_BASE = {"GR": ("g", 1), "G": ("g", 1), "KG": ("g", 1000), "ML": ("ml", 1), "CC": ("ml", 1),
            "LT": ("ml", 1000), "L": ("ml", 1000), "UN": ("un", 1), "U": ("un", 1)}

DEFAULT_STORE = {"_IL-storeId": "843", "_IL-storeReference": "580"}  # aCuenta El Monte (RM)


def content_from_subunit(sub_unit: str | None, sub_qty) -> tuple[float, str] | None:
    """(cantidad total en g/ml/un, base). 10GR x 3 = 30 g."""
    if not sub_unit or not sub_qty:
        return None
    m = _SUBUNIT.match(sub_unit)
    if not m:
        return None
    base, mult = _TO_BASE[m.group(2).upper()]
    total = float(m.group(1).replace(",", ".")) * mult * float(sub_qty)
    return (total, base) if total > 0 else None


def _promos(p: dict) -> list[Promo]:
    out = []
    for pr in p.get("promotions") or []:
        if not pr or not pr.get("isActive", True):
            continue
        t = (pr.get("type") or "").lower()
        ben = pr.get("benefit") or {}
        qty = ben.get("qty")
        if t in ("nx$", "nxm", "mxn") or (qty and int(qty) > 1):
            n = int(qty or 0) or 2
            val = money(ben.get("value"))
            label = ben.get("label") or pr.get("description") or t
            out.append(Promo("mxn", label, min_qty=n, unit_price=(val / n) if (val and t == "nx$") else None))
        elif t == "specialprice":
            continue  # precio especial ya reflejado en "price"
        else:
            out.append(Promo("other", pr.get("description") or t))
    return out


class AcuentaScraper(Scraper):
    store = "acuenta"
    base_url = "https://www.acuenta.cl"
    cookies = DEFAULT_STORE

    def page_url(self, source: Source, page: int) -> str:
        u = urlparse(source.url)
        q = dict(parse_qsl(u.query))
        if page > 1:
            q["currentPage"] = str(page)
        else:
            q.pop("currentPage", None)
        return urlunparse(u._replace(query=urlencode(q)))

    def parse_listing(self, html: str, source: Source) -> PageResult:
        rows = rsc.parse_rows(rsc.payload_text(html))
        res = PageResult(products=[], page_size=50)
        seen: set[str] = set()
        for r in rows.values():
            for d in rsc.walk_dicts(r):
                if d.get("__typename") == "PaginationModel" and res.total is None:
                    tot = rsc.resolve(d.get("total"), rows)
                    if isinstance(tot, dict):
                        res.total = tot.get("value")
                if d.get("__typename") != "CatalogProductModel" or d.get("sku") in seen:
                    continue
                seen.add(d["sku"])
                p = rsc.resolve(d, rows)
                price = money(p.get("price"))
                if not price:
                    continue
                prev = money(p.get("previousPrice"))
                eans = [e for e in (p.get("ean") or []) if isinstance(e, str) and e.isdigit()]
                cont = content_from_subunit(p.get("subUnit"), p.get("subQty"))
                unit_price = unit = None
                net = None
                if cont:
                    amount, base = cont
                    q = amount / 1000 if base in ("g", "ml") else amount
                    unit = {"g": "kg", "ml": "l", "un": "un"}[base]
                    unit_price = round(price / q, 2) if q else None
                    net = f"{amount:g} {base}"
                photos = p.get("photosUrl") or []
                stock = p.get("stock")
                res.products.append(Product(
                    store=self.store, sku=str(p["sku"]), name=p.get("name", ""), price=price,
                    list_price=prev if prev and prev > price else None,
                    unit_price=unit_price, unit=unit, net_content=net,
                    ean=eans[0] if eans else None, brand=p.get("brand"),
                    image=photos[0] if photos else None,
                    url=f"{self.base_url}/p/{p.get('slug')}" if p.get("slug") else self.base_url,
                    category=source.category,
                    in_stock=bool(p.get("isAvailable")) and (stock is None or stock > 0),
                    marketplace=bool(p.get("seller")), promos=_promos(p), alt_eans=eans[1:],
                ))
        return res
