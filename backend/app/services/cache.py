"""A small TTL + LRU cache for read-only query results.

WHY IN-MEMORY AND NOT REDIS
---------------------------
Redis would be a new service to run, secure and pay for, and it would sit on the
far side of a network hop. Measured from this stack, a bare `SELECT 1` to the
database costs ~223ms while the search query itself takes 41ms server-side — the
expensive thing here is CROSSING A NETWORK, and a cache that lives across one
solves less than it looks. A process-local dict costs nothing and returns in
microseconds.

The honest limit: each API process keeps its own copy, so N processes mean N
cold starts and N copies of the same entries. That is fine at this size and
stops being fine when there are many processes or the entries get large — at
which point Redis earns its keep and this module is the thing to replace.

Correctness comes from the TTL. Prices reload nightly, so a few minutes of
staleness in an autocomplete list is invisible; `clear()` exists for the tests
and for the ETL to call if it ever wants to invalidate explicitly.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

# Autocomplete fires on keystrokes, so the same prefixes repeat constantly
# within a session. Minutes of TTL, not seconds, because the underlying data
# only changes when the nightly ETL runs.
DEFAULT_TTL_S = 300
DEFAULT_MAXSIZE = 2_048


class TTLCache:
    """Thread-safe LRU with per-entry expiry.

    FastAPI serves requests from a thread pool, so this is touched
    concurrently; the lock is held only around dict operations, never across a
    database call.
    """

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE, ttl: float = DEFAULT_TTL_S):
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires, value = entry
            if expires < now:
                # Expired entries are dropped on read rather than by a sweeper —
                # no background thread to own, and an entry nobody reads costs
                # nothing but a slot.
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key, value) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)      # evict least recently used

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "maxsize": self.maxsize,
                "ttl_seconds": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }


# One cache for product search. Keyed on the query and limit only — never on the
# Session, which is per-request.
search_cache = TTLCache()
