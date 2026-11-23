"""Avisos por Telegram.

- El token del bot vive SOLO en el GitHub Secret TELEGRAM_TOKEN (nunca en código ni logs).
- Vinculación segura: el dueño envía al bot "/start CODIGO". El código se deriva de DB_KEY y solo
  se muestra dentro de la app (que está cifrada para tus dispositivos). Nadie más puede vincularse.
- El chat id se guarda en la BD cifrada (meta), no en archivos.
- Las alertas se encolan en la tabla `alerts` y se envían con reintentos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import httpx

from .models import now_iso
from .storage import Storage

log = logging.getLogger("radar.telegram")
API = "https://api.telegram.org/bot{token}/{method}"
STORE_NAMES = {"lider": "Lider", "jumbo": "Jumbo", "santaisabel": "Santa Isabel", "acuenta": "Acuenta",
               "unimarc": "Unimarc", "alvi": "Alvi", "tottus": "Tottus", "super9": "Super9"}


def link_code() -> str | None:
    k = os.environ.get("DB_KEY", "")
    return hashlib.sha256(("radar-telegram:" + k).encode()).hexdigest()[:6].upper() if k else None


def fmt(n: float) -> str:
    return "$" + f"{round(n):,}".replace(",", ".")


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(kind: str, p: dict) -> str:
    store = STORE_NAMES.get(p.get("store"), p.get("store", ""))
    if kind == "restock":
        return (f"🚨 <b>¡Volvió el stock!</b>\n{esc(p['name'])}\n<b>{fmt(p['price'])}</b> en {store}"
                f" (vigilado a {fmt(p['was_price'])}{' · ' + esc(p['reason']) if p.get('reason') else ''})\n"
                f"<a href=\"{esc(p['url'])}\">Ver en {store}</a>")
    if kind in ("error", "godzilla", "pack"):
        icon = {"error": "💥 <b>Posible error de precio</b>", "godzilla": "🦖 <b>Godzilla</b>",
                "pack": "📦 <b>Pack a precio de unidad</b>"}[kind]
        return (f"{icon}\n{esc(p['name'])}\n<b>{fmt(p['price'])}</b> en {store} · {esc(p.get('why', ''))}\n"
                f"<i>Revisa en la tienda antes de comprar: puede corregirse en minutos.</i>\n"
                f"<a href=\"{esc(p['url'])}\">Ver en {store}</a>")
    return esc(json.dumps(p, ensure_ascii=False))[:500]


class Telegram:
    def __init__(self, st: Storage, token: str | None = None):
        self.st = st
        self.token = token or os.environ.get("TELEGRAM_TOKEN", "").strip()
        self.client = httpx.Client(timeout=20)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _call(self, method: str, **params) -> dict:
        r = self.client.post(API.format(token=self.token, method=method), json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")   # sin token en el mensaje
        return data["result"]

    def poll(self) -> None:
        """Lee mensajes nuevos: vincula al dueño con '/start CODIGO'."""
        offset = self.st.get_meta("tg_offset", 0)
        try:
            updates = self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
        except Exception as e:  # noqa: BLE001
            log.warning("telegram: %s", e)
            return
        code = link_code()
        for u in updates:
            self.st.set_meta("tg_offset", u["update_id"] + 1)
            msg = u.get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            text = (msg.get("text") or "").strip()
            owner = self.st.get_meta("tg_chat")
            if text.startswith("/start"):
                parts = text.split()
                if len(parts) == 2 and code and parts[1].upper() == code and chat:
                    self.st.set_meta("tg_chat", chat)
                    self._send_text(chat, "✅ Radar vinculado. Te avisaré errores de precio y cuando vuelvan "
                                          "a tener stock las ofertas agotadas.")
                    log.info("telegram: dueño vinculado")
                elif chat != owner:
                    self._send_text(chat, "🔒 Bot privado.")
        self.st.commit()

    def _send_text(self, chat, text: str) -> None:
        self._call("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=False)

    def flush(self, limit: int = 20) -> int:
        chat = self.st.get_meta("tg_chat")
        if not chat:
            return 0
        sent = 0
        rows = self.st.db.execute("SELECT id, kind, payload FROM alerts WHERE sent=0 ORDER BY id LIMIT ?",
                                  (limit,)).fetchall()
        for r in rows:
            p = json.loads(r["payload"])
            text = render(r["kind"], p)
            try:
                if p.get("image"):
                    try:
                        self._call("sendPhoto", chat_id=chat, photo=p["image"], caption=text, parse_mode="HTML")
                    except Exception:  # noqa: BLE001 - la foto puede fallar: se envía solo texto
                        self._send_text(chat, text)
                else:
                    self._send_text(chat, text)
                self.st.db.execute("UPDATE alerts SET sent=1 WHERE id=?", (r["id"],))
                sent += 1
            except Exception as e:  # noqa: BLE001
                log.warning("telegram: no se pudo enviar (%s); se reintenta", e)
                break
        self.st.commit()
        return sent

    def status(self) -> dict:
        return {"enabled": self.enabled, "linked": bool(self.st.get_meta("tg_chat")), "code": link_code(),
                "checked": now_iso()}
