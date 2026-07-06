"""Persistence layer (design doc FR-08).

Saves normalized listings + price snapshots after every search, and
answers product/history queries. Product entity resolution in Phase 2
is a normalized-title match; LLM-assisted merging arrives in Phase 3
(FR-12).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ListingRow, PriceSnapshot, Product, Retailer, SearchLog
from app.schemas.models import Listing


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def get_or_create_retailer(db: Session, name: str, adapter_key: str,
                           region: str = "GLOBAL") -> Retailer:
    retailer = db.scalar(select(Retailer).where(Retailer.adapter_key == adapter_key))
    if retailer is None:
        retailer = Retailer(name=name, adapter_key=adapter_key, region=region)
        db.add(retailer)
        db.flush()
    return retailer


def get_or_create_product(db: Session, title: str) -> Product:
    normalized = _norm_title(title)
    product = db.scalar(
        select(Product).where(func.lower(Product.canonical_title) == normalized)
    )
    if product is None:
        product = Product(canonical_title=normalized or title[:500])
        db.add(product)
        db.flush()
    return product


def persist_listings(db: Session, listings: list[Listing],
                     adapter_key_by_name: dict[str, str]) -> list[int]:
    """Upsert listings and append a price snapshot for each. Returns product ids."""
    product_ids: set[int] = set()
    now = datetime.now(UTC)
    for item in listings:
        retailer = get_or_create_retailer(
            db, item.retailer, adapter_key_by_name.get(item.retailer, item.retailer.lower())
        )
        product = get_or_create_product(db, item.product)
        product_ids.add(product.id)

        row = db.scalar(
            select(ListingRow).where(
                ListingRow.retailer_id == retailer.id, ListingRow.url == item.url
            )
        )
        if row is None:
            row = ListingRow(
                product_id=product.id,
                retailer_id=retailer.id,
                title_raw=item.product,
                url=item.url,
                image_url=item.image_url,
                currency=item.currency,
                in_stock=item.in_stock,
            )
            db.add(row)
            db.flush()
        else:
            row.last_seen = now
            row.in_stock = item.in_stock

        db.add(PriceSnapshot(listing_id=row.id, price=item.price, currency=item.currency))
    return sorted(product_ids)


def log_search(db: Session, query: str, region: str | None,
               results_count: int, latency_ms: int) -> None:
    db.add(SearchLog(query_text=query, region=region,
                     results_count=results_count, latency_ms=latency_ms))


def latest_price(db: Session, listing_id: int) -> PriceSnapshot | None:
    return db.scalar(
        select(PriceSnapshot)
        .where(PriceSnapshot.listing_id == listing_id)
        .order_by(PriceSnapshot.captured_at.desc())
        .limit(1)
    )


def get_product_detail(db: Session, product_id: int) -> dict | None:
    product = db.get(Product, product_id)
    if product is None:
        return None
    listings = []
    for row in product.listings:
        snap = latest_price(db, row.id)
        listings.append(
            {
                "listing_id": row.id,
                "retailer": row.retailer.name,
                "title": row.title_raw,
                "url": row.url,
                "image_url": row.image_url,
                "currency": row.currency,
                "in_stock": row.in_stock,
                "latest_price": snap.price if snap else None,
                "captured_at": snap.captured_at.isoformat() if snap else None,
            }
        )
    return {
        "product_id": product.id,
        "canonical_title": product.canonical_title,
        "listings": listings,
    }


def get_price_history(db: Session, product_id: int, days: int = 30) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(PriceSnapshot, ListingRow, Retailer)
        .join(ListingRow, PriceSnapshot.listing_id == ListingRow.id)
        .join(Retailer, ListingRow.retailer_id == Retailer.id)
        .where(ListingRow.product_id == product_id, PriceSnapshot.captured_at >= since)
        .order_by(PriceSnapshot.captured_at)
    ).all()
    return [
        {
            "captured_at": snap.captured_at.isoformat(),
            "price": snap.price,
            "currency": snap.currency,
            "retailer": retailer.name,
        }
        for snap, _listing, retailer in rows
    ]
