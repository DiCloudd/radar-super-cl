"""SQLite: products, prices (solo cuando cambia), runs, alerts, meta, errors, store_health."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Product, now_iso

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS products (
    key TEXT PRIMARY KEY, store TEXT NOT NULL, sku TEXT NOT NULL, name TEXT NOT NULL,
    brand TEXT, ean TEXT, url TEXT, image TEXT, category TEXT, net_content TEXT,
    price REAL, list_price REAL, card_price REAL, unit_price REAL, unit TEXT,
    in_stock INTEGER, marketplace INTEGER, promos TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_change TEXT
);
CREATE INDEX IF NOT EXISTS ix_products_ean ON products(ean);
CREATE INDEX IF NOT EXISTS ix_products_store_cat ON products(store, category);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_key TEXT NOT NULL, ts TEXT NOT NULL,
    price REAL, list_price REAL, card_price REAL, in_stock INTEGER
);
CREATE INDEX IF NOT EXISTS ix_prices_key_ts ON prices(product_key, ts);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, ended TEXT, runner TEXT,
    requests INTEGER, products INTEGER, changes INTEGER, blocked TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, product_key TEXT, kind TEXT,
    payload TEXT, sent INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_key TEXT, detected TEXT, verdict TEXT,
    price REAL, reference REAL, rule TEXT, ended TEXT, end_reason TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS source_stats (
    source TEXT PRIMARY KEY, last_ok TEXT, last_count INTEGER, avg_count REAL,
    last_error TEXT, blocked_until TEXT, block_streak INTEGER DEFAULT 0
);
"""


class Storage:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True) if str(path) != ":memory:" else None
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(products)")}
        if "alt_eans" not in cols:   # migración: EAN alternativos (Acuenta trae varios)
            self.db.execute("ALTER TABLE products ADD COLUMN alt_eans TEXT")

    def close(self) -> None:
        self.db.commit()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.close()

    # ---------- meta ----------
    def get_meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set_meta(self, k: str, v) -> None:
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                        (k, json.dumps(v, ensure_ascii=False)))

    # ---------- productos ----------
    def upsert(self, p: Product) -> tuple[bool, dict | None]:
        """Guarda el producto. Devuelve (cambió_precio, fila_anterior|None)."""
        ts = p.found_at or now_iso()
        prev = self.db.execute("SELECT * FROM products WHERE key=?", (p.key,)).fetchone()
        promos = json.dumps([pr.__dict__ for pr in p.promos], ensure_ascii=False) if p.promos else None
        prev_d = dict(prev) if prev else None
        changed = (prev is None or prev["price"] != p.price or prev["list_price"] != p.list_price
                   or prev["card_price"] != p.card_price or bool(prev["in_stock"]) != p.in_stock)
        ean = p.ean or (prev["ean"] if prev else None)   # el EAN no se pierde si el listado no lo trae
        self.db.execute(
            """INSERT INTO products(key,store,sku,name,brand,ean,url,image,category,net_content,price,
                 list_price,card_price,unit_price,unit,in_stock,marketplace,promos,first_seen,last_seen,last_change,
                 alt_eans)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET name=excluded.name, brand=excluded.brand, ean=excluded.ean,
                 alt_eans=COALESCE(excluded.alt_eans, products.alt_eans),
                 url=excluded.url, image=excluded.image, category=COALESCE(products.category, excluded.category),
                 net_content=COALESCE(excluded.net_content, products.net_content), price=excluded.price,
                 list_price=excluded.list_price, card_price=excluded.card_price, unit_price=excluded.unit_price,
                 unit=excluded.unit, in_stock=excluded.in_stock, marketplace=excluded.marketplace,
                 promos=excluded.promos, last_seen=excluded.last_seen,
                 last_change=CASE WHEN ? THEN excluded.last_seen ELSE products.last_change END""",
            (p.key, p.store, p.sku, p.name, p.brand, ean, p.url, p.image, p.category, p.net_content,
             p.price, p.list_price, p.card_price, p.unit_price, p.unit, int(p.in_stock), int(p.marketplace),
             promos, ts, ts, ts, ",".join(p.alt_eans) or None, int(changed)),
        )
        if changed:
            self.db.execute(
                "INSERT INTO prices(product_key,ts,price,list_price,card_price,in_stock) VALUES(?,?,?,?,?,?)",
                (p.key, ts, p.price, p.list_price, p.card_price, int(p.in_stock)))
        return changed and prev is not None, prev_d

    def set_ean(self, key: str, ean: str) -> None:
        self.db.execute("UPDATE products SET ean=? WHERE key=?", (ean, key))

    def missing_ean(self, store: str, limit: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT key,url FROM products WHERE store=? AND ean IS NULL ORDER BY first_seen LIMIT ?",
            (store, limit)).fetchall()

    def history(self, key: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM prices WHERE product_key=? ORDER BY ts, id", (key,)).fetchall()

    def count(self, store: str | None = None) -> int:
        if store:
            return self.db.execute("SELECT COUNT(*) FROM products WHERE store=?", (store,)).fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    # ---------- salud de fuentes ----------
    def source_ok(self, source: str, count: int) -> float | None:
        """Registra una lectura correcta; devuelve el promedio previo (para el vigilante de diseño)."""
        r = self.db.execute("SELECT avg_count FROM source_stats WHERE source=?", (source,)).fetchone()
        prev_avg = r["avg_count"] if r else None
        avg = count if prev_avg is None else round(prev_avg * 0.8 + count * 0.2, 2)
        self.db.execute(
            """INSERT INTO source_stats(source,last_ok,last_count,avg_count,block_streak) VALUES(?,?,?,?,0)
               ON CONFLICT(source) DO UPDATE SET last_ok=excluded.last_ok, last_count=excluded.last_count,
               avg_count=excluded.avg_count""", (source, now_iso(), count, avg))
        return prev_avg

    def source_error(self, source: str, err: str) -> None:
        self.db.execute(
            """INSERT INTO source_stats(source,last_error) VALUES(?,?)
               ON CONFLICT(source) DO UPDATE SET last_error=excluded.last_error""", (source, err[:300]))

    # ---------- runs ----------
    def start_run(self, runner: str) -> int:
        cur = self.db.execute("INSERT INTO runs(started,runner) VALUES(?,?)", (now_iso(), runner))
        return cur.lastrowid

    def end_run(self, run_id: int, **kw) -> None:
        kw["ended"] = now_iso()
        cols = ",".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*kw.values(), run_id))
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()
