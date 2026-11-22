"""🚨 Vigilancia de reposición, lectura de fichas y avisos por Telegram."""
import gzip
import json
from pathlib import Path

import pytest

from radar import watch
from radar.notifier import Telegram, link_code, render
from radar.pdp import parse_pdp
from radar.storage import Storage

FX = Path(__file__).parent / "fixtures"


def load(n):
    return gzip.open(FX / n, "rt", encoding="utf-8").read()


@pytest.mark.parametrize("store,fx,price,stock", [
    ("lider", "lider_pdp.html.gz", 1190, True),
    ("acuenta", "acuenta_pdp.html.gz", 170, True),
    ("unimarc", "unimarc_pdp.html.gz", 250, True),      # su JSON-LD dice OutOfStock: manda __NEXT_DATA__
    ("santaisabel", "sisa_pdp.html.gz", 370, True),
])
def test_pdp_price_and_stock(store, fx, price, stock):
    info = parse_pdp(store, load(fx))
    assert info and info.price == price and info.in_stock is stock


def row(key="lider:1", price=1090, list_price=None, in_stock=0, **kw):
    d = {"key": key, "store": "lider", "name": "Cerveza Berries 2.5° Pack 6 Un Lata", "price": price,
         "list_price": list_price, "in_stock": in_stock, "marketplace": 0, "url": "https://www.lider.cl/ip/x",
         "net_content": None}
    d.update(kw)
    return d


def test_watchlist_includes_out_of_stock_deals_only():
    st = Storage(":memory:")
    rows = [row("lider:1"), row("lider:2", in_stock=1), row("lider:3", price=700, list_price=1000),
            row("lider:4", price=900, list_price=1000), row("lider:5", marketplace=1)]
    w = watch.build_watchlist(st, rows, {"lider:1": "⚡ bajó 50%", "lider:2": "🔥 ganga"})
    assert set(w) == {"lider:1", "lider:3"}          # agotada+bajada, agotada con -30%
    assert w["lider:1"]["price"] == 1090


def test_restock_alert_only_at_good_price():
    st = Storage(":memory:")
    watch.build_watchlist(st, [row("lider:1"), row("lider:2")], {"lider:1": "x", "lider:2": "x"})
    assert watch.on_reading(st, "lider:1", "Cerveza", "lider", "u", 1090, True)        # mismo precio
    assert not watch.on_reading(st, "lider:2", "Cerveza", "lider", "u", 2990, True)    # volvió cara
    assert not watch.on_reading(st, "lider:9", "x", "lider", "u", 1, True)             # no vigilado
    alerts = st.db.execute("SELECT kind, payload FROM alerts").fetchall()
    assert [a["kind"] for a in alerts] == ["restock"]
    assert json.loads(alerts[0]["payload"])["price"] == 1090
    assert st.get_meta("watch") == {}                                                   # ambos salen de la lista


def test_restock_detected_from_node_ingest(tmp_path):
    from radar.models import Product
    from radar.nodesync import export_node, ingest
    cloud = Storage(":memory:")
    cloud.upsert(Product(store="alvi", sku="1", name="Pañales XG", price=9990, url="u", in_stock=False,
                         found_at="2026-11-22T10:00:00+00:00"))
    watch.build_watchlist(cloud, [dict(row("alvi:1", price=9990, store="alvi"))], {"alvi:1": "🔥 ganga"})
    node = Storage(":memory:")
    node.upsert(Product(store="alvi", sku="1", name="Pañales XG", price=9990, url="u", in_stock=True,
                        found_at="2026-11-22T11:00:00+00:00"))
    f = tmp_path / "n.json.gz"
    export_node(node, ["alvi"], f)
    ingest(cloud, f)
    assert cloud.db.execute("SELECT kind FROM alerts").fetchone()["kind"] == "restock"


class FakeTG(Telegram):
    def __init__(self, st, updates):
        super().__init__(st, token="x")
        self.updates, self.sent = updates, []

    def _call(self, method, **p):
        if method == "getUpdates":
            u, self.updates = self.updates, []
            return u
        self.sent.append((method, p))
        return {}


def test_telegram_link_requires_code_and_sends_alerts(monkeypatch):
    monkeypatch.setenv("DB_KEY", "clave-de-prueba")
    st = Storage(":memory:")
    intruso = {"update_id": 1, "message": {"chat": {"id": 666}, "text": "/start"}}
    dueño = {"update_id": 2, "message": {"chat": {"id": 42}, "text": f"/start {link_code().lower()}"}}
    tg = FakeTG(st, [intruso, dueño])
    tg.poll()
    assert st.get_meta("tg_chat") == 42
    assert any(p["chat_id"] == 666 and "privado" in p["text"] for _, p in tg.sent)
    st.db.execute("INSERT INTO alerts(ts, product_key, kind, payload) VALUES('t','k','restock',?)",
                  (json.dumps({"name": "Cerveza", "store": "lider", "url": "https://x", "price": 1090,
                               "was_price": 1090, "reason": "⚡"}),))
    assert tg.flush() == 1
    assert "Volvió el stock" in tg.sent[-1][1]["text"] and tg.sent[-1][1]["chat_id"] == 42
    assert tg.flush() == 0                                   # no se reenvía


def test_render_error_message_escapes_html():
    t = render("error", {"name": "<b>x</b>", "store": "jumbo", "url": "https://j", "price": 990, "why": "50%"})
    assert "&lt;b&gt;x&lt;/b&gt;" in t and "$990" in t
