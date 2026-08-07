"""Null out `stores.address` values that are placeholders rather than places.

`normalize_store` applies `clean_address` on every load, so this only exists to
close the same gap the city merge and the chain rename had: rows already stored
keep their garbage until the ETL next rewrites them.

The UI prints the address under a 📍, so "📍 {}" reads as data we believe. NULL
lets the component skip the line, which is the honest rendering of "we do not
know where this branch is".

Dry-run by default.

    python -m scripts.scrub_store_addresses            # report only
    python -m scripts.scrub_store_addresses --apply    # write it
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from backend.app.db import engine          # noqa: E402
from etl.normalize import clean_address    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    args = ap.parse_args()

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, chain_name, store_name, address FROM stores WHERE address IS NOT NULL")
        ).all()

    # The same function the ETL uses, so the report cannot disagree with what the
    # next load would produce.
    doomed = [(sid, chain, name, addr) for sid, chain, name, addr in rows if clean_address(addr) is None]
    if not doomed:
        print(f"nothing to do — all {len(rows)} stored addresses survive clean_address().")
        return 0

    by_value: dict[str, int] = {}
    for *_, addr in doomed:
        by_value[addr] = by_value.get(addr, 0) + 1

    print(f"addresses stored: {len(rows)}")
    print(f"to be nulled:     {len(doomed)}  ({len(by_value)} distinct values)\n")
    print(f"{'n':>5}   value")
    print("-" * 60)
    for value, n in sorted(by_value.items(), key=lambda kv: -kv[1]):
        print(f"{n:>5}   {value!r}")
    print("\nexamples:")
    for sid, chain, name, addr in doomed[:5]:
        print(f"  #{sid} {chain} · {name!r} -> address {addr!r} becomes NULL")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to write it.")
        return 0

    with engine.begin() as conn:
        total = 0
        for sid, *_ in doomed:
            total += conn.execute(
                text("UPDATE stores SET address = NULL WHERE id = :id"), {"id": sid}
            ).rowcount
    print(f"\napplied — {total} addresses cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
