"""Parsers probados con HTML REAL (recortado/comprimido) capturado de cada tienda."""
import gzip
from pathlib import Path

import pytest

from radar.config import Source
from radar.scrapers import SCRAPERS
from radar.scrapers.acuenta import content_from_subunit
from radar.scrapers.santaisabel import parse_ean_from_pdp

FX = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return gzip.open(FX / name, "rt", encoding="utf-8").read()


def src(store, url, cat="despensa"):
    return Source(name=f"{store}-{cat}", store=store, category=cat, url=url)


# ---------------- Santa Isabel ----------------
@pytest.fixture(scope="module")
def sisa():
    s = src("santaisabel", "https://www.santaisabel.cl/despensa?orderBy=OrderByPriceASC")
    return SCRAPERS["santaisabel"].parse_listing(load("sisa_list.html.gz"), s)


def test_sisa_grid_has_40_products(sisa):
    assert len(sisa.products) == 40
    assert not sisa.warnings


def test_sisa_fields(sisa):
    p = next(x for x in sisa.products if x.name == "Salsa de Tomate Coliseo Italiana 200 g")
    assert p.price == 370 and p.list_price is None
    assert p.unit_price == 1850 and p.unit == "kg"
    assert p.url == "https://www.santaisabel.cl/salsa-de-tomate-it-2077189/p"
    assert p.brand == "Coliseo" and p.in_stock and not p.marketplace


def test_sisa_offer_and_list_price(sisa):
    p = next(x for x in sisa.products if x.name.startswith("Caracoles Talliani"))
    assert p.price == 1000 and p.list_price == 1280
    assert p.discount_pct == pytest.approx(21.9, abs=0.1)
    mxn = [pr for pr in p.promos if pr.kind == "mxn"]
    assert mxn and mxn[0].min_qty == 2 and mxn[0].unit_price == 970


def test_sisa_card_promo_separated(sisa):
    p = next(x for x in sisa.products if x.name.startswith("Atún Lomitos en Agua"))
    card = [pr for pr in p.promos if pr.card_only]
    assert card, "la promo con Tarjeta Cencosud debe quedar marcada como card_only"
    assert p.price == 1280  # el precio público no se mezcla con el de tarjeta


def test_sisa_pdp_ean():
    assert parse_ean_from_pdp(load("sisa_pdp.html.gz")) == "7804608221644"


def test_sisa_page_url():
    s = src("santaisabel", "https://www.santaisabel.cl/despensa?orderBy=OrderByPriceASC")
    sc = SCRAPERS["santaisabel"]
    assert sc.page_url(s, 1) == "https://www.santaisabel.cl/despensa?orderBy=OrderByPriceASC"
    assert sc.page_url(s, 3).endswith("orderBy=OrderByPriceASC&page=3")


# ---------------- Acuenta ----------------
@pytest.fixture(scope="module")
def acuenta():
    s = src("acuenta", "https://www.acuenta.cl/ca/despensa/05?sort=2")
    return SCRAPERS["acuenta"].parse_listing(load("acuenta_list.html.gz"), s)


def test_acuenta_50_unique_products_sorted(acuenta):
    assert len(acuenta.products) == 50
    assert len({p.sku for p in acuenta.products}) == 50
    prices = [p.price for p in acuenta.products]
    assert prices == sorted(prices), "sort=2 debe venir de menor a mayor"
    assert acuenta.total == 687


def test_acuenta_fields(acuenta):
    p = next(x for x in acuenta.products if x.sku == "937918")
    assert p.name == "Bicarbonato de Sodio 30 g Lider" and p.price == 170
    assert p.ean == "7802400914511"
    assert p.net_content == "30 g" and p.unit == "kg"
    assert p.unit_price == pytest.approx(170 / 0.03, rel=1e-3)
    assert p.url == "https://www.acuenta.cl/p/bicarbonato-de-sodio-30-g-lider-937918"
    assert p.in_stock


def test_acuenta_subunit():
    assert content_from_subunit("10GR", 3) == (30.0, "g")
    assert content_from_subunit("1LT", 1.5) == (1500.0, "ml")
    assert content_from_subunit("1UN", 6) == (6.0, "un")
    assert content_from_subunit("RARO", 2) is None
    assert content_from_subunit("10GR", None) is None


def test_acuenta_page_url():
    s = src("acuenta", "https://www.acuenta.cl/ca/despensa/05?sort=2")
    assert SCRAPERS["acuenta"].page_url(s, 2) == "https://www.acuenta.cl/ca/despensa/05?sort=2&currentPage=2"


# ---------------- Unimarc ----------------
@pytest.fixture(scope="module")
def unimarc():
    s = src("unimarc", "https://www.unimarc.cl/category/despensa?orderBy=OrderByPriceASC")
    return SCRAPERS["unimarc"].parse_listing(load("unimarc_list.html.gz"), s)


def test_unimarc_50_products(unimarc):
    assert len(unimarc.products) == 50
    assert unimarc.total == 1626


def test_unimarc_club_price_is_separate(unimarc):
    p = next(x for x in unimarc.products if x.ean == "7802410053514")
    assert p.card_price == 250       # precio Club Unimarc
    assert p.price == 360            # público
    assert p.list_price is None
    assert p.unit == "kg" and p.unit_price == 24000
    assert p.net_content == "15 g"
