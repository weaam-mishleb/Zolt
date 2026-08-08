"""Read-only audit for the two anomalies a retroactive purge was proposed for.

Writes nothing. It exists because both proposed migrations — deleting conditional
promotions and severing multipack product_map links — are irreversible against
data that measured clean, and the honest next step is to find a real case first.

    python -m scripts.audit_promo_anomalies

1. CONDITIONAL PROMOTIONS THAT WOULD ACTUALLY BE APPLIED
   `load_active_promotions` filters these at QUERY time, so they are already
   inert no matter what the table holds — a DELETE would change no price. What
   matters instead is the opposite list: promotions that are cheaper than the
   shelf price (so the engine WILL take them) and are NOT caught by any keyword.
   Those are the candidates for a new keyword, and the only ones that can still
   quote a price the register will not honour.

2. CANONICAL GROUPS MIXING A SINGLE WITH A MULTIPACK
   A group holding both "קוקה קולה 1.5 ל" and "קוקה קולה שישייה" would let a
   single-bottle basket be priced against a six-pack. Reported per group with its
   members, so a human can confirm before anything is unlinked.
"""
from __future__ import annotations

import sys

from sqlalchemy import text

sys.path.insert(0, ".")

from backend.app.db import engine                              # noqa: E402
from backend.app.services.promotions import is_conditional     # noqa: E402

# Same shape the comparison uses to tell a pack apart from a unit.
_MULTIPACK_RE = r"שישי|שמיני|רביעי|מארז|מאגד|[0-9]+ *[xX*] *[0-9]+|[0-9]+ *יח"


def main() -> int:
    with engine.connect() as conn:
        print("── 1. cheaper-than-shelf promotions NOT caught by any keyword ──")
        print("   (these are the ones that can still quote an unhonourable price)")
        rows = conn.execute(
            text(
                """
                SELECT p.description, p.reward_kind, p.min_qty, p.discounted_price,
                       MIN(pc.price) shelf, COUNT(DISTINCT p.store_id) branches
                FROM promotions p
                JOIN promotion_items pi ON pi.promotion_id = p.id
                JOIN product_map pm     ON pm.canonical_id = pi.canonical_id
                JOIN prices pc          ON pc.product_id = pm.product_id
                                       AND pc.store_id = p.store_id
                WHERE p.reward_kind = 'FIXED_PRICE'
                  AND p.discounted_price > 0
                  AND p.min_qty <= 1
                  AND (p.ends_at IS NULL OR p.ends_at >= NOW())
                GROUP BY p.description, p.reward_kind, p.min_qty, p.discounted_price
                HAVING p.discounted_price < MIN(pc.price) * 0.6
                ORDER BY branches DESC
                LIMIT 25
                """
            )
        ).all()
        shown = 0
        for desc, kind, min_qty, price, shelf, branches in rows:
            if is_conditional(desc):
                continue           # already filtered — cannot affect a price
            shown += 1
            print(f"   {branches:>4} branches  ₪{price} vs shelf ₪{shelf}  {(desc or '')[:52]!r}")
        print(f"   -> {shown} descriptions worth a human read")

        print("\n── 2. canonical groups mixing a single with a multipack ──")
        rows = conn.execute(
            text(
                f"""
                SELECT pm.canonical_id,
                       SUM(p.name REGEXP '{_MULTIPACK_RE}') multi,
                       COUNT(*) total
                FROM product_map pm
                JOIN products p ON p.id = pm.product_id
                GROUP BY pm.canonical_id
                HAVING multi > 0 AND multi < total
                LIMIT 20
                """
            )
        ).all()
        for cid, multi, total in rows:
            print(f"   canonical {cid}: {multi} multipack of {total} members")
            for (name,) in conn.execute(
                text(
                    "SELECT p.name FROM product_map pm JOIN products p ON p.id = pm.product_id "
                    "WHERE pm.canonical_id = :c LIMIT 8"
                ),
                {"c": cid},
            ).all():
                print(f"       {name}")
        print(f"   -> {len(rows)} mixed groups")
        if not rows:
            print("      none: entity resolution is keeping packs and units apart.")

    print("\nread-only — nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
