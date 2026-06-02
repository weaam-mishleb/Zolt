"""Row normalization for the Israeli "price transparency" CSV feed.

Both store and price rows are accessed *by column name* (not position) because
the chains ship the same column names in different orders (e.g. Shufersal's
store file orders/omits columns differently from Rami Levy / Osher Ad).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

# Values that mean "no data" in the feed.
_PLACEHOLDERS = {"", "לא ידוע", "none", "null", "nan", "na", "n/a"}

# Product fields carried from a price row into the products table.
PRODUCT_FIELDS = (
    "barcode",
    "name",
    "manufacturer",
    "unit_qty",
    "quantity",
    "unit_of_measure",
    "is_weighted",
)


def clean_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().strip("'").strip('"').strip()
    if s.lower() in _PLACEHOLDERS:
        return None
    return s


def norm_code(v) -> str | None:
    """Canonicalize store/sub-chain codes by stripping leading zeros so the
    padded price-feed codes ('001', '044') match the store-file codes ('1')."""
    s = clean_str(v)
    if s is None:
        return None
    return str(int(s)) if s.isdigit() else s


def to_decimal(v) -> Decimal | None:
    s = clean_str(v)
    if s is None:
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def to_bool(v) -> bool | None:
    s = clean_str(v)
    if s is None:
        return None
    return s.lower() in {"1", "true", "yes", "y"}


def parse_dt(v) -> datetime | None:
    s = clean_str(v)
    if not s:
        return None
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def normalize_store(row: dict, chain_name_default: str) -> dict | None:
    chain_id = clean_str(row.get("chainid"))
    store_code = norm_code(row.get("storeid"))
    if not chain_id or not store_code:
        return None
    return {
        "chain_id": chain_id,
        "chain_name": clean_str(row.get("chainname")) or chain_name_default,
        "sub_chain_id": norm_code(row.get("subchainid")) or "",
        "store_code": store_code,
        "store_name": clean_str(row.get("storename")),
        "address": clean_str(row.get("address")),
        "city": clean_str(row.get("city")),
        "zip_code": clean_str(row.get("zipcode")),
    }


def normalize_price(row: dict) -> dict | None:
    """Return a flat dict with product + price fields, or None if unusable."""
    barcode = clean_str(row.get("itemcode"))
    chain_id = clean_str(row.get("chainid"))
    store_code = norm_code(row.get("storeid"))
    price = to_decimal(row.get("itemprice"))
    if not barcode or not chain_id or not store_code or price is None:
        return None

    allow = to_bool(row.get("allowdiscount"))
    return {
        # product
        "barcode": barcode,
        "name": clean_str(row.get("itemname")) or barcode,
        "manufacturer": clean_str(row.get("manufacturename")),
        "unit_qty": clean_str(row.get("unitqty")),
        "quantity": to_decimal(row.get("quantity")),
        "unit_of_measure": clean_str(row.get("unitofmeasure")),
        "is_weighted": bool(to_bool(row.get("bisweighted"))),
        # join keys
        "chain_id": chain_id,
        "store_code": store_code,
        # price
        "price": price,
        "unit_price": to_decimal(row.get("unitofmeasureprice")),
        "allow_discount": True if allow is None else allow,  # column is NOT NULL
        "item_status": clean_str(row.get("itemstatus")),
        "price_update_time": parse_dt(row.get("priceupdatetime")),
    }
