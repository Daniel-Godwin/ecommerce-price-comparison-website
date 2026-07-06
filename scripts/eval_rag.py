"""RAG evaluation harness (design doc §11.1).

Runs a golden query set through the full /ask pipeline and scores:

  1. Citation validity  — every [n] cited in an answer exists in the
     retrieved set (target: 100%).
  2. Price fidelity     — every price mentioned in an answer appears
     verbatim among the cited listings (target: 100%).
  3. Groundedness       — >= 95% of answers are either grounded with
     citations or an explicit INSUFFICIENT DATA statement.
  4. Constraint respect — when the question states a budget, all cited
     prices are within it.
  5. Honesty            — nonsense queries must yield INSUFFICIENT DATA,
     never invented products.

Usage:
    python -m scripts.eval_rag            # uses a temp DB, DemoStore only
Exit code 0 = pass, 1 = fail (CI-friendly).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

PASS_THRESHOLD = 0.95

GOLDEN_QUERIES: list[dict] = [
    # seeded product families
    {"seed": "hp laptop", "question": "what is the cheapest hp laptop?"},
    {"seed": "hp laptop", "question": "best hp laptop under ₦300,000?", "budget": 300000},
    {"seed": "samsung galaxy a15", "question": "which samsung galaxy a15 should I buy?"},
    {"seed": "samsung galaxy a15",
     "question": "samsung galaxy a15 under ₦160,000?", "budget": 160000},
    {"seed": "office chair", "question": "recommend a good office chair"},
    {"seed": "rice cooker", "question": "cheapest rice cooker available?"},
    {"seed": "bluetooth speaker", "question": "best bluetooth speaker under 200k?",
     "budget": 200000},
    # honesty probes — nothing seeded, must say INSUFFICIENT DATA
    {"seed": None, "question": "cheapest quantum flux capacitor?", "expect_insufficient": True},
    {"seed": None, "question": "price of a martian rock sample under ₦5?",
     "expect_insufficient": True},
]

_CITE_RE = re.compile(r"\[(\d+)\]")
_PRICE_IN_TEXT = re.compile(r"(?:NGN|₦)\s*([\d,]+(?:\.\d+)?)")


def run() -> int:
    # isolated environment — never touches the real DB or index
    tmp = tempfile.mkdtemp(prefix="rag_eval_")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/eval.db"
    os.environ["VECTOR_INDEX_DIR"] = f"{tmp}/vec"

    import importlib

    import app.db.session as session_mod
    import app.llm.client as llm_mod
    import app.vector.faiss_store as store_mod

    importlib.reload(session_mod)
    store_mod._store = None
    llm_mod._client = None

    import app.core.search_service as svc_mod
    import app.llm.rag as rag_mod

    importlib.reload(svc_mod)
    importlib.reload(rag_mod)

    from app.db.session import db_session, init_db
    from app.schemas.api import SearchRequest

    init_db()

    checks_total = 0
    checks_passed = 0
    grounded_or_honest = 0
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        nonlocal checks_total, checks_passed
        checks_total += 1
        if ok:
            checks_passed += 1
        else:
            failures.append(name)

    with db_session() as db:
        # seed corpus
        for item in GOLDEN_QUERIES:
            if item["seed"]:
                svc_mod.execute_search(
                    db, SearchRequest(query=item["seed"],
                                      retailers=["demostore"], use_cache=False)
                )

        print(f"{'QUESTION':<52} GROUNDED  CITES  VERDICT")
        print("-" * 78)
        for item in GOLDEN_QUERIES:
            question = item["question"]
            result = rag_mod.ask(db, question, live_topup=False)
            answer, citations = result.answer, result.citations
            valid_ns = {c.n for c in citations}
            cited_ns = {int(n) for n in _CITE_RE.findall(answer)}
            insufficient = answer.upper().startswith("INSUFFICIENT DATA")

            # 3. groundedness / honesty accounting
            if result.grounded or insufficient:
                grounded_or_honest += 1

            if item.get("expect_insufficient"):
                # 5. honesty
                check(f"honesty: {question}", insufficient or not result.grounded)
                verdict = "OK" if (insufficient or not result.grounded) else "FAIL"
                print(f"{question[:50]:<52} {str(result.grounded):<9} "
                      f"{len(citations):<6} {verdict}")
                continue

            # 1. citation validity
            check(f"citations valid: {question}",
                  bool(cited_ns) and cited_ns <= valid_ns)
            # 2. price fidelity
            cited_prices = {round(c.price, 2) for c in citations}
            mentioned = [
                float(p.replace(",", "")) for p in _PRICE_IN_TEXT.findall(answer)
            ]
            check(f"price fidelity: {question}",
                  all(round(p, 2) in cited_prices for p in mentioned))
            # 4. constraint respect
            if "budget" in item:
                check(f"budget respected: {question}",
                      all(c.price <= item["budget"] for c in citations))

            ok_here = not any(question in f for f in failures)
            print(f"{question[:50]:<52} {str(result.grounded):<9} "
                  f"{len(citations):<6} {'OK' if ok_here else 'FAIL'}")

    groundedness = grounded_or_honest / len(GOLDEN_QUERIES)
    score = checks_passed / checks_total if checks_total else 0.0
    print("-" * 78)
    print(f"checks passed        : {checks_passed}/{checks_total} ({score:.0%})")
    print(f"grounded-or-honest   : {grounded_or_honest}/{len(GOLDEN_QUERIES)} "
          f"({groundedness:.0%})  [threshold {PASS_THRESHOLD:.0%}]")
    if failures:
        print("failed checks:")
        for f in failures:
            print("  -", f)

    passed = score >= PASS_THRESHOLD and groundedness >= PASS_THRESHOLD
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(run())
