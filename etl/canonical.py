"""Entity resolution — collapse chain-specific SKUs into canonical products.

WHY THIS EXISTS
---------------
Chains publish different barcodes/PLUs and different wordings for the same
physical item, so matching has to be fuzzy. Today that fuzzy work runs PER
REQUEST, on the user's latency budget. At 33 chains it stops holding.

This module moves identity resolution OFFLINE: every product is assigned a
`canonical_id` once, here, and the online API then does a plain indexed join.

    query-time cost:  O(basket × candidates × text work)  →  O(log n)

BLOCKING KEY
------------
Two products are the same item iff they share (normalized name, size signature).
The size signature is what keeps 'ביסלי גריל 55 גר' distinct from the 4-pack
'ביסלי גריל 4*55' — the exact bug that let a multipack price hijack a basket.

SCOPING, AND WHY CROSS-CHAIN GROUPING SURVIVES IT
-------------------------------------------------
The ETL matrix runs one job per chain, so an unscoped pass meant six runners
each resolving ALL products, computing identical groupings and upserting the
same rows at the same moment — redundant work whose only real output was lock
contention.

`--chains` narrows a run to the products a chain actually prices. Grouping stays
global regardless, because it is the DB that enforces identity:
canonical_products is UNIQUE(name_norm, size_signature) and the insert is an
upsert, so the second chain to meet a blocking key joins the row the first one
created instead of making its own. Resolution is per chain; identity is shared.

Run:
    python -m etl.canonical                    # resolve everything
    python -m etl.canonical --chains shufersal # only that chain's products
    python -m etl.canonical --limit 5000       # smoke test on a slice
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import bindparam, text

from .config import DEFAULT_DATA_DIR, chain_id_for
from .loader import Loader, _lock_ordered

# Reuse the SAME normalization the comparison engine uses, so offline identity
# and online behaviour can never drift apart. (etl already depends on backend
# for the shared engine — see etl/run.py.)
from backend.app.services.comparison import _norm_name, size_tokens

_SELECT_PRODUCTS = text(
    """
    SELECT id, name, name_norm, manufacturer, quantity, unit_qty, is_weighted
    FROM products
    WHERE id > :after
    ORDER BY id
    LIMIT :chunk
    """
)

# Scoped variant. `products` deliberately carries no chain column — that is the
# point of the table — so the chain is reached through the prices that reference
# it. EXISTS rather than a JOIN because a product priced in 400 branches must
# still come back once; the UNIQUE(product_id, store_id) index on prices makes
# the lookup a probe, and stores is tiny.
_SELECT_PRODUCTS_FOR_CHAINS = text(
    """
    SELECT p.id, p.name, p.name_norm, p.manufacturer, p.quantity, p.unit_qty, p.is_weighted
    FROM products p
    WHERE p.id > :after
      AND EXISTS (
          SELECT 1 FROM prices pr
          JOIN stores s ON s.id = pr.store_id
          WHERE pr.product_id = p.id AND s.chain_id IN :chain_ids
      )
    ORDER BY p.id
    LIMIT :chunk
    """
).bindparams(bindparam("chain_ids", expanding=True))

_CANON_UPSERT = text(
    """
    INSERT INTO canonical_products
        (canonical_name, name_norm, brand, net_qty, unit, size_signature, is_weighted)
    VALUES
        (:canonical_name, :name_norm, :brand, :net_qty, :unit, :size_signature, :is_weighted)
    ON DUPLICATE KEY UPDATE
        canonical_name = IF(CHAR_LENGTH(VALUES(canonical_name)) > CHAR_LENGTH(canonical_name),
                            VALUES(canonical_name), canonical_name),
        brand   = COALESCE(canonical_products.brand, VALUES(brand)),
        net_qty = COALESCE(canonical_products.net_qty, VALUES(net_qty)),
        unit    = COALESCE(canonical_products.unit, VALUES(unit))
    """
)

_MAP_UPSERT = text(
    """
    INSERT INTO product_map (product_id, canonical_id, match_method, confidence, needs_review)
    VALUES (:product_id, :canonical_id, :match_method, :confidence, :needs_review)
    ON DUPLICATE KEY UPDATE
        canonical_id = VALUES(canonical_id),
        match_method = VALUES(match_method),
        confidence   = VALUES(confidence),
        needs_review = VALUES(needs_review),
        matched_at   = CURRENT_TIMESTAMP
    """
)


_SELECT_CANON_IDS = text(
    "SELECT id, name_norm, size_signature FROM canonical_products WHERE name_norm IN :names"
).bindparams(bindparam("names", expanding=True))

_REFRESH_MEMBERS_ALL = text(
    """
    UPDATE canonical_products c
    SET member_count = (SELECT COUNT(*) FROM product_map m WHERE m.canonical_id = c.id)
    """
)

_REFRESH_MEMBERS = text(
    """
    UPDATE canonical_products c
    SET member_count = (SELECT COUNT(*) FROM product_map m WHERE m.canonical_id = c.id)
    WHERE c.id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


