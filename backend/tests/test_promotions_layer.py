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


# ── the runner-up ("יש גם 3 ב-17") ──────────────────────────────────────────
from decimal import Decimal as _D  # noqa: E402

from backend.app.services.promo_engine import Promotion as _Promo  # noqa: E402
from backend.app.services.promotions import _attach_alternative, _unit_cost  # noqa: E402


def _p(pid, kind, min_qty, price, ids=(7,)):
    return _Promo(
        id=pid, reward_kind=kind, canonical_ids=frozenset(ids), gift_canonical_ids=frozenset(),
        min_qty=_D(str(min_qty)), max_qty=None, discounted_price=_D(str(price)),
        discount_rate=None, discount_amount=None, min_basket_amount=None,
        allow_stacking=False, description=f"{min_qty} ב-{price}", starts_at=None, ends_at=None,
    )


def _store(applied_id, charged, qty=1, unit_price="9.90"):
    return {
        "store_id": 1,
        "items": [{
            "product_id": 99, "quantity": qty, "unit_price": float(unit_price),
            "line_total": charged, "found": True,
            "applied_promotion": {"id": applied_id}, "available_promotion": None,
            "alternative_promotion": None,
        }],
    }


def test_unit_cost_divides_a_bundle_but_not_a_per_unit_price():
    assert _unit_cost(_p(1, "FIXED_PRICE", 1, 5)) == _D("5")
    assert _unit_cost(_p(2, "BUNDLE_PRICE", 3, 17)) == _D("17") / _D("3")
    # kinds whose value depends on the shelf price or the basket are not quotable
    # as "₪X a unit", so they must not become a runner-up claim
    assert _unit_cost(_Promo(
        id=3, reward_kind="PCT_OFF", canonical_ids=frozenset({7}), gift_canonical_ids=frozenset(),
        min_qty=_D("1"), max_qty=None, discounted_price=None, discount_rate=_D("0.3"),
        discount_amount=None, min_basket_amount=None, allow_stacking=False,
        description=None, starts_at=None, ends_at=None,
    )) is None


def test_the_beaten_bundle_is_reported_as_the_runner_up():
    """Production case: ₪5 a unit was charged, "3 ב-17" existed and lost."""
    store = _store(applied_id=1, charged=5.0)
    _attach_alternative(store, [_p(1, "FIXED_PRICE", 1, 5), _p(2, "BUNDLE_PRICE", 3, 17)], {99: 7})
    alt = store["items"][0]["alternative_promotion"]
    assert alt["id"] == 2 and alt["min_qty"] == 3.0 and alt["discounted_price"] == 17.0


def test_a_cheaper_rival_is_never_advertised_as_the_runner_up():
    """If something cheaper turns up here the engine would have taken it, so this
    combination means something is wrong — stay silent rather than publish a
    contradiction telling the shopper we charged more than we had to."""
    store = _store(applied_id=1, charged=5.0)
    _attach_alternative(store, [_p(1, "FIXED_PRICE", 1, 5), _p(2, "BUNDLE_PRICE", 3, 9)], {99: 7})
    assert store["items"][0]["alternative_promotion"] is None


def test_an_undiscounted_line_gets_no_runner_up():
    """`available_promotion` covers those; the two must never both be populated."""
    store = _store(applied_id=1, charged=9.90)
    store["items"][0]["applied_promotion"] = None
    _attach_alternative(store, [_p(2, "BUNDLE_PRICE", 3, 17)], {99: 7})
    assert store["items"][0]["alternative_promotion"] is None


def test_the_applied_promotion_is_not_reported_against_itself():
    store = _store(applied_id=2, charged=17.0, qty=3)
    _attach_alternative(store, [_p(2, "BUNDLE_PRICE", 3, 17)], {99: 7})
    assert store["items"][0]["alternative_promotion"] is None


# ── conditional promotions must never price a basket ────────────────────────
#
# The bug this closes: 'קרוסייל תכנית אוגוסט- אקסל ב5' arrives as FIXED_PRICE,
# min_qty=1, ₪5.00 — indistinguishable from an unconditional price cut. Yellow
# sells one XL for ₪9.90; the ₪5 is conditional on buying a sandwich. Quoting ₪5
# for a single can is a price the register will not honour.
import dataclasses  # noqa: E402

from datetime import datetime  # noqa: E402

import pytest  # noqa: E402

from backend.app.services.promotions import is_conditional  # noqa: E402


@pytest.mark.parametrize(
    "description",
    [
        "קרוסייל תכנית אוגוסט- אקסל ב5",   # the production string
        "קרוס סייל אוגוסט",
        "מבצע מותנה בהצגת כרטיס",
        "בקניה מעל 50 ש\"ח",
        "בקנייה של כריך",                  # the two-yod spelling
        "קופון אלביט-2 חטיפים",
        "קופון במבה קלאסי",
    ],
)
def test_conditional_descriptions_are_rejected(description):
    assert is_conditional(description) is True


@pytest.mark.parametrize(
    "description",
    [
        "משקה אנרגיה  250 מ ל 3ב19XL",
        "במבה פרו/קלאסי/יום הולדת 80 גרם 3ב20",
        "סודה נורדיק 3ב16",
        "אקסל ב5",           # a genuine unconditional per-unit price survives
        None,
        "",
    ],
)
def test_unconditional_descriptions_survive(description):
    assert is_conditional(description) is False


