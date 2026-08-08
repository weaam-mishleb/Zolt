"""Tests for the staging-table bulk merge.

WHY THIS EXISTS
---------------
The production server has a 1,024 MB InnoDB buffer pool and a 1,619 MB working
set (`prices` alone is 1,068 MB), so every upsert probes an index that is not
cached and throughput collapsed to ~300 rows/s.

Staging splits that work in two. Rows land in a table with NO unique key, NO
secondary index and NO foreign keys — append-only writes that never probe a cold
index — and the index maintenance happens once, in a server-side merge that
moves no data across the WAN.

The shape matters: prices arrive as a stream of ~5,000-row chunks, so this
cannot be a per-call decision. A first attempt put a size threshold inside
`upsert_prices` and it never once triggered, because no single chunk was ever
large enough. Hence an explicit session spanning the whole chain.
"""
from __future__ import annotations

import pytest

from etl.loader import _PRICE_COLUMNS, _PRICE_UPDATE_COLUMNS, _PRICES_STAGE_DDL, Loader


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Conn:
    def __init__(self, engine, connection_id):
        self.engine = engine
        self.log = engine.log
        self.connection_id = connection_id
        self.closed = False

    def execute(self, sql, params=None):
        self.log.append((" ".join(str(sql).split()), params))
        self.engine.connection_ids.append(self.connection_id)
        return self

    def begin(self):
        return _Transaction(self)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Engine:
    def __init__(self):
        self.log: list[tuple[str, object]] = []
        self.connection_ids: list[int] = []
        self.connections: list[_Conn] = []

    def _connection(self, connection_class=_Conn):
        conn = connection_class(self, len(self.connections) + 1)
        self.connections.append(conn)
        return conn

    def begin(self):
        return self._connection()

    def connect(self):
        return self._connection()

    def execution_options(self, **_kw):
        return self

    def dispose(self):
        pass


@pytest.fixture
def loader():
    return Loader(_Engine(), batch_size=100, retry_base_delay=0.001)


def _sql(loader):
    return [s for s, _ in loader.engine.log]


def test_bulk_upsert_is_strictly_sliced_into_5000_row_transactions():
    """A full promotion catalogue can approach one million item links.

    The caller may hand Loader the whole extracted chunk, but no individual
    executemany transaction may grow past the promotion loader's 5,000-row
    boundary: bounded packets, bounded undo/locks, and bounded retry cost.
    """
    loader = Loader(_Engine(), batch_size=5_000, retry_base_delay=0.001)
    rows = [{"promotion_id": i, "canonical_id": i, "is_gift": 0} for i in range(12_001)]

    loader.upsert_many(
        "INSERT IGNORE INTO promotion_items VALUES (...) ",
        rows,
        ("promotion_id", "canonical_id", "is_gift"),
        "promotion_items upsert",
    )

    batches = [params for _sql, params in loader.engine.log]
    assert [len(batch) for batch in batches] == [5_000, 5_000, 2_001]
    assert loader.rows_written["promotion_items upsert"] == 12_001


# ── the staging table itself ────────────────────────────────────────────────
def test_the_staging_table_has_no_index_no_unique_key_and_no_foreign_keys():
    """That absence IS the optimisation. An index here would reintroduce exactly
    the cold-index probing the staging path exists to avoid."""
    ddl = _PRICES_STAGE_DDL.upper()
    assert "UNIQUE" not in ddl
    assert "FOREIGN KEY" not in ddl
    assert "KEY IDX" not in ddl
    assert "CREATE TEMPORARY TABLE" in ddl
    # …except the surrogate PK, which exists only to slice the merge.
    assert "SEQ" in ddl and "AUTO_INCREMENT" in ddl


def test_staging_columns_match_what_the_merge_writes():
    assert set(_PRICE_UPDATE_COLUMNS) < set(_PRICE_COLUMNS)
    for col in _PRICE_COLUMNS:
        assert col.upper() in _PRICES_STAGE_DDL.upper()
    # the key columns are inserted but never updated — updating them is a no-op
    assert "product_id" not in _PRICE_UPDATE_COLUMNS
    assert "store_id" not in _PRICE_UPDATE_COLUMNS


