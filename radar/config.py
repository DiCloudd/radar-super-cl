"""Configuración global. Los secretos solo llegan por variables de entorno (GitHub Secrets)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RADAR_DATA", ROOT / "data"))
DB_PATH = Path(os.environ.get("RADAR_DB", DATA_DIR / "radar.db"))
SOURCES_FILE = Path(os.environ.get("RADAR_SOURCES", ROOT / "sources.toml"))

# "cloud" en GitHub Actions; "chile" en el nodo opcional. GitHub sobrescribe RUNNER_NAME.
MONITOR_RUNNER = os.environ.get("MONITOR_RUNNER", "local")

# Cortesía por dominio
MIN_SPACING_S = 4.0          # espacio mínimo entre peticiones al mismo dominio
JITTER_S = 1.5               # jitter aleatorio extra
DOMAIN_SPACING_S = {"www.lider.cl": 8.0, "super9.cl": 30.0}   # dominios sensibles: más espacio entre peticiones
BUDGET_WARN_PER_MIN = 15     # avisar si un dominio supera esto
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2

# Circuit breaker / backoff ante bloqueo
BLOCK_BACKOFF_BASE_S = 15 * 60      # 15 min, se duplica en cada bloqueo seguido
BLOCK_BACKOFF_MAX_S = 12 * 3600

# Ciclos
FAST_CYCLE_S = 120           # trampas ⚡ cada ~2 min
SWEEP_COOLDOWN_S = 15 * 60   # pausa mínima entre barridos completos de una misma categoría
END_REPEAT_RATIO = 0.8      # fin de categoría si >=80% de productos ya vistos en el barrido

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class Source:
    name: str
    store: str
    category: str
    url: str
    max_pages: int = 50
    fast: bool = False
    fast_every: int = 1      # la trampa corre 1 de cada N ciclos rápidos
    fast_offset: int = 0
    enabled: bool = True
    runners: list[str] = field(default_factory=lambda: ["cloud", "chile", "local"])

    def active_on(self, runner: str) -> bool:
        return self.enabled and runner in self.runners


# Tienda de referencia por cadena (precios y stock dependen del local). "id" cambia -> el historial
# de esa cadena parte de cero (los precios de otro local no son comparables con los anteriores).
LOCATIONS = {
    "acuenta": {"id": "782", "label": "aCuenta Curicó Terminal (Maule)",
                "cookies": {"_IL-storeId": "782", "_IL-storeReference": "528"}},
    "santaisabel": {"id": "santaisabeln687", "label": "Santa Isabel Curicó Camilo Henríquez",
                    "cookies": {"seller": "santaisabeln687",
                                "preselected-shipping-method": "seller-1-sla-RetiroenTienda"}},
    "jumbo": {"id": "jumboclj592", "label": "Jumbo Curicó (Av. O'Higgins 201)",
              "cookies": {"seller": "jumboclj592", "preselected-shipping-method": "seller-1-sla-ClickalAuto"}},
    # Unimarc no tiene retiro online en Curicó: el más cercano del Maule es Talca (canal de venta 62)
    "unimarc": {"id": "sc62", "label": "Unimarc Talca (Maule; Curicó no tiene retiro online)",
                "cookies": {"co_sc": "62"}},
    # Lider: la tienda se fija con su cookie de ubicación (pendiente). Alvi: exige iniciar sesión.
}

# Tiendas que necesitan algo extra para activarse:
#  - en la nube: la llave de socio (GitHub Secret)
#  - en el nodo de Chile (tu PC): TOTTUS_BROWSER=1 -> se lee con Edge real (sin evasión)
REQUIRES_ENV = {"tottus": "TOTTUS_PARTNER_TOKEN"}
BROWSER_ENV = {"tottus": "TOTTUS_BROWSER"}


def store_enabled(store: str, runner: str) -> bool:
    if store not in REQUIRES_ENV:
        return True
    if os.environ.get(REQUIRES_ENV[store], "").strip():
        return True
    return runner in ("chile", "local") and os.environ.get(BROWSER_ENV.get(store, ""), "") == "1"


def load_sources(path: Path = SOURCES_FILE, runner: str | None = None) -> list[Source]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = data.get("store_defaults", {})
    out = []
    for raw in data.get("source", []):
        merged = {**defaults.get(raw["store"], {}), **raw}
        out.append(Source(**merged))
    if runner:
        out = [s for s in out if s.active_on(runner) and store_enabled(s.store, runner)]
    return out
