"""Batch upserts into MySQL via INSERT ... ON DUPLICATE KEY UPDATE.

The classic VALUES(col) form is used (not the 8.0.19 row-alias) so PyMySQL's
executemany can rewrite each group of rows into a single multi-row INSERT.

Resilient to flaky remote/cloud databases AND to six runners writing at once:
each batch is its OWN transaction, rows are ordered so concurrent writers take
locks in the same sequence, and the batch is retried with jittered backoff.
Because the upserts are idempotent, retrying a batch — even one that may have
partially committed — is always safe.
"""
from __future__ import annotations

import random
import sys
import time

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

# Retryable MySQL errors, split by what they MEAN — the right response differs,
# and treating them alike is what made the deadlocks fatal.
#
# Connection-level: the socket is gone. Every pooled connection is dead, so the
# pool has to be thrown away before a retry can possibly succeed.
#   2013 Lost connection during query · 2006 Server has gone away · 1053 Shutdown
_CONNECTION_LOST = {2013, 2006, 1053}

# Lock-level: the connection is perfectly healthy and MySQL has ALREADY rolled
# our transaction back — that is how it breaks the cycle. Only one thing is
# needed here: wait, then try again.
# Disposing the pool for these would be actively harmful, since six parallel
# runners would then all reconnect at once on top of the contention that caused
# the deadlock in the first place.
#   1213 Deadlock found · 1205 Lock wait timeout exceeded
_LOCK_CONTENTION = {1213, 1205}

_RETRYABLE = _CONNECTION_LOST | _LOCK_CONTENTION


def _mysql_errno(exc: BaseException) -> int | None:
    """The numeric MySQL error code inside a wrapped driver exception, if any."""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None)
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _lock_ordered(rows: list[dict], key_cols: tuple[str, ...]) -> list[dict]:
    """Sort a batch by its conflict key so every writer locks in one order.

    This is the fix that stops deadlocks happening, rather than surviving them.
    InnoDB locks each affected index entry as the multi-row INSERT walks the
    batch. If one runner's batch reaches barcode X then Y while another reaches
    Y then X, each holds what the other needs and MySQL kills one with 1213.
    Sorting on the unique key gives every runner the same acquisition order, so
    the cycle cannot form — the later writer simply waits.

    Retries below are still needed for the cases ordering cannot cover (gap
    locks, and contention with the price/store writers), but they stop being the
    load-bearing part.
    """
    # (is-not-None, value) keeps NULLs from being compared against real values;
    # within a single column the types are homogeneous, so this is total.
    return sorted(rows, key=lambda r: tuple((r.get(c) is not None, r.get(c)) for c in key_cols))

_STORE_UPSERT = text(
    """
    INSERT INTO stores
        (chain_id, chain_name, sub_chain_id, store_code,
         store_name, address, city, zip_code)
    VALUES
        (:chain_id, :chain_name, :sub_chain_id, :store_code,
         :store_name, :address, :city, :zip_code)
    ON DUPLICATE KEY UPDATE
        chain_name = VALUES(chain_name),
        store_name = VALUES(store_name),
        address    = VALUES(address),
        city       = VALUES(city),
        zip_code   = VALUES(zip_code)
    """
)

_PRODUCT_UPSERT = text(
    """
    INSERT INTO products
        (barcode, name, manufacturer, unit_qty, quantity, unit_of_measure, is_weighted)
    VALUES
        (:barcode, :name, :manufacturer, :unit_qty, :quantity, :unit_of_measure, :is_weighted)
    ON DUPLICATE KEY UPDATE
        name            = VALUES(name),
        manufacturer    = VALUES(manufacturer),
        unit_qty        = VALUES(unit_qty),
        quantity        = VALUES(quantity),
        unit_of_measure = VALUES(unit_of_measure),
        is_weighted     = VALUES(is_weighted)
    """
)

_PRICE_UPSERT = text(
    """
    INSERT INTO prices
        (product_id, store_id, price, unit_price, allow_discount,
         item_status, price_update_time)
    VALUES
        (:product_id, :store_id, :price, :unit_price, :allow_discount,
         :item_status, :price_update_time)
    ON DUPLICATE KEY UPDATE
        price             = VALUES(price),
        unit_price        = VALUES(unit_price),
        allow_discount    = VALUES(allow_discount),
        item_status       = VALUES(item_status),
        price_update_time = VALUES(price_update_time)
    """
)

_SELECT_IDS_BY_BARCODE = text("SELECT id, barcode FROM products WHERE barcode IN :bcs").bindparams(
    bindparam("bcs", expanding=True)
)


