"""Add the image-cache columns to `products`. Idempotent.

    python -m scripts.add_product_image_columns            # report only
    python -m scripts.add_product_image_columns --apply    # run the DDL

Three columns, not one:

  image_url         the resolved URL, NULL until we look
  image_source      which provider answered ('off' | 'google' | 'none')
  image_checked_at  when we last looked

`image_source='none'` with a timestamp is the important one — a NEGATIVE cache.
Without it, every product the providers do not know is re-queried on every request
forever, and the Google fallback is metered per call. A miss has to be as durable
as a hit or the bill never stops.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from backend.app.db import engine  # noqa: E402

_COLUMNS = {
    "image_url": "VARCHAR(1024) NULL",
    "image_source": "VARCHAR(16) NULL",
    "image_checked_at": "DATETIME NULL",
}
# Resolution walks products that have never been looked at, newest-demand first.
_INDEX = ("idx_products_image_checked", "image_checked_at")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="run the DDL (default: report only)")
    args = ap.parse_args()

    with engine.connect() as conn:
        existing = {r[0] for r in conn.execute(text("DESCRIBE products")).all()}
        idx = {
            r[2] for r in conn.execute(text("SHOW INDEX FROM products")).all()
        }

    missing = {c: d for c, d in _COLUMNS.items() if c not in existing}
    need_index = _INDEX[0] not in idx

    if not missing and not need_index:
        print("nothing to do — products already has the image columns and index.")
        return 0

    stmts = [f"ALTER TABLE products ADD COLUMN {c} {d}" for c, d in missing.items()]
    if need_index:
        stmts.append(f"CREATE INDEX {_INDEX[0]} ON products ({_INDEX[1]})")

    print(f"products columns present: {sorted(existing & set(_COLUMNS))or 'none of them'}")
    for s in stmts:
        print(f"  {s}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    # Separate statements, not one combined ALTER: MySQL is happy either way here,
    # but a partial failure is far easier to reason about when each column is its
    # own committed step.
    with engine.begin() as conn:
        for s in stmts:
            conn.execute(text(s))
            print(f"  ok  {s}")
    print("\napplied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
