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

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

_TWO_PLACES = Decimal("0.01")


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
    """Resolve basket prices from the DB, then rank via `build_comparison`."""
    # merge duplicate product ids by summing their quantities
    qty_by_pid: dict[int, Decimal] = {}
    for it in items:
        qty_by_pid[it.product_id] = qty_by_pid.get(it.product_id, Decimal("0")) + Decimal(
            str(it.quantity)
        )
    pids = list(qty_by_pid.keys())

    empty = {
        "city": city,
        "requested_product_ids": pids,
        "products": [],
        "store_count": 0,
        "complete_store_count": 0,
        "winner_store_id": None,
        "stores": [],
    }
    if not pids:
        return empty

    prod_rows = db.execute(
        text("SELECT id, name, barcode FROM products WHERE id IN :pids").bindparams(
            bindparam("pids", expanding=True)
        ),
        {"pids": pids},
    ).mappings().all()
    products = {
        r["id"]: {"id": r["id"], "name": r["name"], "barcode": r["barcode"]} for r in prod_rows
    }

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
        {"city": city, "pids": pids},
    ).mappings().all()

    result = build_comparison(city, pids, qty_by_pid, products, [dict(r) for r in price_rows])
    return result
