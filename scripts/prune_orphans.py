"""Delete canonical products that nothing maps to any more.

WHY THIS EXISTS
---------------
`etl.canonical` only ever upserts. Nothing removes a canonical row once its last
member product disappears — and members disappear routinely, because a reload
that changes how barcodes are keyed replaces `products` rows, which cascades
`product_map` away and strands the canonical behind it.

Measured on production: 299,922 of 487,891 canonical rows (61.5%) had no product
mapping to them at all, and 928,197 promotion_items — 70% of that table — pointed
at those dead canonicals.

That is not a tidiness problem, it is a throughput problem. The instance has a
1,024 MB InnoDB buffer pool and a 1,619 MB working set, so upserts miss the pool
and go to disk on nearly every batch; ~237 MB of what it is failing to cache is
rows nothing can ever read. Deleting them is the cheapest RAM available.

Deleting a canonical cascades to promotion_items and product_map (both ON DELETE
CASCADE), so nothing is left dangling. A promotion link pointing at a canonical
no product maps to could never resolve to a price anyway.

Safe to re-run: after the first pass there is nothing left to match.

Usage:
    python -m scripts.prune_orphans            # dry run, changes nothing
    python -m scripts.prune_orphans --apply    # delete
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

# Deleted in bounded slices: one 300k-row DELETE on a server whose working set
# already does not fit in RAM is exactly the kind of long transaction that
# caused the trouble in the first place.
CHUNK = 5_000

_ORPHAN_IDS = text(
    """
    SELECT c.id FROM canonical_products c
    WHERE NOT EXISTS (SELECT 1 FROM product_map m WHERE m.canonical_id = c.id)
    LIMIT :chunk
    """
)

_COUNT_ORPHANS = text(
    """
    SELECT COUNT(*) FROM canonical_products c
    WHERE NOT EXISTS (SELECT 1 FROM product_map m WHERE m.canonical_id = c.id)
    """
)

_COUNT_DEAD_LINKS = text(
    """
    SELECT COUNT(*) FROM promotion_items pi
    WHERE NOT EXISTS (SELECT 1 FROM product_map m WHERE m.canonical_id = pi.canonical_id)
    """
)

_DELETE = text("DELETE FROM canonical_products WHERE id IN :ids").bindparams(
    __import__("sqlalchemy").bindparam("ids", expanding=True)
)


def survey(conn) -> tuple[int, int, int]:
    """(orphaned canonicals, dead promo links, total canonicals)."""
    return (
        conn.execute(_COUNT_ORPHANS).scalar(),
        conn.execute(_COUNT_DEAD_LINKS).scalar(),
        conn.execute(text("SELECT COUNT(*) FROM canonical_products")).scalar(),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Remove canonical products nothing maps to")
    p.add_argument("--apply", action="store_true", help="perform the delete")
    p.add_argument("--chunk", type=int, default=CHUNK)
    args = p.parse_args()

    from backend.app.db import engine

    with engine.connect() as conn:
        orphans, dead_links, total = survey(conn)

    share = (orphans / total * 100) if total else 0.0
    print(f"canonical_products      : {total:,}")
    print(f"  orphaned (no member)  : {orphans:,}  ({share:.1f}%)")
    print(f"  promotion_items freed : {dead_links:,} (cascade)")

    if not orphans:
        print("\n✅ nothing to prune")
        return

    if not args.apply:
        print("\nDRY RUN — nothing changed. Re-run with --apply to delete.")
        return

    removed = 0
    while True:
        with engine.begin() as conn:
            ids = [r[0] for r in conn.execute(_ORPHAN_IDS, {"chunk": args.chunk})]
            if not ids:
                break
            conn.execute(_DELETE, {"ids": ids})
            removed += len(ids)
        print(f"  · pruned {removed:,}/{orphans:,}", flush=True)

    with engine.connect() as conn:
        left, _, _ = survey(conn)
    if left:
        print(f"\n❌ {left:,} orphans survived", file=sys.stderr)
        sys.exit(1)

    print(f"\n✅ pruned {removed:,} canonical products and their dead promotion links")
    print("   run `python -m etl.canonical` if you want member_count refreshed")


if __name__ == "__main__":
    main()
