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

from sqlalchemy import bindparam, text

sys.path.insert(0, ".")

from backend.app.db import engine          # noqa: E402
from etl.cities import CANONICAL_MERGES, CONDITIONAL_MERGES, canonical_city   # noqa: E402


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
        # Rows are resolved INDIVIDUALLY, not per city string: a conditional merge
        # ("חצור" is either חצור הגלילית or חצור-אשדוד) reads that row's own
        # address, so a blanket UPDATE ... WHERE city = 'חצור' would file half of
        # them in the wrong town — the exact failure this whole change is about.
        rows = conn.execute(
            text(
                "SELECT id, city, address FROM stores "
                "WHERE city IS NOT NULL AND city <> '' AND city IN :srcs"
            ).bindparams(bindparam("srcs", expanding=True)),
            {"srcs": list(CANONICAL_MERGES)},
        ).all()

    plan: list[tuple[int, str, str]] = []       # (store_id, from, to)
    for store_id, city, address in rows:
        dst = canonical_city(city, address)
        if dst and dst != city:
            plan.append((store_id, city, dst))
    if not plan:
        print("nothing to do — no store carries a superseded spelling.")
        return 0

    pairs: dict[tuple[str, str], int] = {}
    for _, src, dst in plan:
        pairs[(src, dst)] = pairs.get((src, dst), 0) + 1

    retired = {src for src, _ in pairs}
    print(f"distinct city values now: {len(counts)}")
    print(f"stores to move:           {len(plan)}  across {len(pairs)} mappings\n")
    print(f"{'from':<26}{'n':>5}   {'into':<26}{'already there':>14}   effect")
    print("-" * 92)
    for (src, dst), n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        there = counts.get(dst, 0)
        effect = "heals a split city" if there else "rename (no split)"
        marker = " *" if src in CONDITIONAL_MERGES else ""
        print(f"{src + marker:<26}{n:>5}   {dst:<26}{there:>14}   {effect}")
    healed = sum(1 for (_, dst) in pairs if counts.get(dst))
    if any(src in CONDITIONAL_MERGES for src, _ in pairs):
        print("\n  * resolved per store from that store's own address.")
    print(f"\n{healed} of these mappings heal a city currently divided in the filter.")
    # Retiring a spelling does not always remove a value: a rename whose target is
    # not yet in the database trades one distinct value for another. Subtracting
    # the retired names alone under-counts by exactly those, so add the targets
    # back in rather than reporting a number the apply step will not produce.
    after = (set(counts) - retired) | {dst for _, dst in pairs}
    print(f"distinct city values after: {len(after)}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write it.")
        return 0

    # One transaction: a half-applied merge would leave a city split differently
    # than it is now, which is harder to reason about than not having started.
    with engine.begin() as conn:
        total = 0
        for store_id, _, dst in plan:
            total += conn.execute(
                text("UPDATE stores SET city = :dst WHERE id = :id"),
                {"dst": dst, "id": store_id},
            ).rowcount
    print(f"\napplied — {total} stores updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
