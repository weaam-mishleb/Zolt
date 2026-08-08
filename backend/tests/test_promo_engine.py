"""Unit tests for the promotion rule engine (pure, no DB).

Every case states the arithmetic it expects, because "the cart got cheaper" is
not a testable claim — "₪75 became ₪65 because one bundle of 2 saved ₪10" is.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from backend.app.services.promo_engine import (
    BasketLine,
    Promotion,
    price_basket,
)

NOW = datetime(2026, 6, 15, 12, 0, 0)
D = Decimal


def line(cid, qty, price, name=None):
    return BasketLine(canonical_id=cid, quantity=D(str(qty)), unit_price=D(str(price)), name=name)


def promo(pid=1, kind="FIXED_PRICE", ids=(1,), **kw):
    defaults = dict(
        min_qty=D("1"), max_qty=None, discounted_price=None, discount_rate=None,
        discount_amount=None, min_basket_amount=None, allow_stacking=False,
        starts_at=None, ends_at=None,
    )
    for k, v in kw.items():
        defaults[k] = D(str(v)) if isinstance(v, (int, float)) and k not in {
            "allow_stacking"
        } else v
    return Promotion(id=pid, reward_kind=kind, canonical_ids=frozenset(ids), **defaults)


# ── baseline ────────────────────────────────────────────────────────────────
def test_no_promotions_leaves_the_total_untouched():
    r = price_basket([line(1, 2, 10), line(2, 1, 5)], [], NOW)
    assert r["base_total"] == 25.0
    assert r["final_total"] == 25.0
    assert r["total_savings"] == 0.0
    assert r["applied_promotions"] == []


def test_empty_basket_is_zero_not_an_error():
    r = price_basket([], [promo()], NOW)
    assert r["base_total"] == 0.0 and r["final_total"] == 0.0


# ── FIXED_PRICE — 'ב-9.90' ──────────────────────────────────────────────────
def test_fixed_price_discounts_every_unit():
    # 3 × ₪12 = ₪36; promo price ₪9 → saves ₪3 per unit = ₪9
    r = price_basket([line(1, 3, 12)], [promo(kind="FIXED_PRICE", discounted_price=9)], NOW)
    assert r["total_savings"] == 9.0
    assert r["final_total"] == 27.0


def test_fixed_price_is_ignored_when_it_is_not_cheaper():
    """A 'promo' above the shelf price must never raise the bill."""
    r = price_basket([line(1, 2, 8)], [promo(kind="FIXED_PRICE", discounted_price=10)], NOW)
    assert r["total_savings"] == 0.0
    assert r["applied_promotions"] == []


# ── BUNDLE_PRICE — '2 ב-40' ─────────────────────────────────────────────────
def test_bundle_price_forms_whole_bundles_only():
    # 3 × ₪25 = ₪75. One bundle of 2 (₪50 → ₪40) saves ₪10; the 3rd is full price.
    r = price_basket([line(1, 3, 25)], [promo(kind="BUNDLE_PRICE", min_qty=2, discounted_price=40)], NOW)
    assert r["total_savings"] == 10.0
    assert r["final_total"] == 65.0


def test_bundle_below_min_qty_does_not_apply():
    r = price_basket([line(1, 1, 25)], [promo(kind="BUNDLE_PRICE", min_qty=2, discounted_price=40)], NOW)
    assert r["total_savings"] == 0.0


def test_bundle_pools_units_across_eligible_lines():
    """'מאגדת שלגוני 2 ב-40' is valid across flavours — a real case from the feed."""
    lines = [line(1, 1, 25, "תות"), line(2, 1, 22, "מלון")]
    p = promo(kind="BUNDLE_PRICE", ids=(1, 2), min_qty=2, discounted_price=40)
    r = price_basket(lines, [p], NOW)
    assert r["base_total"] == 47.0
    assert r["total_savings"] == 7.0           # 47 − 40
    assert r["final_total"] == 40.0


def test_bundle_takes_the_dearest_units_first():
    """With more units than fit one bundle, discounting the dearest saves most."""
    lines = [line(1, 1, 30), line(2, 1, 20), line(3, 1, 10)]
    p = promo(kind="BUNDLE_PRICE", ids=(1, 2, 3), min_qty=2, discounted_price=40)
    r = price_basket(lines, [p], NOW)
    # bundle takes 30+20 = 50 → 40 (saves 10); the ₪10 unit stays full price
    assert r["total_savings"] == 10.0
    assert r["final_total"] == 50.0


# ── PCT_OFF ─────────────────────────────────────────────────────────────────
def test_percent_off():
    r = price_basket([line(1, 2, 10)], [promo(kind="PCT_OFF", discount_rate=D("0.30"))], NOW)
    assert r["total_savings"] == 6.0
    assert r["final_total"] == 14.0


def test_literal_100_percent_coupon_cannot_create_a_free_cart():
    """Yellow publishes conditional Elbit bundle components as 100% line discounts."""
    lines = [line(1, 1, 9.90, "XL"), line(2, 1, 9.90, "במבה")]
    promos = [
        promo(pid=1, kind="PCT_OFF", ids=(1,), discount_rate=D("1.00")),
        promo(pid=2, kind="PCT_OFF", ids=(2,), discount_rate=D("1.00")),
    ]

    r = price_basket(lines, promos, NOW)

    assert r["base_total"] == 19.80
    assert r["final_total"] == 19.80
    assert r["total_savings"] == 0.0
    assert r["applied_promotions"] == []


@pytest.mark.parametrize("kind", ["FIXED_PRICE", "BUNDLE_PRICE"])
def test_zero_price_rule_is_not_treated_as_an_unconditional_giveaway(kind):
    p = promo(kind=kind, min_qty=1, discounted_price=0)
    assert price_basket([line(1, 1, 10)], [p], NOW)["final_total"] == 10.0


# ── AMOUNT_OFF — basket-level threshold ─────────────────────────────────────
def test_amount_off_applies_when_the_threshold_is_met():
    p = promo(kind="AMOUNT_OFF", discount_amount=20, min_basket_amount=100)
    r = price_basket([line(1, 1, 120)], [p], NOW)
    assert r["total_savings"] == 20.0
    assert r["final_total"] == 100.0


def test_amount_off_does_not_apply_below_the_threshold():
    p = promo(kind="AMOUNT_OFF", discount_amount=20, min_basket_amount=100)
    r = price_basket([line(1, 1, 80)], [p], NOW)
    assert r["total_savings"] == 0.0


def test_amount_off_never_exceeds_the_basket():
    p = promo(kind="AMOUNT_OFF", discount_amount=500, min_basket_amount=10)
    r = price_basket([line(1, 1, 50)], [p], NOW)
    assert r["final_total"] == 0.0            # clamped, never negative


# ── NTH_FREE — '1+1' ────────────────────────────────────────────────────────
def test_one_plus_one_gives_one_free_unit_per_pair():
    # 4 × ₪10; buy-2-get-1 → 2 free units → saves ₪20
    r = price_basket([line(1, 4, 10)], [promo(kind="NTH_FREE", min_qty=2)], NOW)
    assert r["total_savings"] == 20.0
    assert r["final_total"] == 20.0


def test_nth_free_makes_the_cheapest_unit_free():
    """Retailers free the cheapest eligible unit — assuming otherwise overstates."""
    lines = [line(1, 1, 30), line(2, 1, 10)]
    r = price_basket(lines, [promo(kind="NTH_FREE", ids=(1, 2), min_qty=2)], NOW)
    assert r["total_savings"] == 10.0          # the ₪10 one, not the ₪30 one


def test_nth_free_needs_a_full_set():
    r = price_basket([line(1, 1, 10)], [promo(kind="NTH_FREE", min_qty=2)], NOW)
    assert r["total_savings"] == 0.0


def test_same_item_nth_free_with_threshold_one_is_rejected():
    p = promo(kind="NTH_FREE", min_qty=1)
    assert price_basket([line(1, 1, 10)], [p], NOW)["final_total"] == 10.0


def test_buy_one_trigger_get_different_gift_still_allows_threshold_one():
    p = Promotion(
        id=1,
        reward_kind="NTH_FREE",
        canonical_ids=frozenset({1}),
        gift_canonical_ids=frozenset({2}),
        min_qty=D("1"),
    )
    r = price_basket([line(1, 1, 20), line(2, 1, 5)], [p], NOW)
    assert r["final_total"] == 20.0


# ── max_qty cap ('מוגבל ל-3') ───────────────────────────────────────────────
def test_max_qty_caps_the_discounted_units():
    # 5 × ₪12, promo price ₪9 but capped at 3 units → saves 3 × ₪3 = ₪9
    p = promo(kind="FIXED_PRICE", discounted_price=9, max_qty=3)
    r = price_basket([line(1, 5, 12)], [p], NOW)
    assert r["total_savings"] == 9.0


# ── time bounds ─────────────────────────────────────────────────────────────
def test_expired_promotion_is_ignored():
    p = promo(kind="FIXED_PRICE", discounted_price=5,
              starts_at=NOW - timedelta(days=10), ends_at=NOW - timedelta(days=1))
    assert price_basket([line(1, 1, 10)], [p], NOW)["total_savings"] == 0.0


def test_future_promotion_is_ignored():
    p = promo(kind="FIXED_PRICE", discounted_price=5, starts_at=NOW + timedelta(days=1))
    assert price_basket([line(1, 1, 10)], [p], NOW)["total_savings"] == 0.0


def test_open_ended_promotion_is_active():
    """The feed very often omits dates — absent bounds must not mean 'inactive'."""
    p = promo(kind="FIXED_PRICE", discounted_price=5, starts_at=None, ends_at=None)
    assert price_basket([line(1, 1, 10)], [p], NOW)["total_savings"] == 5.0


def test_promotion_active_exactly_within_window():
    p = promo(kind="FIXED_PRICE", discounted_price=5,
              starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(hours=1))
    assert price_basket([line(1, 1, 10)], [p], NOW)["total_savings"] == 5.0


# ── conflicts ───────────────────────────────────────────────────────────────
def test_non_stacking_promotions_compete_and_the_best_one_wins():
    """1+1 vs 30% off on the same item — only the better one may apply."""
    lines = [line(1, 2, 100)]                       # ₪200
    one_plus_one = promo(pid=1, kind="NTH_FREE", min_qty=2, allow_stacking=False)   # saves 100
    thirty_off = promo(pid=2, kind="PCT_OFF", discount_rate=D("0.30"), allow_stacking=False)  # saves 60
    r = price_basket(lines, [thirty_off, one_plus_one], NOW)
    assert r["total_savings"] == 100.0
    assert [p["id"] for p in r["applied_promotions"]] == [1]


def test_stacking_promotions_may_combine():
    lines = [line(1, 2, 100)]
    a = promo(pid=1, kind="PCT_OFF", discount_rate=D("0.10"), allow_stacking=True)   # 20
    b = promo(pid=2, kind="PCT_OFF", discount_rate=D("0.20"), allow_stacking=True)   # 40
    r = price_basket(lines, [a, b], NOW)
    assert r["total_savings"] == 60.0
    assert len(r["applied_promotions"]) == 2


def test_promotions_on_different_products_do_not_conflict():
    lines = [line(1, 1, 50), line(2, 1, 50)]
    a = promo(pid=1, kind="FIXED_PRICE", ids=(1,), discounted_price=40)
    b = promo(pid=2, kind="FIXED_PRICE", ids=(2,), discounted_price=45)
    r = price_basket(lines, [a, b], NOW)
    assert r["total_savings"] == 15.0            # 10 + 5
    assert len(r["applied_promotions"]) == 2


def test_basket_level_discount_stacks_with_a_line_discount():
    """AMOUNT_OFF consumes no units, so it never blocks a per-item promo."""
    lines = [line(1, 1, 120)]
    line_promo = promo(pid=1, kind="FIXED_PRICE", discounted_price=100)      # saves 20
    basket_promo = promo(pid=2, kind="AMOUNT_OFF", discount_amount=10, min_basket_amount=100)
    r = price_basket(lines, [line_promo, basket_promo], NOW)
    assert r["total_savings"] == 30.0
    assert len(r["applied_promotions"]) == 2


def test_non_stacking_promo_cannot_ride_along_with_a_stacking_one():
    """Regression: `allowmultiplediscounts=0` means EXCLUSIVE on those units.

    An earlier version only blocked promos that arrived *after* a non-stacking
    one, so a stacking promo evaluated first could illegally combine with it.
    """
    lines = [line(1, 1, 100)]
    stacking = promo(pid=1, kind="PCT_OFF", discount_rate=D("0.10"), allow_stacking=True)   # 10
    exclusive = promo(pid=2, kind="PCT_OFF", discount_rate=D("0.50"), allow_stacking=False)  # 50
    r = price_basket(lines, [stacking, exclusive], NOW)
    assert len(r["applied_promotions"]) == 1
    assert r["applied_promotions"][0]["id"] == 2          # the exclusive, better one
    assert r["total_savings"] == 50.0


def test_exact_resolution_beats_naive_best_first():
    """Two small promos together may beat one big one — greedy-by-value alone
    would take the big one and stop."""
    lines = [line(1, 1, 100), line(2, 1, 100)]
    big = promo(pid=1, kind="PCT_OFF", ids=(1, 2), discount_rate=D("0.30"),
                allow_stacking=False)                      # 60 across both items
    small_a = promo(pid=2, kind="PCT_OFF", ids=(1,), discount_rate=D("0.40"),
                    allow_stacking=False)                  # 40 on item 1
    small_b = promo(pid=3, kind="PCT_OFF", ids=(2,), discount_rate=D("0.40"),
                    allow_stacking=False)                  # 40 on item 2
    r = price_basket(lines, [big, small_a, small_b], NOW)
    assert r["total_savings"] == 80.0                      # 40 + 40, not 60
    assert {p["id"] for p in r["applied_promotions"]} == {2, 3}


def test_promotions_touching_no_common_product_are_independent():
    lines = [line(i, 1, 10) for i in range(1, 6)]
    promos = [
        promo(pid=i, kind="FIXED_PRICE", ids=(i,), discounted_price=8, allow_stacking=False)
        for i in range(1, 6)
    ]
    r = price_basket(lines, promos, NOW)
    assert len(r["applied_promotions"]) == 5
    assert r["total_savings"] == 10.0                      # 5 × ₪2


# ── gift items that are a DIFFERENT product ─────────────────────────────────
def test_gift_of_another_product_discounts_the_gift_line():
    """'קנה 2 X קבל Y' — the free unit comes from the gift product."""
    lines = [line(1, 2, 50, "X"), line(2, 1, 30, "Y")]
    p = Promotion(
        id=1, reward_kind="NTH_FREE", canonical_ids=frozenset({1}),
        gift_canonical_ids=frozenset({2}), min_qty=D("2"),
    )
    r = price_basket(lines, [p], NOW)
    assert r["total_savings"] == 30.0                      # the gift, not an X
    assert r["final_total"] == 100.0


def test_gift_not_in_the_basket_yields_no_discount():
    """An offer the shopper did not take is not a saving."""
    lines = [line(1, 2, 50, "X")]
    p = Promotion(
        id=1, reward_kind="NTH_FREE", canonical_ids=frozenset({1}),
        gift_canonical_ids=frozenset({2}), min_qty=D("2"),
    )
    assert price_basket(lines, [p], NOW)["total_savings"] == 0.0


def test_gift_equal_to_the_trigger_behaves_as_same_item_one_plus_one():
    lines = [line(1, 2, 50)]
    p = Promotion(
        id=1, reward_kind="NTH_FREE", canonical_ids=frozenset({1}),
        gift_canonical_ids=frozenset({1}), min_qty=D("2"),
    )
    assert price_basket(lines, [p], NOW)["total_savings"] == 50.0


# ── output contract (what the UI renders) ───────────────────────────────────
def test_line_carries_original_and_discounted_totals_for_the_ui():
    r = price_basket([line(1, 2, 10, "במבה")], [promo(kind="PCT_OFF", discount_rate=D("0.50"))], NOW)
    ln = r["lines"][0]
    assert ln["original_line_total"] == 20.0
    assert ln["line_total"] == 10.0
    assert ln["applied_promotion"]["reward_kind"] == "PCT_OFF"
    assert ln["name"] == "במבה"


def test_untouched_line_has_no_promotion_attached():
    lines = [line(1, 1, 10), line(2, 1, 10)]
    r = price_basket(lines, [promo(kind="FIXED_PRICE", ids=(1,), discounted_price=5)], NOW)
    by_id = {ln["canonical_id"]: ln for ln in r["lines"]}
    assert by_id[1]["applied_promotion"] is not None
    assert by_id[2]["applied_promotion"] is None
    assert by_id[2]["line_total"] == by_id[2]["original_line_total"]


def test_savings_are_attributed_across_the_lines_a_bundle_consumed():
    lines = [line(1, 1, 25), line(2, 1, 22)]
    p = promo(kind="BUNDLE_PRICE", ids=(1, 2), min_qty=2, discounted_price=40)
    r = price_basket(lines, [p], NOW)
    # both lines were consumed by the bundle, so both show a discount
    assert all(ln["line_total"] < ln["original_line_total"] for ln in r["lines"])
    # and the parts still add up to the whole
    assert sum(ln["line_total"] for ln in r["lines"]) == pytest.approx(r["final_total"])


# ── safety ──────────────────────────────────────────────────────────────────
def test_unknown_reward_kind_never_invents_a_discount():
    r = price_basket([line(1, 1, 10)], [promo(kind="UNKNOWN")], NOW)
    assert r["total_savings"] == 0.0


def test_total_is_never_negative():
    p1 = promo(pid=1, kind="AMOUNT_OFF", discount_amount=80, min_basket_amount=1)
    p2 = promo(pid=2, kind="AMOUNT_OFF", discount_amount=80, min_basket_amount=1)
    r = price_basket([line(1, 1, 100)], [p1, p2], NOW)
    assert r["final_total"] >= 0.0


def test_promotion_for_an_item_not_in_the_basket_is_ignored():
    r = price_basket([line(1, 1, 10)], [promo(kind="FIXED_PRICE", ids=(999,), discounted_price=1)], NOW)
    assert r["total_savings"] == 0.0


# ── cheapest-wins: a per-unit price against a bundle ────────────────────────
#
# These pin a behaviour that has already been mistaken for a bug once.
#
# The rule being locked: whichever offer produces the lower total wins, and a
# bundle never becomes mandatory just because it exists. A per-unit FIXED_PRICE
# beating a bundle is legitimate and must not be "corrected" into forced bundle
# arithmetic, which would charge more than the shop does.
#
# NOTE on the case that first raised this: the production example was
# 'קרוסייל תכנית אוגוסט- אקסל ב5' — ₪5.00 per unit against a ₪9.90 shelf price.
# That one turned out to be a CROSS-SALE, conditional on buying a sandwich, and it
# is now rejected outright before the engine sees it (see is_conditional in
# services/promotions.py and its tests). These cases therefore describe an
# unconditional per-unit price, which is a real thing the feed also carries — the
# selection rule below is correct, it was the input that was not.
def test_per_unit_price_beats_a_more_expensive_bundle():
    per_unit = promo(pid=1, kind="FIXED_PRICE", discounted_price=5)      # ₪5 each
    bundle = promo(pid=2, kind="BUNDLE_PRICE", min_qty=3, discounted_price=17)
    r = price_basket([line(1, 3, "9.90")], [per_unit, bundle], NOW)
    # 3 × ₪5.00 = ₪15.00, not the ₪17.00 bundle
    assert r["final_total"] == pytest.approx(15.0)
    assert r["base_total"] == pytest.approx(29.70)
    assert r["lines"][0]["applied_promotion"]["id"] == 1


def test_bundle_beats_a_more_expensive_per_unit_price():
    """The mirror image — the same rule has to pick the bundle when it is cheaper,
    otherwise 'cheapest wins' is really 'per-unit always wins'."""
    per_unit = promo(pid=1, kind="FIXED_PRICE", discounted_price=8)      # 3 × 8 = 24
    bundle = promo(pid=2, kind="BUNDLE_PRICE", min_qty=3, discounted_price=17)
    r = price_basket([line(1, 3, "9.90")], [per_unit, bundle], NOW)
    assert r["final_total"] == pytest.approx(17.0)
    assert r["lines"][0]["applied_promotion"]["id"] == 2


def test_a_single_unit_gets_the_per_unit_price_and_not_a_third_of_the_bundle():
    """One unit, both offers present. ₪5.00 is the real per-unit deal; ₪17/3 =
    ₪5.67 is not a price anyone offers and must never be charged."""
    per_unit = promo(pid=1, kind="FIXED_PRICE", discounted_price=5)
    bundle = promo(pid=2, kind="BUNDLE_PRICE", min_qty=3, discounted_price=17)
    r = price_basket([line(1, 1, "9.90")], [per_unit, bundle], NOW)
    assert r["final_total"] == pytest.approx(5.0)
    assert r["lines"][0]["applied_promotion"]["id"] == 1


def test_one_unit_with_only_a_bundle_pays_full_price():
    """Without a per-unit offer the single unit is simply not discounted — the
    bundle is not prorated."""
    bundle = promo(pid=2, kind="BUNDLE_PRICE", min_qty=3, discounted_price=17)
    r = price_basket([line(1, 1, "9.90")], [bundle], NOW)
    assert r["final_total"] == pytest.approx(9.90)
    assert r["total_savings"] == 0.0


def test_bundle_remainder_stays_at_shelf_price():
    """4 units against a 3-for bundle: one bundle plus one unit at ₪9.90.
    This is the modulo arithmetic, stated as money rather than as an operator."""
    bundle = promo(pid=2, kind="BUNDLE_PRICE", min_qty=3, discounted_price=17)
    r = price_basket([line(1, 4, "9.90")], [bundle], NOW)
    assert r["final_total"] == pytest.approx(17.0 + 9.90)


# ── the corrected production case, priced end to end ────────────────────────
#
# With the cross-sale filtered out upstream, the only offer left on the XL line is
# the genuine "3 ב-17" bundle. These state, in money, what the register charges.
_XL_SHELF = "9.90"
_XL_BUNDLE = promo(pid=2, kind="BUNDLE_PRICE", min_qty=3, discounted_price=17)


@pytest.mark.parametrize(
    "qty, expected",
    [
        (1, 9.90),           # one can is NOT ₪5, and NOT ₪17/3 — it is shelf price
        (2, 19.80),          # still short of the bundle
        (3, 17.00),          # exactly one bundle
        (4, 26.90),          # one bundle + one unit at shelf price
        (6, 34.00),          # two bundles
        (7, 43.90),          # two bundles + one unit
    ],
)
def test_xl_line_matches_the_register(qty, expected):
    r = price_basket([line(1, qty, _XL_SHELF)], [_XL_BUNDLE], NOW)
    assert r["final_total"] == pytest.approx(expected)


def test_a_conditional_cross_sale_reaching_the_engine_would_change_the_price():
    """Not a wish — a guard rail. It documents WHY the filter has to live upstream:
    the engine is pure arithmetic and cannot know that ₪5 had a condition, so if
    one ever gets through, the price silently becomes wrong rather than failing."""
    cross_sale = promo(pid=1, kind="FIXED_PRICE", discounted_price=5)
    r = price_basket([line(1, 1, _XL_SHELF)], [cross_sale, _XL_BUNDLE], NOW)
    assert r["final_total"] == pytest.approx(5.0)   # the price we must never quote
