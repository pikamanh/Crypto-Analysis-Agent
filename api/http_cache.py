"""Tiny shared TTL cache for upstream HTTP fetches — used by both the
Deribit engine (options_engine.py) and the Nasdaq/Yahoo engine
(qqq_options_engine.py) so a burst of dashboard refreshes from multiple
browser tabs doesn't hammer either upstream API or blow past its rate
limits.

Deliberately dumb: one process-wide dict per caller, keyed by whatever
string the caller derives (usually URL+params). No eviction beyond TTL
expiry — fine at this scale (a handful of distinct keys per engine).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Tuple

Cache = Dict[str, Tuple[float, Any]]


def cached_get(cache: Cache, key: str, ttl: float, fetch_fn: Callable[[], Any]) -> Any:
    now = time.time()
    hit = cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fetch_fn()
    cache[key] = (now, value)
    return value
