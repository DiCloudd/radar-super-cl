from .acuenta import AcuentaScraper
from .base import PageResult, Scraper
from .lider import LiderScraper
from .santaisabel import SantaIsabelScraper
from .unimarc import UnimarcScraper

SCRAPERS: dict[str, Scraper] = {
    s.store: s for s in (SantaIsabelScraper(), AcuentaScraper(), UnimarcScraper(), LiderScraper())
}

__all__ = ["SCRAPERS", "PageResult", "Scraper"]
