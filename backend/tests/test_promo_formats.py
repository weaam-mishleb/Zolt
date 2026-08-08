"""Unit tests for the promotion-format adapters (DB-free).

Every case here is a real shape found in the dataset. The object-vs-array tests
guard the trap that silently dropped every single-item promotion in the first
version of the loader.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from etl.promo_formats import (
    EXTRACTORS,
    _as_list,
    _fit_rate,
    classify_reward,
    detect_family,
    extract_flat_family,
    extract_groups_family,
    extract_items_family,
)


# ── format detection ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "cols, expected",
    [
        (["chainid", "groups", "promotionid"], "groups"),
        (["chainid", "promotionitems", "promotionid"], "items"),
        (["chainid", "itemcode", "promotionid"], "flat"),
        (["chainid", "promotionid"], "unknown"),
        ([], "unknown"),
        # a file carrying both must resolve deterministically (groups wins)
        (["groups", "promotionitems"], "groups"),
    ],
)
def test_detect_family(cols, expected):
    assert detect_family(cols) == expected


def test_every_known_family_has_an_extractor():
    for fam in ("items", "groups", "flat"):
        assert fam in EXTRACTORS


# ── THE cardinality trap: {"item": {...}} vs {"item": [...]} ────────────────
def test_as_list_unwraps_single_object():
    assert _as_list({"item": {"itemcode": "1"}}, "item") == [{"itemcode": "1"}]


def test_as_list_keeps_array():
    node = {"item": [{"itemcode": "1"}, {"itemcode": "2"}]}
    assert len(_as_list(node, "item")) == 2


@pytest.mark.parametrize("node", [None, {}, {"item": None}, {"item": "x"}, []])
def test_as_list_tolerates_junk(node):
    assert _as_list(node, "item") == []


def test_items_family_single_item_is_not_dropped():
    """The exact regression: one item serialises as an OBJECT, not a list."""
    row = {
        "promotionid": "12952",
        "promotiondescription": "חומץ בלסמי-ב13.90",
        "discountedprice": "13.9",
        "promotionitems": json.dumps({"item": {"itemcode": "7290105966469", "isgiftitem": "0"}}),
    }
    (header, items), = extract_items_family(row)
    assert header["promo_id_src"] == "12952"
    assert [i["itemcode"] for i in items] == ["7290105966469"]


def test_items_family_multi_item_and_gifts():
    row = {
        "promotionid": "12951",
        "promotionitems": json.dumps({"item": [{"itemcode": "a"}, {"itemcode": "b"}]}),
        "giftsitems": json.dumps({"item": {"itemcode": "g"}}),
    }
    (header, items), = extract_items_family(row)
    assert header["has_gift"] is True
    assert {i["itemcode"] for i in items} == {"a", "b", "g"}
    assert [i["is_gift"] for i in items if i["itemcode"] == "g"] == [1]


# ── GROUPS family: pricing lives per-item, two levels deep ──────────────────
def _groups_row(n_groups=1):
    groups = [
        {
            "groupid": str(g + 1),
            "minpurchaseamount": "0.00",
            "discounttype": "NO_BODY",
            "promotionitems": {
                "promotionitem": [
                    {"itemcode": f"g{g}i{i}", "rewardtype": "10", "minqty": "2",
                     "maxqty": "NO_BODY", "discountrate": "30.50",
                     "discountedprice": "36.00"}
                    for i in range(2)
                ]
            },
        }
        for g in range(n_groups)
    ]
    node = {"group": groups[0] if n_groups == 1 else groups}
    return {
        "promotionid": "0001111223",
        "promotiondescription": "רביולי 2 ב 36",
        "promotionstartdatetime": "2026-06-01 00:00:00",
        "promotionenddatetime": "2026-07-04 23:59:00",
        "groups": json.dumps(node),
    }


def test_groups_family_hoists_per_item_pricing_to_the_header():
    (header, items), = extract_groups_family(_groups_row())
    assert header["discounted_price"] == 36
    assert header["min_qty"] == 2
    # 30.50 in the feed means 30.5%; the adapter stores it as a fraction.
    # Compared as Decimal — pytest.approx does not bridge Decimal/float.
    assert header["discount_rate"] == Decimal("0.305")
    assert header["max_qty"] is None                          # 'NO_BODY' → NULL
    assert header["min_basket_amount"] == 0
    assert len(items) == 2


# ── discount_rate has to fit DECIMAL(6,3) ───────────────────────────────────
def test_a_rate_that_cannot_be_a_fraction_is_dropped():
    """Victory ships 162 headers with rates like -94400. The whole-percent rule
    only fires above 1, so negatives were never normalized, and MySQL rejected
    the INSERT — killing that chain's entire promotion load over one row."""
    assert _fit_rate(Decimal("-94400")) is None
    assert _fit_rate(Decimal("1000")) is None
    assert _fit_rate(Decimal("-1000")) is None
    assert _fit_rate(None) is None