class Loader:
    """DB writer with per-batch transactions, deterministic lock ordering and
    jittered retry — built for a flaky WAN to a cloud DB *and* for six matrix
    runners upserting the same national barcodes at the same moment."""

    def __init__(
        self,
        engine: Engine,
        batch_size: int = 250,
        max_retries: int = 12,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 60.0,
    ):
        self.engine = engine
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay

    def _backoff(self, attempt: int) -> float:
        """Exponentially growing wait, randomised across its upper half.

        Plain exponential backoff is the wrong tool when the contention is
        BETWEEN six GitHub runners: they fail at the same moment, wait the same
        interval, and collide again on the same rows — the retries themselves
        become the thundering herd. Randomising the interval is what actually
        staggers them.

        Jittering only the upper half (rather than [0, window]) keeps a floor
        under every wait, so a hot batch cannot spin through its whole retry
        budget in a few milliseconds.
        """
        window = min(self.retry_base_delay * (2**attempt), self.retry_max_delay)
        return random.uniform(window / 2, window)

    def _with_retry(self, operation, what: str):
        """Run `operation()`, retrying the DB failures that are worth retrying.

        No explicit rollback appears here on purpose. `_commit_batch` runs inside
        `engine.begin()`, whose context manager rolls the transaction back when
        an exception leaves the block and returns a clean connection to the pool;
        on a deadlock MySQL has rolled it back already. Adding a manual
        `.rollback()` would act on a transaction that no longer exists.
        """
        for attempt in range(self.max_retries + 1):  # 1 initial try + max_retries
            try:
                return operation()
            except OperationalError as exc:
                errno = _mysql_errno(exc)
                if errno not in _RETRYABLE or attempt >= self.max_retries:
                    raise

                if errno in _CONNECTION_LOST:
                    # Dead socket: the pool holds unusable connections. Drop it.
                    # NOT done for lock errors — see _LOCK_CONTENTION above.
                    self.engine.dispose()

                delay = self._backoff(attempt)
                kind = "deadlock/lock wait" if errno in _LOCK_CONTENTION else "connection lost"
                print(
                    f"  ! {kind} ({errno}) during {what} — retry "
                    f"{attempt + 1}/{self.max_retries} in {delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)

    def run_with_retry(self, operation, what: str = "statement"):
        """Public entry point for callers that own their SQL but want this
        module's deadlock handling — `etl.canonical` writes canonical_products
        and product_map from every chain at once and needs exactly the same
        protection."""
        return self._with_retry(operation, what)

    def _commit_batch(self, sql, batch: list[dict]) -> None:
        # A fresh connection from the pool (pre-ping) + its own short transaction.
        # `begin()` commits on clean exit and rolls back on any exception.
        with self.engine.begin() as conn:
            conn.execute(sql, batch)

    def _executemany(self, sql, rows: list[dict], key_cols: tuple[str, ...]) -> None:
        rows = _lock_ordered(rows, key_cols)
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i : i + self.batch_size]
            self._with_retry(lambda b=batch: self._commit_batch(sql, b), "upsert batch")

    def upsert_stores(self, rows: list[dict]) -> None:
        self._executemany(_STORE_UPSERT, rows, ("chain_id", "sub_chain_id", "store_code"))

    def upsert_products(self, rows: list[dict]) -> None:
        # The hot one: every chain re-inserts the same national barcodes.
        self._executemany(_PRODUCT_UPSERT, rows, ("barcode",))

    def upsert_prices(self, rows: list[dict]) -> None:
        self._executemany(_PRICE_UPSERT, rows, ("product_id", "store_id"))

    def load_store_map(self) -> dict[tuple[str, str], int]:
        """{(chain_id, store_code): store_id} — store_code already normalized."""

        def op():
            with self.engine.connect() as conn:
                rows = conn.execute(text("SELECT id, chain_id, store_code FROM stores"))
                return {(chain_id, store_code): sid for sid, chain_id, store_code in rows}

        return self._with_retry(op, "load_store_map")

    def load_product_ids(self, barcodes: list[str]) -> dict[str, int]:
        """{barcode: product_id} for the requested barcodes (queried in batches)."""
        out: dict[str, int] = {}
        for i in range(0, len(barcodes), self.batch_size):
            chunk = barcodes[i : i + self.batch_size]

            def op(c=chunk):
                with self.engine.connect() as conn:
                    return list(conn.execute(_SELECT_IDS_BY_BARCODE, {"bcs": c}))

            for pid, barcode in self._with_retry(op, "load_product_ids"):
                out[barcode] = pid
        return out
