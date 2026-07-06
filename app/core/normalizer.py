"""Normalizer (design doc §5.2.2, FR-04).

Converts RawListing → canonical Listing. Handles currency symbols
(₦, ₺, $, €, £), thousands separators (both 1,299.99 and 1.299,99
conventions), price ranges ("₦ 1,000 - ₦ 2,500" → lower bound), and
drops invalid records with a logged reason rather than crashing.
"""
from __future__ import annotations

import logging
import re

from app.schemas.models import Listing, RawListing

logger = logging.getLogger(__name__)

_CURRENCY_SYMBOLS = {
    "₦": "NGN",
    "NGN": "NGN",
    "₺": "TRY",
    "TL": "TRY",
    "$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
}

_NUMBER_RE = re.compile(r"[\d][\d.,\s]*")


def detect_currency(price_text: str, hint: str | None = None) -> str | None:
    """Infer ISO currency from symbols in the text, else fall back to hint."""
    for symbol, iso in _CURRENCY_SYMBOLS.items():
        if symbol in price_text:
            return iso
    return hint


def parse_price(price_text: str) -> float | None:
    """Extract a numeric price from arbitrary retailer text.

    Rules:
    - take the FIRST number found (ranges like "1,000 - 2,500" → 1000)
    - decide the decimal separator by which of '.'/',' appears last
      and whether the trailing group has 1–2 digits
    """
    match = _NUMBER_RE.search(price_text)
    if not match:
        return None
    token = match.group().strip().replace(" ", "").replace("\u00a0", "")

    has_dot, has_comma = "." in token, "," in token
    if has_dot and has_comma:
        # the later of the two is the decimal separator
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif has_comma:
        head, _, tail = token.rpartition(",")
        if len(tail) in (1, 2):          # "1299,99" → decimal comma
            token = head.replace(",", "") + "." + tail
        else:                            # "148,500" → thousands comma
            token = token.replace(",", "")
    elif has_dot:
        head, _, tail = token.rpartition(".")
        if len(tail) == 3 and head:      # "1.299" (EU thousands) → 1299
            token = token.replace(".", "")
        # else "999.99" → already fine

    try:
        value = float(token)
    except ValueError:
        return None
    return value if value > 0 else None


def normalize(raw: RawListing) -> Listing | None:
    """Convert one RawListing to a canonical Listing, or None if invalid."""
    price = parse_price(raw.price_text)
    if price is None:
        logger.info("dropped listing (unparseable price %r) from %s",
                    raw.price_text, raw.retailer)
        return None
    currency = detect_currency(raw.price_text, raw.currency_hint)
    if currency is None:
        logger.info("dropped listing (unknown currency %r) from %s",
                    raw.price_text, raw.retailer)
        return None
    title = re.sub(r"\s+", " ", raw.title).strip()
    if not title:
        return None
    return Listing(
        product=title,
        retailer=raw.retailer,
        price=price,
        currency=currency,
        url=raw.url,
        image_url=raw.image_url,
    )


def normalize_all(raws: list[RawListing]) -> list[Listing]:
    out = []
    for raw in raws:
        listing = normalize(raw)
        if listing is not None:
            out.append(listing)
    return out
