"""Basket comparison engine.

Given a basket (product ids + quantities) and a city, compute the total cost of
the basket at every relevant branch of Shufersal / Rami Levy / Osher Ad in that
city, then rank them.

Ranking rule (important):
  * A store is "complete" only if it carries *every* requested product.
  * Only complete stores compete for the winner and receive a numeric `rank`
    (1 = cheapest).
  * Incomplete stores are still returned (with their partial total and the list
    of missing products) but get `rank = None` — they are excluded from the
    competition for first place, not hidden.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

_TWO_PLACES = Decimal("0.01")
_NAME_WS = re.compile(r"\s+")

# Characters that are operators in MySQL FULLTEXT BOOLEAN MODE — stripped from tokens.
_FT_OPERATORS = re.compile(r'[+\-><()~*"@]')
# Pure numbers / sizes / percentages — not "prominent".
_NUMERIC = re.compile(r"^[0-9]+([.,][0-9]+)?%?$")
# Size / packaging / unit words to skip when picking prominent words.
_STOP_TOKENS = {
    # units / sizes / packaging
    "ליטר", "ל", "מל", "מיליליטר", "גרם", "גר", "ג", "קג", "קילו", "קילוגרם",
    "יחידה", "יח", "יחי", "מארז", "שישיה", "שישייה", "אריזה", "אריזת", "זוג",
    "חבילה", "בקבוק", "פחית", "קרטון", "ק", "מ", "כ",
    # common Hebrew function words
    "על", "של", "עם", "או", "גם", "את", "אל",
}
# Cap matches per basket item so a generic word can't blow up the price query.
_MATCH_CAP = 80


def _norm_name(name: str | None) -> str:
    """Trim and collapse inner whitespace so the same item matches by name."""
    return _NAME_WS.sub(" ", (name or "").strip())


def prominent_tokens(name: str | None, limit: int = 3) -> list[str]:
    """The first few brand/product words of a name (skip numbers/sizes/units).

    'קוקה קולה שישיה 1.5 ליטר' → ['קוקה', 'קולה']
    """
    out: list[str] = []
    for raw in re.split(r"[\s\-/,]+", _norm_name(name)):
        word = _FT_OPERATORS.sub("", raw).strip("'\"").strip()
        if len(word) < 2 or _NUMERIC.match(word) or word in _STOP_TOKENS:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


def _match_product_ids(db: Session, tokens: list[str]) -> list[int]:
    """Product ids whose name overlaps the given prominent tokens.

    Primary: FULLTEXT boolean search requiring each token as a prefix.
    Fallback: LIKE on the first token (covers tokens below the FULLTEXT
    minimum length, or when boolean search finds nothing).
    """
    if not tokens:
        return []
    expr = " ".join(f"+{t}*" for t in tokens)
    rows = db.execute(
        text(
            """
            SELECT id FROM products
            WHERE MATCH(name) AGAINST (:expr IN BOOLEAN MODE)
            LIMIT :cap
            """
        ),
        {"expr": expr, "cap": _MATCH_CAP},
    ).all()
    if rows:
        return [r[0] for r in rows]

    rows = db.execute(
        text("SELECT id FROM products WHERE name LIKE :like LIMIT :cap"),
        {"like": f"%{tokens[0]}%", "cap": _MATCH_CAP},
    ).all()
    return [r[0] for r in rows]


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _select_branches(ordered: list[dict], limit: int) -> list[dict]:
    """Cap the result to `limit` branches, favouring the cheapest while keeping
    a mix of chains: take the cheapest branch of each chain first, then fill the
    remaining slots with the next-cheapest branches. Display order is preserved.
    """
    if len(ordered) <= limit:
        return ordered

    rank_of = {s["store_id"]: i for i, s in enumerate(ordered)}
    chosen: list[dict] = []
    seen: set[int] = set()

    seen_chains: set[str] = set()
    for s in ordered:  # cheapest branch per chain (ordered is already cheapest-first)
        if s["chain_name"] not in seen_chains:
            seen_chains.add(s["chain_name"])
            chosen.append(s)
            seen.add(s["store_id"])

    for s in ordered:  # fill remaining slots with the cheapest branches left
        if len(chosen) >= limit:
            break
        if s["store_id"] not in seen:
            chosen.append(s)
            seen.add(s["store_id"])

    chosen = chosen[:limit]
    chosen.sort(key=lambda s: rank_of[s["store_id"]])
    return chosen


def build_comparison(
    city: str,
    pids: list[int],
    qty_by_pid: dict[int, Decimal],
    products: dict[int, dict],
    price_rows: list[dict],
    limit: int = 10,
) -> dict:
    """Pure ranking logic (no DB) — unit-testable.

    `price_rows` are dict rows with: store_id, chain_id, chain_name, store_name,
    address, city, product_id, price (Decimal). Results are capped to `limit`
    branches (cheapest-first, with a mix of chains).
    """
    # group prices by store; guard against accidental duplicates (keep cheapest)
    stores: dict[int, dict] = {}
    for r in price_rows:
        sid = r["store_id"]
        acc = stores.get(sid)
        if acc is None:
            acc = stores[sid] = {
                "store_id": sid,
                "chain_id": r["chain_id"],
                "chain_name": r["chain_name"],
                "store_name": r["store_name"],
                "address": r["address"],
                "city": r["city"],
                "prices": {},
            }
        pid = r["product_id"]
        price = r["price"]
        if price is not None and (acc["prices"].get(pid) is None or price < acc["prices"][pid]):
            acc["prices"][pid] = price

    result_stores: list[dict] = []
    for acc in stores.values():
        items_out = []
        total = Decimal("0")
        missing: list[int] = []
        for pid in pids:
            qty = qty_by_pid[pid]
            price = acc["prices"].get(pid)
            if price is None:
                missing.append(pid)
                items_out.append(
                    {
                        "product_id": pid,
                        "quantity": float(qty),
                        "unit_price": None,
                        "line_total": None,
                        "found": False,
                    }
                )
            else:
                line = price * qty
                total += line
                items_out.append(
                    {
                        "product_id": pid,
                        "quantity": float(qty),
                        "unit_price": _money(price),
                        "line_total": _money(line),
                        "found": True,
                    }
                )
        result_stores.append(
            {
                "store_id": acc["store_id"],
                "chain_id": acc["chain_id"],
                "chain_name": acc["chain_name"],
                "store_name": acc["store_name"],
                "address": acc["address"],
                "city": acc["city"],
                "total": _money(total),
                "found_count": len(pids) - len(missing),
                "missing_count": len(missing),
                "missing_product_ids": missing,
                "is_complete": not missing,
                "rank": None,
                "items": items_out,
            }
        )

    # complete stores compete for the winner; incomplete are shown but unranked
    complete = [s for s in result_stores if s["is_complete"]]
    incomplete = [s for s in result_stores if not s["is_complete"]]
    complete.sort(key=lambda s: s["total"])
    for i, s in enumerate(complete, start=1):
        s["rank"] = i
    # show the "closest to complete, then cheapest" incomplete stores first
    incomplete.sort(key=lambda s: (s["missing_count"], s["total"]))

    ordered = complete + incomplete
    winner_id = complete[0]["store_id"] if complete else None
    shown = _select_branches(ordered, limit)  # cap to `limit` branches

    return {
        "city": city,
        "requested_product_ids": pids,
        "products": [
            products.get(pid, {"id": pid, "name": None, "barcode": None}) for pid in pids
        ],
        "store_count": len(ordered),          # total branches found in the city
        "complete_store_count": len(complete),
        "shown_store_count": len(shown),      # branches actually returned (≤ limit)
        "winner_store_id": winner_id,
        "stores": shown,
    }


def compare_basket(db: Session, city: str, items: list) -> dict:
    """Compare a basket with fuzzy, text-based product matching.

    Because the chains use different barcodes AND different name wordings for the
    same item, both barcode and exact-name matching drop competing chains. For
    each basket item we instead take its prominent words and FULLTEXT-match every
    overlapping product in the DB, so 'קוקה קולה שישיה' also reaches Rami Levy's
    'קוקה קולה 1.5'. Each basket item keeps a representative id, and per store we
    take the cheapest matching product to represent that item (so the response
    shape — and the UI — stay unchanged).
    """
    empty = {
        "city": city,
        "requested_product_ids": [],
        "products": [],
        "store_count": 0,
        "complete_store_count": 0,
        "winner_store_id": None,
        "stores": [],
    }

    # 1. one basket line per distinct submitted id; sum quantities for repeats
    qty_by_repr: dict[int, Decimal] = {}
    for it in items:
        qty_by_repr[it.product_id] = qty_by_repr.get(it.product_id, Decimal("0")) + Decimal(
            str(it.quantity)
        )
    repr_ids = list(qty_by_repr)
    if not repr_ids:
        return empty

    # 2. names (+ barcode for display) of the submitted products
    sub_rows = db.execute(
        text("SELECT id, name, barcode FROM products WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": repr_ids},
    ).mappings().all()
    id_info = {r["id"]: r for r in sub_rows}

    repr_ids = [rid for rid in repr_ids if rid in id_info]
    if not repr_ids:
        return empty
    products_meta = {
        rid: {
            "id": rid,
            "name": (id_info[rid]["name"] or "").strip(),
            "barcode": id_info[rid]["barcode"],
        }
        for rid in repr_ids
    }

    # 3. fuzzy-match each basket item to overlapping products (a product may map
    #    to several basket lines — emitted once per line later).
    pid_to_reprs: dict[int, set[int]] = defaultdict(set)
    for rid in repr_ids:
        pid_to_reprs[rid].add(rid)  # the item itself is always a candidate
        for pid in _match_product_ids(db, prominent_tokens(id_info[rid]["name"])):
            pid_to_reprs[pid].add(rid)

    # 4. prices for every matched product in the requested city
    price_rows = db.execute(
        text(
            """
            SELECT s.id AS store_id, s.chain_id, s.chain_name, s.store_name,
                   s.address, s.city, pr.product_id, pr.price
            FROM stores s
            JOIN prices pr ON pr.store_id = s.id
            WHERE s.city = :city AND pr.product_id IN :pids
            """
        ).bindparams(bindparam("pids", expanding=True)),
        {"city": city, "pids": list(pid_to_reprs)},
    ).mappings().all()

    # 5. fan each price row out to the basket line(s) it matched; build_comparison
    #    then keeps the MIN price per (store, item) = cheapest matching product.
    remapped: list[dict] = []
    for r in price_rows:
        for rep in pid_to_reprs.get(r["product_id"], ()):
            row = dict(r)
            row["product_id"] = rep
            remapped.append(row)

    return build_comparison(city, repr_ids, qty_by_repr, products_meta, remapped)
