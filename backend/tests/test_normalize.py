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

from etl.normalize import GTIN_MIN_LENGTH, first_alias, normalize_price, product_key

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


# ── namespacing internal item codes ─────────────────────────────────────────
KING_STORE = "7290058108879"
STOP_MARKET = "7290639000004"


def test_a_short_internal_code_is_namespaced_to_its_chain():
    assert product_key("12", KING_STORE) == f"{KING_STORE}_12"


def test_a_real_gtin_is_left_global():
    """EAN-13, UPC-A and EAN-8 mean the same product everywhere — namespacing
    them would destroy the cross-chain matching this system exists to do."""
    assert product_key("7290000066318", KING_STORE) == "7290000066318"   # EAN-13
    assert product_key("123456789012", KING_STORE) == "123456789012"     # UPC-A
    assert product_key("12345678", KING_STORE) == "12345678"             # EAN-8


def test_the_boundary_is_exactly_gtin_min_length():
    assert GTIN_MIN_LENGTH == 8
    at = "1" * GTIN_MIN_LENGTH
    below = "1" * (GTIN_MIN_LENGTH - 1)
    assert product_key(at, KING_STORE) == at
    assert product_key(below, KING_STORE) == f"{KING_STORE}_{below}"


def test_the_same_short_code_in_two_chains_stops_colliding():
    """THE regression. products.barcode is globally UNIQUE, so before this,
    item '12' — tomatoes in Stop Market, frozen cod in King Store — collapsed
    into one product row and the comparison ranked them against each other."""
    assert product_key("12", KING_STORE) != product_key("12", STOP_MARKET)


def test_one_chain_keeps_one_namespace_across_its_slugs():
    """mahsani_ashuk and mahsani_ashuk_new_source are two slugs for chain
    7290661400001. Keying on the chain id — not our slug — is what stops that
    chain's own products splitting in half."""
    assert product_key("12", "7290661400001") == product_key("12", "7290661400001")


def test_product_key_passes_through_missing_input():
    assert product_key(None, KING_STORE) is None
    assert product_key("", KING_STORE) is None
    assert product_key("12", None) == "12"      # no chain to namespace against


def test_normalize_price_stores_the_namespaced_key():
    r = normalize_price(_row(itemcode="12", itemname="עגבניות"))
    assert r["barcode"] == f"{_IDENT['chainid']}_12"
    assert r["name"] == "עגבניות"


def test_promotions_resolve_on_the_same_key_the_price_loader_stores():
    """etl.promotions looks promo items up in products.barcode. If it kept using
    the raw feed code while the loader stored a namespaced one, every short-code
    promotion would resolve to nothing and only a counter would ever show it."""
    stored = normalize_price(_row(itemcode="12", itemname="עגבניות"))["barcode"]
    assert product_key("12", _IDENT["chainid"]) == stored


def test_a_gtin_row_is_unchanged_end_to_end():
    r = normalize_price(_row(itemname="חלב"))
    assert r["barcode"] == "7290000066318"


def test_the_nameless_fallback_still_equals_the_stored_barcode():
    """dq_check fails a chain on `name = barcode`, so the fallback has to be the
    same namespaced value that gets stored — or that gate stops firing."""
    r = normalize_price(_row(itemcode="12"))
    assert r["name"] == r["barcode"] == f"{_IDENT['chainid']}_12"


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
