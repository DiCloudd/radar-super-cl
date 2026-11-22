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


# ---------------- Lider ----------------
from radar.scrapers.lider import ean_from_item_id, valid_ean  # noqa: E402


@pytest.fixture(scope="module")
def lider():
    s = src("lider", "https://www.lider.cl/browse/bebidas-y-licores/bebidas-gaseosas/3294424_56657077?sort=price_low")
    return SCRAPERS["lider"].parse_listing(load("lider_list.html.gz"), s)


def test_lider_40_products_sorted(lider):
    assert len(lider.products) == 40
    prices = [p.price for p in lider.products]
    # la página trae un bloque destacado + el listado principal ordenado de menor a mayor
    run = best = 1
    for a, b in zip(prices, prices[1:]):
        run = run + 1 if b >= a else 1
        best = max(best, run)
    assert best >= 20
    assert lider.total and lider.total >= 200


def test_lider_ean_from_item_id():
    assert ean_from_item_id("00780161059199") == "7801610591994"
    assert valid_ean("7801610591994")
    assert ean_from_item_id("ABC") is None


def test_lider_fields(lider):
    p = lider.products[0]
    assert p.ean and valid_ean(p.ean)
    assert p.url.startswith("https://www.lider.cl/ip/") and "?" not in p.url
    assert all(not x.marketplace for x in lider.products[:5])


# ---------------- Jumbo (plataforma Santa Isabel) ----------------
def test_jumbo_reuses_cencosud_parser():
    s = src("jumbo", "https://www.jumbo.cl/despensa?orderBy=OrderByPriceASC")
    res = SCRAPERS["jumbo"].parse_listing(load("jumbo_list.html.gz"), s)
    assert len(res.products) == 40 and not res.warnings
    p = res.products[0]
    assert p.store == "jumbo" and p.url.startswith("https://www.jumbo.cl/") and p.url.endswith("/p")


# ---------------- Alvi (plataforma Unimarc) ----------------
def test_alvi_parser_with_wholesale_steps():
    s = src("alvi", "https://www.alvi.cl/category/abarrotes?orderBy=OrderByPriceASC")
    res = SCRAPERS["alvi"].parse_listing(load("alvi_list.html.gz"), s)
    assert len(res.products) == 50 and res.total == 950
    p = next(x for x in res.products if x.ean == "7807975012464")
    assert p.price == 890 and p.unit == "kg"
    steps = [x for x in p.promos if x.kind == "mxn"]
    assert [(x.min_qty, x.unit_price) for x in steps] == [(3, 850), (6, 810)]
    assert "nameComplete" not in p.name and p.name.startswith("Atún lomito")


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


def test_acuenta_weighted_products_use_final_price():
    # Regresión (captura real): Acuenta muestra "$3.390 por KG" y cobra $7.797 (2,3 kg)
    s = src("acuenta", "https://www.acuenta.cl/ca/carnes-y-pescados/03?sort=2", "carnes")
    res = SCRAPERS["acuenta"].parse_listing(load("acuenta_carnes.html.gz"), s)
    p = next(x for x in res.products if x.name == "Pollo Trutro Entero Envasado 900 g Ariztia")
    assert p.unit_price == 3490 and p.unit == "kg"
    assert p.price == round(3490 * 0.9)
    assert p.net_content == "≈0.9 kg"
    assert p.ean == "7804115002446"            # el EAN real primero, el interno (2...) después
    q = next(x for x in res.products if x.name == "Pollo Trutro Largo Congelado 800 g Seara")
    assert q.price == 3390 and not (q.net_content or "").startswith("≈")   # por unidad: sin cambios


# ---------------- Tottus (socio) ----------------
def test_tottus_parser_real_sample():
    html = (FX / "tottus_list.html").read_text(encoding="utf-8")
    s = src("tottus", "https://www.tottus.cl/tottus-cl/lista/CATG27074/Aseo-y-Limpieza?sortBy=derived.currentPrice%2Casc", "limpieza")
    res = SCRAPERS["tottus"].parse_listing(html, s)
    assert len(res.products) == 8 and res.total == 1158 and res.page_size == 48
    p = next(x for x in res.products if x.sku == "112737586")
    assert p.price == 402 and p.list_price == 670 and p.discount_pct == pytest.approx(40, abs=0.1)
    assert p.unit == "un" and p.unit_price == 8 and p.net_content == "50 UN"
    assert p.brand == "Elite" and not p.marketplace
    assert p.url.startswith("https://www.tottus.cl/tottus-cl/articulo/")
    q = next(x for x in res.products if x.sku == "117637238")
    assert q.price == 410 and q.list_price is None


def test_tottus_partner_header_only_with_token(monkeypatch):
    sc = SCRAPERS["tottus"]
    monkeypatch.delenv("TOTTUS_PARTNER_TOKEN", raising=False)
    assert sc.extra_headers() == {}
    monkeypatch.setenv("TOTTUS_PARTNER_TOKEN", "llave-de-prueba")
    assert sc.extra_headers() == {"X-Radar-Partner": "llave-de-prueba"}


# ---------------- Shopify (Super9) ----------------
def test_shopify_products_json():
    s = src("super9", "https://super9.cl/products.json", "todos")
    res = SCRAPERS["super9"].parse_listing((FX / "shopify_products.json").read_text(encoding="utf-8"), s)
    assert len(res.products) == 2                      # el de precio 0 se descarta
    a = res.products[0]
    assert a.price == 1690 and a.list_price == 1990    # "1690.00" -> 1690 (no 169000)
    assert a.ean == "7801420220138" and a.url == "https://super9.cl/products/arroz-tucapel-1kg"
    b = res.products[1]
    assert b.price == 1290 and b.name == "Bebida Coca-Cola 591 ml" and not b.in_stock
    assert SCRAPERS["super9"].page_url(s, 2) == "https://super9.cl/products.json?limit=250&page=2"


def test_shopify_empty_catalog():
    s = src("super9", "https://super9.cl/products.json", "todos")
    assert SCRAPERS["super9"].parse_listing('{"products":[]}', s).products == []
