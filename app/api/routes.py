"""API routes (design doc §7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.adapters import all_adapters
from app.core.search_service import execute_search
from app.db import repository as repo
from app.db.session import get_db
from app.schemas.api import (
    AskRequest,
    AskResponse,
    CitationOut,
    CostReport,
    HealthResponse,
    RetailerInfo,
    SearchRequest,
    SearchResponse,
)
from app.vector.faiss_store import get_store

router = APIRouter(prefix="/api/v1")


@router.post("/search", response_model=SearchResponse)
def search_endpoint(req: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    return execute_search(db, req)


@router.get("/products/{product_id}")
def product_detail(product_id: int, db: Session = Depends(get_db)) -> dict:
    detail = repo.get_product_detail(db, product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")
    return detail


@router.get("/products/{product_id}/history")
def product_history(
    product_id: int,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    if repo.get_product_detail(db, product_id) is None:
        raise HTTPException(status_code=404, detail="product not found")
    return {"product_id": product_id, "days": days,
            "series": repo.get_price_history(db, product_id, days)}


@router.get("/retailers", response_model=list[RetailerInfo])
def retailers() -> list[RetailerInfo]:
    return [
        RetailerInfo(key=a.key, name=a.name, region=a.region,
                     currency=a.currency, degraded=a.is_degraded)
        for a in all_adapters()
    ]


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    store = get_store()
    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        db=db_status,
        vector_index="ok",
        indexed_products=store.count,
    )


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    """RAG-grounded conversational answer with citations (FR-11)."""
    import time as _time

    from app.llm.rag import ask as rag_ask

    started = _time.monotonic()
    result = rag_ask(db, req.question, live_topup=req.live_topup)
    return AskResponse(
        question=req.question,
        answer=result.answer,
        grounded=result.grounded,
        citations=[CitationOut(**c.model_dump()) for c in result.citations],
        intent=result.intent.model_dump(),
        listings_considered=result.listings_considered,
        latency_ms=int((_time.monotonic() - started) * 1000),
    )


@router.get("/llm/costs", response_model=CostReport)
def llm_costs(db: Session = Depends(get_db)) -> CostReport:
    """LLM spend observability (design doc NFR-12)."""
    import os
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from app.db.models import LLMCall

    since = datetime.now(UTC) - timedelta(days=1)
    rows = db.execute(
        select(LLMCall.purpose, sa_func.count(), sa_func.sum(LLMCall.cost_usd))
        .where(LLMCall.created_at >= since)
        .group_by(LLMCall.purpose)
    ).all()
    return CostReport(
        daily_budget_usd=float(os.getenv("LLM_DAILY_BUDGET_USD", "2.00")),
        spent_last_24h_usd=round(sum(float(r[2] or 0) for r in rows), 6),
        calls_last_24h=sum(int(r[1]) for r in rows),
        by_purpose={r[0]: int(r[1]) for r in rows},
    )


@router.get("/products/recent/list")
def recent_products(limit: int = Query(default=12, ge=1, le=50),
                    db: Session = Depends(get_db)) -> list[dict]:
    """Most recently tracked products with their latest price (ticker feed)."""
    from sqlalchemy import select

    from app.adapters import _demo_enabled
    from app.db.models import Product

    rows = db.execute(
        select(Product).order_by(Product.id.desc()).limit(limit)
    ).scalars().all()
    hide_demo = not _demo_enabled()
    out = []
    for product in rows:
        detail = repo.get_product_detail(db, product.id)
        listings = [x for x in (detail or {}).get("listings", [])
                    if x.get("latest_price")
                    and not (hide_demo and x["retailer"].startswith("DemoStore"))]
        if not listings:
            continue
        cheapest = min(listings, key=lambda x: x["latest_price"])
        out.append({
            "product_id": product.id,
            "title": cheapest["title"][:60],
            "price": cheapest["latest_price"],
            "currency": cheapest["currency"],
            "retailer": cheapest["retailer"],
        })
    return out
