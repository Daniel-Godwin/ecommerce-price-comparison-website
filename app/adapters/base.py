"""Retailer adapter interface (design doc §5.2.1).

Every retailer is one subclass of ``BaseAdapter`` implementing
``_search()``. The base class supplies: a pooled HTTP session,
timeouts, retry with exponential backoff (NFR-04), and a simple
circuit breaker so a repeatedly failing retailer is skipped
(health check → degraded) instead of slowing every search.
"""
from __future__ import annotations

import abc
import logging
import time

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.schemas.models import RawListing

logger = logging.getLogger(__name__)


class AdapterError(RuntimeError):
    """Raised when an adapter cannot produce results."""


class BaseAdapter(abc.ABC):
    """Common behaviour for all retailer adapters."""

    #: unique key, e.g. "jumia" — used in UI filters and logs
    key: str = "base"
    #: human-readable retailer name
    name: str = "Base"
    #: region code this adapter serves, e.g. "NG"
    region: str = "GLOBAL"
    #: ISO currency the retailer prices in
    currency: str = "USD"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.settings.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        # circuit breaker state
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    # ------------------------------------------------------------------ #
    # circuit breaker (NFR-04)                                            #
    # ------------------------------------------------------------------ #
    @property
    def is_degraded(self) -> bool:
        if self._opened_at is None:
            return False
        cooldown = self.settings.circuit_breaker_cooldown_seconds
        if time.monotonic() - self._opened_at > cooldown:
            # half-open: allow one trial request
            self._opened_at = None
            self._consecutive_failures = 0
            return False
        return True

    def _record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.settings.circuit_breaker_failures:
            self._opened_at = time.monotonic()
            logger.warning("adapter %s degraded (circuit open)", self.key)

    # ------------------------------------------------------------------ #
    # public API                                                          #
    # ------------------------------------------------------------------ #
    def search(self, query: str) -> list[RawListing]:
        """Search this retailer. Raises AdapterError on failure."""
        if self.is_degraded:
            raise AdapterError(f"{self.name} is temporarily degraded")
        try:
            results = self._search(query)
            self._record_success()
            return results
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all boundary
            self._record_failure()
            raise AdapterError(f"{self.name}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # helpers for subclasses                                              #
    # ------------------------------------------------------------------ #
    def _get(self, url: str, **kwargs) -> requests.Response:
        """HTTP GET with timeout + exponential-backoff retry."""

        @retry(
            stop=stop_after_attempt(self.settings.max_retries),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
            retry=retry_if_exception_type(
                (requests.ConnectionError, requests.Timeout)
            ),
            reraise=True,
        )
        def _do() -> requests.Response:
            resp = self.session.get(
                url, timeout=self.settings.request_timeout_seconds, **kwargs
            )
            resp.raise_for_status()
            return resp

        return _do()

    # ------------------------------------------------------------------ #
    # to implement per retailer                                           #
    # ------------------------------------------------------------------ #
    @abc.abstractmethod
    def _search(self, query: str) -> list[RawListing]:
        """Fetch and parse raw listings for *query* from this retailer."""
        raise NotImplementedError
