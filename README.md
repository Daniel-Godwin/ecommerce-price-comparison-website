# Intelligent E-Commerce Price Comparison Platform

A price comparison platform that aggregates product prices across multiple online retailers, with an intelligence layer (LLM APIs + Retrieval-Augmented Generation) arriving in Phase 3 to power natural-language product search and grounded, cited comparisons.

> **Status: Phase 3 — Intelligence Layer** ✅
> Everything from Phases 1–2, plus: LLM query understanding with a zero-cost fast path · `/ask` RAG endpoint with grounded, cited answers · citation verification against retrieved data · daily LLM budget guard with graceful fallback · cost observability endpoint

## Why

Shoppers waste time navigating multiple retail websites to find the lowest price for the same product, and traditional comparison tools rely on rigid keyword matching. This platform searches many retailers concurrently, normalizes their listings into one canonical schema, and (from Phase 3) understands natural-language queries like *"a budget phone with a good camera under ₦150,000"* — answering with real, current prices, never hallucinated ones.

## Quick start

```bash
git clone https://github.com/Daniel-Godwin/ecommerce-price-comparison-website.git
cd ecommerce-price-comparison-website
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Option A — REST API (Phase 2)
uvicorn app.main:app --reload          # OpenAPI docs at http://localhost:8000/docs

# Option B — Streamlit UI (Phase 1)
streamlit run frontend/streamlit_app.py

# Option C — full stack with PostgreSQL via Docker
docker compose up --build              # API :8000, Postgres, scheduler
```

Then search for any product. The **DemoStore** adapter returns realistic sample data with no network access, so the full pipeline (search → normalize → analytics → UI) is demonstrable even offline or when live retailers block automated traffic.

Run the tests:

```bash
pytest -q
```

## Architecture (Phase 2)

```
Streamlit UI ─┐
              ├→ FastAPI /api/v1: search · products · history · retailers · health
curl/clients ─┘        │  (rate limiting · validation · CORS)
                       ▼
                 Search service
                  ├→ Orchestrator → [ Jumia | Konga | DemoStore ] adapters
                  │     (concurrent, retries, circuit breaker, TTL cache)
                  ├→ Normalizer (₦/$/€/₺ parsing → canonical schema)
                  ├→ Persistence: products · listings · price_snapshots · searches
                  │     (SQLite dev / PostgreSQL prod, SQLAlchemy 2.0)
                  └→ FAISS vector index (pluggable embedder) → semantic neighbors

jobs/scheduler.py refreshes popular queries in the background.
```

### API at a glance

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/ask` | Natural-language question → RAG answer grounded in retrieved listings, with citations |
| `POST /api/v1/search` | Multi-retailer search: listings, analytics, persisted product ids, semantic neighbors |
| `GET /api/v1/products/{id}` | Product detail with latest price per retailer |
| `GET /api/v1/products/{id}/history?days=30` | Price snapshots over time |
| `GET /api/v1/retailers` | Supported sources + degraded status |
| `GET /api/v1/health` | DB + vector-index health |
| `GET /api/v1/llm/costs` | LLM spend in the last 24 h vs. the daily budget |

### Intelligence layer (Phase 3)

- **Intent parsing** (`app/llm/intent.py`): plain keyword queries take a free heuristic fast path; conversational queries ("best phone under ₦150k?") go to a small LLM. Budget exhaustion or provider failure degrades to the fast path — never a hard failure.
- **RAG `/ask`** (`app/llm/rag.py`): hybrid retrieval (FAISS + latest-price SQL + intent filters) → citation-numbered context → grounded generation → **citation verification** (every cited number must exist in the retrieved set; one regeneration, then honest plain-comparison fallback). Prices always come from the database — the LLM only narrates.
- **Providers** (`app/llm/client.py`): set `ANTHROPIC_API_KEY` to use Claude (Haiku for intent, Sonnet for answers). Without a key, a deterministic **offline stub** answers from retrieved data — the platform runs fully without any paid API. Every call is logged to `llm_calls`; `LLM_DAILY_BUDGET_USD` (default $2) caps daily spend.

**Embeddings** are pluggable: a dependency-free hashing embedder is the default (keeps torch out of CI); install `sentence-transformers` and restart to upgrade to true semantic embeddings — the index rebuilds itself automatically.

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
| **2** | FastAPI REST API, DB persistence + price history, embeddings + FAISS semantic index, scheduler, Docker | ✅ this release |
| **3** | LLM query understanding, `/ask` RAG endpoint with citations + groundedness verification, budget guard, cost endpoint | ✅ this release |

Full specification: see the *Software Design & Development Documentation* (v1.0) in the project docs.

## Quality gates

- `pytest -q` — 46 unit/adapter/integration tests
- `python -m scripts.eval_rag` — golden-set RAG evaluation: citation validity, price fidelity, budget respect, honesty on nonsense queries (pass threshold ≥ 95%; runs in CI on every push)

## Deployment

**Docker (any host):** `docker compose up --build` → API :8000 + PostgreSQL + scheduler.

**Render.com (free tier):** the included `render.yaml` is a one-click blueprint — New + → Blueprint → select this repo. It provisions the API (Docker) with a health check on `/api/v1/health` and a free PostgreSQL instance. Add `ANTHROPIC_API_KEY` in the dashboard to activate real LLM answers; without it the offline stub serves grounded answers at zero cost. Note: the free tier's disk is ephemeral, so the FAISS index rebuilds from the database on restart — fine at this scale.

## Ethics & compliance

Official retailer APIs are preferred over scraping wherever available. Scraping adapters respect polite rate limits, cache aggressively, deep-link back to the original retailer page, and process only public product listings — no personal data.

## Author

**Daniel Godwin** — MSc AI Engineering, Istanbul Okan University.