# ── the session ─────────────────────────────────────────────────────────────
def test_a_session_creates_stages_merges_and_drops(loader):
    loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    rows = [{c: 1 for c in _PRICE_COLUMNS} for _ in range(250)]
    loader.stage_add(rows, "prices upsert")
    loader.stage_commit(_PRICE_UPDATE_COLUMNS, "prices upsert")

    sql = _sql(loader)
    assert any(s.startswith("CREATE TEMPORARY TABLE _stage_prices_") for s in sql)
    assert any(s.startswith("INSERT INTO _stage_prices_") for s in sql)
    assert any("INSERT INTO prices" in s and "SELECT" in s for s in sql)
    assert sql[-1].startswith("DROP TEMPORARY TABLE IF EXISTS _stage_prices_")


def test_every_staging_statement_uses_one_pinned_connection(loader):
    """TEMPORARY tables disappear if any batch rotates to another pool slot."""
    loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    pinned = loader._stage["connection"]
    loader.stage_add([{c: 1 for c in _PRICE_COLUMNS} for _ in range(250)], "prices upsert")
    loader.stage_commit(
        _PRICE_UPDATE_COLUMNS,
        "prices upsert",
        post_merge=("SELECT COUNT(*) FROM {stage}",),
    )

    # setup + 3 staged batches + 3 merge slices + post-merge + cleanup
    assert len(set(loader.engine.connection_ids)) == 1
    assert pinned.closed


def test_the_merge_reads_the_staging_table_not_the_client(loader):
    """The point of the merge phase: the rows never cross the WAN twice."""
    loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    loader.stage_add([{c: 1 for c in _PRICE_COLUMNS}], "prices upsert")
    loader.stage_commit(_PRICE_UPDATE_COLUMNS, "prices upsert")

    merge = next(s for s in _sql(loader) if s.startswith("INSERT INTO prices") and "SELECT" in s)
    assert "FROM _stage_prices_" in merge
    assert "ON DUPLICATE KEY UPDATE" in merge
    assert "WHERE seq BETWEEN" in merge          # sliced, not one giant statement


def test_the_merge_is_sliced_into_bounded_transactions(loader):
    """A single INSERT ... SELECT over 1.3M rows is one transaction with an
    enormous undo log holding locks for minutes — the long-transaction mistake
    that made the promotion writes deadlock."""
    loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    loader.stage_add([{c: 1 for c in _PRICE_COLUMNS} for _ in range(500)], "prices upsert")
    loader.stage_commit(_PRICE_UPDATE_COLUMNS, "prices upsert")

    slices = [p for s, p in loader.engine.log if p and "lo" in p]
    assert len(slices) == 5                                   # 500 rows / batch_size 100
    assert [p["lo"] for p in slices] == [1, 101, 201, 301, 401]


def test_the_staging_table_is_dropped_even_when_the_merge_fails():
    """A leftover staging table is invisible to the app and would quietly eat
    the buffer pool this path exists to protect."""
    class _BoomConn(_Conn):
        def execute(self, sql, params=None):
            if " ".join(str(sql).split()).startswith("INSERT INTO prices"):
                raise RuntimeError("merge exploded")
            return super().execute(sql, params)

    class _Boom(_Engine):
        def connect(self):
            return self._connection(_BoomConn)

    ldr = Loader(_Boom(), batch_size=100, retry_base_delay=0.001)
    ldr.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    ldr.stage_add([{c: 1 for c in _PRICE_COLUMNS}], "prices upsert")
    with pytest.raises(RuntimeError):
        ldr.stage_commit(_PRICE_UPDATE_COLUMNS, "prices upsert")
    assert ldr._stage is None


def test_an_empty_chain_still_cleans_up_and_merges_nothing(loader):
    loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    loader.stage_commit(_PRICE_UPDATE_COLUMNS, "prices upsert")
    sql = _sql(loader)
    assert not any("INSERT INTO prices" in s for s in sql)
    assert sql[-1].startswith("DROP TEMPORARY TABLE IF EXISTS")


def test_rows_are_staged_in_the_conflict_key_order(loader):
    """Sorted before staging so the MERGE walks the target's unique index in
    order instead of jumping around it."""
    loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS, ("product_id", "store_id"))
    rows = [{**{c: 0 for c in _PRICE_COLUMNS}, "product_id": p, "store_id": 1}
            for p in (5, 1, 3)]
    loader.stage_add(rows, "prices upsert")
    staged = next(p for s, p in loader.engine.log if s.startswith("INSERT INTO _stage_"))
    assert [r["product_id"] for r in staged] == [1, 3, 5]


def test_two_processes_do_not_share_a_staging_table(loader):
    assert str(__import__("os").getpid()) in loader._stage_name("prices")
