"""Reglas de errores de precio: sensibilidad (errores sintéticos) y cero falsos positivos."""
from datetime import datetime, timedelta, timezone

from radar.analysis import analyze

NOW = datetime(2026, 11, 21, 20, 0, tzinfo=timezone.utc)


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def r(key, store, price, **kw):
    d = {"key": key, "store": store, "name": kw.pop("name", "Whisky Johnnie Walker Black Label 750 ml"),
         "price": price, "list_price": None, "in_stock": 1, "marketplace": 0}
    d.update(kw)
    return d


def kinds(findings, key):
    return [f.kind for f in findings if f.key == key]


GROUP = {"a:1": "g", "l:1": "g", "s:1": "g"}


def test_detects_relative_error_after_real_drop():
    rows = [r("a:1", "acuenta", 9990), r("l:1", "lider", 29990), r("s:1", "santaisabel", 30990)]
    hist = {"a:1": [(ts(200), 29990), (ts(1), 9990)]}
    assert kinds(analyze(rows, GROUP, hist, NOW), "a:1") == ["error"]


def test_always_cheap_is_ganga_not_error():
    # nunca bajó: es su precio habitual -> ganga, no error
    rows = [r("a:1", "acuenta", 9990), r("l:1", "lider", 29990), r("s:1", "santaisabel", 30990)]
    hist = {"a:1": [(ts(500), 9990)]}
    assert kinds(analyze(rows, GROUP, hist, NOW), "a:1") == ["ganga"]


def test_old_drop_is_not_error():
    rows = [r("a:1", "acuenta", 9990), r("l:1", "lider", 29990), r("s:1", "santaisabel", 30990)]
    hist = {"a:1": [(ts(500), 29990), (ts(100), 9990)]}  # bajó hace 4 días
    assert "error" not in kinds(analyze(rows, GROUP, hist, NOW), "a:1")


def test_single_reference_needs_stricter_ratio():
    g = {"a:1": "g", "l:1": "g"}
    rows = [r("a:1", "acuenta", 11000), r("l:1", "lider", 27000)]   # 40,7% de la referencia
    hist = {"a:1": [(ts(200), 27000), (ts(1), 11000)]}
    assert "error" not in kinds(analyze(rows, g, hist, NOW), "a:1")
    rows[0]["price"] = 9000                                          # 33% -> sí
    hist = {"a:1": [(ts(200), 27000), (ts(1), 9000)]}
    assert kinds(analyze(rows, g, hist, NOW), "a:1") == ["error"]


def test_layer_filters_block_errors():
    hist = {"a:1": [(ts(200), 29990), (ts(1), 9990)]}
    base = [r("l:1", "lider", 29990), r("s:1", "santaisabel", 30990)]
    for bad in (dict(in_stock=0), dict(marketplace=1), dict(name="Whisky Johnnie Walker mini 50 ml")):
        rows = [r("a:1", "acuenta", 9990, **bad)] + base
        assert analyze(rows, GROUP, hist, NOW) == [] or "a:1" not in [f.key for f in analyze(rows, GROUP, hist, NOW)]


def test_store_normal_price_already_low_is_not_error():
    rows = [r("a:1", "acuenta", 9990, list_price=12990), r("l:1", "lider", 29990), r("s:1", "santaisabel", 30990)]
    hist = {"a:1": [(ts(200), 29990), (ts(1), 9990)]}
    assert "error" not in kinds(analyze(rows, GROUP, hist, NOW), "a:1")


def test_godzilla_drop_24h():
    rows = [r("a:1", "acuenta", 5990)]
    hist = {"a:1": [(ts(300), 11990), (ts(3), 5990)]}
    assert kinds(analyze(rows, {}, hist, NOW), "a:1") == ["godzilla"]


def test_cheap_items_ignored():
    rows = [r("a:1", "acuenta", 200), r("l:1", "lider", 990), r("s:1", "santaisabel", 990)]
    hist = {"a:1": [(ts(200), 990), (ts(1), 200)]}
    assert analyze(rows, GROUP, hist, NOW) == []


def test_error_without_other_stores_needs_big_own_drop():
    rows = [r("a:1", "acuenta", 2990)]
    assert kinds(analyze(rows, {}, {"a:1": [(ts(100), 12990), (ts(1), 2990)]}, NOW), "a:1") == ["error"]
    rows = [r("a:1", "acuenta", 690)]
    assert "error" not in kinds(analyze(rows, {}, {"a:1": [(ts(100), 2490), (ts(1), 690)]}, NOW), "a:1")
