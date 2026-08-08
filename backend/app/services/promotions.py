"""Bridge between the database and the pure promotion rule engine.

Everything DB-shaped lives here; `promo_engine` stays pure. This module:
  1. resolves the basket's products to canonical ids (product_map),
  2. loads the promotions active at the candidate branches right now,
  3. hands both to the engine, per branch,
  4. re-ranks the branches by their POST-promotion price.

Step 4 is the point of the whole feature: the cheapest branch before discounts
is frequently not the cheapest after them.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .promo_engine import BasketLine, Promotion, price_basket

log = logging.getLogger("zolt.promotions")

_SELECT_CANONICAL = text(
    "SELECT m.product_id, m.canonical_id FROM product_map m WHERE m.product_id IN :pids"
).bindparams(bindparam("pids", expanding=True))

_SELECT_ACTIVE = text(
    """
    SELECT p.id, p.store_id, p.reward_kind, p.min_qty, p.max_qty,
           p.discounted_price, p.discount_rate, p.discount_amount,
           p.min_basket_amount, p.allow_stacking, p.description,
           p.starts_at, p.ends_at, pi.canonical_id, pi.is_gift
    FROM promotions p
    JOIN promotion_items pi ON pi.promotion_id = p.id
    WHERE p.store_id IN :stores
      AND pi.canonical_id IN :canon
      AND (p.starts_at IS NULL OR p.starts_at <= :now)
      AND (p.ends_at   IS NULL OR p.ends_at   >= :now)
      -- The comparison has no club/coupon identity. Applying a restricted
      -- price as though every shopper receives it is worse than omitting it.
      AND (p.club_id IS NULL
           OR TRIM(p.club_id) IN ('', '0', '0 - כלל הלקוחות'))
      -- A literal free line in the GROUPS feed is commonly one component of a
      -- coupon/employee/meal entitlement. Until we model its other required
      -- groups, it must not enter either basket pricing or the upsell badge.
      AND (p.reward_kind <> 'PCT_OFF'
           OR p.discount_rate IS NULL OR p.discount_rate < 1)
      AND (p.reward_kind NOT IN ('FIXED_PRICE', 'BUNDLE_PRICE')
           OR p.discounted_price IS NULL OR p.discounted_price > 0)
    """
).bindparams(bindparam("stores", expanding=True), bindparam("canon", expanding=True))


def canonical_ids_for(db: Session, product_ids: list[int]) -> dict[int, int]:
    """{product_id: canonical_id} for the basket's representative products."""
    if not product_ids:
        return {}
    return {
        pid: cid
        for pid, cid in db.execute(_SELECT_CANONICAL, {"pids": product_ids}).all()
    }


def load_active_promotions(
    db: Session, store_ids: list[int], canonical_ids: list[int], now: datetime
) -> dict[int, list[Promotion]]:
    """{store_id: [Promotion]} — only promotions valid *now* that touch the basket."""
    if not store_ids or not canonical_ids:
        return {}

    rows = db.execute(
        _SELECT_ACTIVE, {"stores": store_ids, "canon": canonical_ids, "now": now}
    ).mappings().all()

    # A promotion spans several items, so rows are folded back into one object.
    acc: dict[tuple[int, int], dict] = {}
    for r in rows:
        key = (r["store_id"], r["id"])
        entry = acc.get(key)
        if entry is None:
            entry = acc[key] = {
                "id": r["id"],
                "store_id": r["store_id"],
                "reward_kind": r["reward_kind"],
                "min_qty": r["min_qty"] or Decimal("1"),
                "max_qty": r["max_qty"],
                "discounted_price": r["discounted_price"],
                "discount_rate": r["discount_rate"],
                "discount_amount": r["discount_amount"],
                "min_basket_amount": r["min_basket_amount"],
                "allow_stacking": bool(r["allow_stacking"]),
                "description": r["description"],
                "starts_at": r["starts_at"],
                "ends_at": r["ends_at"],
                "items": set(),
                "gifts": set(),
            }
        (entry["gifts"] if r["is_gift"] else entry["items"]).add(r["canonical_id"])

    out: dict[int, list[Promotion]] = {}
    for entry in acc.values():
        promo = Promotion(
            id=entry["id"],
            reward_kind=entry["reward_kind"],
            canonical_ids=frozenset(entry["items"]),
            gift_canonical_ids=frozenset(entry["gifts"]),
            min_qty=entry["min_qty"],
            max_qty=entry["max_qty"],
            discounted_price=entry["discounted_price"],
            discount_rate=entry["discount_rate"],
            discount_amount=entry["discount_amount"],
            min_basket_amount=entry["min_basket_amount"],
            allow_stacking=entry["allow_stacking"],
            description=entry["description"],
            starts_at=entry["starts_at"],
            ends_at=entry["ends_at"],
        )
        out.setdefault(entry["store_id"], []).append(promo)
    return out


