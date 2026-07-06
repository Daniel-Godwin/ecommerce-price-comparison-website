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


def all_adapters() -> list[BaseAdapter]:
    return list(_REGISTRY.values())


def get_adapters(keys: list[str] | None = None) -> list[BaseAdapter]:
    """Return adapters for the given keys (all if keys is None/empty)."""
    if not keys:
        return all_adapters()
    return [_REGISTRY[k] for k in keys if k in _REGISTRY]
