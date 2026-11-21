"""Jumbo (Cencosud): misma plataforma que Santa Isabel -> reutiliza su extractor.

robots.txt de Jumbo prohíbe ?map=, ?ps=, sc= y URLs con dos '?': nuestras URLs
(/categoria?orderBy=...&page=N) no usan ninguno.
"""
from .santaisabel import SantaIsabelScraper


class JumboScraper(SantaIsabelScraper):
    store = "jumbo"
    base_url = "https://www.jumbo.cl"
