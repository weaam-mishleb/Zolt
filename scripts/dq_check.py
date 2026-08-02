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
import csv
import sys
from pathlib import Path

from sqlalchemy import text

from etl.config import DEFAULT_DATA_DIR, store_file

# A chain with fewer prices than this almost certainly failed to load properly;
# the smallest real chain in the feed carries thousands of rows.
MIN_PRICES_PER_CHAIN = 100
MIN_STORES_PER_CHAIN = 1
# Share of zero/negative prices tolerated before the whole database is flagged.
MAX_BAD_PRICE_SHARE = 0.001   # 0.1%


def chain_id_for(slug: str, data_dir: Path) -> str | None:
    """Read the numeric chain id from the chain's own store file.

    The DB keys chains by the feed's numeric id, not by our slug, so the file is
    the only authoritative bridge between the two.
    """
    path = store_file(data_dir, slug)
    if not path.exists():
        return None
    csv.field_size_limit(10**9)
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            cid = (row.get("chainid") or "").strip()
            if cid and cid.lower() not in {"none", "null", ""}:
                return cid
    return None


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

    with engine.connect() as conn:
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

    if stores < MIN_STORES_PER_CHAIN:
        problems.append(f"no stores loaded for chain_id={cid} (expected ≥{MIN_STORES_PER_CHAIN})")
    if prices < MIN_PRICES_PER_CHAIN:
        problems.append(
            f"only {prices:,} prices for chain_id={cid} "
            f"(expected ≥{MIN_PRICES_PER_CHAIN:,}) — the load produced no usable data"
        )

    if not problems:
        print(f"  ✓ {slug}: chain_id={cid} · {stores:,} stores · {prices:,} prices", flush=True)
    return problems


def check_all(engine) -> list[str]:
    """Whole-database sanity, for a post-run summary."""
    problems: list[str] = []
    with engine.connect() as conn:
        chains = conn.execute(text("SELECT COUNT(DISTINCT chain_id) FROM stores")).scalar()
        stores = conn.execute(text("SELECT COUNT(*) FROM stores")).scalar()
        prices = conn.execute(text("SELECT COUNT(*) FROM prices")).scalar()
        orphan = conn.execute(
            text("SELECT COUNT(*) FROM prices p LEFT JOIN stores s ON s.id = p.store_id "
                 "WHERE s.id IS NULL")
        ).scalar()
        negative = conn.execute(text("SELECT COUNT(*) FROM prices WHERE price <= 0")).scalar()

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
