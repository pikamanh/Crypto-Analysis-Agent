"""Tiny shared TTL cache for upstream HTTP fetches — used by both the
Deribit engine (options_engine.py) and the Nasdaq/Yahoo engine
(qqq_options_engine.py) so a burst of dashboard refreshes from multiple
browser tabs doesn't hammer either upstream API or blow past its rate
limits.

Deliberately dumb: one process-wide dict per caller, keyed by whatever
string the caller derives (usually URL+params). No eviction beyond TTL
expiry — fine at this scale (a handful of distinct keys per engine).

Per-key locking: FastAPI runs sync `def` routes in a thread pool, so a
burst of concurrent requests hitting a cold (or just-expired) key would,
without a lock, each call fetch_fn() themselves — for the dashboard
computations (options_engine/qqq_options_engine), that means each one
independently re-solving IV + GEX/DEX for the whole chain at once, which
is exactly what was piling up and starving the process on Render's
throttled CPU. The lock makes the first caller do the work and everyone
else waiting on the same key just reuse its result.

Stale-on-error: Yahoo/Nasdaq are free, unofficial, no-auth endpoints that
occasionally 429 or hang — polling them on a fixed schedule from a single
outbound IP (Render's) makes that more likely, not less, than requests
from ad-hoc/residential IPs. Rather than let a single upstream hiccup fail
the whole ingest tick or a live dashboard request, a failed fetch_fn()
falls back to the last cached value regardless of its TTL, as long as it
isn't older than _MAX_STALE_SECONDS. Only a cold cache (never fetched
successfully) or a cache stale beyond that cap still raises.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Tuple

logger = logging.getLogger(__name__)

Cache = Dict[str, Tuple[float, Any]]

# How long a stale value is still preferable to a hard failure. 30 minutes
# is generous relative to every TTL callers actually use (15-45s) — it's
# meant to ride out a rate-limit window, not to quietly serve hour-old data
# forever if an upstream goes down for good.
_MAX_STALE_SECONDS = 1800

_locks: Dict[int, Dict[str, threading.Lock]] = {}
_locks_guard = threading.Lock()


def _lock_for(cache: Cache, key: str) -> threading.Lock:
    # Locks are keyed by (cache dict identity, key) rather than living on
    # Cache itself, since Cache is a plain dict, not a class we can attach
    # state to.
    with _locks_guard:
        per_cache = _locks.setdefault(id(cache), {})
        return per_cache.setdefault(key, threading.Lock())


def cached_get(cache: Cache, key: str, ttl: float, fetch_fn: Callable[[], Any]) -> Any:
    now = time.time()
    hit = cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]

    with _lock_for(cache, key):
        # Re-check: another thread may have refreshed this key while we
        # were waiting on the lock, in which case there's nothing to do.
        now = time.time()
        hit = cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        try:
            value = fetch_fn()
        except Exception:
            if hit is not None and now - hit[0] < _MAX_STALE_SECONDS:
                logger.warning(
                    "cached_get(%r): fetch failed, serving stale value from %.0fs ago",
                    key, now - hit[0], exc_info=True,
                )
                return hit[1]
            raise
        cache[key] = (now, value)
        return value
