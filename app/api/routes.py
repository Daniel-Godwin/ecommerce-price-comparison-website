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