def test_a_conditional_promotion_is_never_advertised_either():
    """`_worth_advertising` gates the badge. Even though the loader already drops
    these, the badge must not be the one place a fabricated deal leaks through."""
    from backend.app.services.promotions import _worth_advertising

    cross_sale = _Promo(
        id=1, reward_kind="FIXED_PRICE", canonical_ids=frozenset({7}),
        gift_canonical_ids=frozenset(), min_qty=_D("1"), max_qty=None,
        discounted_price=_D("5"), discount_rate=None, discount_amount=None,
        min_basket_amount=None, allow_stacking=False,
        description="קרוסייל תכנית אוגוסט- אקסל ב5", starts_at=None, ends_at=None,
    )
    assert _worth_advertising(cross_sale, _D("9.90")) is False
    # the same numbers without the conditional wording are fine
    honest = dataclasses.replace(cross_sale, description="אקסל ב5")
    assert _worth_advertising(honest, _D("9.90")) is True


@pytest.mark.parametrize(
    "description, rejected",
    [
        ("/ במבה 80ג ב3AR", True),        # the production string
        ("ב5AR", True),
        ("AR", True),
        ("מבצע AR מיוחד", True),
        # "AR" inside a Latin word is NOT the marker — these must survive, or the
        # filter quietly deletes whole brands.
        ("CARLSBERG 500 מל", False),
        ("ארטיק MARS", False),
        ("SPARKLING WATER", False),
        ("במבה 80 גרם 3ב20", False),
        # 'ארוחה' is deliberately NOT blacklisted: measured against production it
        # matches 26 active descriptions and every one is a PRODUCT name
        # ("ארוחה נודלס 110-120גרם 2ב20" — instant noodles), never a meal deal.
        ("ארוחה נודלס 110-120גרם 2ב20", False),
    ],
)
def test_the_AR_meal_deal_marker_is_latin_letter_bounded(description, rejected):
    assert is_conditional(description) is rejected


# ── the single-unit floor is convenience-store only ─────────────────────────
#
# Same arithmetic, opposite meaning by retail format: a forecourt shop cutting one
# snack 70% is nearly always describing a meal deal it never encoded, while a
# supermarket doing it is running a loss leader to pull you through the door.
# Enforcing it everywhere refused ~10,354 real promotions, 5,645 of them Shufersal.
@pytest.mark.parametrize(
    "chain_id, chain, enforced",
    [
        ("7290644700005", "פז-yellow", True),
        ("7290492000005", "דור אלון", True),
        ("7290027600007", "שופרסל", False),
        ("7290058140886", "רמי לוי", False),
        ("7290055700007", "קרפור", False),
        (None, "unknown chain", False),
        ("", "empty", False),
    ],
)
def test_only_convenience_chains_enforce_the_single_unit_floor(chain_id, chain, enforced):
    from backend.app.services.promotions import enforces_single_unit_floor

    assert enforces_single_unit_floor(chain_id) is enforced, chain


@pytest.mark.parametrize(
    "single_unit_floor, expected",
    [
        (True, 9.90),    # forecourt: the ₪3.00 cut is refused, shelf price stands
        (False, 3.00),   # supermarket: the same cut is a legitimate loss leader
    ],
)
def test_the_same_promotion_prices_differently_by_retail_format(single_unit_floor, expected):
    from backend.app.services.promo_engine import BasketLine, price_basket

    bamba = _Promo(
        id=1, reward_kind="FIXED_PRICE", canonical_ids=frozenset({7}),
        gift_canonical_ids=frozenset(), min_qty=_D("1"), max_qty=None,
        discounted_price=_D("3.00"), discount_rate=None, discount_amount=None,
        min_basket_amount=None, allow_stacking=False,
        description="במבה 80ג ב3", starts_at=None, ends_at=None,
    )
    line = BasketLine(canonical_id=7, quantity=_D("1"), unit_price=_D("9.90"))
    r = price_basket([line], [bamba], datetime(2026, 6, 15), single_unit_floor=single_unit_floor)
    assert r["final_total"] == pytest.approx(expected)


def test_bundles_are_exempt_from_the_floor_at_a_convenience_store_too():
    """A bundle states its own condition, so the format scoping must not change it."""
    from backend.app.services.promo_engine import BasketLine, price_basket

    bundle = _Promo(
        id=2, reward_kind="BUNDLE_PRICE", canonical_ids=frozenset({7}),
        gift_canonical_ids=frozenset(), min_qty=_D("3"), max_qty=None,
        discounted_price=_D("9.00"), discount_rate=None, discount_amount=None,
        min_basket_amount=None, allow_stacking=False,
        description="3 ב-9", starts_at=None, ends_at=None,
    )
    lines = [BasketLine(canonical_id=7, quantity=_D("3"), unit_price=_D("9.90"))]
    r = price_basket(lines, [bundle], datetime(2026, 6, 15), single_unit_floor=True)
    assert r["final_total"] == pytest.approx(9.0)
