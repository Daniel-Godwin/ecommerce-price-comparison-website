"""Query understanding (design doc §5.2.3, FR-10).

Simple keyword queries bypass the LLM entirely (fast path) — zero cost,
zero latency. Only queries with natural-language signals (question words,
budget phrasing, attribute language) go to the small model. On budget
exhaustion or provider failure the fast path is the fallback, so /ask and
/search never hard-fail on LLM problems.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from app.llm.client import BudgetExceeded, LLMError, get_llm, small_model

logger = logging.getLogger(__name__)

_PROMPT = (Path(__file__).parent / "prompts" / "intent.txt").read_text()

# signals that the query is conversational rather than plain keywords
_NL_SIGNALS = re.compile(
    r"\b(what|which|best|cheapest|good|under|below|less than|budget|"
    r"recommend|should|want|need|looking for|between|around)\b|\?",
    re.I,
)
_PRICE_RE = re.compile(
    r"(?:under|below|less than|max|budget of|around)\s*[₦$€₺]?\s*([\d.,]+)\s*(k)?",
    re.I,
)
_CURRENCY_RE = {"₦": "NGN", "$": "USD", "€": "EUR", "₺": "TRY"}


class Intent(BaseModel):
    product_terms: str
    attributes: list[str] = Field(default_factory=list)
    max_price: float | None = None
    currency: str | None = None
    region: str | None = None
    source: str = "fast_path"     # fast_path | llm | fallback


def _fast_path(query: str) -> Intent:
    """Heuristic parse — free and instant."""
    max_price = None
    match = _PRICE_RE.search(query)
    if match:
        from app.core.normalizer import parse_price

        max_price = parse_price(match.group(1))
        if max_price and match.group(2):
            max_price *= 1000
    currency = next(
        (iso for sym, iso in _CURRENCY_RE.items() if sym in query), None
    )
    cleaned = _PRICE_RE.sub(" ", query)
    cleaned = re.sub(
        r"\b(what|which|is|are|the|best|cheapest|a|an|good|me|find|show|"
        r"recommend|should|i|buy|want|need|looking|for|with|please)\b",
        " ", cleaned, flags=re.I,
    )
    cleaned = re.sub(r"[₦$€₺]", " ", cleaned)
    terms = " ".join(cleaned.split()).strip(" ?.!,") or query.strip(" ?.!")
    return Intent(product_terms=terms, max_price=max_price, currency=currency)


def parse_intent(query: str, force_llm: bool = False) -> Intent:
    query = query.strip()
    is_conversational = bool(_NL_SIGNALS.search(query))

    if not is_conversational and not force_llm:
        return _fast_path(query)                      # plain keywords

    try:
        raw = get_llm().complete(
            _PROMPT.replace("{query}", query),
            purpose="intent",
            model=small_model(),
            max_tokens=300,
        )
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        data = json.loads(raw)
        intent = Intent(
            product_terms=str(data.get("product_terms") or query)[:200],
            attributes=[str(a) for a in (data.get("attributes") or [])][:10],
            max_price=data.get("max_price"),
            currency=data.get("currency"),
            region=data.get("region"),
            source="llm",
        )
        if not intent.product_terms.strip():
            raise ValueError("empty product_terms")
        return intent
    except (BudgetExceeded, LLMError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("intent LLM unavailable (%s); using fast path", exc)
        fallback = _fast_path(query)
        fallback.source = "fallback"
        return fallback
