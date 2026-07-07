"""Adapter registry (design doc NFR-05).

Adding a retailer = write one adapter file + register it here.
"""
from __future__ import annotations

from app.adapters.base import BaseAdapter
from app.adapters.demo import DemoStoreAdapter
from app.adapters.jumia import JumiaAdapter
from app.adapters.konga import KongaAdapter

_ADAPTER_CLASSES: list[type[BaseAdapter]] = [
    JumiaAdapter,
    KongaAdapter,
    DemoStoreAdapter,
]

# instantiate once — sessions and circuit breakers are stateful
_REGISTRY: dict[str, BaseAdapter] = {cls.key: cls() for cls in _ADAPTER_CLASSES}


def _demo_enabled() -> bool:
    import os

    return os.getenv("ENABLE_DEMO_ADAPTER", "true").lower() not in ("false", "0", "off")


def all_adapters() -> list[BaseAdapter]:
    """Default adapter set. DemoStore is excluded when ENABLE_DEMO_ADAPTER is
    off (production) but remains addressable by explicit key for tests."""
    return [a for a in _REGISTRY.values()
            if a.key != "demostore" or _demo_enabled()]


def get_adapters(keys: list[str] | None = None) -> list[BaseAdapter]:
    """Return adapters for the given keys (all if keys is None/empty)."""
    if not keys:
        return all_adapters()
    return [_REGISTRY[k] for k in keys if k in _REGISTRY]
