"""Batch upserts into MySQL via INSERT ... ON DUPLICATE KEY UPDATE.

The classic VALUES(col) form is used (not the 8.0.19 row-alias) so PyMySQL's
executemany can rewrite each group of rows into a single multi-row INSERT.

Resilient to flaky remote/cloud databases: each batch is its OWN transaction and
is retried with exponential backoff on transient connection drops (MySQL errors
2013 / 2006 / 1053). Because the upserts are idempotent, retrying a batch — even
one that may have partially committed — is always safe.
"""
from __future__ import annotations

import sys
import time

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

# MySQL client error codes for a dropped / gone-away connection — safe to retry.
#   2013 Lost connection during query · 2006 Server has gone away · 1053 Shutdown
_TRANSIENT_CODES = {2013, 2006, 1053}


def _is_transient(exc: BaseException) -> bool:
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None)
    return bool(args) and args[0] in _TRANSIENT_CODES

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
    """DB writer with per-batch transactions and automatic retry on transient
    connection loss — built for uploading over a flaky WAN to a cloud DB."""

    def __init__(
        self,
        engine: Engine,
        batch_size: int = 500,
        max_retries: int = 5,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 30.0,
    ):
        self.engine = engine
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay

    def _with_retry(self, operation, what: str):
        """Run `operation()`, retrying transient DB drops with exponential backoff."""
        delay = self.retry_base_delay
        for attempt in range(self.max_retries + 1):  # 1 initial try + max_retries
            try:
                return operation()
            except OperationalError as exc:
                if attempt >= self.max_retries or not _is_transient(exc):
                    raise
                code = exc.orig.args[0] if getattr(exc, "orig", None) else "?"
                print(
                    f"  ! transient DB error {code} during {what} — retry "
                    f"{attempt + 1}/{self.max_retries} in {delay:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay = min(delay * 2, self.retry_max_delay)

    def _commit_batch(self, sql, batch: list[dict]) -> None:
        # a fresh connection from the pool (pre-ping) + its own short transaction
        with self.engine.begin() as conn:
            conn.execute(sql, batch)

    def _executemany(self, sql, rows: list[dict]) -> None:
        for i in range(0, len(rows), self.batch_size):
            batch = rows[i : i + self.batch_size]
            self._with_retry(lambda b=batch: self._commit_batch(sql, b), "upsert batch")

    def upsert_stores(self, rows: list[dict]) -> None:
        self._executemany(_STORE_UPSERT, rows)

    def upsert_products(self, rows: list[dict]) -> None:
        self._executemany(_PRODUCT_UPSERT, rows)

    def upsert_prices(self, rows: list[dict]) -> None:
        self._executemany(_PRICE_UPSERT, rows)

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
