import asyncio
import gzip
from pathlib import Path

import pytest

from radar import config
from radar.config import Source, load_sources
from radar.fetchers import BlockedError, detect_block
from radar.models import Product
from radar.monitor import Monitor
from radar.storage import Storage

FX = Path(__file__).parent / "fixtures"


def prod(sku="1", price=1000.0, **kw):
    return Product(store="acuenta", sku=sku, name=f"P{sku}", price=price, url="u", **kw)


def test_prices_only_on_change():
    st = Storage(":memory:")
    assert st.upsert(prod(price=1000)) == (False, None)
    changed, prev = st.upsert(prod(price=1000))
    assert not changed and prev["price"] == 1000
    changed, _ = st.upsert(prod(price=800))
    assert changed
    assert [r["price"] for r in st.history("acuenta:1")] == [1000, 800]


def test_ean_is_kept_when_listing_lacks_it():
    st = Storage(":memory:")
    st.upsert(prod(ean="7802400914511"))
    st.upsert(prod(ean=None))
    assert st.db.execute("SELECT ean FROM products").fetchone()[0] == "7802400914511"


def test_detect_block_markers():
    assert detect_block(200, "<title>Just a moment...</title>") == "cloudflare"
    assert detect_block(403, "<HTML><HEAD><TITLE>Access Denied</TITLE>") == "akamai"
    assert detect_block(200, "<h1>Robot or human?</h1>") == "perimeterx"
    assert detect_block(429, "slow down") == "http429"
    assert detect_block(200, "<html>ok</html>") is None


def test_sources_toml_valid():
    srcs = load_sources()
    names = [s.name for s in srcs]
    assert len(names) == len(set(names))
    assert {s.store for s in srcs} == {"santaisabel", "acuenta", "unimarc", "lider", "jumbo", "alvi"}
    cloud = load_sources(runner="cloud")
    assert not [s for s in cloud if s.store in ("unimarc", "alvi")], "SMU no debe correr en la nube"


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeFetcher:
    """Devuelve la página fixture de Acuenta; en página >=3 repite la misma (fin por repetidos)."""

    def __init__(self, block=False):
        self.html = gzip.open(FX / "acuenta_list.html.gz", "rt", encoding="utf-8").read()
        self.block, self.calls = block, []

    async def get(self, url, **kw):
        self.calls.append(url)
        if self.block:
            raise BlockedError("www.acuenta.cl", "cloudflare", 403)
        return FakeResp(self.html)

    async def close(self):
        pass


SRC = Source(name="acuenta-despensa", store="acuenta", category="despensa",
             url="https://www.acuenta.cl/ca/despensa/05?sort=2", fast=True)


def test_crawler_detects_end_by_repeats():
    st = Storage(":memory:")
    m = Monitor(st, [SRC], runner="test", fetcher=FakeFetcher())
    asyncio.run(m.crawl_step(SRC))           # página 1
    assert st.get_meta("cursor:acuenta-despensa")["page"] == 2
    asyncio.run(m.crawl_step(SRC))           # página 2 = mismos productos -> fin del barrido
    cur = st.get_meta("cursor:acuenta-despensa")
    assert cur["page"] == 1 and cur["sweeps"] == 1
    assert st.count("acuenta") == 50


def test_block_pauses_store_with_backoff():
    st = Storage(":memory:")
    m = Monitor(st, [SRC], runner="test", fetcher=FakeFetcher(block=True))
    assert asyncio.run(m.read_page(SRC, 1, fast=True)) is None
    assert m.store_paused("acuenta")
    b1 = st.get_meta("block:acuenta")
    assert b1["streak"] == 1
    st.set_meta("block:acuenta", {**b1, "until": "2000-01-01T00:00:00+00:00"})
    asyncio.run(m.read_page(SRC, 1, fast=True))
    assert st.get_meta("block:acuenta")["streak"] == 2   # backoff se duplica


def test_paused_store_is_not_requested():
    st = Storage(":memory:")
    f = FakeFetcher()
    m = Monitor(st, [SRC], runner="test", fetcher=f)
    m.mark_blocked("acuenta", "test")
    assert asyncio.run(m.read_page(SRC, 1, fast=True)) is None
    assert f.calls == []


def test_change_detected_between_reads():
    st = Storage(":memory:")
    m = Monitor(st, [SRC], runner="test", fetcher=FakeFetcher())
    st.upsert(Product(store="acuenta", sku="937918", name="x", price=999, url="u"))
    asyncio.run(m.read_page(SRC, 1, fast=True))
    ch = [c for c in m.changes if c.product.sku == "937918"]
    assert ch and ch[0].prev["price"] == 999 and ch[0].product.price == 170


class Fake404(FakeFetcher):
    async def get(self, url, **kw):
        self.calls.append(url)
        return FakeResp("not found", 404) if "currentPage=2" in url else FakeResp(self.html)


def test_404_after_last_page_ends_sweep_with_cooldown():
    # Regresión: Santa Isabel responde 404 pasada la última página y el cursor quedaba pegado.
    st = Storage(":memory:")
    f = Fake404()
    m = Monitor(st, [SRC], runner="test", fetcher=f)
    asyncio.run(m.crawl_step(SRC))
    asyncio.run(m.crawl_step(SRC))
    cur = st.get_meta("cursor:acuenta-despensa")
    assert cur["page"] == 1 and cur["sweeps"] == 1
    assert m.in_cooldown(SRC), "tras un barrido completo la categoría espera antes de repetirse"
    assert not st.db.execute("SELECT last_error FROM source_stats WHERE last_error IS NOT NULL").fetchall()


def test_node_export_ingest_roundtrip(tmp_path):
    from radar.nodesync import export_node, ingest
    node = Storage(":memory:")
    node.upsert(Product(store="unimarc", sku="7861", name="Ajo", price=360, url="u", ean="7802410053514",
                        found_at="2026-11-21T10:00:00+00:00"))
    f = tmp_path / "unimarc.json.gz"
    assert export_node(node, ["unimarc"], f) == 1
    cloud = Storage(":memory:")
    assert ingest(cloud, f) == (1, 0)
    assert ingest(cloud, f) == (0, 0)          # idempotente
    node.upsert(Product(store="unimarc", sku="7861", name="Ajo", price=250, url="u",
                        found_at="2026-11-21T11:00:00+00:00"))
    export_node(node, ["unimarc"], f)
    assert ingest(cloud, f) == (1, 1)          # cambio de precio -> historial
    assert [r["price"] for r in cloud.history("unimarc:7861")] == [360, 250]


@pytest.mark.parametrize("val", [config.MIN_SPACING_S])
def test_spacing_is_courteous(val):
    assert val >= 4


def test_marketplace_prices_flagged_in_history():
    st = Storage(":memory:")
    st.upsert(prod(price=18990, marketplace=True))
    st.upsert(prod(price=1690))
    rows = [dict(r) for r in st.db.execute("SELECT price, marketplace FROM prices ORDER BY id")]
    assert rows == [{"price": 18990, "marketplace": 1}, {"price": 1690, "marketplace": 0}]
