"""Jumia Nigeria adapter — HTML scraping via BeautifulSoup.

Selectors are deliberately isolated in module-level constants so a
markup change is a one-file fix (design doc, risk register R-1).
"""
from __future__ import annotations

from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from app.adapters.base import BaseAdapter
from app.schemas.models import RawListing

BASE_URL = "https://www.jumia.com.ng"
SEARCH_URL = BASE_URL + "/catalog/?q={query}"

# --- selectors (update here if Jumia changes markup) -------------------
CARD_SELECTOR = "article.prd"
NAME_SELECTOR = "h3.name"
PRICE_SELECTOR = "div.prc"
LINK_SELECTOR = "a.core"
IMAGE_SELECTOR = "img"


class JumiaAdapter(BaseAdapter):
    key = "jumia"
    name = "Jumia"
    region = "NG"
    currency = "NGN"

    def _search(self, query: str) -> list[RawListing]:
        url = SEARCH_URL.format(query=quote_plus(query))
        resp = self._get(url)
        return self.parse(resp.text)

    def parse(self, html: str) -> list[RawListing]:
        """Parse a Jumia search-results page (separated for fixture tests)."""
        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        for card in soup.select(CARD_SELECTOR):
            name = card.select_one(NAME_SELECTOR)
            price = card.select_one(PRICE_SELECTOR)
            link = card.select_one(LINK_SELECTOR)
            if not (name and price and link and link.get("href")):
                continue
            img = card.select_one(IMAGE_SELECTOR)
            image_url = None
            if img is not None:
                image_url = img.get("data-src") or img.get("src")
            listings.append(
                RawListing(
                    title=name.get_text(strip=True),
                    price_text=price.get_text(strip=True),
                    url=urljoin(BASE_URL, link["href"]),
                    image_url=image_url,
                    retailer=self.name,
                    currency_hint=self.currency,
                )
            )
        return listings
