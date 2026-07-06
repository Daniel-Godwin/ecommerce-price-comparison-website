"""LLM client layer (design doc §8.1).

Provider-agnostic and deliberately thin:

- AnthropicClient — real calls via the Messages API (needs ANTHROPIC_API_KEY).
- StubLLM — deterministic offline model used automatically when no API key
  is configured. It answers intent-parsing and RAG prompts well enough for
  dev, tests, and demos, so the platform never *requires* a paid key to run.

Every call is logged to the llm_calls table (tokens, cost, latency) and a
daily budget guard (LLM_DAILY_BUDGET_USD) refuses non-essential calls once
the cap is hit — callers must catch BudgetExceeded and fall back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy import func, select

from app.db.models import LLMCall
from app.db.session import db_session

logger = logging.getLogger(__name__)

# indicative USD per 1M tokens (input, output) — update as pricing changes
_PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}
_DEFAULT_SMALL = "claude-haiku-4-5"
_DEFAULT_MID = "claude-sonnet-4-6"


class BudgetExceeded(RuntimeError):
    """Daily LLM budget reached — caller should degrade gracefully."""


class LLMError(RuntimeError):
    """Provider call failed after retries."""


def _spend_today() -> float:
    since = datetime.now(UTC) - timedelta(days=1)
    with db_session() as db:
        total = db.scalar(
            select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(
                LLMCall.created_at >= since
            )
        )
    return float(total or 0.0)


def _log_call(purpose: str, model: str, p_tokens: int, c_tokens: int,
              cost: float, latency_ms: int) -> None:
    try:
        with db_session() as db:
            db.add(LLMCall(purpose=purpose, model=model,
                           prompt_tokens=p_tokens,
                           completion_tokens=c_tokens, cost_usd=cost,
                           latency_ms=latency_ms))
    except Exception as exc:  # noqa: BLE001 — observability must never
        logger.warning("llm_calls logging failed: %s", exc)  # break requests


def _check_budget() -> None:
    budget = float(os.getenv("LLM_DAILY_BUDGET_USD", "2.00"))
    spent = _spend_today()
    if spent >= budget:
        raise BudgetExceeded(
            f"daily LLM budget reached (${spent:.2f} / ${budget:.2f})"
        )


# --------------------------------------------------------------------- #
# real provider                                                          #
# --------------------------------------------------------------------- #
class AnthropicClient:
    name = "anthropic"

    def __init__(self) -> None:
        self.api_key = os.environ["ANTHROPIC_API_KEY"]

    def complete(self, prompt: str, purpose: str, model: str | None = None,
                 max_tokens: int = 800, system: str | None = None) -> str:
        _check_budget()
        model = model or _DEFAULT_SMALL
        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        started = time.monotonic()
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=60,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            raise LLMError(f"anthropic {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        usage = data.get("usage", {})
        p_tok = int(usage.get("input_tokens", 0))
        c_tok = int(usage.get("output_tokens", 0))
        in_price, out_price = _PRICES.get(model, (3.0, 15.0))
        cost = (p_tok * in_price + c_tok * out_price) / 1_000_000
        _log_call(purpose, model, p_tok, c_tok, cost, latency_ms)
        return "".join(
            block.get("text", "") for block in data.get("content", [])
        ).strip()


# --------------------------------------------------------------------- #
# offline stub                                                           #
# --------------------------------------------------------------------- #
class StubLLM:
    """Deterministic offline model for dev/tests/demo (no API key needed).

    - intent prompts  → heuristic JSON intent
    - ask/RAG prompts → template answer citing the cheapest listings found
      in the provided context block
    """

    name = "stub"

    def complete(self, prompt: str, purpose: str, model: str | None = None,
                 max_tokens: int = 800, system: str | None = None) -> str:
        started = time.monotonic()
        if purpose == "intent":
            out = self._intent(prompt)
        else:
            out = self._answer(prompt)
        _log_call(purpose, "stub", len(prompt) // 4, len(out) // 4, 0.0,
                  int((time.monotonic() - started) * 1000))
        return out

    # -- intent ------------------------------------------------------- #
    def _intent(self, prompt: str) -> str:
        q_match = re.search(r"USER QUERY:\s*(.+)", prompt)
        query = (q_match.group(1) if q_match else prompt).strip()
        max_price = None
        price_match = re.search(
            r"(?:under|below|less than|max|budget of)\s*[₦$€₺]?\s*([\d.,]+)\s*(k)?",
            query, re.I,
        )
        if price_match:
            from app.core.normalizer import parse_price

            max_price = parse_price(price_match.group(1))
            if max_price and price_match.group(2):
                max_price *= 1000
        cleaned = query
        if price_match:                      # strip ONLY the price phrase
            cleaned = cleaned.replace(price_match.group(0), " ")
        cleaned = re.sub(
            r"\b(what|which|is|are|the|best|cheapest|an|good|me|find|show|"
            r"under|below|less than|max|budget of|with|for|i|want|need)\b",
            " ", cleaned, flags=re.I,
        )
        cleaned = re.sub(r"[₦$€₺]", " ", cleaned)
        cleaned = re.sub(r"^\s*a\s+|\s+a\s+", " ", cleaned)  # standalone article only
        terms = " ".join(cleaned.split()).strip(" ?.!") or query
        return json.dumps(
            {"product_terms": terms, "attributes": [], "max_price": max_price,
             "currency": None, "region": None}
        )

    # -- grounded answer ---------------------------------------------- #
    def _answer(self, prompt: str) -> str:
        rows = re.findall(
            r"\[(\d+)\]\s+(.+?)\s+\|\s+([A-Z]{3})\s+([\d,.]+)\s+\|\s+(\S+)",
            prompt,
        )
        if not rows:
            return ("INSUFFICIENT DATA: no listings were retrieved for this "
                    "question, so I cannot make a grounded recommendation.")
        parsed = [
            (int(n), title, cur, float(price.replace(",", "")))
            for n, title, cur, price, _r in rows
        ]
        parsed.sort(key=lambda r: r[3])
        best = parsed[0]
        lines = [
            f"Based on the retrieved listings, the best-priced option is "
            f"{best[1]} at {best[2]} {best[3]:,.0f} [{best[0]}]."
        ]
        if len(parsed) > 1:
            second = parsed[1]
            lines.append(
                f"The next alternative is {second[1]} at {second[2]} "
                f"{second[3]:,.0f} [{second[0]}]."
            )
        if len(parsed) > 2:
            third = parsed[2]
            lines.append(
                f"A third option is {third[1]} at {third[2]} "
                f"{third[3]:,.0f} [{third[0]}]."
            )
        lines.append(
            "Prices were captured from live retailer data; follow the "
            "citation links to buy."
        )
        return " ".join(lines)


# --------------------------------------------------------------------- #
_client = None


def get_llm():
    """AnthropicClient when a key is configured, StubLLM otherwise."""
    global _client
    if _client is None:
        if os.getenv("ANTHROPIC_API_KEY"):
            _client = AnthropicClient()
            logger.info("LLM: anthropic client active")
        else:
            _client = StubLLM()
            logger.info("LLM: no API key found — using offline stub")
    return _client


def small_model() -> str | None:
    return _DEFAULT_SMALL


def mid_model() -> str | None:
    return _DEFAULT_MID
