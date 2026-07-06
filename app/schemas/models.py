"""Canonical data models for the price comparison platform.

Every adapter returns RawListing objects; the normalizer converts them
into Listing objects (the canonical schema from the design doc, §6.3).
"""
from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class RawListing(BaseModel):
    """Unprocessed listing exactly as an adapter saw it."""

    title: str
    price_text: str                      # e.g. "₦ 148,500", "$1,299.99"
    url: str
    image_url: str | None = None
    retailer: str
    currency_hint: str | None = None  # adapter may know the currency


class Listing(BaseModel):
    """Canonical, normalized listing (design doc §6.3)."""

    product: str
    retailer: str
    price: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)  # ISO-4217
    url: str
    image_url: str | None = None
    in_stock: bool = True
    captured_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


class PriceAnalytics(BaseModel):
    """Aggregate statistics over a set of listings (FR-06)."""

    count: int
    min_price: float
    max_price: float
    avg_price: float
    currency: str
    best_deal: Listing


class SourceStatus(BaseModel):
    """Health outcome of one adapter for one search (NFR-03)."""

    retailer: str
    ok: bool
    listings_found: int = 0
    error: str | None = None
    elapsed_ms: int = 0


class SearchResult(BaseModel):
    """Full response of a search: listings + analytics + source health."""

    query: str
    listings: list[Listing]
    analytics: PriceAnalytics | None = None
    sources_status: list[SourceStatus]
    from_cache: bool = False
