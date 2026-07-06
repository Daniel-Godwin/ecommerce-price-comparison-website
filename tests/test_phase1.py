"""Phase 1 test suite (design doc §11.1: unit + adapter + integration)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.base import AdapterError, BaseAdapter
from app.adapters.demo import DemoStoreAdapter
from app.adapters.jumia import JumiaAdapter
from app.core.analytics import compute_analytics, top_cheapest
from app.core.normalizer import detect_currency, normalize, normalize_all, parse_price
from app.core.orchestrator import _run_adapter, search
from app.schemas.models import RawListing

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------- #
# normalizer                                                            #
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₦ 148,500", 148500.0),
        ("₦132,000 - ₦145,000", 132000.0),   # range → lower bound
        ("$1,299.99", 1299.99),
        ("1.299,99 €", 1299.99),              # EU decimal comma
        ("€1.299", 1299.0),                    # EU thousands dot
        ("999.99", 999.99),
        ("₺ 24.999", 24999.0),
        ("price on request", None),
        ("₦0", None),                          # non-positive rejected
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    ("text", "hint", "expected"),
    [
        ("₦ 148,500", None, "NGN"),
        ("$12.99", None, "USD"),
        ("₺ 24.999", None, "TRY"),
        ("148500", "NGN", "NGN"),   # falls back to adapter hint
        ("148500", None, None),
    ],
)
def test_detect_currency(text, hint, expected):
    assert detect_currency(text, hint) == expected


def test_normalize_drops_invalid_and_cleans_title():
    raws = [
        RawListing(title="  Good   Product ", price_text="₦ 1,000",
                   url="https://x.com/1", retailer="R"),
        RawListing(title="Bad price", price_text="call us",
                   url="https://x.com/2", retailer="R"),
    ]
    out = normalize_all(raws)
    assert len(out) == 1
    assert out[0].product == "Good Product"
    assert out[0].price == 1000.0
    assert out[0].currency == "NGN"


# --------------------------------------------------------------------- #
# analytics                                                             #
# --------------------------------------------------------------------- #
def _mk(price, retailer="R", currency="NGN"):
    raw = RawListing(title=f"P{price}", price_text=f"{price}",
                     url="https://x.com", retailer=retailer,
                     currency_hint=currency)
    return normalize(raw)


def test_analytics_min_max_avg_best_deal():
    listings = [_mk(100), _mk(300), _mk(200)]
    a = compute_analytics(listings)
    assert (a.min_price, a.max_price, a.avg_price) == (100, 300, 200)
    assert a.best_deal.price == 100
    assert a.count == 3


def test_analytics_uses_dominant_currency_only():
    listings = [_mk(100), _mk(200), _mk(5, currency="USD")]
    a = compute_analytics(listings)
    assert a.currency == "NGN"
    assert a.count == 2          # USD listing excluded from NGN stats


def test_top_cheapest_orders_and_limits():
    listings = [_mk(300), _mk(100), _mk(200), _mk(50)]
    top = top_cheapest(listings, 3)
    assert [x.price for x in top] == [50, 100, 200]


def test_analytics_empty():
    assert compute_analytics([]) is None


# --------------------------------------------------------------------- #
# adapters (fixture-based — never hit live sites in CI)                 #
# --------------------------------------------------------------------- #
def test_jumia_parse_fixture():
    html = (FIXTURES / "jumia_search.html").read_text()
    raws = JumiaAdapter().parse(html)
    assert len(raws) == 2                      # broken card skipped
    assert raws[0].title.startswith("Samsung Galaxy A15 128GB")
    assert raws[0].url.startswith("https://www.jumia.com.ng/")
    assert raws[0].image_url == "https://ng.jumia.is/a15.jpg"
    listings = normalize_all(raws)
    assert [x.price for x in listings] == [148500.0, 132000.0]


def test_demo_adapter_is_deterministic():
    a, b = DemoStoreAdapter(), DemoStoreAdapter()
    r1, r2 = a.search("hp laptop"), b.search("hp laptop")
    assert [x.price_text for x in r1] == [x.price_text for x in r2]
    assert len(r1) == 5


# --------------------------------------------------------------------- #
# orchestrator resilience (NFR-03) + cache                              #
# --------------------------------------------------------------------- #
class FailingAdapter(BaseAdapter):
    key, name, region, currency = "fail", "AlwaysFails", "NG", "NGN"

    def _search(self, query):
        raise RuntimeError("boom")


def test_run_adapter_reports_failure_without_raising():
    status, raws = _run_adapter(FailingAdapter(), "anything")
    assert status.ok is False
    assert "boom" in status.error
    assert raws == []


def test_circuit_breaker_opens_after_repeated_failures():
    a = FailingAdapter()
    for _ in range(a.settings.circuit_breaker_failures):
        with pytest.raises(AdapterError):
            a.search("x")
    assert a.is_degraded is True
    with pytest.raises(AdapterError, match="degraded"):
        a.search("x")


def test_search_end_to_end_with_demo_and_cache():
    r1 = search("hp laptop", adapter_keys=["demostore"], use_cache=True)
    assert r1.from_cache is False
    assert len(r1.listings) == 5
    assert r1.analytics is not None
    assert r1.analytics.best_deal.price == r1.analytics.min_price
    # listings arrive sorted by price
    prices = [x.price for x in r1.listings]
    assert prices == sorted(prices)

    r2 = search("hp laptop", adapter_keys=["demostore"], use_cache=True)
    assert r2.from_cache is True


def test_search_partial_results_when_one_source_fails(monkeypatch):
    import app.core.orchestrator as orch

    demo = DemoStoreAdapter()
    failing = FailingAdapter()
    monkeypatch.setattr(orch, "get_adapters", lambda keys=None: [demo, failing])

    result = search("rice cooker", use_cache=False)
    ok = {s.retailer: s.ok for s in result.sources_status}
    assert ok["DemoStore (sample data)"] is True
    assert ok["AlwaysFails"] is False
    assert len(result.listings) == 5           # demo results still returned
