"""Export the branches the city matcher still cannot place, ready for review.

WHY THIS EXISTS
---------------
The matcher recovers 183 of the 540 city-less branches with zero false
positives. The rest need a human, and the point of this export is to make that
human's job as small as possible: the 357 remaining branches collapse to far
fewer DISTINCT strings, so the work is mapping keys, not rows.

Output is a CSV with an empty `city` column. Fill it in, save it as
etl/city_aliases.json (see --emit-json), and the pipeline picks it up.

Leave `city` blank to leave a key unmapped. Write NOT_A_CITY for a brand or a
descriptor that is not a place at all ("Am" for AM:PM, "אונליין").

Usage:
    python -m scripts.export_unmapped_cities                 # CSV to stdout
    python -m scripts.export_unmapped_cities -o review.csv
    python -m scripts.export_unmapped_cities --emit-json review.csv   # merges in place
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict

from sqlalchemy import text

from etl.cities import _CHAIN_PREFIXES, _norm_locality, locality_from_store_name

NOT_A_CITY_MARKER = "NOT_A_CITY"


def review_key(store_name: str) -> str:
    """The string a human would actually map: chain branding removed.

    Grouping on this is what turns 357 rows into a much shorter list — every
    "שלי חיפה- X" branch collapses onto the same key.
    """
    s = re.sub(r"\s+", " ", str(store_name or "").strip())
    s = re.sub(r"^(אונליין|online)\s*[-|]\s*", "", s, flags=re.IGNORECASE)
    for prefix in sorted(_CHAIN_PREFIXES, key=len, reverse=True):
        if s.startswith(prefix):
            s = s[len(prefix):].strip(" -|,.*")
            break
    # The locality, when present, leads; the tail is a street or a franchisee.
    head = re.split(r"[-–—|,/*]+", s)[0].strip()
    return _norm_locality(head or s)


def collect(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT chain_name, store_name, address FROM stores "
                "WHERE city IS NULL ORDER BY store_name"
            )
        ).all()

    groups: dict[str, list[tuple]] = defaultdict(list)
    for chain, name, addr in rows:
        if locality_from_store_name(name, addr):
            continue                       # already solved, not for review
        groups[review_key(name)].append((chain, name, addr))

    out = []
    for key, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        chain, name, addr = members[0]
        out.append(
            {
                "key": key,
                "branches": len(members),
                "city": "",                # ← fill this in
                "example_store_name": name,
                "example_address": addr or "",
                "chain": chain or "",
                "other_names": " | ".join(n for _, n, _ in members[1:4]),
            }
        )
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Export unmapped branches for manual review")
    p.add_argument("-o", "--out", help="write CSV here instead of stdout")
    p.add_argument("--emit-json", metavar="CSV",
                   help="merge a filled-in CSV into the aliases file")
    p.add_argument("--stdout", action="store_true",
                   help="with --emit-json, print instead of writing the file")
    args = p.parse_args()

    if args.emit_json:
        # MERGE with what is already there. The usage line pipes this straight
        # into etl/city_aliases.json, so replacing the file wholesale would
        # silently drop every alias reviewed in an earlier pass.
        from etl.cities import _ALIASES_FILE

        try:
            existing = json.loads(_ALIASES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        aliases: dict[str, str | None] = dict(existing.get("aliases") or {})
        blocked: list[str] = list(existing.get("not_a_city") or [])

        added = 0
        with open(args.emit_json, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                city = (row.get("city") or "").strip()
                key = (row.get("key") or "").strip()
                if not city or not key:
                    continue
                if city == NOT_A_CITY_MARKER:
                    if key.lower() not in {b.lower() for b in blocked}:
                        blocked.append(key.lower())
                else:
                    aliases[key] = city
                added += 1

        existing.update({"aliases": aliases, "not_a_city": blocked})

        if args.stdout:
            json.dump(existing, sys.stdout, ensure_ascii=False, indent=1)
            print()
        else:
            # Written directly, NOT via a shell redirect. `--emit-json x >
            # city_aliases.json` would have the shell truncate the file before
            # this process ever reads it, so the merge would silently start from
            # nothing and wipe every previously reviewed alias.
            _ALIASES_FILE.write_text(
                json.dumps(existing, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
            print(f"  → {_ALIASES_FILE}", file=sys.stderr)
        print(f"  merged {added} reviewed keys — {len(aliases)} aliases, "
              f"{len(blocked)} blocked", file=sys.stderr)
        return

    from backend.app.db import engine

    rows = collect(engine)
    total = sum(r["branches"] for r in rows)
    fields = ["key", "branches", "city", "example_store_name",
              "example_address", "chain", "other_names"]

    handle = open(args.out, "w", newline="", encoding="utf-8-sig") if args.out else sys.stdout
    try:
        w = csv.DictWriter(handle, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    finally:
        if args.out:
            handle.close()

    if args.out:
        print(f"✅ {len(rows):,} keys covering {total:,} branches → {args.out}", file=sys.stderr)
        print(f"   fill in the `city` column, then:\n"
              f"   python -m scripts.export_unmapped_cities --emit-json {args.out}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
