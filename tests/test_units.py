import pytest

from radar.units import is_small_format, parse_content, unit_price


@pytest.mark.parametrize("text,amount,base,pack", [
    ("Bebida 1,5 L", 1500, "ml", 1),
    ("Agua 1500 ml", 1500, "ml", 1),
    ("Cerveza 6 x 350 cc", 2100, "ml", 6),
    ("Cerveza Pack 12 x 330 ml", 3960, "ml", 12),
    ("Arroz 900 g", 900, "g", 1),
    ("Azúcar Granulada 5 kg Acuenta", 5000, "g", 1),
    ("Pañales pack 12 un", 12, "un", 12),
    ("Huevos 12 unidades", 12, "un", 12),
    ("Jurel Natural Lata Drenado 300 g - Neto 425 g", 425, "g", 1),
])
def test_parse_content(text, amount, base, pack):
    c = parse_content(text)
    assert c is not None
    assert c.amount == pytest.approx(amount)
    assert c.base == base and c.pack == pack


def test_bulk_per_kilo():
    c = parse_content("Plátano x kg")
    assert c.bulk and unit_price(990, c) == (990, "kg")


def test_unknown_returns_none():
    assert parse_content("Set de regalo navideño") is None
    assert parse_content(None) is None


def test_unit_price():
    assert unit_price(1290, parse_content("1,5 L")) == (860.0, "l")
    assert unit_price(0, parse_content("1 kg")) is None


def test_small_format():
    assert is_small_format("Shampoo tamaño viaje 50 ml")
    assert not is_small_format("Shampoo 400 ml")
