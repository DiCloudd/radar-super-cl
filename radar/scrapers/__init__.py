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
