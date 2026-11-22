from .acuenta import AcuentaScraper
from .alvi import AlviScraper
from .base import PageResult, Scraper
from .jumbo import JumboScraper
from .lider import LiderScraper
from .santaisabel import SantaIsabelScraper
from .tottus import TottusScraper
from .unimarc import UnimarcScraper

SCRAPERS: dict[str, Scraper] = {
    s.store: s for s in (SantaIsabelScraper(), AcuentaScraper(), UnimarcScraper(), LiderScraper(),
                              JumboScraper(), AlviScraper(), TottusScraper())
}

__all__ = ["SCRAPERS", "PageResult", "Scraper"]
