"""Read queries for the search service: product search/autocomplete and stores."""
from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

# Characters that carry special meaning in MySQL FULLTEXT BOOLEAN MODE.
_BOOLEAN_OPERATORS = re.compile(r'[+\-><()~*"@]')


def _boolean_expr(q: str) -> str:
    """Turn a free-text query into a prefix-matching boolean expression.

    "חלב תנו" -> "+חלב* +תנו*"  (every token required, prefix-matched).
    """
    tokens = [_BOOLEAN_OPERATORS.sub(" ", t).strip() for t in q.split()]
    tokens = [t for t in tokens if t]
    return " ".join(f"+{t}*" for t in tokens)


def search_products(db: Session, q: str, limit: int = 10) -> list[dict]:
    """Search products by name.

    Primary path: FULLTEXT boolean search with prefix wildcards (fast, ranked).
    Fallback: LIKE substring match — covers very short queries and tokens that
    are below the FULLTEXT minimum token length.
    """
    q = q.strip()
    if not q:
        return []

    expr = _boolean_expr(q)
    if expr:
        ft_sql = text(
            """
            SELECT id, barcode, name, manufacturer, unit_qty, unit_of_measure,
                   MATCH(name) AGAINST (:expr IN BOOLEAN MODE) AS score
            FROM products
            WHERE MATCH(name) AGAINST (:expr IN BOOLEAN MODE)
            ORDER BY score DESC, CHAR_LENGTH(name) ASC
            LIMIT :limit
            """
        )
        rows = db.execute(ft_sql, {"expr": expr, "limit": limit}).mappings().all()
        if rows:
            return [dict(r) for r in rows]

    # Fallback — substring match, ranking exact prefixes first.
    like_sql = text(
        """
        SELECT id, barcode, name, manufacturer, unit_qty, unit_of_measure
        FROM products
        WHERE name LIKE :contains
        ORDER BY (name LIKE :prefix) DESC, CHAR_LENGTH(name) ASC
        LIMIT :limit
        """
    )
    rows = db.execute(
        like_sql, {"contains": f"%{q}%", "prefix": f"{q}%", "limit": limit}
    ).mappings().all()
    return [dict(r) for r in rows]


def list_stores(
    db: Session,
    city: str | None = None,
    chain: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List stores, optionally filtered by city (and chain name/id)."""
    clauses: list[str] = []
    params: dict = {"limit": limit, "offset": offset}

    if city:
        clauses.append("city LIKE :city")
        params["city"] = f"%{city}%"
    if chain:
        clauses.append("(chain_name LIKE :chain OR chain_id = :chain_exact)")
        params["chain"] = f"%{chain}%"
        params["chain_exact"] = chain

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = text(
        f"""
        SELECT id, chain_id, chain_name, sub_chain_id, store_code,
               store_name, address, city, zip_code
        FROM stores
        {where}
        ORDER BY chain_name, city, store_name
        LIMIT :limit OFFSET :offset
        """
    )
    return [dict(r) for r in db.execute(sql, params).mappings().all()]


def list_cities(db: Session) -> list[str]:
    """Distinct, non-empty city names — used to populate the city filter."""
    sql = text(
        """
        SELECT DISTINCT city FROM stores
        WHERE city IS NOT NULL AND city <> ''
        ORDER BY city
        """
    )
    return [row[0] for row in db.execute(sql).all()]
