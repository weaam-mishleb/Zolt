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
import io
import os
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


def _read_csv_chunks(path: Path, chunksize: int, on_progress=None):
    """Yield lists of row-dicts using the stdlib csv module (bounded, low memory).

    All columns stay as strings (barcodes / zero-padded codes preserved).
    Malformed lines are skipped, and the grouped identity columns are
    forward-filled across rows AND chunk boundaries (a store block may straddle
    a chunk), so the chunk size never affects correctness.

    `on_progress(bytes_pos)` is called after each yielded chunk with the raw
    byte offset consumed so far (read-ahead makes it slightly ahead of the
    decoded position — close enough for a progress percentage).
    """
    carry: dict[int, str] = {}
    with open(path, "rb") as raw:
        fh = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
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
                if on_progress is not None:
                    on_progress(raw.tell())
                batch = []
        if batch:
            yield batch
        if on_progress is not None:
            on_progress(raw.tell())


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
def _merge_product(known: dict | None, incoming: dict) -> dict:
    """Combine two sightings of one barcode, preferring real values over blanks.

    The same product is published once per branch that sells it, and the
    branches do not agree: measured across Rami Levy, `unit_of_measure` differs
    between sightings of the same barcode in 35% of cases and `manufacturer` in
    6% — nearly always because one branch omits what another supplies. Taking
    the last sighting wholesale (what the plain upsert did) therefore threw
    away real metadata whenever the final branch happened to leave it blank.
    """
    if known is None:
        return dict(incoming)
    merged = dict(known)
    for field, value in incoming.items():
        if value is not None and value != "":
            merged[field] = value
    return merged


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
    progress=None,
) -> None:
    product_ids: dict[str, int] = {}  # barcode -> id (synthetic in dry-run)
    # barcode -> best-known attributes so far. A product arrives once per branch
    # it is sold in, so re-upserting it every time meant 1.15M writes for
    # Shufersal's 33,217 distinct products (34.7x) and 1.61M for Rami Levy's
    # 21,932 (73.3x). Keeping the merged view here means only genuine changes go
    # to the database: 52,868 and 102,081 writes respectively.
    product_attrs: dict[str, dict] = {}
    next_pid = 0

    # Byte-based progress: prices are ~99% of the work, so bytes consumed
    # across all price files are mapped onto the 1→99% range.
    paths = {slug: price_file(data_dir, slug, full=full) for slug in chains}
    total_bytes = sum(p.stat().st_size for p in paths.values() if p.exists()) or 1
    bytes_before = 0

    for slug in chains:
        path = paths[slug]
        if not path.exists():
            print(f"  ! missing price file: {path.name} — skipping {slug}", file=sys.stderr)
            continue

        on_progress = None
        if progress is not None:
            base, label = bytes_before, f"טוען מחירים: {CHAINS[slug]}"
            on_progress = lambda pos, base=base, label=label: progress.update(  # noqa: E731
                1 + int((base + pos) / total_bytes * 98), phase=label
            )

        seen = 0
        chain_prices = 0
        # One staging table for the WHOLE chain: the rows land in an unindexed
        # table as they stream, and the index maintenance happens once, in a
        # server-side merge that moves no data over the WAN.
        if loader is not None:
            from .loader import _PRICE_COLUMNS, _PRICES_STAGE_DDL
            loader.stage_begin("prices", _PRICES_STAGE_DDL, _PRICE_COLUMNS,
                               ("product_id", "store_id"))
        for batch in _read_csv_chunks(path, chunksize, on_progress):
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
                # Send a product only when what we know about it actually
                # changed. Branches disagree about the OPTIONAL fields — one
                # omits the manufacturer another supplies — so the sightings are
                # MERGED rather than replaced, which also stops a later NULL
                # from erasing a real value.
                changed = []
                for barcode, incoming in products.items():
                    previous = product_attrs.get(barcode)
                    merged = _merge_product(previous, incoming)
                    if merged != previous:
                        product_attrs[barcode] = merged
                        changed.append(merged)
                if changed:
                    loader.upsert_products(changed)
                missing = [b for b in products if b not in product_ids]
                if missing:
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
                loader.stage_add(upserts, "prices upsert")

            stats["prices"] += len(upserts)
            chain_prices += len(upserts)
            seen += len(batch)
            if limit_rows and seen >= limit_rows:
                break

        # End of the chain: one server-side merge, sliced into bounded
        # transactions. Nothing here crosses the WAN.
        if loader is not None:
            from .loader import _PRICE_UPDATE_COLUMNS
            loader.stage_commit(_PRICE_UPDATE_COLUMNS, "prices upsert")

        print(f"  · {slug:<10} rows={seen:<9} prices upserted={chain_prices}")
        bytes_before += path.stat().st_size

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

    # Track this run in etl_jobs so the admin panel can poll a live progress %.
    # A dispatched run (GitHub Actions) attaches to the row the backend created
    # (--job-id / $ETL_JOB_ID); otherwise a fresh row is created here.
    job = None
    if loader is not None:
        from .progress import ProgressReporter, create_job

        job_id = getattr(args, "job_id", None)
        if job_id is None:
            job_id = create_job(engine, source="cli", full=args.full)
        job = ProgressReporter(engine, job_id)
        job.start(phase="מאתחל את הריצה")
        print(f"  · progress tracked in etl_jobs id={job_id}")

    print(f"Zolt ETL — data_dir={data_dir}  chains={chains}  "
          f"{'DRY-RUN' if dry else 'DB'}  {'full' if args.full else 'snapshot'}")
    t0 = time.time()

    stats: Counter = Counter()
    try:
        print("Stores:")
        if job:
            job.update(1, "טוען סניפים")
        store_map = load_stores(data_dir, chains, loader)
        print(f"  → stores in map: {len(store_map)}")

        print("Prices:")
        load_prices(
            data_dir,
            chains,
            loader,
            store_map,
            full=args.full,
            chunksize=args.chunksize,
            limit_rows=args.limit_rows,
            stats=stats,
            progress=job,
        )
    except Exception as exc:
        if job:
            job.finish(False, f"{type(exc).__name__}: {exc}")
        raise

    dt = time.time() - t0
    if job:
        job.finish(
            True,
            f"{stats['prices']:,} prices · {stats['products']:,} products · {dt:.0f}s",
        )
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
    # Rows actually sent per table. `products upsert` should be close to the
    # distinct-product count, not to the row count — if it tracks rows read, the
    # per-branch repeats are being re-sent again and that is the whole cost.
    if loader is not None:
        print(f"  rows written    : {loader.write_summary()}")
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
    job_id: int | None = None,
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
            job_id=job_id,
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
    p.add_argument("--job-id", type=int, default=None,
                   help="existing etl_jobs row to report progress into "
                        "(default: $ETL_JOB_ID if set, else a new row is created)")
    args = p.parse_args()
    if args.job_id is None and os.environ.get("ETL_JOB_ID", "").strip():
        args.job_id = int(os.environ["ETL_JOB_ID"])
    run(args)


if __name__ == "__main__":
    main()
