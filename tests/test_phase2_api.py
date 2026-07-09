"""Phase 2 test suite: persistence, vector store, and API integration.

Uses an isolated temp SQLite DB + temp vector index per test session,
and only the DemoStore adapter — CI never touches live retailers.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient bound to a fresh temp DB and temp vector index."""
    tmp = tmp_path_factory.mktemp("phase2")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
    os.environ["VECTOR_INDEX_DIR"] = str(tmp / "vec")

    # re-import with the temp env applied
    import app.db.session as session_mod
    import app.vector.faiss_store as store_mod

    importlib.reload(session_mod)
    store_mod._store = None

    # rebind modules that captured the old session at import time
    import app.api.routes as routes_mod
    import app.core.search_service as svc_mod
    import app.main as main_mod

    importlib.reload(svc_mod)
    importlib.reload(routes_mod)
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as test_client:
        yield test_client


# --------------------------------------------------------------------- #
# health & retailers                                                     #
# --------------------------------------------------------------------- #
def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_retailers_listed(client):
    resp = client.get("/api/v1/retailers")
    assert resp.status_code == 200
    keys = {r["key"] for r in resp.json()}
    assert {"jumia", "konga", "demostore"} <= keys


# --------------------------------------------------------------------- #
# search → persistence → semantic index                                  #
# --------------------------------------------------------------------- #
def test_search_persists_and_indexes(client):
    resp = client.post(
        "/api/v1/search",
        json={"query": "hp laptop", "retailers": ["demostore"], "use_cache": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["listings"]) == 5
    assert body["analytics"]["count"] == 5
    assert body["product_ids"], "products should be persisted"
    assert body["latency_ms"] >= 0

    # health now reports indexed products
    health = client.get("/api/v1/health").json()
    assert health["indexed_products"] >= len(body["product_ids"])


def test_semantic_neighbors_returned(client):
    # second, related query should find neighbors from the first
    resp = client.post(
        "/api/v1/search",
        json={"query": "hp laptop 128gb", "retailers": ["demostore"], "use_cache": False},
    )
    body = resp.json()
    assert body["similar_products"], "vector index should return neighbors"
    top = body["similar_products"][0]
    assert "hp laptop" in top["canonical_title"]
    assert 0.0 < top["similarity"] <= 1.001


def test_max_price_filter_and_analytics_recompute(client):
    unfiltered = client.post(
        "/api/v1/search",
        json={"query": "rice cooker", "retailers": ["demostore"], "use_cache": False},
    ).json()
    cap = unfiltered["analytics"]["avg_price"]
    filtered = client.post(
        "/api/v1/search",
        json={"query": "rice cooker", "retailers": ["demostore"],
              "use_cache": False, "max_price": cap},
    ).json()
    assert all(x["price"] <= cap for x in filtered["listings"])
    assert filtered["analytics"]["max_price"] <= cap
    assert len(filtered["listings"]) < len(unfiltered["listings"])


# --------------------------------------------------------------------- #
# product detail & price history                                         #
# --------------------------------------------------------------------- #
def test_product_detail_and_history(client):
    search = client.post(
        "/api/v1/search",
        json={"query": "office chair", "retailers": ["demostore"], "use_cache": False},
    ).json()
    pid = search["product_ids"][0]

    detail = client.get(f"/api/v1/products/{pid}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["product_id"] == pid
    assert body["listings"] and body["listings"][0]["latest_price"] > 0

    history = client.get(f"/api/v1/products/{pid}/history?days=7")
    assert history.status_code == 200
    series = history.json()["series"]
    assert len(series) >= 1
    assert series[0]["price"] > 0


def test_repeat_search_appends_snapshots(client):
    """Same query twice (cache off) → each listing gains a second snapshot."""
    first = client.post(
        "/api/v1/search",
        json={"query": "office chair", "retailers": ["demostore"], "use_cache": False},
    ).json()
    pid = first["product_ids"][0]
    series = client.get(f"/api/v1/products/{pid}/history?days=7").json()["series"]
    assert len(series) >= 2, "price history should accumulate snapshots"


def test_product_not_found(client):
    assert client.get("/api/v1/products/999999").status_code == 404


# --------------------------------------------------------------------- #
# validation                                                             #
# --------------------------------------------------------------------- #
def test_search_validation_rejects_short_query(client):
    resp = client.post("/api/v1/search", json={"query": "x"})
    assert resp.status_code == 422


def test_search_box_parses_natural_language(client):
    """Regression (pre-launch): the website search box must understand
    'best phone under 150000' — extract terms + price cap — not search
    the literal sentence (which returns nothing)."""
    client.post("/api/v1/search",
                json={"query": "hp laptop", "retailers": ["demostore"], "use_cache": False})
    resp = client.post(
        "/api/v1/search",
        json={"query": "best hp laptop under 150000",
              "retailers": ["demostore"], "use_cache": False},
    )
    body = resp.json()
    assert body["listings"], "natural-language query should return listings"
    assert all(x["price"] <= 150000 for x in body["listings"])
