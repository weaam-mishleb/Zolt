"""Batch upserts into MySQL via INSERT ... ON DUPLICATE KEY UPDATE.

The classic VALUES(col) form is used (not the 8.0.19 row-alias) so PyMySQL's
executemany can rewrite each group of rows into a single multi-row INSERT —
the fast path for bulk upserts.
"""
from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

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
    """Thin DB-writer. Each upsert runs its batches inside one transaction."""

    def __init__(self, engine: Engine, batch_size: int = 1000):
        self.engine = engine
        self.batch_size = batch_size

    def _executemany(self, sql, rows: list[dict]) -> None:
        if not rows:
            return
        with self.engine.begin() as conn:
            for i in range(0, len(rows), self.batch_size):
                conn.execute(sql, rows[i : i + self.batch_size])

    def upsert_stores(self, rows: list[dict]) -> None:
        self._executemany(_STORE_UPSERT, rows)

    def upsert_products(self, rows: list[dict]) -> None:
        self._executemany(_PRODUCT_UPSERT, rows)

    def upsert_prices(self, rows: list[dict]) -> None:
        self._executemany(_PRICE_UPSERT, rows)

    def load_store_map(self) -> dict[tuple[str, str], int]:
        """{(chain_id, store_code): store_id} — store_code already normalized."""
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id, chain_id, store_code FROM stores"))
            return {(chain_id, store_code): sid for sid, chain_id, store_code in rows}

    def load_product_ids(self, barcodes: list[str]) -> dict[str, int]:
        """{barcode: product_id} for the requested barcodes (queried in batches)."""
        out: dict[str, int] = {}
        if not barcodes:
            return out
        with self.engine.connect() as conn:
            for i in range(0, len(barcodes), self.batch_size):
                chunk = barcodes[i : i + self.batch_size]
                rows = conn.execute(_SELECT_IDS_BY_BARCODE, {"bcs": chunk})
                for pid, barcode in rows:
                    out[barcode] = pid
        return out
