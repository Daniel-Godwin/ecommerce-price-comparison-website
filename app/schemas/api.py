"""API request/response models (design doc §7)."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.models import Listing, PriceAnalytics, SourceStatus


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    region: str | None = None
    retailers: list[str] | None = None      # adapter keys; None = all
    max_price: float | None = Field(default=None, gt=0)
    use_cache: bool = True


class SemanticHit(BaseModel):
    product_id: int
    canonical_title: str
    similarity: float


class SearchResponse(BaseModel):
    query: str
    listings: list[Listing]
    analytics: PriceAnalytics | None
    sources_status: list[SourceStatus]
    from_cache: bool
    product_ids: list[int] = []             # persisted product entities
    similar_products: list[SemanticHit] = []  # semantic neighbors (FR-09)
    latency_ms: int


class RetailerInfo(BaseModel):
    key: str
    name: str
    region: str
    currency: str
    degraded: bool


class HealthResponse(BaseModel):
    status: str
    db: str
    vector_index: str
    indexed_products: int


class AskRequest(BaseModel):
    question: str = Field(min_length=5, max_length=500)
    region: str | None = None
    live_topup: bool = True          # run a live search before retrieval


class CitationOut(BaseModel):
    n: int
    retailer: str
    title: str
    price: float
    currency: str
    url: str


class AskResponse(BaseModel):
    question: str
    answer: str
    grounded: bool
    citations: list[CitationOut]
    intent: dict
    listings_considered: int
    latency_ms: int


class CostReport(BaseModel):
    daily_budget_usd: float
    spent_last_24h_usd: float
    calls_last_24h: int
    by_purpose: dict[str, int]
