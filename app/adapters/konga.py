"""Konga Nigeria adapter — HTML scraping via BeautifulSoup.

Konga is heavily JavaScript-rendered; the server-rendered fallback markup
is sparse, so this adapter parses whatever product cards are present and
degrades gracefully to zero results rather than erroring.
"""
from __future__ import annotations

from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from app.adapters.base import BaseAdapter
from app.schemas.models import RawListing

BASE_URL = "https://www.konga.com"
SEARCH_URL = BASE_URL + "/search?search={query}"

# --- selectors (update here if Konga changes markup) -------------------
CARD_SELECTOR = "li.bbLUj"          # product card list item
NAME_SELECTOR = "h3"
PRICE_SELECTOR = "span.d0011"       # price span
LINK_SELECTOR = "a"
IMAGE_SELECTOR = "img"


class KongaAdapter(BaseAdapter):
    key = "konga"
    name = "Konga"
    region = "NG"
    currency = "NGN"

    def _search(self, query: str) -> list[RawListing]:
        url = SEARCH_URL.format(query=quote_plus(query))
        resp = self._get(url)
        return self.parse(resp.text)

    def parse(self, html: str) -> list[RawListing]:
        soup = BeautifulSoup(html, "html.parser")
        listings: list[RawListing] = []
        for card in soup.select(CARD_SELECTOR):
            name = card.select_one(NAME_SELECTOR)
            price = card.select_one(PRICE_SELECTOR)
            link = card.select_one(LINK_SELECTOR)
            if not (name and price and link and link.get("href")):
                continue
            img = card.select_one(IMAGE_SELECTOR)
            listings.append(
                RawListing(
                    title=name.get_text(strip=True),
                    price_text=price.get_text(strip=True),
                    url=urljoin(BASE_URL, link["href"]),
                    image_url=img.get("src") if img is not None else None,
                    retailer=self.name,
                    currency_hint=self.currency,
                )
            )
        return listings
