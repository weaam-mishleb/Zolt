"""Tests for the search result cache.

WHY THIS EXISTS
---------------
Search is dominated by network, not by the query: measured against production, a
bare `SELECT 1` round-trip costs ~223ms while the MATCH itself runs in 41ms
server-side. A cache that lives in the process therefore removes almost the
whole cost of a repeated query — 300ms to 0.02ms — which is why it is a local
dict and not Redis, which would sit across the very hop being avoided.

The subtle part is not expiry, it is ALIASING. The first version handed callers
the cached list itself, so one caller mutating a row rewrote what every later
request saw. A test caught it; reading the code had not.
"""
from __future__ import annotations

import time

from backend.app.services.cache import TTLCache


def test_a_value_survives_a_round_trip():
    c = TTLCache()
    c.set("k", [{"name": "חלב"}])
    assert c.get("k") == [{"name": "חלב"}]


def test_a_miss_is_none_not_an_error():
    assert TTLCache().get("nope") is None


def test_an_entry_expires():
    c = TTLCache(ttl=0.05)
    c.set("k", 1)
    assert c.get("k") == 1
    time.sleep(0.06)
    assert c.get("k") is None


def test_expired_entries_are_dropped_on_read():
    """No sweeper thread to own; a stale entry costs a slot until someone asks."""
    c = TTLCache(ttl=0.01)
    c.set("k", 1)
    time.sleep(0.02)
    c.get("k")
    assert c.stats()["entries"] == 0


def test_the_least_recently_used_entry_is_evicted_first():
    c = TTLCache(maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")          # 'a' is now the most recently used
    c.set("c", 3)       # evicts 'b'
    assert c.get("a") == 1
    assert c.get("b") is None
    assert c.get("c") == 3


def test_the_cache_never_grows_past_maxsize():
    c = TTLCache(maxsize=10)
    for i in range(100):
        c.set(i, i)
    assert c.stats()["entries"] == 10


def test_stats_report_the_hit_rate():
    c = TTLCache()
    c.set("k", 1)
    c.get("k"); c.get("k"); c.get("miss")
    s = c.stats()
    assert (s["hits"], s["misses"], s["hit_rate"]) == (2, 1, round(2 / 3, 3))


def test_clear_empties_and_resets_counters():
    c = TTLCache()
    c.set("k", 1); c.get("k")
    c.clear()
    assert c.stats()["entries"] == 0
    assert c.stats()["hits"] == 0


def test_a_cached_list_is_not_shared_with_the_caller():
    """THE bug: search handed back the cached list itself, so a caller mutating
    one row rewrote what every later request saw. search_products copies on the
    way in AND on the way out."""
    from backend.app.services.search import _remember

    rows = [{"name": "חלב", "availability": 5}]
    returned = _remember(("חלב", 10), rows)
    returned[0]["name"] = "MUTATED"
    from backend.app.services.cache import search_cache

    assert search_cache.get(("חלב", 10))[0]["name"] == "חלב"
    search_cache.clear()


def test_the_key_includes_the_limit():
    """Same query, different limit, is a different result set — sharing one slot
    would serve 10 rows to a request that asked for 50."""
    c = TTLCache()
    c.set(("חלב", 10), ["ten"])
    c.set(("חלב", 50), ["fifty"])
    assert c.get(("חלב", 10)) == ["ten"]
    assert c.get(("חלב", 50)) == ["fifty"]
