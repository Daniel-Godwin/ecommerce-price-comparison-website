"""Database models (design doc §6.1).

Tables: retailers, products, listings, price_snapshots, searches, llm_calls.
Works with SQLite (dev default) and PostgreSQL (production) via DATABASE_URL.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Retailer(Base):
    __tablename__ = "retailers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    adapter_key: Mapped[str] = mapped_column(String(50), unique=True)
    region: Mapped[str] = mapped_column(String(10), default="GLOBAL")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(20), default="ok")

    listings: Mapped[list[ListingRow]] = relationship(back_populates="retailer")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_title: Mapped[str] = mapped_column(String(500), index=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    listings: Mapped[list[ListingRow]] = relationship(back_populates="product")


class ListingRow(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("retailer_id", "url", name="uq_listing_retailer_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    retailer_id: Mapped[int] = mapped_column(ForeignKey("retailers.id"), index=True)
    title_raw: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    currency: Mapped[str] = mapped_column(String(3))
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped[Product] = relationship(back_populates="listings")
    retailer: Mapped[Retailer] = relationship(back_populates="listings")
    snapshots: Mapped[list[PriceSnapshot]] = relationship(back_populates="listing")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    listing: Mapped[ListingRow] = relationship(back_populates="snapshots")


class SearchLog(Base):
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_text: Mapped[str] = mapped_column(String(500), index=True)
    intent_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    region: Mapped[str | None] = mapped_column(String(10), nullable=True)
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCall(Base):
    """Reserved for Phase 3 — cost observability (design doc NFR-12)."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
