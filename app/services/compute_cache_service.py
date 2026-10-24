"""Tiered cache for deterministic computation results.

Optimization task §6.3 asks for a *computation* cache layer distinct from the
existing section cache:

  * financial metrics  — expensive to rebuild (scans the local financial index)
  * peer market data   — expensive network I/O, currently fetched twice per run
  * valuation          — legacy Python program plus a live FX round-trip

Design decisions:

1. **In-process TTL cache only.** No new table. Valuation results additionally
   reuse the existing ``valuation_results`` row (its ``assumptions`` JSONB
   carries the cache key), so a warm restart still skips recomputation.
2. **Cache keys include ``CALC_VERSION``.** Bumping that constant invalidates
   every derived result when a formula changes, satisfying §6.5 rule 2.
3. **Keys reuse :func:`compute_input_hash`** from ``section_cache_service`` —
   previously defined but never called anywhere.
4. Every lookup is reported to the performance tracker so cache hit rate is a
   measured number, not an assumption.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from collections import OrderedDict
import threading
import time
from typing import Any

from app.core.logging import get_logger
from app.services.performance_tracker import record_cache_event
from app.services.section_cache_service import compute_input_hash

logger = get_logger(__name__)

# Bump when any cached formula, mapping or normalization rule changes.
CALC_VERSION = "calc-v1"


def cache_key(namespace: str, *parts: Any) -> str:
    """Build a stable cache key namespaced by computation version."""

    return f"{namespace}:{CALC_VERSION}:{compute_input_hash(*parts)}"


class TTLCache:
    """Small thread-safe LRU+TTL cache for computation results."""

    def __init__(self, maxsize: int = 256, ttl_seconds: float = 900.0) -> None:
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            stored_at, value = entry
            if self.ttl_seconds and (time.monotonic() - stored_at) > self.ttl_seconds:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: Hashable, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def invalidate(self, key: Hashable | None = None) -> None:
        with self._lock:
            if key is None:
                self._data.clear()
            else:
                self._data.pop(key, None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else None,
                "ttl_seconds": self.ttl_seconds,
            }


# Process-wide caches. Separate instances keep unrelated TTL policies apart:
# market data goes stale within minutes, computed financial metrics do not.
_market_cache = TTLCache(maxsize=512, ttl_seconds=300.0)
_metric_cache = TTLCache(maxsize=256, ttl_seconds=1800.0)
_valuation_cache = TTLCache(maxsize=128, ttl_seconds=1800.0)

_CACHES = {
    "market": _market_cache,
    "metrics": _metric_cache,
    "valuation": _valuation_cache,
}

CACHE_MARKET = "market"
CACHE_METRICS = "metrics"
CACHE_VALUATION = "valuation"


def get_cache(name: str) -> TTLCache:
    return _CACHES[name]


def cache_stats() -> dict[str, dict[str, Any]]:
    """Return hit-rate stats per cache tier (feeds §10.1 question 5)."""

    return {name: cache.stats() for name, cache in _CACHES.items()}


def invalidate_all() -> None:
    for cache in _CACHES.values():
        cache.invalidate()


def cached_compute(
    cache_name: str,
    key: str,
    producer: Callable[[], Any],
    *,
    force_refresh: bool = False,
    cache_ok: Callable[[Any], bool] | None = None,
) -> tuple[Any, bool]:
    """Return ``(value, cache_hit)`` computing *producer* only on a miss.

    ``cache_ok`` lets a caller reject a stored value that is present but no
    longer usable (e.g. an empty error payload cached by an earlier failure).
    """

    cache = get_cache(cache_name)
    if not force_refresh:
        hit = cache.get(key)
        if hit is not None and (cache_ok is None or cache_ok(hit)):
            record_cache_event(True)
            return hit, True
    record_cache_event(False)
    value = producer()
    if cache_ok is None or cache_ok(value):
        cache.set(key, value)
    return value, False
