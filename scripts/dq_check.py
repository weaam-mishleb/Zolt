"""Data-quality gate — fail a chain's load loudly instead of silently.

WHY THIS EXISTS
---------------
A nationwide run once reported 26 green jobs while loading **nothing**: the
Kaggle download failed, the loader printed "missing price file" to stderr and
exited 0, and every job went green. `etl_jobs` recorded the truth —
"0 prices · 0 products · 1s" — but nobody was looking.

Tolerating a missing file is right (one absent promo file must not kill a
chain). Tolerating an EMPTY LOAD is not. This gate draws that line: after a
chain loads, it must be able to see that chain's data in the database, or the
job fails.

Usage (one chain, in the ETL matrix):
    python -m scripts.dq_check --chain shufersal
Usage (whole DB, after a full run):
    python -m scripts.dq_check --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

# chain_id_for lives in etl.config because etl.canonical needs the same
# slug → chain_id bridge; re-exported here so `scripts.dq_check.chain_id_for`
# keeps working for existing callers and tests.
from etl.config import DEFAULT_DATA_DIR, chain_id_for, store_file

# A chain with fewer prices than this almost certainly failed to load properly;
# the smallest real chain in the feed carries thousands of rows.
MIN_PRICES_PER_CHAIN = 100
MIN_STORES_PER_CHAIN = 1
# Share of zero/negative prices tolerated before the whole database is flagged.
MAX_BAD_PRICE_SHARE = 0.001   # 0.1%

# Share of a chain's products allowed to be named after their own item code.
#
# `normalize_price` falls back to the barcode when a row carries no name, so a
# chain whose name column we failed to read loads at ~100% barcode-named: right
# row count, right price count, every existing gate green — and worthless, since
# all matching here is by name. Measured across the 33-chain snapshot, a chain
# read correctly sits at 0.0–3.0%; the failure signature is 100%. 25% is clear
# of the noise and nowhere near the failure.
MAX_BARCODE_NAME_SHARE = 0.25


def _with_db_retry(engine, fn):
    """Run a read through the loader's retry policy.

    Writes have been protected for a while; reads were not, and this gate is the
    LAST step of a chain's job — the moment the server is most loaded. Reusing
    Loader keeps one definition of what is worth retrying (deadlocks, lock
    waits, dropped connections) rather than a second, drifting copy.
    """
    from etl.loader import Loader

    return Loader(engine).run_with_retry(lambda: _call(engine, fn), "data-quality read")


def _call(engine, fn):
    with engine.connect() as conn:
        return fn(conn)


def check_chain(engine, slug: str, data_dir: Path) -> list[str]:
    """Return a list of failures for this chain (empty list = healthy)."""
    problems: list[str] = []

    cid = chain_id_for(slug, data_dir)
    if cid is None:
        return [
            f"cannot determine chain_id for '{slug}' — "
            f"{store_file(data_dir, slug).name} is missing or empty "
            f"(did the download actually succeed?)"
        ]

    # Retried like every other DB call in the pipeline. This gate runs LAST,
    # when up to four sibling runners have been hammering the same server for
    # an hour — an unretried blip here paints a red X on a chain whose data
    # loaded perfectly.
    def _read(conn):
        stores = conn.execute(
            text("SELECT COUNT(*) FROM stores WHERE chain_id = :c"), {"c": cid}
        ).scalar()
        prices = conn.execute(
            text(
                "SELECT COUNT(*) FROM prices p JOIN stores s ON s.id = p.store_id "
                "WHERE s.chain_id = :c"
            ),
            {"c": cid},
        ).scalar()
        # AVG over a boolean gives the share directly. NULL when the chain has no
        # products at all — the empty-load checks below already cover that case.
        barcode_named = conn.execute(
            text(
                "SELECT AVG(pd.name = pd.barcode) FROM ("
                "  SELECT DISTINCT pr.product_id AS pid FROM prices pr"
                "  JOIN stores s ON s.id = pr.store_id WHERE s.chain_id = :c"
                ") t JOIN products pd ON pd.id = t.pid"
            ),
            {"c": cid},
        ).scalar()
        return stores, prices, barcode_named

    stores, prices, barcode_named = _with_db_retry(engine, _read)

    if barcode_named is not None and float(barcode_named) > MAX_BARCODE_NAME_SHARE:
        problems.append(
            f"{float(barcode_named):.1%} of this chain's products are named after their own "
            f"item code (max {MAX_BARCODE_NAME_SHARE:.0%}) — the feed's product-name column "
            f"was not read. Check the aliases in etl/normalize.py against this chain's header"
        )

    if stores < MIN_STORES_PER_CHAIN:
        problems.append(f"no stores loaded for chain_id={cid} (expected ≥{MIN_STORES_PER_CHAIN})")
    if prices < MIN_PRICES_PER_CHAIN:
        problems.append(
            f"only {prices:,} prices for chain_id={cid} "
            f"(expected ≥{MIN_PRICES_PER_CHAIN:,}) — the load produced no usable data"
        )

    if not problems:
        named = "" if barcode_named is None else f" · {1 - float(barcode_named):.1%} named"
        print(
            f"  ✓ {slug}: chain_id={cid} · {stores:,} stores · {prices:,} prices{named}",
            flush=True,
        )
    return problems


def check_all(engine) -> list[str]:
    """Whole-database sanity, for a post-run summary."""
    problems: list[str] = []

    def _read(conn):
        chains = conn.execute(text("SELECT COUNT(DISTINCT chain_id) FROM stores")).scalar()
        stores = conn.execute(text("SELECT COUNT(*) FROM stores")).scalar()
        prices = conn.execute(text("SELECT COUNT(*) FROM prices")).scalar()
        orphan = conn.execute(
            text("SELECT COUNT(*) FROM prices p LEFT JOIN stores s ON s.id = p.store_id "
                 "WHERE s.id IS NULL")
        ).scalar()
        negative = conn.execute(text("SELECT COUNT(*) FROM prices WHERE price <= 0")).scalar()
        return chains, stores, prices, orphan, negative

    chains, stores, prices, orphan, negative = _with_db_retry(engine, _read)

    print(f"  chains={chains}  stores={stores:,}  prices={prices:,}")
    if prices < MIN_PRICES_PER_CHAIN:
        problems.append("the database holds essentially no prices")
    if orphan:
        problems.append(f"{orphan:,} prices reference a store that does not exist")

    # Bad prices are a RATIO check, not an absolute one. The feed always carries
    # a handful of junk rows (48 in ~2M = 0.002%); failing the run on those
    # would train everyone to ignore this gate. Fail only on a real spike.
    if negative:
        share = negative / prices if prices else 1.0
        msg = f"{negative:,} prices are zero or negative ({share:.3%})"
        if share > MAX_BAD_PRICE_SHARE:
            problems.append(msg)
        else:
            print(f"  ⚠ {msg} — below the {MAX_BAD_PRICE_SHARE:.1%} threshold, not failing")
    return problems


def main() -> None:
    p = argparse.ArgumentParser(description="Zolt data-quality gate")
    p.add_argument("--chain", help="slug to verify (as used by the ETL matrix)")
    p.add_argument("--all", action="store_true", help="whole-database checks")
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args()

    if not args.chain and not args.all:
        p.error("pass --chain <slug> or --all")

    from backend.app.db import engine

    problems: list[str] = []
    if args.chain:
        problems += check_chain(engine, args.chain, Path(args.data_dir))
    if args.all:
        problems += check_all(engine)

    if problems:
        target = args.chain or "database"
        print(f"\n❌ data-quality gate FAILED for {target}:", file=sys.stderr)
        for msg in problems:
            print(f"   · {msg}", file=sys.stderr)
        # A red job is the whole point: a silent empty load is worse than a
        # loud failure, because it looks like success.
        sys.exit(1)

    print("✅ data-quality gate passed", flush=True)


if __name__ == "__main__":
    main()
