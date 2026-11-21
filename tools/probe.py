"""Sonda de reconocimiento: prueba URLs y reporta estado, bloqueos y fuentes de datos.

Uso:
    PROBE_URLS="https://a.cl/robots.txt https://a.cl/" python tools/probe.py [carpeta_salida]

Las URLs llegan por variable de entorno (nunca interpoladas en el shell).
Cada URL puede llevar un Referer con la sintaxis  URL|REFERER .
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.6",
}

BLOCK_MARKERS = {
    "perimeterx": re.compile(r"px-captcha|perimeterx|_pxhd|Robot or human", re.I),
    "cloudflare": re.compile(r"Just a moment|cf-chl|challenge-platform|cf_chl_opt", re.I),
    "akamai": re.compile(r"Access Denied.*akamai|_abck|bm-verify", re.I | re.S),
    "captcha": re.compile(r"g-recaptcha|hcaptcha|captcha-delivery|datadome", re.I),
    "incapsula": re.compile(r"Incapsula|_Incapsula_Resource", re.I),
}


def safe_name(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", url)[:120]


def analyze(text: str) -> dict:
    info: dict = {}
    info["blocks"] = [k for k, rx in BLOCK_MARKERS.items() if rx.search(text[:200_000])]
    info["next_data"] = "__NEXT_DATA__" in text
    info["jsonld"] = len(re.findall(r"application/ld\+json", text))
    info["vtex"] = bool(re.search(r"vtex", text[:300_000], re.I))
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    info["title"] = (m.group(1).strip()[:80] if m else "")
    return info


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "probe_out")
    out.mkdir(parents=True, exist_ok=True)
    raw = os.environ.get("PROBE_URLS", "").strip()
    if not raw:
        default = Path(__file__).with_name("probe_urls.txt")
        raw = default.read_text(encoding="utf-8") if default.exists() else ""
    items = [u for u in raw.split() if u.startswith("http")]
    results = []
    with httpx.Client(http2=True, follow_redirects=True, timeout=25, headers=BASE_HEADERS) as c:
        for item in items:
            url, _, ref = item.partition("|")
            headers = {"Referer": ref} if ref else {}
            t0 = time.time()
            try:
                r = c.get(url, headers=headers)
                text = r.text
                res = {
                    "url": url,
                    "status": r.status_code,
                    "final": str(r.url),
                    "ctype": r.headers.get("content-type", ""),
                    "bytes": len(r.content),
                    "ms": int((time.time() - t0) * 1000),
                    "server": r.headers.get("server", ""),
                }
                res.update(analyze(text))
                (out / (safe_name(url) + ".txt")).write_text(text, encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                res = {"url": url, "error": f"{type(e).__name__}: {e}"[:200]}
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    # Cadena incompleta del servidor: completar vía AIA y reintentar (verificando).
                    try:
                        from urllib.parse import urlparse
                        from radar import tls as aia  # noqa: PLC0415
                        host = urlparse(url).hostname
                        ctx = aia.ssl_context_for(host)
                        with httpx.Client(verify=ctx, http2=True, follow_redirects=True,
                                          timeout=25, headers=BASE_HEADERS) as c2:
                            r = c2.get(url, headers=headers)
                        res = {"url": url, "status": r.status_code, "final": str(r.url),
                               "bytes": len(r.content), "tls": "aia-completed"}
                        res.update(analyze(r.text))
                        (out / (safe_name(url) + ".txt")).write_text(r.text, encoding="utf-8")
                    except Exception as e2:  # noqa: BLE001
                        res["aia_error"] = f"{type(e2).__name__}: {e2}"[:200]
            results.append(res)
            print(json.dumps(res, ensure_ascii=False))
            time.sleep(4)
    (out / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
