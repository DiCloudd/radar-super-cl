from .acuenta import AcuentaScraper
from .alvi import AlviScraper
from .base import PageResult, Scraper
from .jumbo import JumboScraper
from .lider import LiderScraper
from .santaisabel import SantaIsabelScraper
from .shopify import ShopifyScraper
from .tottus import TottusScraper
from .unimarc import UnimarcScraper

SCRAPERS: dict[str, Scraper] = {
    s.store: s for s in (SantaIsabelScraper(), AcuentaScraper(), UnimarcScraper(), LiderScraper(),
                              JumboScraper(), AlviScraper(), TottusScraper(),
                              ShopifyScraper("super9", "https://super9.cl"))
}

__all__ = ["SCRAPERS", "PageResult", "Scraper"]

# Tienda de referencia por cadena (config.LOCATIONS): sus cookies se envían en cada lectura
from ..config import LOCATIONS as _LOC  # noqa: E402

for _s, _sc in SCRAPERS.items():
    if _LOC.get(_s, {}).get("cookies"):
        _sc.cookies = dict(_LOC[_s]["cookies"])
