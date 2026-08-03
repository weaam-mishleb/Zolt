"""Row normalization for the Israeli "price transparency" CSV feed.

Both store and price rows are accessed *by column name* (not position) because
the chains ship the same column names in different orders (e.g. Shufersal's
store file orders/omits columns differently from Rami Levy / Osher Ad).

They also ship them under different *names*. The price feed is not one schema:
counted across all 33 chain files in the Kaggle snapshot,

    itemname          23 chains   itemnm             12 chains
    manufacturername  20 chains   manufacturename    20 chains
    priceupdatetime   20 chains   priceupdatedate    20 chains
    unitofmeasure     31 chains   unitmeasure         2 chains
    bisweighted       31 chains   blsweighted         1 chain

so every varying column is read through an alias list (`first_alias`) rather
than a single name. Where a chain ships both spellings the alternate is blank or
identical — zero disagreeing rows across the snapshot — so first-non-empty-wins
is safe.

This is not cosmetic. Reading only `itemname` made 10 chains load every product
named after its own item code, which passes every count-based gate and is
useless downstream, because all product matching in this system is name-based.
Store files, by contrast, are uniform across all 31 chains that publish one —
no aliasing needed there.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from .cities import normalize_city

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


def first_alias(row: dict, *names: str) -> str | None:
    """First alias in `names` that carries a real value, else None.

    The chains disagree on column *names*, not just their order — see the table
    in the module docstring. Falling back through the aliases is what keeps a
    chain from loading with a whole field silently blank.
    """
    for name in names:
        v = clean_str(row.get(name))
        if v is not None:
            return v
    return None


# EAN-8 is the shortest real barcode, so an item code below 8 characters is a
# chain's own internal numbering, not a GTIN.
GTIN_MIN_LENGTH = 8


def product_key(item_code, chain_id) -> str | None:
    """The value stored in `products.barcode` — namespaced when it isn't a GTIN.

    `products.barcode` is globally UNIQUE, which is only safe for codes that are
    globally meaningful. About ten chains number their loose goods themselves,
    and those numberings collide: item '12' is tomatoes in Stop Market, frozen
    cod in King Store and red cabbage in Keshet. Under one UNIQUE key they
    collapse into a single product row, and the comparison engine ends up
    ranking unrelated items against each other.

    Prefixing with the feed's chain id gives every chain its own namespace.
    It is the chain id and NOT our slug on purpose: two slugs can serve one
    chain — mahsani_ashuk and mahsani_ashuk_new_source both publish
    7290661400001 — and a slug prefix would split that one chain's products in
    half.

    Measured over the snapshot: 3.0% of rows carry a short code. Of the short
    codes appearing in more than one chain, 91.7% name genuinely different
    products (this fixes those) and 8.3% name the same product under a shared
    code (this splits those — and `etl.canonical` merges them straight back,
    because it blocks on the identical name they carry).
    """
    code = clean_str(item_code)
    if code is None:
        return None
    chain = clean_str(chain_id)
    if chain is None or len(code) >= GTIN_MIN_LENGTH:
        return code
    return f"{chain}_{code}"


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
        # Unify across chains: Shufersal sends a (variant) name, Rami Levy / Osher
        # Ad send a numeric CBS code — fall back to the store name when needed.
        "city": normalize_city(row.get("city"), row.get("storename")),
        "zip_code": clean_str(row.get("zipcode")),
    }


def normalize_price(row: dict) -> dict | None:
    """Return a flat dict with product + price fields, or None if unusable."""
    item_code = clean_str(row.get("itemcode"))
    chain_id = clean_str(row.get("chainid"))
    store_code = norm_code(row.get("storeid"))
    price = to_decimal(row.get("itemprice"))
    if not item_code or not chain_id or not store_code or price is None:
        return None

    # Internal (non-GTIN) codes get namespaced to their chain — see product_key.
    # Everything downstream, including etl.promotions, must key on this value.
    barcode = product_key(item_code, chain_id)

    allow = to_bool(row.get("allowdiscount"))
    return {
        # product
        "barcode": barcode,
        # The barcode fallback keeps the NOT NULL column satisfied, but a chain
        # loading mostly barcode-named products means its name column was missed
        # — scripts.dq_check fails the job on that.
        "name": first_alias(row, "itemname", "itemnm") or barcode,
        "manufacturer": first_alias(row, "manufacturername", "manufacturename"),
        "unit_qty": clean_str(row.get("unitqty")),
        "quantity": to_decimal(row.get("quantity")),
        "unit_of_measure": first_alias(row, "unitofmeasure", "unitmeasure"),
        "is_weighted": bool(to_bool(first_alias(row, "bisweighted", "blsweighted"))),
        # join keys
        "chain_id": chain_id,
        "store_code": store_code,
        # price
        "price": price,
        "unit_price": to_decimal(row.get("unitofmeasureprice")),
        "allow_discount": True if allow is None else allow,  # column is NOT NULL
        "item_status": clean_str(row.get("itemstatus")),
        "price_update_time": parse_dt(first_alias(row, "priceupdatetime", "priceupdatedate")),
    }
