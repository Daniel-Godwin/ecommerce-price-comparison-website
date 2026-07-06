"""Demo adapter — deterministic sample data, no network.

Purpose (design doc §11): the platform must be demonstrable and testable
even when live retailers block automated traffic or change markup. This
adapter fabricates realistic listings derived from the query so the UI,
orchestrator, normalizer, analytics and cache can all be exercised
end-to-end offline. It is clearly labeled in the UI.
"""
from __future__ import annotations

import hashlib

from app.adapters.base import BaseAdapter
from app.schemas.models import RawListing

_VARIANTS = [
    ("{q} — 64GB", 0.92),
    ("{q} 128GB (2026 model)", 1.00),
    ("{q} Pro Edition", 1.35),
    ("Refurbished {q}", 0.71),
    ("{q} with free case", 1.04),
]


class DemoStoreAdapter(BaseAdapter):
    key = "demostore"
    name = "DemoStore (sample data)"
    region = "NG"
    currency = "NGN"

    def _search(self, query: str) -> list[RawListing]:
        # deterministic base price from query hash → stable demos & tests
        digest = hashlib.sha256(query.lower().encode()).hexdigest()
        base = 50_000 + int(digest[:6], 16) % 250_000
        listings = []
        for i, (tpl, factor) in enumerate(_VARIANTS):
            price = round(base * factor, -2)  # round to nearest ₦100
            listings.append(
                RawListing(
                    title=tpl.format(q=query.title()),
                    price_text=f"₦ {price:,.0f}",
                    url=f"https://example.com/demostore/{digest[:8]}/{i}",
                    image_url=None,
                    retailer=self.name,
                    currency_hint=self.currency,
                )
            )
        return listings
