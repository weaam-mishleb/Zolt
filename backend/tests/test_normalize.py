"""Tests for price-row normalization, and specifically for column aliasing.

WHY THIS EXISTS
---------------
The price feed is not one schema. The three chains this project started with
(Shufersal, Rami Levy, Osher Ad) all ship `itemname`, so reading that one name
worked — and hid the fact that 10 of the other 30 chains ship `itemnm` instead.
Those chains loaded every product named after its own item code: full row
counts, green jobs, data that no name-based matching could ever use.

The header spellings asserted below are taken from the real chain files in the
Kaggle snapshot, not invented.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from etl.normalize import first_alias, normalize_price

# Identity columns every price row needs to survive normalization.
_IDENT = {"itemcode": "7290000066318", "chainid": "7290058140886", "storeid": "001"}


def _row(**extra) -> dict:
    return {**_IDENT, "itemprice": "12.90", **extra}


# ── first_alias ─────────────────────────────────────────────────────────────
def test_first_alias_prefers_the_earlier_name():
    assert first_alias({"a": "x", "b": "y"}, "a", "b") == "x"


def test_first_alias_falls_through_a_blank_column():
    """Chains that ship both spellings leave the alternate empty."""
    assert first_alias({"a": "", "b": "y"}, "a", "b") == "y"


def test_first_alias_falls_through_the_feeds_quote_placeholder():
    """Rami Levy fills the unused `itemnm` column with a literal ''."""
    assert first_alias({"itemname": "''", "itemnm": "חלב"}, "itemname", "itemnm") == "חלב"


def test_first_alias_returns_none_when_no_alias_carries_data():
    assert first_alias({"a": "", "b": None}, "a", "b") is None


# ── the itemnm regression ───────────────────────────────────────────────────
def test_itemnm_chains_get_a_real_product_name():
    """King Store, Good Pharm, Bareket and 7 more ship the name as `itemnm`."""
    r = normalize_price(_row(itemnm="פסטרמה מקסיקנית"))
    assert r["name"] == "פסטרמה מקסיקנית"
    assert r["name"] != r["barcode"]


def test_itemname_still_wins_where_both_columns_exist():
    r = normalize_price(_row(itemname="חלב תנובה 3%", itemnm="''"))
    assert r["name"] == "חלב תנובה 3%"


def test_a_genuinely_nameless_row_still_falls_back_to_the_barcode():
    """The column is NOT NULL, so the fallback stays — dq_check is what stops a
    whole chain from loading this way."""
    r = normalize_price(_row())
    assert r["name"] == "7290000066318"


# ── the other alias pairs ───────────────────────────────────────────────────
def test_manufacturer_is_read_under_either_spelling():
    assert normalize_price(_row(manufacturername="תנובה"))["manufacturer"] == "תנובה"
    assert normalize_price(_row(manufacturename="תנובה"))["manufacturer"] == "תנובה"


def test_price_update_time_is_read_under_either_spelling():
    want = datetime(2026, 6, 1, 10, 30)
    assert normalize_price(_row(priceupdatetime="2026-06-01 10:30:00"))["price_update_time"] == want
    assert normalize_price(_row(priceupdatedate="2026-06-01 10:30:00"))["price_update_time"] == want


def test_unit_of_measure_is_read_under_either_spelling():
    """Mahsani HaShuk and Het Cohen call it `unitmeasure`."""
    assert normalize_price(_row(unitofmeasure="100 גרם"))["unit_of_measure"] == "100 גרם"
    assert normalize_price(_row(unitmeasure="100 גרם"))["unit_of_measure"] == "100 גרם"


def test_is_weighted_is_read_under_either_spelling():
    """Wolt ships `blsweighted` — an l where every other chain has an i."""
    assert normalize_price(_row(bisweighted="1"))["is_weighted"] is True
    assert normalize_price(_row(blsweighted="1"))["is_weighted"] is True
    assert normalize_price(_row(bisweighted="0"))["is_weighted"] is False
    assert normalize_price(_row())["is_weighted"] is False


# ── unchanged behaviour the aliasing must not disturb ───────────────────────
def test_a_row_without_identity_or_price_is_rejected():
    assert normalize_price({"itemcode": "1", "chainid": "1"}) is None
    assert normalize_price(_row(itemprice="")) is None


def test_padded_store_codes_are_normalized_to_match_the_store_file():
    assert normalize_price(_row())["store_code"] == "1"


def test_price_is_decimal():
    assert normalize_price(_row())["price"] == Decimal("12.90")


def test_allow_discount_defaults_to_true_because_the_column_is_not_null():
    assert normalize_price(_row())["allow_discount"] is True
    assert normalize_price(_row(allowdiscount="0"))["allow_discount"] is False
