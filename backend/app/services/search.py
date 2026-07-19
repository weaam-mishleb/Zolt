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
    """Search products by name, barcode or manufacturer (FR-2.1), one entry per
    distinct name.

    Different chains use different barcodes for the same item, so `products`
    holds several rows with the same `name`. We `GROUP BY name` (picking the
    lowest id as the representative) so the autocomplete shows no duplicates.

    Barcode path: an all-digit query is treated as a barcode prefix (uses the
    UNIQUE(barcode) index).
    Primary path: FULLTEXT boolean search with prefix wildcards (fast, ranked).
    Fallback: LIKE substring match on name OR manufacturer — covers very short
    queries and tokens below the FULLTEXT minimum token length.
    """
    q = q.strip()
    if not q:
        return []

    # Ranking: staples first. `availability` = number of branches carrying the
    # product — 'חלב 3% תנובה' (441 branches) must outrank 'מקציף חלב', which a
    # shortest-name-first sort used to bury. Prefix matches ("חלב…") still beat
    # substring matches ("שוקולד חלב"), so the adjective 'חלבי' pollution sinks.
    if q.isdigit() and len(q) >= 4:
        bc_sql = text(
            """
            SELECT MIN(p.id) AS id, MIN(p.barcode) AS barcode, p.name,
                   MIN(p.manufacturer) AS manufacturer, MIN(p.unit_qty) AS unit_qty,
                   MIN(p.unit_of_measure) AS unit_of_measure,
                   MIN(p.quantity) AS quantity, MAX(p.is_weighted) AS is_weighted,
                   COUNT(pr.id) AS availability
            FROM products p
            LEFT JOIN prices pr ON pr.product_id = p.id
            WHERE p.barcode LIKE :prefix
            GROUP BY p.name
            ORDER BY availability DESC, CHAR_LENGTH(p.name) ASC
            LIMIT :limit
            """
        )
        rows = db.execute(bc_sql, {"prefix": f"{q}%", "limit": limit}).mappings().all()
        return [dict(r) for r in rows]

    expr = _boolean_expr(q)
    if expr:
        ft_sql = text(
            """
            SELECT MIN(p.id) AS id, MIN(p.barcode) AS barcode, p.name,
                   MIN(p.manufacturer) AS manufacturer, MIN(p.unit_qty) AS unit_qty,
                   MIN(p.unit_of_measure) AS unit_of_measure,
                   MIN(p.quantity) AS quantity, MAX(p.is_weighted) AS is_weighted,
                   COUNT(pr.id) AS availability,
                   MAX(MATCH(p.name) AGAINST (:expr IN BOOLEAN MODE)) AS score
            FROM products p
            LEFT JOIN prices pr ON pr.product_id = p.id
            WHERE MATCH(p.name) AGAINST (:expr IN BOOLEAN MODE)
            GROUP BY p.name
            ORDER BY (p.name LIKE :starts) DESC, availability DESC,
                     score DESC, CHAR_LENGTH(p.name) ASC
            LIMIT :limit
            """
        )
        rows = db.execute(
            ft_sql, {"expr": expr, "starts": f"{q}%", "limit": limit}
        ).mappings().all()
        if rows:
            return [dict(r) for r in rows]

    # Fallback — substring match, ranking exact prefixes first.
    like_sql = text(
        """
        SELECT MIN(p.id) AS id, MIN(p.barcode) AS barcode, p.name,
               MIN(p.manufacturer) AS manufacturer, MIN(p.unit_qty) AS unit_qty,
               MIN(p.unit_of_measure) AS unit_of_measure,
               MIN(p.quantity) AS quantity, MAX(p.is_weighted) AS is_weighted,
               COUNT(pr.id) AS availability
        FROM products p
        LEFT JOIN prices pr ON pr.product_id = p.id
        WHERE p.name LIKE :contains OR p.manufacturer LIKE :contains
        GROUP BY p.name
        ORDER BY (p.name LIKE :prefix) DESC, availability DESC, CHAR_LENGTH(p.name) ASC
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
