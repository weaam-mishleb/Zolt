"""Unit tests for the basket comparison ranking logic (no DB required)."""
from __future__ import annotations

from decimal import Decimal

from backend.app.services.comparison import build_comparison, prominent_tokens

CITY = "תל אביב"
PRODUCTS = {
    1: {"id": 1, "name": "חלב", "barcode": "111"},
    2: {"id": 2, "name": "לחם", "barcode": "222"},
    3: {"id": 3, "name": "ביצים", "barcode": "333"},
}


def _row(store_id, chain, pid, price, name="branch"):
    return {
        "store_id": store_id,
        "chain_id": str(store_id),
        "chain_name": chain,
        "store_name": name,
        "address": "x",
        "city": CITY,
        "product_id": pid,
        "price": Decimal(str(price)),
    }


def _by_id(result):
    return {s["store_id"]: s for s in result["stores"]}


def test_winner_is_cheapest_complete_and_incomplete_excluded():
    pids = [1, 2, 3]
    qty = {1: Decimal("1"), 2: Decimal("2"), 3: Decimal("1")}
    rows = [
        # Rami Levy branch 10 — complete, total = 5 + 3*2 + 10 = 21
        _row(10, "רמי לוי", 1, 5), _row(10, "רמי לוי", 2, 3), _row(10, "רמי לוי", 3, 10),
        # Shufersal branch 20 — complete, total = 6 + 2*2 + 8 = 18 (cheapest)
        _row(20, "שופרסל", 1, 6), _row(20, "שופרסל", 2, 2), _row(20, "שופרסל", 3, 8),
        # Osher Ad branch 30 — INCOMPLETE (missing product 3), total = 3 + 2*2 = 7
        _row(30, "אושר עד", 1, 3), _row(30, "אושר עד", 2, 2),
    ]

    res = build_comparison(CITY, pids, qty, PRODUCTS, rows)
    stores = _by_id(res)

    # winner is the cheapest COMPLETE store, not the (cheaper) incomplete one
    assert res["winner_store_id"] == 20
    assert res["complete_store_count"] == 2
    assert res["store_count"] == 3  # incomplete store is still present/displayed

    # ranking among complete stores
    assert stores[20]["rank"] == 1 and stores[20]["total"] == 18.0
    assert stores[10]["rank"] == 2 and stores[10]["total"] == 21.0

    # incomplete store: shown, unranked, marked missing
    o = stores[30]
    assert o["rank"] is None
    assert o["is_complete"] is False
    assert o["missing_product_ids"] == [3]
    assert o["found_count"] == 2 and o["missing_count"] == 1
    assert o["total"] == 7.0  # partial total over found items only

    # the missing line is reported with found=False / null prices
    missing_line = next(i for i in o["items"] if i["product_id"] == 3)
    assert missing_line["found"] is False
    assert missing_line["unit_price"] is None and missing_line["line_total"] is None

    # complete stores are ordered before incomplete ones
    assert [s["store_id"] for s in res["stores"]] == [20, 10, 30]


def test_no_complete_store_yields_no_winner_but_still_lists_stores():
    pids = [1, 2, 3]
    qty = {1: Decimal("1"), 2: Decimal("1"), 3: Decimal("1")}
    rows = [
        _row(10, "רמי לוי", 1, 5), _row(10, "רמי לוי", 2, 5),   # missing 3
        _row(20, "שופרסל", 1, 4),                                  # missing 2,3
    ]
    res = build_comparison(CITY, pids, qty, PRODUCTS, rows)

    assert res["winner_store_id"] is None
    assert res["complete_store_count"] == 0
    assert res["store_count"] == 2
    # incomplete stores ordered by (fewest missing, then cheapest)
    assert [s["store_id"] for s in res["stores"]] == [10, 20]
    assert all(s["rank"] is None for s in res["stores"])


def test_cheapest_barcode_per_name_wins_in_store():
    # After name-grouping, two barcodes of the same item collapse onto one
    # representative id — the store must be costed with the cheaper barcode.
    pids = [1]
    qty = {1: Decimal("1")}
    rows = [
        _row(10, "רמי לוי", 1, "9.90"),
        _row(10, "רמי לוי", 1, "7.50"),  # same store + representative product
    ]
    res = build_comparison(CITY, pids, qty, PRODUCTS, rows)
    s = res["stores"][0]
    assert s["total"] == 7.5
    assert s["items"][0]["unit_price"] == 7.5


def test_prominent_tokens_skips_sizes_units_and_stopwords():
    assert prominent_tokens("קוקה קולה שישיה 1.5 ליטר") == ["קוקה", "קולה"]
    assert prominent_tokens("חלב תנובה 3% 1 ליטר") == ["חלב", "תנובה"]
    assert prominent_tokens("500 גרם") == []                 # only size/unit
    assert prominent_tokens("סוכריות על מקל") == ["סוכריות", "מקל"]  # 'על' dropped
    assert prominent_tokens("אבא אמא ילד בית גן") == ["אבא", "אמא", "ילד"]  # capped at 3


def test_quantity_multiplies_line_total():
    pids = [1]
    qty = {1: Decimal("3")}
    rows = [_row(10, "רמי לוי", 1, "4.50")]
    res = build_comparison(CITY, pids, qty, PRODUCTS, rows)
    s = res["stores"][0]
    assert s["total"] == 13.5
    assert s["items"][0]["unit_price"] == 4.5
    assert s["items"][0]["line_total"] == 13.5