def test_storable_rates_are_left_alone():
    assert _fit_rate(Decimal("0.305")) == Decimal("0.305")
    assert _fit_rate(Decimal("999.999")) == Decimal("999.999")
    assert _fit_rate(Decimal("1.4286")) == Decimal("1.4286")   # real max in the feed
    assert _fit_rate(Decimal("-0.5")) == Decimal("-0.5")       # odd, but storable


def test_a_junk_rate_does_not_take_the_rest_of_the_promotion_with_it():
    """The row still loads; only the unusable field is dropped. These promos are
    FIXED_PRICE / NTH_FREE, which the engine prices from the other columns."""
    row = _groups_row()
    node = json.loads(row["groups"])
    node["group"]["promotionitems"]["promotionitem"][0]["discountrate"] = "-94400"
    row["groups"] = json.dumps(node)

    (header, items), = extract_groups_family(row)
    assert header["discount_rate"] is None
    assert header["discounted_price"] == 36        # still priced
    assert header["min_qty"] == 2
    assert len(items) == 2


def test_whole_percent_normalization_runs_before_the_bound():
    """3000 in the feed is 30%, and must survive — the bound is applied after
    the /100, not instead of it."""
    row = _groups_row()
    node = json.loads(row["groups"])
    node["group"]["promotionitems"]["promotionitem"][0]["discountrate"] = "3000"
    row["groups"] = json.dumps(node)

    (header, _), = extract_groups_family(row)
    assert header["discount_rate"] == Decimal("30")


def test_items_and_flat_families_guard_the_rate_too():
    items_row = {"promotionid": "1", "discountrate": "-94400",
                 "promotionitems": json.dumps({"item": {"itemcode": "123"}})}
    (header, _), = extract_items_family(items_row)
    assert header["discount_rate"] is None

    flat_row = {"promotionid": "1", "itemcode": "123", "discountrate": "-94400"}
    (header, _), = extract_flat_family(flat_row)
    assert header["discount_rate"] is None


def test_groups_family_keeps_multiple_groups_distinct():
    """Two groups under one promotionid must not collide on the unique key."""
    out = extract_groups_family(_groups_row(n_groups=2))
    assert len(out) == 2
    ids = {h["promo_id_src"] for h, _ in out}
    assert ids == {"0001111223#1", "0001111223#2"}


def test_groups_family_single_group_keeps_the_plain_id():
    (header, _), = extract_groups_family(_groups_row(n_groups=1))
    assert header["promo_id_src"] == "0001111223"


def test_groups_family_ignores_group_without_items():
    row = {"promotionid": "1", "groups": json.dumps({"group": {"groupid": "1"}})}
    assert extract_groups_family(row) == []


# ── FLAT family ─────────────────────────────────────────────────────────────
def test_flat_family_one_row_one_item():
    row = {"promotionid": "7", "itemcode": "123", "discountedprice": "9.9", "isgiftitem": "0"}
    (header, items), = extract_flat_family(row)
    assert header["promo_id_src"] == "7"
    assert items == [{"itemcode": "123", "is_gift": 0}]


def test_flat_family_without_itemcode_yields_no_items():
    (_, items), = extract_flat_family({"promotionid": "7", "itemcode": "NO_BODY"})
    assert items == []


# ── reward classification ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "header, expected",
    [
        ({"has_gift": True}, "NTH_FREE"),
        ({"discounted_price": 36, "min_qty": 2}, "BUNDLE_PRICE"),
        ({"discounted_price": 9.9, "min_qty": 1}, "FIXED_PRICE"),
        ({"discounted_price": 9.9}, "FIXED_PRICE"),          # min_qty absent → 1
        ({"discount_rate": 0.3}, "PCT_OFF"),
        ({"discounted_price": 0, "discount_rate": 1}, "UNKNOWN"),
        ({"min_basket_amount": 100}, "AMOUNT_OFF"),
        ({}, "UNKNOWN"),
        ({"discounted_price": 0, "discount_rate": 0}, "UNKNOWN"),
    ],
)
def test_classify_reward(header, expected):
    assert classify_reward(header) == expected


def test_gift_wins_over_price():
    """A gift promo priced as a bundle is still a 1+1 — the engine must see that."""
    assert classify_reward({"has_gift": True, "discounted_price": 36, "min_qty": 2}) == "NTH_FREE"
