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


def test_telegram_loop_runs_in_db_thread_and_never_crashes_window(monkeypatch):
    # Regresión: tg.poll se llamaba en otro hilo -> sqlite3.ProgrammingError -> ventana caída
    import asyncio
    import time as _t

    import radar.notifier as nt
    from radar.monitor import Monitor
    st = Storage(":memory:")
    calls = []

    class T(nt.Telegram):
        def __init__(self, s):
            super().__init__(s, token="x")

        def _call(self, method, **p):
            calls.append(method)
            if method == "getUpdates":
                self.st.get_meta("tg_offset", 0)    # usa la BD: falla si no es el mismo hilo
                return []
            raise RuntimeError("red caída")

    monkeypatch.setattr(nt, "Telegram", T)
    m = Monitor(st, [], runner="test")
    asyncio.run(m._safe(m.telegram_loop(_t.monotonic() + 1, every=60), "telegram"))
    assert "getUpdates" in calls


def test_safe_wrapper_swallows_errors():
    import asyncio
    from radar.monitor import Monitor

    async def boom():
        raise ValueError("x")
    asyncio.run(Monitor._safe(boom(), "prueba"))   # no lanza


def test_first_publish_seeds_silently_then_alerts_new(tmp_path, monkeypatch):
    # Regresión: errores/avisos/vigilancia se perdían (se exportaba después de cifrar la BD). Al
    # arreglarlo, la primera vez no debe llegar una ráfaga de avisos viejos.
    from radar.analysis import Finding
    from radar.publisher import _record_errors
    st = Storage(":memory:")
    by_key = {"a:1": {"name": "x", "store": "jumbo", "url": "u", "image": None, "in_stock": 1, "price": 1}}
    _record_errors(st, [Finding("a:1", "godzilla", 1, 2, "b", 50)], by_key)
    assert st.db.execute("SELECT sent FROM alerts").fetchone()["sent"] == 2       # silencioso
    by_key["a:2"] = dict(by_key["a:1"])
    _record_errors(st, [Finding("a:1", "godzilla", 1, 2, "b", 50), Finding("a:2", "error", 1, 3, "b", 60)], by_key)
    rows = st.db.execute("SELECT product_key, sent FROM alerts ORDER BY id").fetchall()
    assert [(r["product_key"], r["sent"]) for r in rows] == [("a:1", 2), ("a:2", 0)]   # solo el nuevo avisa


def test_unimarc_weighted_meat_marked_variable():
    from radar.scrapers.unimarc import _WEIGHTED
    assert _WEIGHTED.search("Plateada vacuno Wagyu Beef al vacío 1.6 Kg")
    assert _WEIGHTED.search("Trutro ala de pollo Super Pollo granel 500 g")
    assert not _WEIGHTED.search("Arroz grado 1 Tucapel 1 kg")


def test_only_imperdibles_go_to_telegram():
    from radar.analysis import Finding
    from radar.publisher import imperdibles
    by_key = {"j:1": {"store": "jumbo", "price": 8490, "marketplace": 0, "in_stock": 1},
              "l:1": {"store": "lider", "price": 8990, "marketplace": 0, "in_stock": 1},     # promo en todas
              "j:2": {"store": "jumbo", "price": 2990, "marketplace": 0, "in_stock": 1},
              "l:2": {"store": "lider", "price": 5890, "marketplace": 0, "in_stock": 1}}     # solo Jumbo la tiene
    groups = {"j:1": "g1", "l:1": "g1", "j:2": "g2", "l:2": "g2"}
    members = {"g1": [by_key["j:1"] | {"key": "j:1"}, by_key["l:1"] | {"key": "l:1"}],
               "g2": [by_key["j:2"] | {"key": "j:2"}, by_key["l:2"] | {"key": "l:2"}]}
    f = [Finding("j:1", "godzilla", 8490, 14990, "", 43), Finding("j:2", "godzilla", 2990, 5890, "", 49),
         Finding("x:9", "error", 1, 2, "", 60)]
    by_key["x:9"] = {"store": "acuenta", "price": 1, "marketplace": 0, "in_stock": 1}
    assert imperdibles(f, by_key, groups, members) == {"j:2", "x:9"}


def test_restock_telegram_only_for_imperdible_reasons():
    assert watch.worth_telegram("📦 pack a precio de unidad")
    assert watch.worth_telegram("💥 error")
    assert watch.worth_telegram("🔥 ganga 52% imperdible")
    assert not watch.worth_telegram("⚡ bajó 40%")
    assert not watch.worth_telegram("-35%")
    assert not watch.worth_telegram("🦖 Godzilla")
