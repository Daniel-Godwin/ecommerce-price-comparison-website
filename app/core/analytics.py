"""Price analytics (design doc FR-06): min, max, average, best deal.

Analytics are computed per-currency; mixing NGN and USD averages would
be meaningless. Phase 1 reports on the dominant currency of the result
set; multi-currency conversion is a documented future enhancement.
"""
from __future__ import annotations

from collections import Counter

from app.schemas.models import Listing, PriceAnalytics


def dominant_currency(listings: list[Listing]) -> str | None:
    if not listings:
        return None
    return Counter(x.currency for x in listings).most_common(1)[0][0]


def compute_analytics(listings: list[Listing]) -> PriceAnalytics | None:
    currency = dominant_currency(listings)
    if currency is None:
        return None
    pool = [x for x in listings if x.currency == currency]
    prices = [x.price for x in pool]
    best = min(pool, key=lambda x: x.price)
    return PriceAnalytics(
        count=len(pool),
        min_price=min(prices),
        max_price=max(prices),
        avg_price=round(sum(prices) / len(prices), 2),
        currency=currency,
        best_deal=best,
    )


def top_cheapest(listings: list[Listing], n: int = 3) -> list[Listing]:
    """Top-N cheapest within the dominant currency (FR-05)."""
    currency = dominant_currency(listings)
    pool = [x for x in listings if x.currency == currency]
    return sorted(pool, key=lambda x: x.price)[:n]
