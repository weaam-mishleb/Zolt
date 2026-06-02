"""Debug: count prices / stores / products per chain in the DB.

Run from the project root:  python -m scripts.debug_chain_counts
Confirms the data imbalance between the 3 chains.
"""
from __future__ import annotations

from sqlalchemy import text

from backend.app.db import engine

QUERY = text(
    """
    SELECT s.chain_id,
           s.chain_name,
           COUNT(*)                       AS price_count,
           COUNT(DISTINCT s.id)           AS store_count,
           COUNT(DISTINCT pr.product_id)  AS product_count
    FROM prices pr
    JOIN stores s ON s.id = pr.store_id
    GROUP BY s.chain_id, s.chain_name
    ORDER BY price_count DESC
    """
)


def main() -> None:
    with engine.connect() as conn:
        rows = conn.execute(QUERY).mappings().all()

    total = sum(r["price_count"] for r in rows) or 1
    print(f"{'chain':<14}{'chain_id':<16}{'prices':>10}{'%':>8}{'stores':>9}{'products':>10}")
    print("-" * 67)
    for r in rows:
        pct = r["price_count"] / total * 100
        print(
            f"{(r['chain_name'] or ''):<14}{r['chain_id']:<16}"
            f"{r['price_count']:>10,}{pct:>7.1f}%{r['store_count']:>9}{r['product_count']:>10,}"
        )
    print("-" * 67)
    print(f"{'TOTAL':<30}{total:>10,}")


if __name__ == "__main__":
    main()
