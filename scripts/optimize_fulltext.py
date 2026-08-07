"""Purge tombstoned documents from the products FULLTEXT index.

WHY THIS EXISTS
---------------
Search took ~10 seconds for every query, and the obvious suspects were all
wrong. Measured on production:

    q='קוטג'    FULLTEXT matches     52 products -> 9.96s
    q='שוקולד'  FULLTEXT matches  5,361 products -> 10.37s
    the LIKE full-table-scan fallback                0.56s

A cost that does not move with the number of matches is not a matching cost, and
a full scan beating the index 20x says the index is the problem. It was:

    products rows                  235,462
    INNODB_FT_DELETED            34,656,440     (147x the live row count)

InnoDB does not remove a document from a FULLTEXT index when the indexed column
is rewritten — it tombstones the old one and appends the new. Every ETL run that
touched `products.name` therefore added a tombstone per row, and nothing ever
collected them. Every MATCH has to filter the whole list.

This is NOT solved once. Every ETL run rewrites product names and regenerates
them: one nightly load put 1,168,018 tombstones back (5x the live rows) and took
search from 280ms to 548ms. That is why the ETL workflow now runs this at the
end of every load rather than leaving it as a manual chore.

Usage:
    python -m scripts.optimize_fulltext            # report only
    python -m scripts.optimize_fulltext --apply
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import text

# Tombstones are normal; a MULTIPLE of the live rows is not. Set below 1.0
# because this now runs after every nightly load, where one run alone puts back
# ~5x — waiting for 2x would mean shipping a slow day whenever a load is small.
# The rebuild costs 8.5s at this table size, so running it eagerly is cheap.
WARN_RATIO = 0.5


def _long_lived_engine():
    """A connection that can outlive the app's socket timeouts.

    The shared engine sets read_timeout=120 for the API and the ETL, which is
    right for them and fatal here: rebuilding a FULLTEXT index with 34M
    tombstones takes far longer than two minutes, and PyMySQL kills the socket
    mid-rebuild — the server keeps working while the client gives up.
    """
    from sqlalchemy import create_engine

    from backend.app.config import settings
    from backend.app.db import _connect_args, _force_ipv4

    args = _connect_args()
    args["read_timeout"] = 3600
    args["write_timeout"] = 3600
    return create_engine(
        _force_ipv4(settings.database_url), poolclass=None, connect_args=args, future=True
    )


def survey(conn) -> tuple[int, int]:
    """(live rows, tombstoned documents) for products' FULLTEXT index."""
    db = conn.execute(text("SELECT DATABASE()")).scalar()
    conn.execute(text(f"SET GLOBAL innodb_ft_aux_table = '{db}/products'"))
    rows = conn.execute(text("SELECT COUNT(*) FROM products")).scalar()
    deleted = conn.execute(
        text("SELECT COUNT(*) FROM INFORMATION_SCHEMA.INNODB_FT_DELETED")
    ).scalar()
    return rows, deleted


def main() -> None:
    p = argparse.ArgumentParser(description="Rebuild the products FULLTEXT index")
    p.add_argument("--apply", action="store_true", help="perform the rebuild")
    args = p.parse_args()

    engine = _long_lived_engine()

    # AUTOCOMMIT: OPTIMIZE TABLE and SET GLOBAL are not transactional.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        rows, deleted = survey(conn)
        ratio = deleted / rows if rows else 0.0
        print(f"  products rows        : {rows:,}")
        print(f"  tombstoned documents : {deleted:,}  ({ratio:.1f}x the live rows)")

        if ratio < WARN_RATIO:
            print(f"\n✅ below {WARN_RATIO}x — a rebuild would not buy anything")
            return
        if not args.apply:
            print("\nDRY RUN — nothing changed. Re-run with --apply to rebuild.")
            return

        # NOT `OPTIMIZE TABLE`. That is the documented way to collect FULLTEXT
        # tombstones and it does not finish the job: it processes only
        # innodb_ft_num_word_optimize words per invocation — 2,000 by default —
        # so on an index this far gone it reports "OK" after 89 seconds having
        # moved every row from INNODB_FT_DELETED into INNODB_FT_BEING_DELETED
        # and cleared nothing. Measured, not assumed.
        #
        # TWO statements, not one ALTER. MySQL collapses a DROP and ADD of the
        # same index in a single ALTER into a no-op — measured: 0.2s, tombstone
        # count unchanged. Split, the DROP does the actual purge (318s when the
        # index held 34M tombstones) and the ADD rebuilds from live rows.
        t0 = time.time()
        conn.execute(text("ALTER TABLE products DROP INDEX ft_product_name"))
        conn.execute(text("ALTER TABLE products ADD FULLTEXT INDEX ft_product_name (name)"))
        elapsed = time.time() - t0
        print(f"  · FULLTEXT index rebuilt in {elapsed:.1f}s")

        rows_after, deleted_after = survey(conn)

    print(f"\n  tombstones: {deleted:,} → {deleted_after:,}   ({elapsed:.1f}s)")
    if deleted_after >= deleted:
        print("::warning::the rebuild did not reduce the tombstone count", file=sys.stderr)
        sys.exit(1)
    print("✅ FULLTEXT index rebuilt")


if __name__ == "__main__":
    main()
