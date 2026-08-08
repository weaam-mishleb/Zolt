"""Tests for the promotion→comparison bridge (DB-free parts).

The behaviour that matters here is the one the whole feature exists for:
the branch that is cheapest BEFORE discounts is often not the cheapest AFTER
them, and the ranking has to reflect that.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.exc import OperationalError

from backend.app.services.promo_engine import Promotion
from backend.app.services.promotions import (
    _SELECT_ACTIVE,
    _attach_available,
    _rerank,
    apply_promotions,
)


def store(sid, total, *, complete=True, missing=0, chain="שופרסל"):
    return {
        "store_id": sid,
        "chain_id": str(sid),
        "chain_name": chain,
        "store_name": f"branch {sid}",
        "address": None,
        "city": "תל אביב",
        "total": total,
        "base_total": total,
        "total_savings": 0.0,
        "applied_promotions": [],
        "found_count": 1,
        "missing_count": missing,
        "missing_product_ids": [],
        "is_complete": complete,
        "rank": None,
        "pct_above_cheapest": None,
        "items": [],
    }


def result(stores):
    return {
        "city": "תל אביב",
        "requested_product_ids": [1],
        "products": [],
        "stores": stores,
        "store_count": len(stores),
        "complete_store_count": 0,
        "shown_store_count": 0,
        "winner_store_id": None,
        "message": None,
    }


def test_a_branch_that_only_wins_after_a_discount_becomes_the_winner():
    """THE point of the feature: base price 110 beats 100 once a promo lands."""
    cheap_base = store(1, 100.0)                       # no promotion
    discounted = store(2, 85.0)                        # was 110, promo took 25
    discounted["base_total"], discounted["total_savings"] = 110.0, 25.0

    res = result([cheap_base, discounted])
    _rerank(res, limit=10)

    assert res["winner_store_id"] == 2
    assert res["stores"][0]["store_id"] == 2
    assert res["stores"][0]["rank"] == 1
    assert res["stores"][1]["rank"] == 2


def test_ranking_uses_the_final_total_not_the_base():
    res = result([store(1, 90.0), store(2, 80.0), store(3, 95.0)])
    _rerank(res, limit=10)
    assert [s["store_id"] for s in res["stores"]] == [2, 1, 3]
    assert [s["rank"] for s in res["stores"]] == [1, 2, 3]


def test_pct_above_cheapest_is_recomputed_against_post_promo_prices():
    res = result([store(1, 100.0), store(2, 80.0)])
    _rerank(res, limit=10)
    by_id = {s["store_id"]: s for s in res["stores"]}
    assert by_id[2]["pct_above_cheapest"] == 0.0
    assert by_id[1]["pct_above_cheapest"] == 25.0      # (100−80)/80


def test_incomplete_branches_stay_unranked_and_last():
    res = result([
        store(1, 50.0, complete=False, missing=1),      # cheapest, but incomplete
        store(2, 90.0),
    ])
    _rerank(res, limit=10)
    assert res["winner_store_id"] == 2                  # a complete branch wins
    assert res["stores"][0]["store_id"] == 2
    incomplete = next(s for s in res["stores"] if s["store_id"] == 1)
    assert incomplete["rank"] is None
    assert incomplete["pct_above_cheapest"] is None


def test_no_complete_branch_means_no_winner():
    res = result([store(1, 50.0, complete=False, missing=1)])
    _rerank(res, limit=10)
    assert res["winner_store_id"] is None
    assert res["complete_store_count"] == 0


def test_cap_applies_after_repricing_and_keeps_the_winner():
    """The cap must never drop the branch that just won on a discount."""
    stores = [store(i, 100.0 + i, chain=f"chain{i % 3}") for i in range(1, 21)]
    stores[-1]["total"] = 1.0                          # last one becomes cheapest
    res = result(stores)
    _rerank(res, limit=10)

    assert res["store_count"] == 20
    assert res["shown_store_count"] == 10
    assert res["winner_store_id"] == 20
    assert res["stores"][0]["store_id"] == 20          # winner is shown, and first


def test_limit_none_shows_every_branch():
    res = result([store(i, float(i)) for i in range(1, 16)])
    _rerank(res, limit=None)
    assert res["shown_store_count"] == 15


def test_available_promotion_rejects_bad_deal_before_selecting_threshold():
    """An expensive qty=1 meal deal must not hide a genuine qty=3 bundle."""
    line = {
        "product_id": 7,
        "found": True,
        "quantity": 1,
        "unit_price": 9.90,
        "applied_promotion": None,
        "available_promotion": None,
    }
    meal_deal = Promotion(
        id=1,
        reward_kind="FIXED_PRICE",
        canonical_ids=frozenset({42}),
        min_qty=Decimal("1"),
        discounted_price=Decimal("34.90"),
        description="meal deal",
    )
    bundle = Promotion(
        id=2,
        reward_kind="BUNDLE_PRICE",
        canonical_ids=frozenset({42}),
        min_qty=Decimal("3"),
        discounted_price=Decimal("16"),
        description="3 for 16",
    )

    _attach_available({"items": [line]}, [meal_deal, bundle], {7: 42})

    assert line["available_promotion"]["id"] == 2
    assert line["available_promotion"]["units_needed"] == 2.0


def test_available_promotion_never_advertises_a_100_percent_coupon():
    line = {
        "product_id": 7,
        "found": True,
        "quantity": 1,
        "unit_price": 9.90,
        "applied_promotion": None,
        "available_promotion": None,
    }
    coupon = Promotion(
        id=1,
        reward_kind="PCT_OFF",
        canonical_ids=frozenset({42}),
        discount_rate=Decimal("1"),
        description="restricted coupon",
    )

    _attach_available({"items": [line]}, [coupon], {7: 42})

    assert line["available_promotion"] is None


def test_active_query_excludes_club_and_unmodelled_free_promotions():
    sql = " ".join(str(_SELECT_ACTIVE).split())
    assert "p.club_id IS NULL" in sql
    assert "0 - כלל הלקוחות" in sql
    assert "p.discount_rate < 1" in sql
    assert "p.discounted_price > 0" in sql


def test_empty_store_list_is_handled():
    res = result([])
    _rerank(res, limit=10)
    assert res["winner_store_id"] is None
    assert res["stores"] == []


# ── graceful degradation ────────────────────────────────────────────────────
class _BrokenDB:
    """A session whose promotion queries fail — e.g. the tables don't exist yet."""

    def __init__(self):
        self.rolled_back = False

    def execute(self, *_a, **_kw):
        raise OperationalError("SELECT 1", {}, Exception("table does not exist"))

    def rollback(self):
        self.rolled_back = True


