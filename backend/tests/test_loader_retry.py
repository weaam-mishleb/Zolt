"""Tests for the loader's deadlock handling.

WHY THIS EXISTS
---------------
Six matrix runners upsert the same national barcodes at the same instant, and
MySQL answered with 1213 "Deadlock found". The retry loop was already there and
already caught OperationalError — but 1213 was not in its retryable set, so it
re-raised on the first deadlock and the job died. The retry never ran.

These tests pin the three things that fixes it: the code is recognised, the
right recovery is chosen for the right failure, and batches are ordered so the
deadlock mostly stops happening in the first place.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from etl.loader import (
    _CONNECTION_LOST,
    _LOCK_CONTENTION,
    _RETRYABLE,
    Loader,
    _lock_ordered,
    _mysql_errno,
)

DEADLOCK = 1213
LOCK_WAIT = 1205
CONN_LOST = 2013
NOT_RETRYABLE = 1062          # duplicate entry — a real bug, must surface


def _op_error(errno: int) -> OperationalError:
    """An OperationalError shaped like the one PyMySQL raises."""
    class _Orig(Exception):
        pass

    orig = _Orig(errno, "boom")
    orig.args = (errno, "boom")
    return OperationalError("INSERT ...", {}, orig)


class _FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    def dispose(self):
        self.dispose_calls += 1


@pytest.fixture
def loader():
    # No real sleeping: base delay small enough that 12 retries stay instant.
    return Loader(_FakeEngine(), max_retries=4, retry_base_delay=0.001, retry_max_delay=0.004)


# ── error classification ────────────────────────────────────────────────────
def test_deadlock_is_retryable():
    """THE regression: 1213 used to be absent, so the loop re-raised at once."""
    assert DEADLOCK in _RETRYABLE
    assert DEADLOCK in _LOCK_CONTENTION


def test_lock_wait_timeout_is_retryable():
    assert LOCK_WAIT in _RETRYABLE


def test_connection_drops_are_still_retryable():
    assert _CONNECTION_LOST <= _RETRYABLE


def test_a_real_bug_is_not_retried():
    """A duplicate-key violation means the data or the schema is wrong. Retrying
    it would just hide it twelve times."""
    assert NOT_RETRYABLE not in _RETRYABLE


def test_mysql_errno_reads_the_wrapped_driver_code():
    assert _mysql_errno(_op_error(DEADLOCK)) == DEADLOCK
    assert _mysql_errno(Exception("nothing wrapped")) is None


# ── retry behaviour ─────────────────────────────────────────────────────────
def test_a_deadlock_is_retried_and_then_succeeds(loader):
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _op_error(DEADLOCK)
        return "committed"

    assert loader._with_retry(op, "upsert batch") == "committed"
    assert calls["n"] == 3


def test_a_deadlock_does_not_throw_the_connection_pool_away(loader):
    """The connection is healthy — MySQL rolled the transaction back itself.
    Disposing here would make six runners reconnect at once, on top of the
    contention that caused the deadlock."""
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _op_error(DEADLOCK)
        return "ok"

    loader._with_retry(op, "upsert batch")
    assert loader.engine.dispose_calls == 0


def test_a_lost_connection_does_throw_the_pool_away(loader):
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _op_error(CONN_LOST)
        return "ok"

    loader._with_retry(op, "upsert batch")
    assert loader.engine.dispose_calls == 1


def test_a_non_retryable_error_surfaces_immediately(loader):
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        raise _op_error(NOT_RETRYABLE)

    with pytest.raises(OperationalError):
        loader._with_retry(op, "upsert batch")
    assert calls["n"] == 1


def test_retries_are_bounded_and_the_last_error_propagates(loader):
    calls = {"n": 0}

    def op():
        calls["n"] += 1
        raise _op_error(DEADLOCK)

    with pytest.raises(OperationalError):
        loader._with_retry(op, "upsert batch")
    assert calls["n"] == loader.max_retries + 1     # initial try + retries


# ── backoff ─────────────────────────────────────────────────────────────────
def test_backoff_grows_and_is_capped():
    ldr = Loader(_FakeEngine(), retry_base_delay=1.0, retry_max_delay=8.0)
    assert all(0.5 <= ldr._backoff(0) <= 1.0 for _ in range(50))
    assert all(1.0 <= ldr._backoff(1) <= 2.0 for _ in range(50))
    assert all(4.0 <= ldr._backoff(10) <= 8.0 for _ in range(50))   # capped


def test_backoff_is_randomised_so_runners_stop_colliding():
    """Without jitter six runners wait the identical interval and collide again
    on the same rows — the retries become the thundering herd."""
    ldr = Loader(_FakeEngine(), retry_base_delay=1.0, retry_max_delay=60.0)
    assert len({ldr._backoff(3) for _ in range(200)}) > 100


# ── deterministic lock ordering ─────────────────────────────────────────────
def test_batches_are_sorted_by_the_conflict_key():
    """Two runners reaching the same barcodes in opposite orders is exactly how
    the deadlock forms. Sorting gives them one shared acquisition order."""
    a = [{"barcode": "300"}, {"barcode": "100"}, {"barcode": "200"}]
    b = [{"barcode": "200"}, {"barcode": "300"}, {"barcode": "100"}]
    assert _lock_ordered(a, ("barcode",)) == _lock_ordered(b, ("barcode",))


def test_ordering_uses_every_column_of_a_composite_key():
    rows = [
        {"product_id": 2, "store_id": 1},
        {"product_id": 1, "store_id": 9},
        {"product_id": 1, "store_id": 3},
    ]
    assert [
        (r["product_id"], r["store_id"]) for r in _lock_ordered(rows, ("product_id", "store_id"))
    ] == [(1, 3), (1, 9), (2, 1)]


def test_ordering_tolerates_nulls():
    """sub_chain_id is routinely empty/None; comparing it against a string would
    raise and take the whole load down."""
    rows = [{"k": "b"}, {"k": None}, {"k": "a"}]
    assert _lock_ordered(rows, ("k",))[0]["k"] is None


def test_ordering_does_not_drop_or_duplicate_rows():
    rows = [{"barcode": str(i % 7)} for i in range(40)]
    assert len(_lock_ordered(rows, ("barcode",))) == len(rows)
