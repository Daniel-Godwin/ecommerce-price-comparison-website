# Intelligent E-Commerce Price Comparison Platform

A price comparison platform that aggregates product prices across multiple online retailers, with an intelligence layer (LLM APIs + Retrieval-Augmented Generation) arriving in Phase 3 to power natural-language product search and grounded, cited comparisons.

> **Status: Phase 1 — Robust Aggregation Core** ✅
> Concurrent multi-retailer search · normalization · price analytics · caching · circuit breakers · Streamlit UI · tests + CI

## Why

Shoppers waste time navigating multiple retail websites to find the lowest price for the same product, and traditional comparison tools rely on rigid keyword matching. This platform searches many retailers concurrently, normalizes their listings into one canonical schema, and (from Phase 3) understands natural-language queries like *"a budget phone with a good camera under ₦150,000"* — answering with real, current prices, never hallucinated ones.

## Quick start

```bash
git clone https://github.com/Daniel-Godwin/ecommerce-price-comparison-website.git
cd ecommerce-price-comparison-website
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run frontend/streamlit_app.py
```

Then search for any product. The **DemoStore** adapter returns realistic sample data with no network access, so the full pipeline (search → normalize → analytics → UI) is demonstrable even offline or when live retailers block automated traffic.

Run the tests:

```bash
pytest -q
```

## Architecture (Phase 1 slice)

```
Streamlit UI  →  Orchestrator  →  [ Jumia | Konga | DemoStore ] adapters
                     │                (concurrent, per-source retry
                     │                 + circuit breaker)
                     ├→ Normalizer (₦/$/€/₺ parsing, canonical schema)
                     ├→ Analytics  (min / max / avg / best deal / top-3)
                     └→ TTL cache  (30 min, Redis-shaped interface)
```

Key resilience properties (from the design doc):

- **Partial results, never total failure** — a failing retailer is reported in `sources_status`; the others still return.
- **Circuit breaker per adapter** — 3 consecutive failures marks a source degraded for 5 minutes instead of slowing every search.
- **One file per retailer** — adding a retailer = one adapter subclass + one registry line; selectors are isolated constants.
- **Fixture-based adapter tests** — CI never hits live sites.

## Repository layout

```
app/
├── config.py            # env-driven settings (pydantic-settings)
├── adapters/            # base.py (interface) + jumia.py, konga.py, demo.py
├── core/                # orchestrator, normalizer, analytics, cache
└── schemas/             # canonical Pydantic models
frontend/streamlit_app.py
tests/                   # 25 unit/adapter/integration tests + fixtures
.github/workflows/ci.yml # ruff + pytest on every push
```

## Roadmap

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **1** | Aggregation core: adapters, normalizer, analytics, cache, Streamlit UI, tests, CI | ✅ this release |
| **2** | FastAPI REST API, PostgreSQL persistence + price history, Redis, embeddings + FAISS semantic index, Docker, deployment | 🔜 |
| **3** | LLM query understanding, `/ask` RAG endpoint with citations + groundedness checks, entity resolution, cost dashboard | 🔜 |

Full specification: see the *Software Design & Development Documentation* (v1.0) in the project docs.

## Ethics & compliance

Official retailer APIs are preferred over scraping wherever available. Scraping adapters respect polite rate limits, cache aggressively, deep-link back to the original retailer page, and process only public product listings — no personal data.

## Author

**Daniel Godwin** — MSc AI Engineering, Istanbul Okan University.
