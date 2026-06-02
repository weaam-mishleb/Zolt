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
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

_TWO_PLACES = Decimal("0.01")
_NAME_WS = re.compile(r"\s+")


def _norm_name(name: str | None) -> str:
    """Trim and collapse inner whitespace so the same item matches by name."""
    return _NAME_WS.sub(" ", (name or "").strip())


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def build_comparison(
    city: str,
    pids: list[int],
    qty_by_pid: dict[int, Decimal],
    products: dict[int, dict],
    price_rows: list[dict],
) -> dict:
    """Pure ranking logic (no DB) — unit-testable.

    `price_rows` are dict rows with: store_id, chain_id, chain_name, store_name,
    address, city, product_id, price (Decimal).
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

    return {
        "city": city,
        "requested_product_ids": pids,
        "products": [
            products.get(pid, {"id": pid, "name": None, "barcode": None}) for pid in pids
        ],
        "store_count": len(ordered),
        "complete_store_count": len(complete),
        "winner_store_id": winner_id,
        "stores": ordered,
    }


def compare_basket(db: Session, city: str, items: list) -> dict:
    """Compare a basket by PRODUCT NAME, not strictly by barcode.

    Chains use different barcodes/PLUs for the same item, so matching only the
    submitted product_ids drops competing chains. Instead:
      1. look up the names of the submitted product_ids,
      2. normalize them (trim + collapse whitespace),
      3. find every product in the DB that shares those names,
      4. for each store take the cheapest barcode per name, and
      5. group/rank by name (each name keeps one representative id so the
         existing response shape — and the UI — stay unchanged).
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

    submitted_ids = list({it.product_id for it in items})
    if not submitted_ids:
        return empty

    # 1. names (+ barcode for display) of the submitted products
    sub_rows = db.execute(
        text("SELECT id, name, barcode FROM products WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": submitted_ids},
    ).mappings().all()
    id_info = {r["id"]: r for r in sub_rows}

    # 2. one representative id + summed quantity per normalized name
    repr_for_name: dict[str, int] = {}
    qty_by_repr: dict[int, Decimal] = {}
    products_meta: dict[int, dict] = {}
    for it in items:
        info = id_info.get(it.product_id)
        if info is None:
            continue
        norm = _norm_name(info["name"])
        if not norm:
            continue
        rep = repr_for_name.get(norm)
        if rep is None:
            rep = it.product_id  # first submitted id for this name represents it
            repr_for_name[norm] = rep
            products_meta[rep] = {
                "id": rep,
                "name": (info["name"] or "").strip(),
                "barcode": info["barcode"],
            }
            qty_by_repr[rep] = Decimal("0")
        qty_by_repr[rep] += Decimal(str(it.quantity))

    repr_ids = list(qty_by_repr)
    if not repr_ids:
        return empty

    names = list(repr_for_name)

    # 3. every product (any barcode) whose normalized name matches → its rep id
    match_rows = db.execute(
        text(
            """
            SELECT id, name FROM products
            WHERE REGEXP_REPLACE(TRIM(name), '[[:space:]]+', ' ') IN :names
            """
        ).bindparams(bindparam("names", expanding=True)),
        {"names": names},
    ).mappings().all()
    pid_to_repr: dict[int, int] = {}
    for r in match_rows:
        rep = repr_for_name.get(_norm_name(r["name"]))
        if rep is not None:
            pid_to_repr[r["id"]] = rep
    for rep in repr_ids:  # always include the submitted ids themselves
        pid_to_repr.setdefault(rep, rep)

    # 4. prices for all those barcodes in the requested city
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
        {"city": city, "pids": list(pid_to_repr)},
    ).mappings().all()

    # 5. collapse every barcode onto its name's representative id — build_comparison
    #    then keeps the MIN price per (store, name) = cheapest barcode per store.
    remapped = []
    for r in price_rows:
        row = dict(r)
        row["product_id"] = pid_to_repr[r["product_id"]]
        remapped.append(row)

    return build_comparison(city, repr_ids, qty_by_repr, products_meta, remapped)