def _reprice_store(store: dict, promos: list[Promotion], canon_of: dict[int, int],
                   now: datetime) -> None:
    """Apply promotions to one branch, in place."""
    lines = [
        BasketLine(
            canonical_id=canon_of[it["product_id"]],
            quantity=Decimal(str(it["quantity"])),
            unit_price=Decimal(str(it["unit_price"])),
            product_id=it["product_id"],
        )
        for it in store["items"]
        if it["found"] and it["product_id"] in canon_of
    ]
    if not lines:
        return

    priced = price_basket(lines, promos, now)
    if priced["total_savings"] <= 0:
        return

    by_canonical = {ln["canonical_id"]: ln for ln in priced["lines"]}
    for it in store["items"]:
        cid = canon_of.get(it["product_id"])
        priced_line = by_canonical.get(cid) if cid is not None else None
        if not it["found"] or priced_line is None:
            it["original_line_total"] = it["line_total"]
            it["applied_promotion"] = None
            continue
        it["original_line_total"] = priced_line["original_line_total"]
        it["line_total"] = priced_line["line_total"]
        it["applied_promotion"] = priced_line["applied_promotion"]

    # `total` stays the number the branch is ranked and displayed by.
    store["base_total"] = priced["base_total"]
    store["total"] = priced["final_total"]
    store["total_savings"] = priced["total_savings"]
    store["applied_promotions"] = priced["applied_promotions"]


def _ensure_promo_fields(stores: list[dict]) -> None:
    """Give every branch the promotion fields, discounted or not.

    The response shape must not depend on whether promotions happened to apply —
    a client should never have to handle 'this key exists sometimes'.
    """
    for store in stores:
        store.setdefault("base_total", store["total"])
        store.setdefault("total_savings", 0.0)
        store.setdefault("applied_promotions", [])
        for item in store.get("items", []):
            item.setdefault("original_line_total", item.get("line_total"))
            item.setdefault("applied_promotion", None)
            item.setdefault("available_promotion", None)


def _worth_advertising(promo: Promotion, unit_price: Decimal) -> bool:
    """Would this promotion actually make the line CHEAPER?

    The guard is the whole reason this is not a one-liner. A FIXED_PRICE or
    BUNDLE_PRICE promotion can sit above the shelf price — production has a
    'גריל עוף' promo at ₪40.90 on a line priced ₪6.90, and the engine correctly
    declines to apply it. Advertising it as a deal would be worse than showing
    nothing: it invents a saving that does not exist and asks the shopper to buy
    more to get it.
    """
    kind = promo.reward_kind
    if kind == "FIXED_PRICE":
        return (
            promo.discounted_price is not None
            and 0 < promo.discounted_price < unit_price
        )
    if kind == "BUNDLE_PRICE":
        return (
            promo.discounted_price is not None
            and promo.discounted_price > 0
            and promo.min_qty >= 1
            and promo.discounted_price < unit_price * promo.min_qty
        )
    if kind == "PCT_OFF":
        return bool(promo.discount_rate and 0 < promo.discount_rate < 1)
    if kind == "NTH_FREE":
        # Buy-one-X-get-Y may legitimately have min_qty=1. A same-item
        # NTH_FREE with min_qty=1 means every unit is free and is another feed
        # encoding of an unmodelled entitlement.
        has_separate_gift = bool(promo.gift_canonical_ids - promo.canonical_ids)
        return promo.min_qty >= (1 if has_separate_gift else 2)
    # AMOUNT_OFF is basket-level — it is not about this product, so a badge on
    # this line would be misleading. UNKNOWN has no numbers to reason about.
    return False


