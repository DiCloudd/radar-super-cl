"""Comparación entre cadenas. Pares REALES observados; cada falso positivo encontrado queda aquí."""
import pytest

from radar.matcher import group_products, name_key, same_product


def row(key, store, name, brand, ean=None, net=None, alt=None):
    return {"key": key, "store": store, "name": name, "brand": brand, "ean": ean, "alt_eans": alt,
            "net_content": net, "marketplace": 0}


@pytest.mark.parametrize("a,b", [
    (("Sopa de Caracolitos (5 Porciones) Sobre 76 g Maggi", "Maggi"), ("Sopa Maggi caracolitos sobre 76 g", "Maggi")),
    (("Pasta Dental Pepsodent Xtra Whitening 130 g", "Pepsodent"), ("Pasta dental extra whitening 130 g Pepsodent", "Pepsodent")),
    (("Agua Botella Purificada sin Gas 500 ml Benedictino", "Benedictino"), ("Agua purificada Benedictino sin gas botella 500 ml", "Benedictino")),
    (("Cebolla Picada 250 g Lider", "Lider"), ("Cebolla Picada Congelada 250 g", "LIDER")),
    (("Compota Watt's pera pouch 90 g", "Watts"), ("Compota Watt's Pera 90 g", "Watt's")),
    (("Jabón Barra Elite Humectante 90 g", "Elite"), ("Jabón Barra Humectante 90 g Elite", "Elite")),
    (("Jabón Líquido Ballerina Yoghurt & Vainilla Coco 650 ml Ballerina", "Ballerina"),
     ("Jabón Líquido Ballerina Yogurt Vainilla y Coco Doypack 650 ml", "Ballerina")),
    (("Yerba Mate Argentina Supremo Serrana 500 g", "Serrana"), ("Yerba Mate Argentina Serrana 500 g Supremo", "Serrana")),
    (("Bebida Botella sabor Original 591 ml Coca-Cola", "CocaCola"), ("Bebida Coca-Cola Original 591 ml", "Coca-Cola")),
])
def test_same_product_real_pairs(a, b):
    assert name_key(a[0], a[1]) == name_key(b[0], b[1])
    assert same_product(a[0], a[1], b[0], b[1])


@pytest.mark.parametrize("a,b", [
    # regresiones: falsos positivos detectados en la auditoría con datos reales
    (("Pasta Dental Pepsodent Integral 18 Limpieza Profunda 75 ml", "Pepsodent"),
     ("Pasta dental Pepsodent integral 18 salud completa 75 ml", "Pepsodent")),
    (("Vino Cabernet Sauvignon Botella 750 mL", "Misiones De Rengo"),
     ("Vino Cabernet Sauvignon /Syrah Reserva 750 ml Misiones de Rengo", "Misiones De Rengo")),
    (("Alimento Húmedo Perro Adulto Sabor Pollo Pouch 100 g Pedigree", "Pedigree"),
     ("Alimento Húmedo Perro Adulto Pedigree Pequeño Pollo 100 g", "Pedigree")),
    (("Leche Cultivada Next Sabor Chirimoya Botella 1 L Soprole", "Soprole"),
     ("Leche Cultivada Soprole Proteína Chirimoya 1 L", "Soprole")),
    (("Cepillo de dientes twister Suave 2 Un Colgate", "Colgate"),
     ("Pack 2 un. Cepillo de Dientes Colgate Twister Medio", "Colgate")),
    (("Flan Soprole Zerolacto Vainilla 110 g", "Soprole"), ("Flan Sabor Vainilla Pote 110 g", "Soprole")),
    (("Cloro Gel Tradicional Doypack 650 ml Clorinda", "Clorinda"), ("Cloro Gel Clorinda Lavanda 650 ml", "Clorinda")),
    (("Vino Cabernet Sauvignon Botella 750 mL", "Misiones De Rengo"),
     ("Vino Misiones de Rengo Cuvée Cabernet Sauvignon 750 cc", "Misiones de Rengo")),
    (("Cerveza Lata Ale 470 ml", "Odissea"), ("Cerveza Odissea Pink Ale Lata 470 cc", "Odissea")),
    # variantes que nunca deben mezclarse
    (("Bebida Coca-Cola Original 591 ml", "Coca-Cola"), ("Bebida Coca-Cola Light 591 ml", "Coca-Cola")),
    (("Bebida Coca-Cola Sin Azúcar 591 ml", "Coca-Cola"), ("Bebida Coca-Cola Original 591 ml", "Coca-Cola")),
    (("Agua Mineral Con Gas 1,6 L", "Cachantun"), ("Agua Mineral Sin Gas 1,6 L", "Cachantun")),
    (("Leche Entera 1 L", "Colun"), ("Leche Descremada 1 L", "Colun")),
    (("Jugo Néctar Durazno 1,5 L", "Watts"), ("Jugo Néctar Naranja 1,5 L", "Watts")),
])
def test_not_same_product(a, b):
    k1, k2 = name_key(a[0], a[1]), name_key(b[0], b[1])
    assert k1 != k2 or not same_product(a[0], a[1], b[0], b[1])


