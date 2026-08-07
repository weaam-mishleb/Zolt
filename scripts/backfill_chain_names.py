"""Apply the `display_names` overrides from etl/chains.json to `stores.chain_name`.

`normalize_store` applies them on every load, so this only exists to close the
same gap the city merge had: rows already in the database keep the old name until
the ETL next rewrites them.

Dry-run by default.

    python -m scripts.backfill_chain_names            # report only
    python -m scripts.backfill_chain_names --apply    # write it
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from backend.app.db import engine              # noqa: E402
from etl.config import CHAIN_DISPLAY_NAMES     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    args = ap.parse_args()

    if not CHAIN_DISPLAY_NAMES:
        print("no display_names configured in etl/chains.json — nothing to do.")
        return 0

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT chain_id, chain_name, COUNT(*) n FROM stores "
                "GROUP BY chain_id, chain_name ORDER BY n DESC"
            )
        ).all()

    plan = [
        (chain_id, current, CHAIN_DISPLAY_NAMES[chain_id], n)
        for chain_id, current, n in rows
        if chain_id in CHAIN_DISPLAY_NAMES and current != CHAIN_DISPLAY_NAMES[chain_id]
    ]
    if not plan:
        print("nothing to do — every configured chain already shows its display name.")
        return 0

    print(f"{'chain_id':<18}{'n':>5}   {'from':<32} -> to")
    print("-" * 84)
    for chain_id, current, target, n in plan:
        print(f"{chain_id:<18}{n:>5}   {current:<32} -> {target}")
    print(f"\n{sum(n for *_, n in plan)} stores would be renamed.")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write it.")
        return 0

    with engine.begin() as conn:
        total = 0
        for chain_id, _, target, _ in plan:
            total += conn.execute(
                text("UPDATE stores SET chain_name = :name WHERE chain_id = :cid"),
                {"name": target, "cid": chain_id},
            ).rowcount
    print(f"\napplied — {total} stores updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
