"""Lectura de la FICHA de un producto (precio actual + stock), para la vigilancia de reposición.

Fuente principal: JSON-LD schema.org/Product (offers.price, offers.availability), que publican
casi todas las cadenas. Respaldos por plataforma cuando el JSON-LD no trae stock.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


@dataclass
class PdpInfo:
    price: float | None
    in_stock: bool | None     # None = no se pudo determinar (no se alerta)
    source: str


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).strip()
    if re.fullmatch(r"\d+(\.\d{1,2})?", s):       # "1690" o "1690.00"
        f = float(s)
    else:                                          # "$1.690" / "1.690"
        d = re.sub(r"[^\d]", "", s)
        f = float(d) if d else 0
    return f if f > 0 else None


def _walk(o):
    stack = [o]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            yield v
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)


def from_jsonld(html: str) -> PdpInfo | None:
    for raw in _LD.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for d in _walk(data):
            t = d.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                offers = d.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if offers.get("@type") == "AggregateOffer":
                    price = _num(offers.get("lowPrice") or offers.get("price"))
                else:
                    price = _num(offers.get("price"))
                av = str(offers.get("availability") or "")
                stock = None
                if av:
                    stock = av.rstrip("/").split("/")[-1] in ("InStock", "LimitedAvailability", "InStoreOnly",
                                                              "OnlineOnly", "PreOrder")
                if price or stock is not None:
                    return PdpInfo(price, stock, "jsonld")
    return None


def from_lider(html: str) -> PdpInfo | None:
    m = _ND.search(html)
    if not m:
        return None
    for d in _walk(json.loads(m.group(1))):
        if d.get("__typename") == "Product" and isinstance(d.get("priceInfo"), dict) and d.get("availabilityStatus"):
            price = _num((d["priceInfo"].get("currentPrice") or {}).get("price") if isinstance(
                d["priceInfo"].get("currentPrice"), dict) else d["priceInfo"].get("linePrice"))
            return PdpInfo(price, str(d.get("availabilityStatus")).upper() == "IN_STOCK", "lider")
    return None


def from_smu(html: str) -> PdpInfo | None:
    """Unimarc / Alvi: __NEXT_DATA__ con price + availableQuantity (su JSON-LD a veces dice
    'OutOfStock' aunque hay stock, por eso este va primero)."""
    m = _ND.search(html)
    if not m:
        return None
    for d in _walk(json.loads(m.group(1))):
        if "availableQuantity" in d and "price" in d:
            q = d.get("availableQuantity")
            return PdpInfo(_num(d.get("price")), (q > 0) if isinstance(q, (int, float)) else None, "smu")
    return None


def from_acuenta(html: str) -> PdpInfo | None:
    """Acuenta (Instaleap): payload RSC con price, stock e isAvailable."""
    from .scrapers import rsc
    rows = rsc.parse_rows(rsc.payload_text(html))
    for r in rows.values():
        for d in rsc.walk_dicts(r):
            if d.get("__typename") == "CatalogProductModel":
                p = rsc.resolve(d, rows)
                stock = p.get("stock")
                ok = bool(p.get("isAvailable")) and (stock is None or stock > 0)
                price = _num(p.get("price"))
                if (p.get("unit") or "").lower() in ("kg", "kilo") and p.get("clickMultiplier"):
                    price = round(price * float(p["clickMultiplier"])) if price else None   # peso variable
                return PdpInfo(price, ok, "acuenta")
    return None


def parse_pdp(store: str, html: str) -> PdpInfo | None:
    order = {"lider": (from_lider, from_jsonld), "unimarc": (from_smu, from_jsonld),
             "alvi": (from_smu, from_jsonld), "acuenta": (from_acuenta, from_jsonld)}.get(store, (from_jsonld,))
    best = None
    for fn in order:
        try:
            info = fn(html)
        except (json.JSONDecodeError, TypeError, AttributeError):
            info = None
        if info and info.in_stock is not None and info.price:
            return info
        best = best or info
    return best
