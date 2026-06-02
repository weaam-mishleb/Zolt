"""Zolt ETL — parse the local dataset and upsert stores/products/prices.

Usage (from the project root):
    python -m etl.run                 # load the 3 chains into MySQL
    python -m etl.run --dry-run       # parse + validate only, no DB needed
    python -m etl.run --full          # use the full price catalog files
    python -m etl.run --chains shufersal --limit-rows 100000

Streaming with the stdlib `csv` module keeps memory flat (no pandas/DataFrame
overhead) even on the multi-hundred-MB price files; products and prices are
upserted in batches of `--batch-size`.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

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
    """Yield lists of row-dicts using the stdlib csv module (bounded, low memory).

    All columns stay as strings (barcodes / zero-padded codes preserved).
    Malformed lines are skipped, and the grouped identity columns are
    forward-filled across rows AND chunk boundaries (a store block may straddle
    a chunk), so the chunk size never affects correctness.
    """
    carry: dict[int, str] = {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return
        ncol = len(header)
        fill_idx = [i for i, name in enumerate(header) if name in GROUP_FILL_COLS]

        batch: list[dict] = []
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error:
                continue  # skip a malformed line, keep going (≈ on_bad_lines='skip')
            if len(row) != ncol:
                continue  # wrong field count → skip
            for i in fill_idx:  # forward-fill grouped chain/store identity columns
                if row[i].strip() in _EMPTY:
                    row[i] = carry.get(i, "")
                else:
                    carry[i] = row[i]
            batch.append({header[i]: row[i] for i in range(ncol)})
            if len(batch) >= chunksize:
                yield batch
                batch = []
        if batch:
            yield batch


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

        loader = Loader(
            engine,
            batch_size=args.batch_size,
            max_retries=getattr(args, "max_retries", 12),
            retry_base_delay=getattr(args, "retry_base_delay", 1.0),
            retry_max_delay=getattr(args, "retry_max_delay", 60.0),
        )

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
    max_retries: int = 12,
    retry_base_delay: float = 1.0,
    retry_max_delay: float = 60.0,
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
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            retry_max_delay=retry_max_delay,
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
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                   help="rows per multi-row INSERT (smaller = more WAN-friendly)")
    p.add_argument("--max-retries", type=int, default=12,
                   help="retries per batch on transient connection drops")
    p.add_argument("--retry-base-delay", type=float, default=1.0,
                   help="initial backoff seconds (doubles each retry)")
    p.add_argument("--retry-max-delay", type=float, default=60.0,
                   help="cap for the exponential backoff, seconds")
    run(p.parse_args())


if __name__ == "__main__":
    main()
