"""Search service (design doc §5.3 steps 4–6).

Wraps the Phase 1 orchestrator with Phase 2 concerns:
persist listings + snapshots, upsert product embeddings into FAISS,
attach semantic neighbors, and log the search.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.adapters import all_adapters
from app.core.orchestrator import search as run_search
from app.db import repository as repo
from app.db.models import Product
from app.schemas.api import SearchRequest, SearchResponse, SemanticHit
from app.vector.faiss_store import get_store

logger = logging.getLogger(__name__)

_ADAPTER_KEY_BY_NAME = {a.name: a.key for a in all_adapters()}


def execute_search(db: Session, req: SearchRequest) -> SearchResponse:
    started = time.monotonic()
    result = run_search(req.query, adapter_keys=req.retailers, use_cache=req.use_cache)

    listings = result.listings
    if req.max_price is not None:
        listings = [x for x in listings if x.price <= req.max_price]

    # persist + index (skip when served purely from cache — already stored)
    product_ids: list[int] = []
    if listings and not result.from_cache:
        product_ids = repo.persist_listings(db, listings, _ADAPTER_KEY_BY_NAME)
        db.flush()
        products = db.query(Product).filter(Product.id.in_(product_ids)).all()
        store = get_store()
        store.upsert([p.id for p in products], [p.canonical_title for p in products])
        store.save()

    # semantic neighbors from everything indexed so far (FR-09)
    similar: list[SemanticHit] = []
    store = get_store()
    if store.count:
        hits = store.search(req.query, k=8)
        hit_ids = [pid for pid, _ in hits]
        titles = {
            p.id: p.canonical_title
            for p in db.query(Product).filter(Product.id.in_(hit_ids)).all()
        }
        similar = [
            SemanticHit(product_id=pid, canonical_title=titles.get(pid, "?"),
                        similarity=round(score, 4))
            for pid, score in hits
            if pid in titles
        ]

    latency_ms = int((time.monotonic() - started) * 1000)
    repo.log_search(db, req.query, req.region, len(listings), latency_ms)

    # recompute analytics if max_price filtering changed the pool
    analytics = result.analytics
    if req.max_price is not None:
        from app.core.analytics import compute_analytics

        analytics = compute_analytics(listings)

    return SearchResponse(
        query=result.query,
        listings=listings,
        analytics=analytics,
        sources_status=result.sources_status,
        from_cache=result.from_cache,
        product_ids=product_ids,
        similar_products=similar,
        latency_ms=latency_ms,
    )
