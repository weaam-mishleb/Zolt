"""Adapters for the three promotion-file FAMILIES in the price-transparency feed.

Measured across the 28 promo files in the dataset — the chains do NOT share one
promotion schema (unlike price files, which are near-uniform):

  ITEMS  (17 chains, e.g. bareket, rami_levy, yohananof)
      One row per promotion. Pricing lives on the ROW. Items in
      `promotionitems` = {"item": [{itemcode}, ...]}.

  GROUPS (9 chains, e.g. shufersal, osher_ad, dor_alon)
      One row per promotion, nested TWO levels deep, and — critically —
      pricing lives PER ITEM, not on the row:
        {"group": {"groupid", "minpurchaseamount", "discounttype",
                   "promotionitems": {"promotionitem": [
                       {itemcode, rewardtype, minqty, discountrate,
                        discountedprice, ...}, ...]}}}
      Uses combined `promotionstartdatetime` instead of date+hour columns.

  FLAT   (2 chains, e.g. het_cohen, mahsani_ashuk)
      One row per (promotion, item); `itemcode` is a plain column.

Every adapter returns the SAME shape, so the loader stays format-agnostic:
    (header: dict, items: list[{"itemcode", "is_gift"}])

Adding a 4th family = adding one function here. Nothing else changes.
"""
from __future__ import annotations

import json

from .normalize import clean_str, to_decimal

# The feed's own null markers. 'NO_BODY' appears throughout the GROUPS family.
_NULLS = {"no_body", "none", "null", ""}


def _val(v):
    """clean_str + the feed's placeholder vocabulary."""
    s = clean_str(v)
    if s is None or s.lower() in _NULLS:
        return None
    return s


def _num(v):
    return to_decimal(_val(v))


def _as_list(node, key: str) -> list[dict]:
    """Unwrap {key: X} where X may be a dict (single) or a list (many).

    THE trap in this feed: cardinality changes the JSON *type*. A loader that
    assumes a list silently drops every single-item promotion.
    """
    if isinstance(node, dict):
        node = node.get(key, [])
    if isinstance(node, dict):
        node = [node]
    if not isinstance(node, list):
        return []
    return [n for n in node if isinstance(n, dict)]


def _load_json(raw) -> dict | list | None:
    s = _val(raw)
    if not s:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def detect_family(fieldnames) -> str:
    """Pick the adapter from the CSV header alone."""
    cols = set(fieldnames or [])
    if "groups" in cols:
        return "groups"
    if "promotionitems" in cols:
        return "items"
    if "itemcode" in cols:
        return "flat"
    return "unknown"


# ── ITEMS family ────────────────────────────────────────────────────────────
def extract_items_family(row: dict) -> list[tuple[dict, list[dict]]]:
    items = [
        {"itemcode": _val(i.get("itemcode")), "is_gift": 0}
        for i in _as_list(_load_json(row.get("promotionitems")), "item")
    ]
    gifts = [
        {"itemcode": _val(i.get("itemcode")), "is_gift": 1}
        for i in _as_list(_load_json(row.get("giftsitems")), "item")
    ]
    header = {
        "promo_id_src": _val(row.get("promotionid")),
        "description": _val(row.get("promotiondescription")),
        "discount_rate": _num(row.get("discountrate")),
        "discount_amount": _num(row.get("discountamount")),
        "discounted_price": _num(row.get("discountedprice")),
        "min_qty": _num(row.get("minqty")),
        "max_qty": _num(row.get("maxqty")),
        "min_basket_amount": _num(row.get("minpurchaseamnt")),
        "reward_type_src": _val(row.get("rewardtype")),
        "discount_type_src": _val(row.get("discounttype")),
        "has_gift": bool(gifts),
        "_start_date": row.get("promotionstartdate"),
        "_start_hour": row.get("promotionstarthour"),
        "_end_date": row.get("promotionenddate"),
        "_end_hour": row.get("promotionendhour"),
    }
    return [(header, [i for i in items + gifts if i["itemcode"]])]


