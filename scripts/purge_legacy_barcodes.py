"""One-off migration: drop products keyed by a raw internal item code.

WHY THIS EXISTS
---------------
`products.barcode` is globally UNIQUE, which is only safe for codes that mean
the same thing everywhere. Around ten chains number their loose goods
themselves, and those numberings collide — item '12' is tomatoes in Stop
Market, frozen cod in King Store, red cabbage in Keshet. They all landed on one
product row, so the comparison engine ranked unrelated items against each other
and whichever chain loaded last overwrote the name.

`etl.normalize.product_key` now stores such codes namespaced to their chain
(`7290058108879_12`). GTINs are untouched, because cross-chain matching runs on
them.

The catch is that the fix does not clean up after itself. The upsert keys on
`barcode`, so a re-load INSERTS the namespaced rows and leaves every legacy row
sitting next to them — still carrying its prices, still turning up in
comparisons. This script removes them.

Deleting a product cascades to `prices` and `product_map` (both ON DELETE
CASCADE), so the rows never outlive their parent. `canonical_products` is left
behind with stale member counts, which is why `etl.canonical` has to run after.

Safe to run more than once: after the first pass there is nothing left to match.

Usage:
    python -m scripts.purge_legacy_barcodes              # dry run, changes nothing
    python -m scripts.purge_legacy_barcodes --apply      # actually delete
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from etl.normalize import GTIN_MIN_LENGTH

# A legacy row is short enough not to be a GTIN. Namespaced rows are never
# caught by this: a chain id plus a separator already exceeds the bound.
_WHERE = "CHAR_LENGTH(barcode) < :n"

_COUNT_PRODUCTS = text(f"SELECT COUNT(*) FROM products WHERE {_WHERE}")
_COUNT_PRICES = text(
    f"SELECT COUNT(*) FROM prices WHERE product_id IN (SELECT id FROM products WHERE {_WHERE})"
)
_COUNT_MAP = text(
    f"SELECT COUNT(*) FROM product_map WHERE product_id IN (SELECT id FROM products WHERE {_WHERE})"
)
_SAMPLE = text(f"SELECT barcode, name FROM products WHERE {_WHERE} ORDER BY barcode LIMIT 8")
_DELETE = text(f"DELETE FROM products WHERE {_WHERE}")


def main() -> None:
    p = argparse.ArgumentParser(description="Remove products keyed by a raw internal item code")
    p.add_argument(
        "--apply",
        action="store_true",
        help="perform the delete; without it the script only reports",
    )
    args = p.parse_args()

    from backend.app.db import engine

    n = {"n": GTIN_MIN_LENGTH}
    with engine.connect() as conn:
        products = conn.execute(_COUNT_PRODUCTS, n).scalar()
        prices = conn.execute(_COUNT_PRICES, n).scalar()
        mapped = conn.execute(_COUNT_MAP, n).scalar()
        sample = conn.execute(_SAMPLE, n).all()

    print(f"legacy products (barcode shorter than {GTIN_MIN_LENGTH}): {products:,}")
    print(f"  prices that cascade away : {prices:,}")
    print(f"  product_map rows          : {mapped:,}")
    if sample:
        print("\n  sample:")
        for barcode, name in sample:
            print(f"    {barcode:<12} {name}")

    if not products:
        print("\n✅ nothing to purge — this database is already namespaced")
        return

    if not args.apply:
        print("\nDRY RUN — nothing was changed. Re-run with --apply to delete.")
        return

    with engine.begin() as conn:
        deleted = conn.execute(_DELETE, n).rowcount

    with engine.connect() as conn:
        left = conn.execute(_COUNT_PRODUCTS, n).scalar()
    if left:
        print(f"\n❌ {left:,} legacy products survived the delete", file=sys.stderr)
        sys.exit(1)

    print(f"\n✅ purged {deleted:,} products (prices and product_map cascaded)")
    print("   run `python -m etl.canonical` next — canonical_products still holds stale members")


if __name__ == "__main__":
    main()
