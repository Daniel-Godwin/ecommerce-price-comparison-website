"""RAG answer pipeline (design doc §8.2, FR-11).

Steps: parse intent → hybrid retrieve (FAISS + SQL filters + live search
top-up) → build citation-numbered context → generate with a grounded
prompt → verify every citation → degrade honestly if verification fails.

Two invariants from the design doc are enforced here:
- Prices come from the database/adapters; the LLM only narrates them.
- If retrieval is thin, the answer says so instead of guessing.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import repository as repo
from app.db.models import Product
from app.llm.client import BudgetExceeded, LLMError, get_llm, mid_model
from app.llm.intent import Intent, parse_intent
from app.vector.faiss_store import get_store

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "ask.txt").read_text()
_CITE_RE = re.compile(r"\[(\d+)\]")
MAX_CONTEXT_LISTINGS = 8
# hits below this cosine similarity are noise, not matches — FAISS always
# returns the NEAREST neighbors even when nothing is actually near
# (calibrated: relevant queries score 0.45+, unrelated ones < 0.27)
MIN_SIMILARITY = 0.35


class Citation(BaseModel):
    n: int
    retailer: str
    title: str
    price: float
    currency: str
    url: str


class AskAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    intent: Intent
    grounded: bool                 # False → degraded/insufficient pathway
    listings_considered: int


# --------------------------------------------------------------------- #
# retrieval                                                              #
# --------------------------------------------------------------------- #
def _retrieve(db: Session, intent: Intent, live_topup: bool) -> list[Citation]:
    """Hybrid retrieval: semantic hits joined with latest prices, filtered."""
    # optionally top up the corpus with a live search on the intent terms
    if live_topup:
        from app.core.search_service import execute_search
        from app.schemas.api import SearchRequest

        try:
            execute_search(db, SearchRequest(query=intent.product_terms))
        except Exception as exc:  # noqa: BLE001 — retrieval must not die here
            logger.warning("live top-up failed: %s", exc)

    store = get_store()
    hits = store.search(intent.product_terms, k=24) if store.count else []
    hits = [(pid, score) for pid, score in hits if score >= MIN_SIMILARITY]

    candidates: list[Citation] = []
    seen_urls: set[str] = set()
    for product_id, _score in hits:
        product = db.get(Product, product_id)
        if product is None:
            continue
        for row in product.listings:
            snap = repo.latest_price(db, row.id)
            if snap is None or row.url in seen_urls:
                continue
            if intent.max_price is not None and snap.price > intent.max_price:
                continue
            if intent.currency and snap.currency != intent.currency:
                continue
            seen_urls.add(row.url)
            candidates.append(
                Citation(n=0, retailer=row.retailer.name, title=row.title_raw,
                         price=snap.price, currency=snap.currency, url=row.url)
            )
    candidates.sort(key=lambda c: c.price)
    top = candidates[:MAX_CONTEXT_LISTINGS]
    for i, cit in enumerate(top, start=1):
        cit.n = i
    return top


def _context_block(citations: list[Citation]) -> str:
    return "\n".join(
        f"[{c.n}] {c.title} | {c.currency} {c.price:,.2f} | {c.url} | {c.retailer}"
        for c in citations
    )


# --------------------------------------------------------------------- #
# verification (design doc §8.2 step 5)                                  #
# --------------------------------------------------------------------- #
def _verify(answer: str, citations: list[Citation]) -> bool:
    """Every cited number must exist; at least one citation unless the
    answer is an explicit insufficient-data statement."""
    cited = {int(n) for n in _CITE_RE.findall(answer)}
    valid = {c.n for c in citations}
    if answer.strip().upper().startswith("INSUFFICIENT DATA"):
        return True
    if not cited:
        return False
    return cited <= valid


def _degraded_answer(citations: list[Citation]) -> str:
    """Plain grounded comparison used when generation/verification fails."""
    if not citations:
        return ("INSUFFICIENT DATA: no matching listings were found for this "
                "question. Try a broader query or remove the price cap.")
    lines = ["Here is a plain comparison of the retrieved listings:"]
    lines += [
        f"[{c.n}] {c.title} — {c.currency} {c.price:,.0f} ({c.retailer})"
        for c in citations[:5]
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------- #
# public entry                                                           #
# --------------------------------------------------------------------- #
def ask(db: Session, question: str, live_topup: bool = True) -> AskAnswer:
    intent = parse_intent(question)
    citations = _retrieve(db, intent, live_topup=live_topup)

    if not citations:
        return AskAnswer(answer=_degraded_answer([]), citations=[],
                         intent=intent, grounded=False, listings_considered=0)

    prompt = _PROMPT.replace("{context}", _context_block(citations)) \
                    .replace("{question}", question)

    answer: str | None = None
    try:
        answer = get_llm().complete(prompt, purpose="ask",
                                    model=mid_model(), max_tokens=500)
        if not _verify(answer, citations):
            logger.warning("citation check failed; regenerating once")
            answer = get_llm().complete(prompt, purpose="ask",
                                        model=mid_model(), max_tokens=500)
            if not _verify(answer, citations):
                answer = None
    except (BudgetExceeded, LLMError) as exc:
        logger.warning("ask generation unavailable (%s); degrading", exc)
        answer = None

    if answer is None:
        return AskAnswer(answer=_degraded_answer(citations),
                         citations=citations, intent=intent,
                         grounded=False, listings_considered=len(citations))

    used = {int(n) for n in _CITE_RE.findall(answer)}
    return AskAnswer(
        answer=answer,
        citations=[c for c in citations if c.n in used] or citations,
        intent=intent,
        grounded=True,
        listings_considered=len(citations),
    )