def size_signature(name: str | None) -> str:
    """Stable pack signature, e.g. 'ביסלי 4*55' → '4|55'. '' when size-less."""
    return "|".join(size_tokens(name))


def blocking_key(name: str | None) -> tuple[str, str]:
    """(normalized name, size signature) — two SKUs sharing this are one item."""
    return _norm_name(name), size_signature(name)


def _confidence(name_norm: str, sig: str) -> tuple[float, bool]:
    """Confidence in the grouping, and whether a human should look.

    A short, size-less name ('קוקוס טבעי') is a far weaker identity claim than a
    long one with an explicit pack size — those are exactly the groupings that
    produced wrong matches in production, so they are flagged rather than
    trusted silently.
    """
    words = len([w for w in name_norm.split() if len(w) > 1])
    if sig and words >= 3:
        return 1.000, False
    if sig or words >= 3:
        return 0.850, False
    return 0.600, True  # short AND size-less → review queue


def resolve(
    engine,
    chunk: int = 5_000,
    limit: int | None = None,
    chain_ids: list[str] | None = None,
) -> dict:
    """Assign every product a canonical id. Idempotent — safe to re-run.

    `chain_ids` restricts the pass to products those chains price. See the
    module docstring for why that does not fragment cross-chain identity.
    """
    t0 = time.time()
    seen: dict[tuple[str, str], int] = {}   # blocking key → canonical_id
    stats = {"products": 0, "canonical": 0, "review": 0, "skipped": 0}
    after, processed = 0, 0
    # Every write here collides with five sibling runners, so it gets the same
    # lock ordering and jittered deadlock retry as the price loader.
    writer = Loader(engine)

    while True:
        take = chunk if limit is None else min(chunk, limit - processed)
        if take <= 0:
            break
        params = {"after": after, "chunk": take}
        stmt = _SELECT_PRODUCTS
        if chain_ids:
            stmt = _SELECT_PRODUCTS_FOR_CHAINS
            params["chain_ids"] = chain_ids
        with engine.connect() as conn:
            rows = conn.execute(stmt, params).mappings().all()
        if not rows:
            break
        after = rows[-1]["id"]
        processed += len(rows)

        # ── 1. distinct canonical candidates in this chunk ──────────────────
        canon_batch: dict[tuple[str, str], dict] = {}
        for r in rows:
            key = blocking_key(r["name"])
            if not key[0]:
                stats["skipped"] += 1
                continue
            # Keep the longest name seen as the display name — the feed truncates
            # at 40 chars, so the longest variant is usually the most complete.
            cur = canon_batch.get(key)
            if cur is None or len(r["name"] or "") > len(cur["canonical_name"]):
                canon_batch[key] = {
                    "canonical_name": r["name"] or key[0],
                    "name_norm": key[0],
                    "brand": r["manufacturer"],
                    "net_qty": r["quantity"],
                    "unit": r["unit_qty"],
                    "size_signature": key[1],
                    "is_weighted": int(bool(r["is_weighted"])),
                }

        new_keys = [k for k in canon_batch if k not in seen]
        if new_keys:
            # Sorted on the blocking key: every chain meets the same national
            # products, so without a shared lock order two runners inserting the
            # same canonical rows in opposite sequences deadlock each other.
            payload = _lock_ordered(
                [canon_batch[k] for k in new_keys], ("name_norm", "size_signature")
            )

            def _write_canon(payload=payload, new_keys=new_keys):
                with engine.begin() as conn:
                    conn.execute(_CANON_UPSERT, payload)
                    # Read back the ids for the keys we just wrote.
                    return conn.execute(
                        _SELECT_CANON_IDS, {"names": [k[0] for k in new_keys]}
                    ).all()

            got = writer.run_with_retry(_write_canon, "canonical upsert")
            for cid, nn, sig in got:
                seen[(nn, sig or "")] = cid
            stats["canonical"] += len(new_keys)

        # ── 2. map every product in the chunk to its canonical id ───────────
        map_batch = []
        for r in rows:
            key = blocking_key(r["name"])
            cid = seen.get(key)
            if not key[0] or cid is None:
                continue
            conf, review = _confidence(key[0], key[1])
            # An exact barcode-level identity is implicit: products.barcode is
            # UNIQUE, so a SKU never lands here twice.
            map_batch.append({
                "product_id": r["id"],
                "canonical_id": cid,
                "match_method": "exact_name" if key[1] else "name_only",
                "confidence": conf,
                "needs_review": int(review),
            })
            if review:
                stats["review"] += 1

        if map_batch:
            ordered = _lock_ordered(map_batch, ("product_id",))

            def _write_map(ordered=ordered):
                with engine.begin() as conn:
                    conn.execute(_MAP_UPSERT, ordered)

            writer.run_with_retry(_write_map, "product_map upsert")
        stats["products"] += len(map_batch)

        print(f"  · resolved {stats['products']:,} products → "
              f"{stats['canonical']:,} canonical", flush=True)

        if len(rows) < take:
            break

    # Refresh the denormalized QA counter.
    #
    # This used to rewrite EVERY canonical_products row — 178,810 of them — in a
    # single transaction, from every one of the 30 matrix jobs. Six runners each
    # taking a row lock on the entire table is a far larger contention source
    # than the upserts above, and scoping only the SELECT would have left it
    # untouched. A scoped run now refreshes just the rows it actually touched.
    #
    # The COUNT itself is deliberately still global: members come from every
    # chain, so a chain-scoped count would be wrong, not merely partial.
    touched = sorted(set(seen.values()))
    if touched:
        def _refresh(touched=touched):
            with engine.begin() as conn:
                if chain_ids:
                    for i in range(0, len(touched), 1_000):
                        conn.execute(_REFRESH_MEMBERS, {"ids": touched[i : i + 1_000]})
                else:
                    conn.execute(_REFRESH_MEMBERS_ALL)

        writer.run_with_retry(_refresh, "member_count refresh")

    stats["elapsed_s"] = round(time.time() - t0, 1)
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description="Zolt entity resolution → canonical products")
    p.add_argument("--chunk", type=int, default=5_000, help="products per pass")
    p.add_argument("--limit", type=int, default=None, help="cap products (smoke test)")
    p.add_argument(
        "--chains",
        default="",
        help="comma-separated slugs; resolve only products these chains price "
             "(omit to resolve the whole database)",
    )
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                   help="where the store files live — used to map slug → chain_id")
    args = p.parse_args()

    from backend.app.db import engine

    chain_ids: list[str] = []
    slugs = [s.strip() for s in args.chains.split(",") if s.strip()]
    for slug in slugs:
        cid = chain_id_for(slug, Path(args.data_dir))
        if cid is None:
            # Silently resolving everything instead would put this job straight
            # back into the contention the scoping exists to avoid.
            print(
                f"::error::cannot map chain '{slug}' to a chain_id — "
                f"store_file_{slug}.csv is missing or empty",
                file=sys.stderr,
            )
            sys.exit(1)
        chain_ids.append(cid)

    scope = f"chains={slugs} ({','.join(chain_ids)})" if chain_ids else "ALL chains"
    print(
        f"canonical: resolving {scope} on {engine.url.host}/{engine.url.database}",
        flush=True,
    )
    stats = resolve(engine, chunk=args.chunk, limit=args.limit, chain_ids=chain_ids or None)
    print("─" * 52)
    print(f"  products mapped   : {stats['products']:,}")
    print(f"  canonical products: {stats['canonical']:,}")
    print(f"  flagged for review: {stats['review']:,}")
    print(f"  skipped (no name) : {stats['skipped']:,}")
    print(f"  elapsed           : {stats['elapsed_s']}s")
    if stats["products"] == 0:
        print("  ! nothing mapped — is the products table empty?", file=sys.stderr)


if __name__ == "__main__":
    main()