class _EmptyDB:
    def execute(self, *_a, **_kw):
        class _R:
            def all(self_inner):
                return []

            def mappings(self_inner):
                return self_inner

        return _R()

    def rollback(self):
        pass


def test_missing_promotion_tables_degrade_to_base_prices():
    """Comparing prices is the product; promotions are a bonus on top.

    Before the schema is migrated the tables simply are not there — the request
    must still return a correct, ranked comparison instead of a 500.
    """
    db = _BrokenDB()
    res = result([store(1, 90.0), store(2, 80.0)])
    out = apply_promotions(db, res, limit=10)

    assert out["winner_store_id"] == 2
    assert [s["rank"] for s in out["stores"]] == [1, 2]
    assert db.rolled_back, "a poisoned session must be rolled back before reuse"


def test_unresolved_products_degrade_to_base_prices():
    """Entity resolution has not run yet → nothing maps → plain comparison."""
    res = result([store(1, 90.0), store(2, 80.0)])
    out = apply_promotions(_EmptyDB(), res, limit=10)
    assert out["winner_store_id"] == 2
    assert out["stores"][0]["total"] == 80.0


def test_degraded_result_still_ranks_and_caps():
    stores = [store(i, float(100 - i)) for i in range(1, 16)]
    out = apply_promotions(_BrokenDB(), result(stores), limit=10)
    assert out["shown_store_count"] == 10
    assert out["store_count"] == 15
    assert out["winner_store_id"] == 15          # cheapest (100−15)