def _attach_available(store: dict, promos: list[Promotion], canon_of: dict[int, int]) -> None:
    """Surface a promotion the shopper has not unlocked yet, per line.

    Only ever set where `applied_promotion` is None, so the two cannot both be
    populated and the UI never has to choose between them.
    """
    by_canonical: dict[int, list[Promotion]] = {}
    for promo in promos:
        for cid in promo.canonical_ids:
            by_canonical.setdefault(cid, []).append(promo)

    for item in store["items"]:
        if not item.get("found") or item.get("applied_promotion") or item.get("unit_price") is None:
            continue
        unit_price = Decimal(str(item["unit_price"]))
        candidates = by_canonical.get(canon_of.get(item["product_id"]), ())
        # Reject fake/expensive deals before ranking reachability. Otherwise a
        # qty=1 meal deal above shelf price shadows a real qty=3 bundle merely
        # because its threshold sorts first.
        promo = min(
            (candidate for candidate in candidates if _worth_advertising(candidate, unit_price)),
            key=lambda candidate: (candidate.min_qty, candidate.id),
            default=None,
        )
        if promo is None:
            continue
        short = float(promo.min_qty) - float(item["quantity"])
        item["available_promotion"] = {
            "id": promo.id,
            "reward_kind": promo.reward_kind,
            "description": promo.description,
            "min_qty": float(promo.min_qty),
            "discounted_price": float(promo.discounted_price) if promo.discounted_price is not None else None,
            "discount_rate": float(promo.discount_rate) if promo.discount_rate is not None else None,
            "discount_amount": float(promo.discount_amount) if promo.discount_amount is not None else None,
            "min_basket_amount": float(promo.min_basket_amount) if promo.min_basket_amount is not None else None,
            "units_needed": short if short > 0 else None,
        }


def _rerank(result: dict, limit: int | None) -> None:
    """Re-apply the ranking rules against post-promotion totals."""
    from .comparison import _select_branches   # local import avoids a cycle

    stores = result["stores"]
    complete = [s for s in stores if s["is_complete"]]
    incomplete = [s for s in stores if not s["is_complete"]]
    complete.sort(key=lambda s: s["total"])
    for i, s in enumerate(complete, start=1):
        s["rank"] = i
    incomplete.sort(key=lambda s: (s["missing_count"], s["total"]))
    for s in incomplete:
        s["rank"] = None

    if complete and complete[0]["total"]:
        cheapest = complete[0]["total"]
        for s in complete:
            s["pct_above_cheapest"] = round((s["total"] - cheapest) / cheapest * 100, 1)
    for s in incomplete:
        s["pct_above_cheapest"] = None

    ordered = complete + incomplete
    shown = _select_branches(ordered, limit) if limit else ordered
    result["winner_store_id"] = complete[0]["store_id"] if complete else None
    result["complete_store_count"] = len(complete)
    result["store_count"] = len(ordered)
    result["shown_store_count"] = len(shown)
    result["stores"] = shown


def apply_promotions(
    db: Session, result: dict, *, now: datetime | None = None, limit: int | None = 10
) -> dict:
    """Layer promotions onto a comparison result and re-rank by final price.

    Best-effort by design: if entity resolution has not run, or no promotion is
    active, the comparison is returned exactly as it came in. A missing
    promotion must degrade to the old (correct) base-price behaviour rather
    than fail the request.
    """
    now = now or datetime.now()
    stores = result.get("stores") or []
    product_ids = result.get("requested_product_ids") or []
    result.setdefault("promotions_applied", False)
    _ensure_promo_fields(stores)           # uniform shape on every code path

    if not stores or not product_ids:
        _rerank(result, limit)
        return result

    # Comparing prices is the product; promotions are an enhancement on top.
    # If the promotion tables are missing (schema not migrated yet) or the
    # queries fail for any reason, fall back to base-price comparison rather
    # than failing the request — the user still gets a correct, useful answer.
    try:
        canon_of = canonical_ids_for(db, product_ids)
        if not canon_of:                   # entity resolution has not run yet
            _rerank(result, limit)
            return result

        promos_by_store = load_active_promotions(
            db,
            [s["store_id"] for s in stores],
            sorted(set(canon_of.values())),
            now,
        )
    except SQLAlchemyError:
        log.warning("promotions unavailable — serving base prices", exc_info=True)
        db.rollback()                      # the session may be poisoned; keep it usable
        _rerank(result, limit)
        return result

    for store in stores:
        promos = promos_by_store.get(store["store_id"])
        if promos:
            _reprice_store(store, promos, canon_of, now)
            # After repricing, so it can see which lines actually got a discount
            # and leave those alone.
            _attach_available(store, promos, canon_of)

    _rerank(result, limit)
    result["promotions_applied"] = any(s.get("total_savings", 0) > 0 for s in stores)
    return result
