"""Lider (Walmart Chile).  Fuente: __NEXT_DATA__ de /browse/<ruta>/<id>?sort=price_low&page=N (40/pág).

- Solo rutas /browse/ (robots.txt no las restringe; /search* y /supermercado* sí, y no se usan).
- PerimeterX aparece a veces: se trata como bloqueo (BlockedError) -> pausa con backoff. Sin evasión.
- usItemId = EAN con ceros a la izquierda y, a veces, sin dígito verificador.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..config import Source
from ..models import Product, Promo
from ..units import parse_content, unit_price
from .base import PageResult, Scraper, money

_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_UNIT = re.compile(r"\$\s*([\d.]+(?:,\d+)?)\s*/\s*([A-Za-zÁ-ú]+)")
_UNIT_MAP = {"kilogramo": ("kg", 1), "kg": ("kg", 1), "gramo": ("kg", 1000), "g": ("kg", 1000),
             "litro": ("l", 1), "lt": ("l", 1), "l": ("l", 1), "mililitro": ("l", 1000), "ml": ("l", 1000),
             "ea": ("un", 1), "unidad": ("un", 1), "un": ("un", 1)}


def ean_check_digit(body: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(reversed(body), start=1))
    return str((10 - total % 10) % 10)


def valid_ean(code: str) -> bool:
    return code.isdigit() and len(code) in (8, 12, 13, 14) and ean_check_digit(code[:-1]) == code[-1]


def ean_from_item_id(item_id: str | None) -> str | None:
    """'00780161059199' -> '7801610591994' (añade el dígito verificador si falta)."""
    if not item_id or not item_id.isdigit():
        return None
    s = item_id.lstrip("0")
    if len(s) == 13 and valid_ean(s):
        return s
    if len(s) == 12:
        return s + ean_check_digit(s)
    if len(s) == 7:   # EAN-8 sin verificador
        return s + ean_check_digit(s)
    return None


def parse_unit(text: str | None) -> tuple[float, str] | None:
    m = _UNIT.search(text or "")
    if not m:
        return None
    unit = m.group(2).lower().rstrip("s")
    if unit not in _UNIT_MAP:
        return None
    label, mult = _UNIT_MAP[unit]
    val = money(m.group(1).split(",")[0])
    return (val * mult, label) if val else None


class LiderScraper(Scraper):
    store = "lider"
    base_url = "https://www.lider.cl"

    def page_url(self, source: Source, page: int) -> str:
        u = urlparse(source.url)
        q = dict(parse_qsl(u.query))
        if page > 1:
            q["page"] = str(page)
        else:
            q.pop("page", None)
        return urlunparse(u._replace(query=urlencode(q)))

    def parse_listing(self, html: str, source: Source) -> PageResult:
        res = PageResult(products=[], page_size=40)
        m = _ND.search(html)
        if not m:
            res.warnings.append("sin __NEXT_DATA__")
            return res
        data = json.loads(m.group(1))
        items, seen = [], set()
        stack = [data]
        while stack:
            v = stack.pop()
            if isinstance(v, dict):
                if v.get("__typename") == "Product" and isinstance(v.get("priceInfo"), dict) and v.get("name"):
                    if v.get("usItemId") not in seen:
                        seen.add(v.get("usItemId"))
                        items.append(v)
                if "maxPage" in v and isinstance(v["maxPage"], int) and res.total is None:
                    res.total = v["maxPage"] * 40   # aproximado: sirve para cortar el barrido
                stack.extend(reversed(list(v.values())))   # preorden: conserva el orden de la tienda
            elif isinstance(v, list):
                stack.extend(reversed(v))
        for it in items:
            p = self._product(it, source)
            if p:
                res.products.append(p)
        return res

    def _product(self, it: dict, source: Source) -> Product | None:
        pi = it["priceInfo"]
        price = money(pi.get("linePrice") or pi.get("itemPrice"))
        if not price:
            return None
        was = money(pi.get("wasPrice"))
        member = money(pi.get("memberPriceString"))
        promos = []
        card_price = None
        if member and member < price:
            card_price = member
            promos.append(Promo("card", "Precio Mi Club Lider", unit_price=member, card_only=True))
        for pd in it.get("promoData") or []:
            txt = (pd or {}).get("text") or (pd or {}).get("description")
            if txt:
                promos.append(Promo("other", str(txt)[:60]))
        # promos por cantidad "2 x $3000" / "3 x $2990": vienen como insignia COMBINA
        badges = it.get("badges") or {}
        members = list(badges.get("flags") or []) + [m for g in (badges.get("groups") or [])
                                                      for m in ((g or {}).get("members") or [])]
        for mem in members:
            if not ((mem or {}).get("key") == "COMBINA" and mem.get("text")):
                continue
            mm = re.search(r"(\d+)\s*x\s*\$?\s*([\d.]+)", mem["text"])
            if mm:
                n, tot = int(mm.group(1)), money(mm.group(2))
                promos.append(Promo("mxn", mem["text"].strip(), min_qty=n,
                                    unit_price=round(tot / n) if tot and n else None))
        # Lider redondea su $/unidad a pesos por gramo/ml ("$2/Mililitro"): impreciso.
        # Preferimos calcularlo desde el contenido del nombre; su valor queda como respaldo.
        up = unit_price(price, parse_content(it.get("name")))
        if not up:
            up = parse_unit(pi.get("unitPrice"))
            if up and (up[1] == "un" or up[0] < 50):
                up = None
        net = None
        if pi.get("finalCostByWeight") or (it.get("salesUnitType") or "").upper() == "WEIGHT":
            # "Costo final por peso": precio estimado por peso promedio -> peso variable
            pk = parse_unit(pi.get("unitPrice"))
            up = (pk[0], "kg") if pk and pk[1] == "kg" else up
            c = parse_content(it.get("name"))
            net = f"≈{c.amount / 1000:g} kg" if c and c.base == "g" else "≈ peso variable"
        ean = ean_from_item_id(it.get("usItemId"))
        img = (it.get("imageInfo") or {}).get("thumbnailUrl")
        status = ((it.get("availabilityStatusV2") or {}).get("value") or "IN_STOCK").upper()
        url = it.get("canonicalUrl") or ""
        url = self.base_url + url.split("?")[0] if url.startswith("/") else url
        return Product(
            store=self.store, sku=str(it.get("usItemId") or it.get("id")), name=it.get("name", ""),
            price=price, list_price=was if was and was > price else None, card_price=card_price,
            unit_price=up[0] if up else None, unit=up[1] if up else None, net_content=net,
            ean=ean, brand=it.get("brand"), image=img, url=url, category=source.category,
            in_stock=status == "IN_STOCK", marketplace=(it.get("sellerType") or "INTERNAL") != "INTERNAL",
            promos=promos,
        )
