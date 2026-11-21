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
BUDGET_WARN_PER_MIN = 15     # avisar si un dominio supera esto
REQUEST_TIMEOUT_S = 30
MAX_RETRIES = 2

# Circuit breaker / backoff ante bloqueo
BLOCK_BACKOFF_BASE_S = 15 * 60      # 15 min, se duplica en cada bloqueo seguido
BLOCK_BACKOFF_MAX_S = 12 * 3600

# Ciclos
FAST_CYCLE_S = 120           # trampas ⚡ cada ~2 min
END_REPEAT_RATIO = 0.8       # fin de categoría si >=80% de productos ya vistos en el barrido

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


def load_sources(path: Path = SOURCES_FILE, runner: str | None = None) -> list[Source]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = data.get("store_defaults", {})
    out = []
    for raw in data.get("source", []):
        merged = {**defaults.get(raw["store"], {}), **raw}
        out.append(Source(**merged))
    if runner:
        out = [s for s in out if s.active_on(runner)]
    return out
