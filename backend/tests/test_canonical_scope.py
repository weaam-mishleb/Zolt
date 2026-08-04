"""Tests for scoping entity resolution to specific chains.

WHY THIS EXISTS
---------------
The ETL matrix runs one job per chain, and `etl.canonical` took no filter — so
six runners each resolved ALL products, computed identical groupings, and
upserted the same rows simultaneously. The redundant work was not the expensive
part; the lock contention was, and it eventually timed the workflow out.

The subtle risk in fixing it is that canonical products are supposed to be
SHARED across chains. These tests pin the invariant that makes scoping safe:
identity lives in the database's unique blocking key, not in one process happening
to see every chain at once.
"""
from __future__ import annotations

import pytest

from etl.canonical import (
    _REFRESH_MEMBERS,
    _REFRESH_MEMBERS_ALL,
    _SELECT_PRODUCTS,
    _SELECT_PRODUCTS_FOR_CHAINS,
    resolve,
)
from etl.config import chain_id_for


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, stmt, params=None):
        self.sink.append((str(stmt), params or {}))
        return self

    def mappings(self):
        return self

    def all(self):
        return []          # no rows → resolve() stops after one pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeEngine:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def connect(self):
        return _FakeConn(self.calls)

    def begin(self):
        return _FakeConn(self.calls)

    def dispose(self):
        pass


# ── which statement gets used ───────────────────────────────────────────────
def test_an_unscoped_run_still_selects_every_product():
    eng = _FakeEngine()
    resolve(eng, chunk=10)
    sql, params = eng.calls[0]
    assert "EXISTS" not in sql
    assert "chain_ids" not in params


def test_a_scoped_run_filters_on_the_chain():
    eng = _FakeEngine()
    resolve(eng, chunk=10, chain_ids=["7290058108879"])
    sql, params = eng.calls[0]
    assert "EXISTS" in sql
    assert params["chain_ids"] == ["7290058108879"]


def test_scoping_reaches_the_chain_through_prices_not_a_product_column():
    """`products` deliberately has no chain column — that is what lets one row
    serve every chain. The filter therefore has to go via prices → stores."""
    sql = str(_SELECT_PRODUCTS_FOR_CHAINS)
    assert "prices" in sql and "stores" in sql
    assert "s.chain_id IN" in sql


def test_several_chains_can_be_scoped_at_once():
    eng = _FakeEngine()
    resolve(eng, chunk=10, chain_ids=["111", "222"])
    assert eng.calls[0][1]["chain_ids"] == ["111", "222"]


def test_the_unscoped_select_is_untouched():
    assert "EXISTS" not in str(_SELECT_PRODUCTS)


# ── the member_count refresh ────────────────────────────────────────────────
def test_a_scoped_refresh_is_bounded_but_still_counts_globally():
    """It must rewrite only the rows this run touched — the unscoped statement
    rewrote all 178,810 canonical rows from every one of the 30 jobs. The COUNT
    stays global, because members come from every chain."""
    scoped = str(_REFRESH_MEMBERS)
    assert "WHERE c.id IN" in scoped
    # the subquery has no chain predicate: a scoped count would be wrong, not partial
    assert "chain_id" not in scoped
    assert "SELECT COUNT(*) FROM product_map m WHERE m.canonical_id = c.id" in scoped


def test_the_unscoped_refresh_still_covers_everything():
    assert "WHERE c.id IN" not in str(_REFRESH_MEMBERS_ALL)


# ── slug → chain_id ─────────────────────────────────────────────────────────
@pytest.fixture
def store_csv(tmp_path):
    def _make(slug: str, chain_id: str = "7290999000001"):
        p = tmp_path / f"store_file_{slug}.csv"
        p.write_text(f"chainid,storeid,storename\n{chain_id},1,סניף\n", encoding="utf-8")
        return tmp_path
    return _make


def test_chain_id_is_resolved_from_the_store_file(store_csv):
    d = store_csv("king_store", "7290058108879")
    assert chain_id_for("king_store", d) == "7290058108879"


def test_an_unmappable_slug_returns_none_so_the_caller_can_fail_loudly(tmp_path):
    """main() exits non-zero on this. Quietly falling back to resolving
    everything would put the job straight back into the contention that scoping
    exists to remove."""
    assert chain_id_for("nope", tmp_path) is None