# ── GROUPS family ───────────────────────────────────────────────────────────
def extract_groups_family(row: dict) -> list[tuple[dict, list[dict]]]:
    """One promotion per GROUP.

    Pricing is read from the group's first item: within a group the parameters
    are uniform (it is "N for ₪X across this eligible set"), so hoisting them to
    the header preserves the semantics without exploding the table into one row
    per item.
    """
    out: list[tuple[dict, list[dict]]] = []
    groups = _as_list(_load_json(row.get("groups")), "group")
    base_id = _val(row.get("promotionid"))
    if not base_id:
        return out

    for gi, g in enumerate(groups):
        pitems = _as_list(g.get("promotionitems"), "promotionitem")
        if not pitems:
            continue
        first = pitems[0]
        gid = _val(g.get("groupid")) or str(gi + 1)
        rate = _num(first.get("discountrate"))
        if rate is not None and rate > 1:      # this family sends whole percent
            rate = rate / 100

        header = {
            # A promo id can carry several groups with different terms — the
            # group id must be part of the key or they would overwrite each other.
            "promo_id_src": base_id if len(groups) == 1 else f"{base_id}#{gid}",
            "description": _val(row.get("promotiondescription")),
            "discount_rate": rate,
            "discount_amount": None,
            "discounted_price": _num(first.get("discountedprice")),
            "min_qty": _num(first.get("minqty")),
            "max_qty": _num(first.get("maxqty")),
            "min_basket_amount": _num(g.get("minpurchaseamount")),
            "reward_type_src": _val(first.get("rewardtype")),
            "discount_type_src": _val(g.get("discounttype")),
            "has_gift": _val(row.get("isgiftitem")) == "1",
            "_start_datetime": row.get("promotionstartdatetime"),
            "_end_datetime": row.get("promotionenddatetime"),
            "_start_hour": row.get("promotionstarthour"),
            "_end_hour": row.get("promotionendhour"),
        }
        items = [
            {"itemcode": _val(i.get("itemcode")), "is_gift": 0}
            for i in pitems
            if _val(i.get("itemcode"))
        ]
        out.append((header, items))
    return out


# ── FLAT family ─────────────────────────────────────────────────────────────
def extract_flat_family(row: dict) -> list[tuple[dict, list[dict]]]:
    code = _val(row.get("itemcode"))
    header = {
        "promo_id_src": _val(row.get("promotionid")),
        "description": _val(row.get("promotiondescription")),
        "discount_rate": _num(row.get("discountrate")),
        "discount_amount": _num(row.get("discountamount")),
        "discounted_price": _num(row.get("discountedprice")),
        "min_qty": _num(row.get("minqty")),
        "max_qty": _num(row.get("maxqty")),
        "min_basket_amount": _num(row.get("minpurchaseamnt")),
        "reward_type_src": _val(row.get("rewardtype")),
        "discount_type_src": _val(row.get("discounttype")),
        "has_gift": _val(row.get("isgiftitem")) == "1",
        "_start_date": row.get("promotionstartdate"),
        "_start_hour": row.get("promotionstarthour"),
        "_end_date": row.get("promotionenddate"),
        "_end_hour": row.get("promotionendhour"),
    }
    items = [{"itemcode": code, "is_gift": 1 if header["has_gift"] else 0}] if code else []
    return [(header, items)]


EXTRACTORS = {
    "items": extract_items_family,
    "groups": extract_groups_family,
    "flat": extract_flat_family,
}


def classify_reward(header: dict) -> str:
    """Map a normalized header onto a reward_kind the rule engine understands.

    Driven by WHICH FIELDS ARE POPULATED rather than the `rewardtype` code:
    the codes differ between chains, the data shape is self-describing.
    `reward_type_src` is persisted so a correction never needs a re-ingest.
    """
    price = header.get("discounted_price")
    rate = header.get("discount_rate")
    min_qty = header.get("min_qty") or 1
    basket_min = header.get("min_basket_amount")

    if header.get("has_gift"):
        return "NTH_FREE"
    if price is not None and price > 0:
        return "BUNDLE_PRICE" if min_qty >= 2 else "FIXED_PRICE"
    if rate is not None and rate > 0:
        return "PCT_OFF"
    if basket_min is not None and basket_min > 0:
        return "AMOUNT_OFF"
    return "UNKNOWN"