def test_pack_vs_unit_never_grouped():
    assert name_key("Cerveza Lager Lata 350 ml", "Cristal") != name_key("Cerveza Lager 6 x 350 ml", "Cristal")


def test_variable_weight_not_compared():
    # el nombre dice 0,950 kg y el contenido declarado 1 kg: peso variable -> no comparar
    assert name_key("Pechuga de pollo entera Super Pollo 0.950 Kg", "Super Pollo", "1 kg") is None


def test_no_brand_no_name_match():
    assert name_key("Arroz grado 1 1 kg", None) is None


def test_group_products_ean_and_name():
    rows = [
        row("a:1", "acuenta", "Bebida Coca-Cola Original 591 ml", "Coca-Cola", ean="7801610591994"),
        row("l:1", "lider", "Bebida Coca-Cola Original Botella Desechable 591 ml", "Coca-Cola", ean="7801610591994"),
        row("s:1", "santaisabel", "Sopa Maggi caracolitos sobre 76 g", "Maggi"),
        row("u:1", "unimarc", "Sopa de Caracolitos (5 Porciones) Sobre 76 g Maggi", "Maggi", ean="7891000000000"),
        row("s:2", "santaisabel", "Bebida Coca-Cola Light 591 ml", "Coca-Cola"),
    ]
    g, method = group_products(rows)
    assert g["a:1"] == g["l:1"] and method[g["a:1"]] == "ean"
    assert g["s:1"] == g["u:1"] and method[g["s:1"]] == "nombre"
    assert "s:2" not in g


def test_product_without_ean_joins_ean_group_by_name():
    rows = [
        row("a:1", "acuenta", "Bebida Botella Sin Azúcar 591 ml Coca-Cola", "Coca-Cola", ean="7801610001196"),
        row("l:1", "lider", "Bebida Coca-Cola Sin Azúcar Botella Desechable 591 ml", "Coca-Cola", ean="7801610001196"),
        row("s:1", "santaisabel", "Bebida Coca-Cola Sin Azúcar 591 ml", "Coca-Cola"),
        row("s:2", "santaisabel", "Bebida Coca-Cola Original 591 ml", "Coca-Cola"),
    ]
    g, method = group_products(rows)
    assert g["s:1"] == g["a:1"] == g["l:1"]
    assert "s:2" not in g


def test_different_eans_never_joined_by_name():
    rows = [row("a:1", "acuenta", "Jalea Soprole Guinda 110 g", "Soprole", ean="7801111111111"),
            row("u:1", "unimarc", "Jalea Soprole guinda 110 g", "Soprole", ean="7802222222222")]
    g, _ = group_products(rows)
    assert not g


def test_no_chaining_through_a_middle_product():
    # A≈B y B≈C pero A≉C: no deben quedar los tres juntos
    rows = [row("l:1", "lider", "Flan Sabor Vainilla Pote 110 g", "Soprole"),
            row("s:1", "santaisabel", "Flan Soprole Vainilla 110 g", "Soprole"),
            row("j:1", "jumbo", "Flan Soprole Zerolacto Vainilla 110 g", "Soprole")]
    g, _ = group_products(rows)
    assert g.get("l:1") == g.get("s:1")
    assert g.get("j:1") != g.get("l:1")


def test_never_two_products_of_same_store():
    rows = [
        row("s:1", "santaisabel", "Jalea Soprole Guinda 110 g", "Soprole"),
        row("s:2", "santaisabel", "Jalea Soprole sabor guinda pote 110 g", "Soprole"),
        row("u:1", "unimarc", "Jalea Soprole guinda 110 g", "Soprole"),
    ]
    g, _ = group_products(rows)
    members = [k for k in g if g[k] == g.get("u:1")]
    assert len([k for k in members if k.startswith("s:")]) <= 1


def test_internal_store_eans_not_used_for_matching():
    from radar.matcher import usable_ean
    assert not usable_ean("2164920000002")      # pesable / código interno
    assert not usable_ean("0400006130211")
    assert usable_ean("7804115001395")
    rows = [row("a:1", "acuenta", "Pollo entero con Menudencia 1900 Kg Super pollo", "Super pollo", ean="2164920000002"),
            row("l:1", "lider", "Pollo entero con Menudencia 2,3 Kg", "Super pollo", ean="2164920000002")]
    g, _ = group_products(rows)
    assert not g


def test_variable_weight_has_no_name_key():
    assert name_key("Pollo Trutro Entero Envasado 900 g Ariztia", "Ariztia", "≈0.9 kg") is None


def test_variable_weight_not_grouped_even_with_same_ean():
    rows = [row("a:1", "acuenta", "Pollo Trutro Entero Envasado 900 g Ariztia", "Ariztia", ean="7804115002446", net="≈0.9 kg"),
            row("l:1", "lider", "Pollo Trutro Entero Envasado", "Ariztia", ean="7804115002446", net="≈0.7 kg")]
    g, _ = group_products(rows)
    assert not g
