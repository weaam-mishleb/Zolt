"""Apply `canonical_merges` to the cities already stored in `stores`.

The ETL rewrites `stores.city` only when it runs, so a merge added to
etl/city_aliases.json fixes every FUTURE load and leaves the rows already in the
database split until the next one. This closes that gap.

Dry-run by default and prints every row it would touch. Nothing is written
without --apply, because a city string is what the comparison filter matches on:
getting one wrong does not raise, it just quietly prices a basket against the
wrong branches.

    python -m scripts.dedupe_store_cities            # report only
    python -m scripts.dedupe_store_cities --apply    # write it
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from backend.app.db import engine          # noqa: E402
from etl.cities import CANONICAL_MERGES, canonical_city   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    args = ap.parse_args()

    if not CANONICAL_MERGES:
        print("no canonical_merges loaded — check etl/city_aliases.json", file=sys.stderr)
        return 1

    with engine.connect() as conn:
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "SELECT city, COUNT(*) FROM stores "
                    "WHERE city IS NOT NULL AND city <> '' GROUP BY city"
                )
            ).all()
        }

    # Only the merges that actually match something in this database.
    live = {src: canonical_city(src) for src in CANONICAL_MERGES if counts.get(src)}
    if not live:
        print("nothing to do — no store carries a superseded spelling.")
        return 0

    moved = sum(counts[src] for src in live)
    healed = sum(1 for src, dst in live.items() if counts.get(dst))
    print(f"distinct city values now: {len(counts)}")
    print(f"merges that match a row:  {len(live)}  ({moved} stores would move)\n")
    print(f"{'from':<26}{'n':>5}   {'into':<22}{'already there':>14}   effect")
    print("-" * 88)
    for src, dst in sorted(live.items(), key=lambda kv: -counts[kv[0]]):
        there = counts.get(dst, 0)
        effect = "heals a split city" if there else "rename (no split)"
        print(f"{src:<26}{counts[src]:>5}   {dst:<22}{there:>14}   {effect}")
    print(f"\n{healed} of these heal a city that is currently divided in the filter.")
    print(f"distinct city values after: {len(counts) - len(live)}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write it.")
        return 0

    # One transaction: a half-applied merge would leave a city split differently
    # than it is now, which is harder to reason about than not having started.
    with engine.begin() as conn:
        total = 0
        for src, dst in live.items():
            total += conn.execute(
                text("UPDATE stores SET city = :dst WHERE city = :src"),
                {"dst": dst, "src": src},
            ).rowcount
    print(f"\napplied — {total} stores updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
