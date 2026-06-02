"""Zolt ETL — parse the local dataset and upsert stores/products/prices.

Usage (from the project root):
    python -m etl.run                 # load the 3 chains into MySQL
    python -m etl.run --dry-run       # parse + validate only, no DB needed
    python -m etl.run --full          # use the full price catalog files
    python -m etl.run --chains shufersal --limit-rows 100000

Streaming with pandas `chunksize` keeps memory flat on the multi-hundred-MB
price files; products and prices are upserted in batches of `--batch-size`.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

from .config import (
    BATCH_SIZE,
    CHAINS,
    CHUNK_SIZE,
    DEFAULT_DATA_DIR,
    GROUP_FILL_COLS,
    price_file,
    store_file,
)
from .normalize import PRODUCT_FIELDS, normalize_price, normalize_store

_EMPTY = ("", "''", '""')


def _read_csv_chunks(path: Path, chunksize: int):
    """Yield lists of row-dicts, forward-filling the grouped identity columns.

    All columns are read as strings (barcodes / zero-padded codes preserved).
    Because a store block can straddle a chunk boundary, the last known value
    of each fill column is carried into the next chunk.
    """
    carry: dict[str, object] = {}
    reader = pd.read_csv(
        path,
        chunksize=chunksize,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8",
        on_bad_lines="skip",
    )
    for chunk in reader:
        cols = [c for c in GROUP_FILL_COLS if c in chunk.columns]
        if cols:
            chunk[cols] = chunk[cols].replace(list(_EMPTY), pd.NA)
            first = chunk.index[0]
            for c in cols:  # seed leading blanks from the previous chunk
                if pd.isna(chunk.at[first, c]) and carry.get(c) is not None:
                    chunk.at[first, c] = carry[c]
            chunk[cols] = chunk[cols].ffill()
            last = chunk.index[-1]
            for c in cols:
                v = chunk.at[last, c]
                if not pd.isna(v):
                    carry[c] = v
            chunk = chunk.where(pd.notna(chunk), "")
        yield chunk.to_dict("records")


# ──────────────────────────── stores ────────────────────────────
def load_stores(data_dir: Path, chains: list[str], loader) -> dict[tuple[str, str], int]:
    """Upsert all store files, then return {(chain_id, store_code): store_id}."""
    rows: dict[tuple[str, str, str], dict] = {}
    for slug in chains:
        path = store_file(data_dir, slug)
        if not path.exists():
            print(f"  ! missing store file: {path.name} — skipping {slug}", file=sys.stderr)
            continue
        count = 0
        for batch in _read_csv_chunks(path, CHUNK_SIZE):
            for rec in batch:
                nr = normalize_store(rec, CHAINS[slug])
                if nr:
                    rows[(nr["chain_id"], nr["sub_chain_id"], nr["store_code"])] = nr
                    count += 1
        print(f"  · {slug:<10} stores parsed: {count}")

    store_rows = list(rows.values())
    if loader is not None:
        loader.upsert_stores(store_rows)
        return loader.load_store_map()

    # dry-run: synthesize ids keyed by (chain_id, store_code)
    return {(r["chain_id"], r["store_code"]): i for i, r in enumerate(store_rows, 1)}


# ──────────────────────────── prices ────────────────────────────
def load_prices(
    data_dir: Path,
    chains: list[str],
    loader,
    store_map: dict[tuple[str, str], int],
    *,
    full: bool,
    chunksize: int,
    limit_rows: int | None,
    stats: Counter,
) -> None:
    product_ids: dict[str, int] = {}  # barcode -> id (synthetic in dry-run)
    next_pid = 0

    for slug in chains:
        path = price_file(data_dir, slug, full=full)
        if not path.exists():
            print(f"  ! missing price file: {path.name} — skipping {slug}", file=sys.stderr)
            continue

        seen = 0
        chain_prices = 0
        for batch in _read_csv_chunks(path, chunksize):
            price_rows = []
            products: dict[str, dict] = {}
            for rec in batch:
                stats["rows_read"] += 1
                nr = normalize_price(rec)
                if nr is None:
                    stats["rows_bad"] += 1
                    continue
                products[nr["barcode"]] = {k: nr[k] for k in PRODUCT_FIELDS}
                price_rows.append(nr)

            # upsert products and resolve barcode -> id
            if loader is not None:
                loader.upsert_products(list(products.values()))
                missing = [b for b in products if b not in product_ids]
                product_ids.update(loader.load_product_ids(missing))
            else:
                for b in products:
                    if b not in product_ids:
                        next_pid += 1
                        product_ids[b] = next_pid

            # build price upserts, resolving store + product surrogate keys
            upserts = []
            for nr in price_rows:
                sid = store_map.get((nr["chain_id"], nr["store_code"]))
                if sid is None:
                    stats["price_no_store"] += 1
                    continue
                upserts.append(
                    {
                        "product_id": product_ids[nr["barcode"]],
                        "store_id": sid,
                        "price": nr["price"],
                        "unit_price": nr["unit_price"],
                        "allow_discount": nr["allow_discount"],
                        "item_status": nr["item_status"],
                        "price_update_time": nr["price_update_time"],
                    }
                )

            if loader is not None and upserts:
                loader.upsert_prices(upserts)

            stats["prices"] += len(upserts)
            chain_prices += len(upserts)
            seen += len(batch)
            if limit_rows and seen >= limit_rows:
                break

        print(f"  · {slug:<10} rows={seen:<9} prices upserted={chain_prices}")

    stats["products"] = len(product_ids)


def run(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    chains = args.chains or list(CHAINS)
    dry = args.dry_run

    loader = None
    if not dry:
        # Imported lazily so --dry-run works without DB drivers/connectivity.
        from backend.app.db import engine

        from .loader import Loader

        loader = Loader(engine, batch_size=args.batch_size)

    print(f"Zolt ETL — data_dir={data_dir}  chains={chains}  "
          f"{'DRY-RUN' if dry else 'DB'}  {'full' if args.full else 'snapshot'}")
    t0 = time.time()

    print("Stores:")
    store_map = load_stores(data_dir, chains, loader)
    print(f"  → stores in map: {len(store_map)}")

    print("Prices:")
    stats: Counter = Counter()
    load_prices(
        data_dir,
        chains,
        loader,
        store_map,
        full=args.full,
        chunksize=args.chunksize,
        limit_rows=args.limit_rows,
        stats=stats,
    )

    dt = time.time() - t0
    print("─" * 52)
    print(f"  rows read       : {stats['rows_read']:,}")
    print(f"  rows skipped    : {stats['rows_bad']:,} (unparsable)")
    print(f"  distinct products: {stats['products']:,}")
    print(f"  prices upserted : {stats['prices']:,}")
    print(f"  price w/o store : {stats['price_no_store']:,} (unmatched branch)")
    matched = stats["prices"]
    usable = matched + stats["price_no_store"]
    rate = (matched / usable * 100) if usable else 0.0
    print(f"  store-match rate: {rate:.1f}%")
    print(f"  elapsed         : {dt:.1f}s")
    if dry:
        print("  (dry-run — nothing written to the database)")


def run_pipeline(
    *,
    data_dir=None,
    chains: list[str] | None = None,
    full: bool = False,
    dry_run: bool = False,
    chunksize: int = CHUNK_SIZE,
    batch_size: int = BATCH_SIZE,
    limit_rows: int | None = None,
) -> None:
    """Programmatic entry point (used by the scheduler / admin trigger)."""
    run(
        argparse.Namespace(
            data_dir=str(data_dir or DEFAULT_DATA_DIR),
            chains=chains,
            full=full,
            dry_run=dry_run,
            chunksize=chunksize,
            batch_size=batch_size,
            limit_rows=limit_rows,
        )
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Zolt local ETL pipeline")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="folder with the CSV files")
    p.add_argument("--chains", nargs="*", choices=list(CHAINS), help="subset of chains")
    p.add_argument("--full", action="store_true", help="use price_full_file_*.csv")
    p.add_argument("--dry-run", action="store_true", help="parse & validate only, no DB writes")
    p.add_argument("--limit-rows", type=int, default=None, help="cap rows per chain (testing)")
    p.add_argument("--chunksize", type=int, default=CHUNK_SIZE)
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    run(p.parse_args())


if __name__ == "__main__":
    main()
