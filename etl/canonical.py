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

Run:
    python -m etl.canonical                # resolve everything
    python -m etl.canonical --limit 5000   # smoke test on a slice
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import text

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


def resolve(engine, chunk: int = 5_000, limit: int | None = None) -> dict:
    """Assign every product a canonical id. Idempotent — safe to re-run."""
    t0 = time.time()
    seen: dict[tuple[str, str], int] = {}   # blocking key → canonical_id
    stats = {"products": 0, "canonical": 0, "review": 0, "skipped": 0}
    after, processed = 0, 0

    while True:
        take = chunk if limit is None else min(chunk, limit - processed)
        if take <= 0:
            break
        with engine.connect() as conn:
            rows = conn.execute(_SELECT_PRODUCTS, {"after": after, "chunk": take}).mappings().all()
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
            with engine.begin() as conn:
                conn.execute(_CANON_UPSERT, [canon_batch[k] for k in new_keys])
                # Read back the ids for the keys we just wrote.
                got = conn.execute(
                    text(
                        "SELECT id, name_norm, size_signature FROM canonical_products "
                        "WHERE name_norm IN :names"
                    ).bindparams(__import__("sqlalchemy").bindparam("names", expanding=True)),
                    {"names": [k[0] for k in new_keys]},
                ).all()
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
            with engine.begin() as conn:
                conn.execute(_MAP_UPSERT, map_batch)
        stats["products"] += len(map_batch)

        print(f"  · resolved {stats['products']:,} products → "
              f"{stats['canonical']:,} canonical", flush=True)

        if len(rows) < take:
            break

    # Refresh the denormalized QA counter in one set-based statement.
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE canonical_products c
            SET member_count = (SELECT COUNT(*) FROM product_map m WHERE m.canonical_id = c.id)
        """))

    stats["elapsed_s"] = round(time.time() - t0, 1)
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description="Zolt entity resolution → canonical products")
    p.add_argument("--chunk", type=int, default=5_000, help="products per pass")
    p.add_argument("--limit", type=int, default=None, help="cap products (smoke test)")
    args = p.parse_args()

    from backend.app.db import engine

    print(f"canonical: resolving products on {engine.url.host}/{engine.url.database}", flush=True)
    stats = resolve(engine, chunk=args.chunk, limit=args.limit)
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
