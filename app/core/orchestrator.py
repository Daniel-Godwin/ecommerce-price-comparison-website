"""Search orchestrator (design doc §5.2, data flow §5.3).

Fans a query out to the selected adapters concurrently, normalizes the
results, computes analytics, and returns a SearchResult. Per-source
failures never fail the whole search (NFR-03) — they are reported in
``sources_status`` instead. Results are cached by (query, adapters).
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.adapters import get_adapters
from app.adapters.base import BaseAdapter
from app.config import get_settings
from app.core.analytics import compute_analytics
from app.core.cache import TTLCache
from app.core.normalizer import normalize_all
from app.schemas.models import SearchResult, SourceStatus

logger = logging.getLogger(__name__)

_cache = TTLCache(default_ttl=get_settings().cache_ttl_seconds)


def _run_adapter(adapter: BaseAdapter, query: str) -> tuple[SourceStatus, list]:
    started = time.monotonic()
    try:
        raws = adapter.search(query)
        elapsed = int((time.monotonic() - started) * 1000)
        return (
            SourceStatus(
                retailer=adapter.name, ok=True,
                listings_found=len(raws), elapsed_ms=elapsed,
            ),
            raws,
        )
    except Exception as exc:  # AdapterError or anything unexpected
        elapsed = int((time.monotonic() - started) * 1000)
        logger.warning("source %s failed: %s", adapter.key, exc)
        return (
            SourceStatus(
                retailer=adapter.name, ok=False,
                error=str(exc), elapsed_ms=elapsed,
            ),
            [],
        )


def search(query: str, adapter_keys: list[str] | None = None,
           use_cache: bool = True) -> SearchResult:
    query = query.strip()
    adapters = get_adapters(adapter_keys)
    cache_key = f"{query.lower()}|{','.join(sorted(a.key for a in adapters))}"

    if use_cache:
        hit = _cache.get(cache_key)
        if hit is not None:
            return hit.model_copy(update={"from_cache": True})

    settings = get_settings()
    statuses: list[SourceStatus] = []
    all_raws: list = []

    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        futures = {pool.submit(_run_adapter, a, query): a for a in adapters}
        for future in as_completed(futures):
            status, raws = future.result()
            statuses.append(status)
            all_raws.extend(raws)

    listings = sorted(normalize_all(all_raws), key=lambda x: x.price)
    result = SearchResult(
        query=query,
        listings=listings,
        analytics=compute_analytics(listings),
        sources_status=sorted(statuses, key=lambda s: s.retailer),
    )
    if use_cache and listings:
        _cache.set(cache_key, result)
    return result
