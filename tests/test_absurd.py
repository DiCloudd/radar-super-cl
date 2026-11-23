"""📦 Pack a precio de unidad. Caso real del usuario + falsos positivos hallados al calibrar con 13.160 productos."""
from radar.absurd import find_absurd, pack_size


def r(key, name, price, brand="Becker", store="lider", **kw):
    d = {"key": key, "store": store, "name": name, "brand": brand, "price": price, "marketplace": 0,
         "net_content": None}
    d.update(kw)
    return d


def test_user_case_six_beers_at_1090():
    res = find_absurd([r("l:p6", "Cerveza Berries 2.5° Pack 6 Un Lata", 1090),
                       r("l:s1", "Cerveza Berries 2.5° Lata 470 ml", 990)])
    assert len(res) == 1 and res[0].key == "l:p6" and res[0].pack == 6


def test_normal_pack_price_not_flagged():
    assert find_absurd([r("l:p6", "Cerveza Berries 2.5° Pack 6 Un Lata", 4990),
                        r("l:s1", "Cerveza Berries 2.5° Lata 470 ml", 990)]) == []


def test_counts_are_not_packs():
    # "200 un" de servilletas y "500 cc" son UN producto, no un pack de 200/500
    assert pack_size(r("x", "Servilletas Elite cocktail 200 un", 2050))[0] == 1
    assert pack_size(r("x", "Vino blanco Gato caja 500 cc", 1770))[0] == 1
    assert pack_size(r("x", "Cerveza Cristal 6 x 350 cc", 4990))[0] == 6
    assert pack_size(r("x", "Bebida Pack 4 latas", 2990))[0] == 4


def test_single_must_not_be_a_pack_itself():
    res = find_absurd([r("u:1", "Pañuelos Elite familiar pack 18 paquetes de 10 un", 4450, brand="Elite"),
                       r("u:2", "Pañuelos pack familiar 18 paquetes de 10 un", 3050, brand="Elite")])
    assert res == []


def test_different_brand_or_size_not_compared():
    assert find_absurd([r("l:p6", "Cerveza Lager Pack 6 Un Lata 350 ml", 1090, brand="Cristal"),
                        r("l:s1", "Cerveza Lager Lata 350 ml", 990, brand="Heineken")]) == []
    assert find_absurd([r("l:p6", "Cerveza Lager Pack 6 latas 350 ml", 2000, brand="Cristal"),
                        r("l:s1", "Cerveza Lager botella 1 L", 2990, brand="Cristal")]) == []
