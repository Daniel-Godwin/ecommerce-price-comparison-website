"""FastAPI application factory (design doc §5.1, §7).

Run:  uvicorn app.main:app --reload
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.db.session import init_db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Intelligent E-Commerce Price Comparison API",
    version="2.0.0",
    description=(
        "Multi-retailer price aggregation with persistence, price history, "
        "and semantic product search. LLM/RAG endpoints arrive in Phase 3."
    ),
    lifespan=lifespan,
)

@app.get("/", include_in_schema=False)
def root():
    return {
        "service": "Intelligent E-Commerce Price Comparison API",
        "version": "2.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to the frontend origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- simple fixed-window rate limit (NFR-08); Redis-backed in production ----
_hits: dict[str, list[float]] = defaultdict(list)
_WINDOW = 60.0


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    limit = get_settings().rate_limit_per_min
    ip = request.client.host if request.client else "unknown"
    now = monotonic()
    _hits[ip] = [t for t in _hits[ip] if now - t < _WINDOW]
    if len(_hits[ip]) >= limit:
        return JSONResponse(status_code=429,
                            content={"error": {"code": 429,
                                               "message": "rate limit exceeded"}})
    _hits[ip].append(now)
    return await call_next(request)


app.include_router(router)
