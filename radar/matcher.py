"""Agrupa el MISMO producto entre cadenas.

Prioridad: 1) EAN (incluye EAN alternativos); 2) marca + contenido neto normalizado + pack
+ nombre muy parecido; 3) sin match, no se compara (mejor no comparar que comparar mal).

Reglas anti-error del match por nombre:
- misma marca (normalizada) y mismo contenido total y pack exactos;
- palabras "de variante" (light, zero, sin azúcar, sabores, tallas, colores…) deben coincidir;
- similitud de palabras significativas ≥ 0,6;
- nunca dos productos de la misma cadena en un grupo;
- no se mezclan granel (x kg) ni formatos muestra/mini/viaje.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from .units import is_small_format, parse_content

STOP = set("""
de del la las el los y con sin en para por al a x un una unid unidad unidades pack paquete
botella botellas desechable retornable lata latas bolsa bolsas caja cajas doypack frasco tarro
sachet sobre bandeja envase pote vidrio pet tetra tetrapak display formato familiar
original clasico clasica tradicional nuevo nueva oferta promo cl chile mas
congelado congelada congelados congeladas cong envasado envasada liquido liquida sabor pouch granulado
porcion porciones aprox
g gr grs gramos kg kgs kilo kilos ml cc lt lts l litro litros cm3 mg
""".split())

# Palabras que distinguen variantes: si difieren entre dos nombres, NO es el mismo producto.
VARIANT = set("""
light zero diet sin azucar endulzado descremada descremado semidescremada semidescremado entera entero
deslactosada deslactosado lactosa integral blanco blanca negro negra tinto rosado rose espumante
frutilla fresa chocolate vainilla manjar naranja limon pina mango durazno manzana pera uva frambuesa
cereza mora maracuya platano coco menta tropical berries citrus ciruela kiwi sandia melon guinda
original picante natural ahumado ajo merken pimienta queso tomate pollo carne vacuno cerdo pavo atun
rn p m g xg xxg xxxg talla etapa recien nacido rizos liso grasa seco sensible anticaspa hombre mujer
nino nina bebe kids adulto senior cachorro gato perro pequeno pequena mediano mediana grande raza
reserva gran premium syrah merlot carmenere cabernet sauvignon chardonnay malbec cuvee especial seleccion
selection estate barrica icono limited edicion
zerolacto lavanda eucaliptus eucalipto floral citrico citrus bosque aloe vera talco primavera marina
fresh pino canela flores manzanilla avena karite rosas jazmin sandalo
gas mineral saborizada
""".split())
# sin "gas" con y sin gas distintos: "con gas" vs "sin gas" se detecta por el par
_SIN = re.compile(r"\bsin\s+(azucar|gas|lactosa|sal|alcohol|gluten)\b")
_CON = re.compile(r"\bcon\s+(gas|sal|alcohol)\b")


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("’", "'").replace("&", " y ")
    return re.sub(r"[^a-z0-9%]+", " ", s.replace("'", "")).strip()


def brand_key(brand: str | None) -> str:
    b = norm(brand)
    b = re.sub(r"\b(s ?a|ltda|spa|chile|cl)\b", "", b)
    return re.sub(r"\s+", "", b)


def tokens(name: str, brand: str | None) -> set[str]:
    n = norm(name)
    # quitar solo medidas "250 g", "1 5 l", "6 x" (van en el contenido); otros números se conservan
    n = re.sub(r"\b\d+(?:\s\d+)?\s*(?:g|gr|grs|kg|kgs|mg|ml|cc|l|lt|lts|un|und|unid|u|x|cm3|m|mts|metros|hojas)\b",
               " ", n)
    bt = set(norm(brand).split())
    bk = brand_key(brand)   # "CocaCola" / "Coca-Cola" -> "cocacola": quita "coca" y "cola" del nombre
    return {t for t in n.split()
            if (t not in STOP and t not in bt and not (len(t) >= 3 and t in bk) and len(t) > 1) or t in VARIANT}


def variant_sig(name: str) -> frozenset[str]:
    n = norm(name)
    sig = {t for t in n.split() if t in VARIANT}
    sig |= {f"sin_{m}" for m in _SIN.findall(n)}
    sig |= {f"con_{m}" for m in _CON.findall(n)}
    # "sin azucar" no debe contar también "azucar" suelto
    if "sin_azucar" in sig:
        sig.discard("azucar")
    return frozenset(sig - {"sin", "con"})


@dataclass(frozen=True)
class NameKey:
    brand: str
    base: str
    amount: int
    pack: int


def usable_ean(e: str | None) -> bool:
    """EAN válido para comparar entre cadenas. Los prefijos 02/04 y 2xx son de uso interno
    (productos pesables, códigos de la propia tienda): el mismo código puede ser otro producto
    en otra cadena, o el mismo producto con otra base de precio (por kg vs por unidad)."""
    if not e or not e.isdigit() or len(e) < 8:
        return False
    e13 = e.zfill(13)
    return not (e13.startswith("2") or e13.startswith("02") or e13.startswith("04"))


def name_key(name: str, brand: str | None, net_content: str | None = None) -> NameKey | None:
    if net_content and net_content.startswith("≈"):
        return None   # peso variable: el precio depende del peso real, no se compara
    b = brand_key(brand)
    if not b or len(b) < 2 or is_small_format(name):
        return None
    cn = parse_content(net_content) if net_content else None
    cm = parse_content(name)
    if cn and cm and (cn.base != cm.base or abs(cn.amount - cm.amount) > 0.02 * max(cn.amount, cm.amount)):
        return None   # el contenido declarado no calza con el del nombre (ej. peso variable): no comparar
    c = cn or cm
    if not c or c.bulk:
        return None
    return NameKey(b, c.base, int(round(c.amount)), c.pack)


def similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def same_product(n1: str, b1: str | None, n2: str, b2: str | None) -> bool:
    if variant_sig(n1) != variant_sig(n2):
        return False
    a, b = tokens(n1, b1), tokens(n2, b2)
    # como máximo UNA palabra significativa distinta por lado (ej. "Limpieza Profunda" vs
    # "Salud Completa" son productos distintos aunque compartan marca y tamaño)
    if not (similarity(a, b) >= 0.6 and len(a - b) <= 1 and len(b - a) <= 1):
        return False
    if (a - b or b - a) and min(len(a), len(b)) < 3:
        return False  # nombre demasiado genérico ("Cerveza Lata Ale"): solo vale coincidencia exacta
    if a - b and b - a:
        # sustitución de una palabra: solo vale si es la MISMA palabra escrita distinto
        # (yoghurt/yogurt, xtra/extra). "Next" vs "Proteína" o "Suave" vs "Medio" = otro producto.
        x, y = next(iter(a - b)), next(iter(b - a))
        return SequenceMatcher(None, x, y).ratio() >= 0.75
    return True


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def group_products(rows: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """rows: dicts con key, store, name, brand, ean, alt_eans, net_content, marketplace.

    Devuelve (grupo_por_key, metodo_por_grupo) donde metodo ∈ {"ean", "nombre"}.
    Solo grupos con ≥2 cadenas distintas.
    """
    uf = _UF()
    by_ean: dict[str, str] = {}
    store_of = {r["key"]: r["store"] for r in rows}
    for r in rows:
        uf.find(r["key"])
        eans = {r.get("ean")} | set((r.get("alt_eans") or "").split(","))
        for e in (e for e in eans if usable_ean(e)):
            if e in by_ean:
                uf.union(by_ean[e], r["key"])
            else:
                by_ean[e] = r["key"]
    ean_groups = defaultdict(set)
    for r in rows:
        ean_groups[uf.find(r["key"])].add(r["key"])
    ean_matched = {k for g in ean_groups.values() if len({store_of[x] for x in g}) > 1 for k in g}
    root_stores: dict[str, set[str]] = defaultdict(set)
    for root, g in ean_groups.items():
        root_stores[root] = {store_of[x] for x in g}

    # --- por nombre: solo para productos que el EAN no cruzó ---
    buckets: dict[NameKey, list[dict]] = defaultdict(list)
    for r in rows:
        # los ya cruzados por EAN también entran: sirven de "ancla" para que un producto sin
        # EAN de otra cadena se una a su grupo (pares ancla-ancla se saltan más abajo)
        if r.get("marketplace"):
            continue
        k = name_key(r["name"], r.get("brand"), r.get("net_content"))
        if k:
            buckets[k].append(r)
    name_union: set[str] = set()
    root_rows: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        root_rows[uf.find(r["key"])].append(r)

    def compatible(ga: list[dict], gb: list[dict]) -> bool:
        """Sin encadenamientos: TODO par entre ambos grupos debe ser el mismo producto
        (salvo pares que el EAN ya confirmó)."""
        for x in ga:
            for y in gb:
                if x["key"] in ean_matched and y["key"] in ean_matched:
                    continue
                kx, ky = name_key(x["name"], x.get("brand"), x.get("net_content")), \
                    name_key(y["name"], y.get("brand"), y.get("net_content"))
                if kx is None or kx != ky or not same_product(x["name"], x.get("brand"), y["name"], y.get("brand")):
                    return False
        return True

    for lst in buckets.values():
        if len({r["store"] for r in lst}) < 2:
            continue
        for i, a in enumerate(lst):
            best = {}
            for b in lst[i + 1:]:
                if b["store"] == a["store"]:
                    continue
                if a["key"] in ean_matched and b["key"] in ean_matched:
                    continue  # el EAN ya decidió (mismo grupo o productos distintos)
                if usable_ean(a.get("ean")) and usable_ean(b.get("ean")) and a["ean"] != b["ean"] and \
                        not ({a["ean"]} & set((b.get("alt_eans") or "").split(","))) and \
                        not ({b["ean"]} & set((a.get("alt_eans") or "").split(","))):
                    continue  # ambos tienen EAN y son distintos: son productos distintos
                if same_product(a["name"], a.get("brand"), b["name"], b.get("brand")):
                    s = similarity(tokens(a["name"], a.get("brand")), tokens(b["name"], b.get("brand")))
                    if s > best.get(b["store"], (0, None))[0]:
                        best[b["store"]] = (s, b)
            for _, b in best.values():
                ra, rb = uf.find(a["key"]), uf.find(b["key"])
                if ra == rb or root_stores[ra] & root_stores[rb]:
                    continue  # uniría dos productos de la misma cadena: se descarta
                if not compatible(root_rows[ra], root_rows[rb]):
                    continue
                uf.union(a["key"], b["key"])
                nr = uf.find(a["key"])
                root_stores[nr] = root_stores[ra] | root_stores[rb]
                root_rows[nr] = root_rows[ra] + root_rows[rb]
                name_union |= {a["key"], b["key"]}

    groups: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        groups[uf.find(r["key"])].add(r["key"])
    out, method = {}, {}
    for root, keys in groups.items():
        if len({store_of[k] for k in keys}) < 2:
            continue
        gid = min(keys)
        for k in keys:
            out[k] = gid
        method[gid] = "nombre" if keys & name_union else "ean"
    return out, method
