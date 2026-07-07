"""Phase 3 test suite: intent parsing, budget guard, RAG groundedness, /ask.

Runs entirely on the offline StubLLM — no API key, no network, no cost.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("phase3")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/test.db"
    os.environ["VECTOR_INDEX_DIR"] = str(tmp / "vec")
    os.environ.pop("ANTHROPIC_API_KEY", None)          # force StubLLM
    os.environ["LLM_DAILY_BUDGET_USD"] = "2.00"

    import app.db.session as session_mod
    import app.llm.client as llm_mod
    import app.vector.faiss_store as store_mod

    importlib.reload(session_mod)
    store_mod._store = None
    llm_mod._client = None

    import app.api.routes as routes_mod
    import app.core.search_service as svc_mod
    import app.llm.intent as intent_mod
    import app.llm.rag as rag_mod
    import app.main as main_mod

    importlib.reload(intent_mod)
    importlib.reload(rag_mod)
    importlib.reload(svc_mod)
    importlib.reload(routes_mod)
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient

    with TestClient(main_mod.app) as test_client:
        yield test_client


# --------------------------------------------------------------------- #
# intent parsing                                                         #
# --------------------------------------------------------------------- #
def test_fast_path_for_plain_keywords(client):
    from app.llm.intent import parse_intent

    intent = parse_intent("samsung galaxy a15 128gb")
    assert intent.source == "fast_path"          # no LLM call for keywords
    assert "samsung galaxy a15" in intent.product_terms


def test_conversational_query_extracts_budget(client):
    from app.llm.intent import parse_intent

    intent = parse_intent("what is the best phone under ₦150,000?")
    assert intent.source in ("llm", "fallback")  # NL signals → LLM path (stub)
    assert intent.max_price == 150000.0
    assert "phone" in intent.product_terms
    assert "under" not in intent.product_terms


def test_k_shorthand_budget(client):
    from app.llm.intent import parse_intent

    intent = parse_intent("recommend a good laptop under 300k")
    assert intent.max_price == 300000.0


# --------------------------------------------------------------------- #
# budget guard                                                           #
# --------------------------------------------------------------------- #
def test_budget_guard_blocks_when_exceeded(client):
    from app.db.models import LLMCall
    from app.db.session import db_session
    from app.llm.client import BudgetExceeded, _check_budget

    with db_session() as db:
        db.add(LLMCall(purpose="test", model="x", prompt_tokens=1,
                       completion_tokens=1, cost_usd=99.0, latency_ms=1))
    with pytest.raises(BudgetExceeded):
        _check_budget()
    # cleanup so later tests aren't blocked
    with db_session() as db:
        db.query(LLMCall).filter(LLMCall.cost_usd == 99.0).delete()


def test_intent_falls_back_when_budget_exhausted(client, monkeypatch):
    """Budget exhaustion must degrade to fast path, never crash (FR-10)."""
    import app.llm.intent as intent_mod
    from app.llm.client import BudgetExceeded

    class BrokeLLM:
        def complete(self, *a, **k):
            raise BudgetExceeded("cap hit")

    monkeypatch.setattr(intent_mod, "get_llm", lambda: BrokeLLM())
    intent = intent_mod.parse_intent("what is the best phone under ₦150,000?")
    assert intent.source == "fallback"
    assert intent.max_price == 150000.0


# --------------------------------------------------------------------- #
# RAG /ask                                                               #
# --------------------------------------------------------------------- #
def _seed(client, query="hp laptop"):
    resp = client.post(
        "/api/v1/search",
        json={"query": query, "retailers": ["demostore"], "use_cache": False},
    )
    assert resp.status_code == 200


def test_ask_returns_grounded_cited_answer(client):
    _seed(client)
    resp = client.post(
        "/api/v1/ask",
        json={"question": "what is the cheapest hp laptop?", "live_topup": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["grounded"] is True
    assert body["citations"], "answer must carry citations"
    assert "[" in body["answer"] and "]" in body["answer"]
    # every citation number in the text exists in the citation list
    import re

    cited = {int(n) for n in re.findall(r"\[(\d+)\]", body["answer"])}
    valid = {c["n"] for c in body["citations"]}
    assert cited <= valid
    # the price narrated for the best deal exists verbatim in citations
    prices = {c["price"] for c in body["citations"]}
    assert any(f"{p:,.0f}" in body["answer"] for p in prices)


def test_ask_respects_budget_cap_in_question(client):
    _seed(client, "office chair")
    resp = client.post(
        "/api/v1/ask",
        json={"question": "best office chair under ₦200,000?", "live_topup": False},
    )
    body = resp.json()
    assert all(c["price"] <= 200000 for c in body["citations"])


def test_ask_insufficient_data_is_honest(client):
    resp = client.post(
        "/api/v1/ask",
        json={"question": "cheapest quantum flux capacitor under ₦1?",
              "live_topup": False},
    )
    body = resp.json()
    assert body["grounded"] is False
    assert body["answer"].upper().startswith("INSUFFICIENT DATA") \
        or "comparison" in body["answer"].lower()
    # never invents citations it doesn't have
    assert body["listings_considered"] == len(body["citations"])


def test_citation_verifier_rejects_invented_citations(client):
    from app.llm.rag import Citation, _verify

    cits = [Citation(n=1, retailer="R", title="T", price=10.0,
                     currency="NGN", url="https://x.com")]
    assert _verify("The best is T [1].", cits) is True
    assert _verify("The best is T [1], also see [7].", cits) is False
    assert _verify("Great products exist.", cits) is False        # no citations
    assert _verify("INSUFFICIENT DATA: nothing retrieved.", cits) is True


def test_cost_endpoint_reports_stub_calls(client):
    resp = client.get("/api/v1/llm/costs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["daily_budget_usd"] == 2.0
    assert body["calls_last_24h"] >= 1                # stub calls were logged
    assert body["spent_last_24h_usd"] == 0.0          # stub costs nothing


def test_intent_preserves_model_numbers(client):
    """Regression: 'a15' must not be stripped as a price (live-demo bug)."""
    from app.llm.intent import parse_intent

    intent = parse_intent("best samsung galaxy a15 under ₦160,000?")
    assert "a15" in intent.product_terms
    assert intent.max_price == 160000.0


def test_irrelevant_query_yields_no_citations(client):
    """Regression (found by eval harness): FAISS nearest-neighbors are not
    matches — retrieval must reject low-similarity hits (MIN_SIMILARITY)."""
    _seed(client, "hp laptop")
    resp = client.post(
        "/api/v1/ask",
        json={"question": "cheapest quantum flux capacitor?", "live_topup": False},
    )
    body = resp.json()
    assert body["grounded"] is False
    assert body["citations"] == []


def test_retrieval_rejects_title_with_no_product_tokens(client):
    """Regression (found in live user testing): junk listings that sneak
    past embedding similarity must fail the lexical title guard."""
    from app.llm.rag import _significant_tokens, _title_matches

    tokens = _significant_tokens("samsung galaxy a15 offers value money and why")
    assert tokens == {"samsung", "galaxy", "a15"}          # noise words dropped
    assert _title_matches("Samsung Galaxy A15 128GB", tokens) is True
    assert _title_matches(
        "RUNSONE Men 3 Colors Pack Ice Silky Underwear Valentine Gift", tokens
    ) is False
    assert _title_matches("Stratford Acoustic Guitar 38 Inches", tokens) is False


def test_accessory_filter_from_live_incident(client):
    """Regression (live user testing #2): asking about a phone must not
    return phone cases; asking about a case must still work."""
    from app.llm.rag import _is_accessory, _wants_accessory

    assert _is_accessory("Samsung Galaxy A15 4G/5G Anti Drop Transparent Back Case")
    assert _is_accessory("Samsung Galaxy A15 Tempered Glass Screen Protector")
    assert not _is_accessory("Samsung Galaxy A15 128GB + 6GB RAM Blue")

    assert _wants_accessory("samsung galaxy a15", []) is False
    assert _wants_accessory("samsung galaxy a15 case", []) is True
    assert _wants_accessory("samsung galaxy a15", ["transparent case"]) is True


def test_demo_adapter_auto_disabled_on_postgres(client, monkeypatch):
    """Production self-configuration: demo defaults OFF on PostgreSQL,
    ON on SQLite, and an explicit ENABLE_DEMO_ADAPTER always wins."""
    from app.adapters import _demo_enabled

    monkeypatch.delenv("ENABLE_DEMO_ADAPTER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host/db")
    assert _demo_enabled() is False

    monkeypatch.setenv("DATABASE_URL", "sqlite:///./dev.db")
    assert _demo_enabled() is True

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host/db")
    monkeypatch.setenv("ENABLE_DEMO_ADAPTER", "true")
    assert _demo_enabled() is True
