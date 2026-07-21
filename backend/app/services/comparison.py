"""Basket comparison engine.

Given a basket (product ids + quantities) and a city, compute the total cost of
the basket at every relevant branch of Shufersal / Rami Levy / Osher Ad in that
city, then rank them.

Ranking rule (important):
  * A store is "complete" only if it carries *every* requested product.
  * Only complete stores compete for the winner and receive a numeric `rank`
    (1 = cheapest).
  * Incomplete stores are still returned (with their partial total and the list
    of missing products) but get `rank = None` — they are excluded from the
    competition for first place, not hidden.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

_TWO_PLACES = Decimal("0.01")
_NAME_WS = re.compile(r"\s+")

# Characters that are operators in MySQL FULLTEXT BOOLEAN MODE — stripped from tokens.
_FT_OPERATORS = re.compile(r'[+\-><()~*"@]')
# Pure numbers / sizes / percentages — not "prominent".
_NUMERIC = re.compile(r"^[0-9]+([.,][0-9]+)?%?$")
# Size / packaging / unit words to skip when picking prominent words.
_STOP_TOKENS = {
    # units / sizes / packaging
    "ליטר", "ל", "מל", "מיליליטר", "גרם", "גר", "ג", "קג", "קילו", "קילוגרם",
    "יחידה", "יח", "יחי", "מארז", "שישיה", "שישייה", "אריזה", "אריזת", "זוג",
    "חבילה", "בקבוק", "פחית", "קרטון", "ק", "מ", "כ",
    # common Hebrew function words
    "על", "של", "עם", "או", "גם", "את", "אל",
}
# Cap matches per basket item so a generic word can't blow up the price query.
_MATCH_CAP = 80


def _norm_name(name: str | None) -> str:
    """Trim and collapse inner whitespace so the same item matches by name."""
    return _NAME_WS.sub(" ", (name or "").strip())


def prominent_tokens(name: str | None, limit: int = 4) -> list[str]:
    """The first few brand/product words of a name (skip numbers/sizes/units).

    'קוקה קולה שישיה 1.5 ליטר' → ['קוקה', 'קולה']

    Up to 4 words: a distinguishing 4th word ('...עם תות') tightens fuzzy
    matching so a plainer variant of the same base can't stand in for it.
    """
    out: list[str] = []
    for raw in re.split(r"[\s\-/,]+", _norm_name(name)):
        word = _FT_OPERATORS.sub("", raw).strip("'\"").strip()
        if len(word) < 2 or _NUMERIC.match(word) or word in _STOP_TOKENS:
            continue
        out.append(word)
        if len(out) >= limit:
            break
    return out


_DIGITS_RE = re.compile(r"\d+")
_PACK_RE = re.compile(r"(\d+)\s*[*x×X]\s*(\d+)")


def size_tokens(name: str | None, limit: int = 2) -> list[str]:
    """Numeric size/quantity tokens that distinguish, e.g., a 10-pack from an
    80g single. 'מארז במבה 10*25 גרם' → ['10', '25'].

    Pack patterns (count×unit, e.g. '4*55גרם') keep BOTH numbers even when the
    count is a single digit — otherwise a 4-pack's signature collapses onto the
    single bag's ('4*55' → ['55']) and the multipack price hijacks the
    comparison. Standalone single digits stay ignored ('חלב 3% 1 ליטר' → [])."""
    text_ = name or ""
    out: list[str] = []

    def _add(n: str) -> None:
        if n not in out and len(out) < limit:
            out.append(n)

    for m in _PACK_RE.finditer(text_):
        _add(m.group(1))
        _add(m.group(2))
    for n in _DIGITS_RE.findall(text_):
        if len(n) >= 2:  # FULLTEXT min token size is 2
            _add(n)
    return out


def _same_name_ids(db: Session, norm_name: str) -> list[int]:
    """Product ids whose (trim+collapsed) name equals `norm_name` — the same
    item sold under different barcodes across chains."""
    if not norm_name:
        return []
    rows = db.execute(
        text(
            """
            SELECT id FROM products
            WHERE REGEXP_REPLACE(TRIM(name), '[[:space:]]+', ' ') = :n
            LIMIT :cap
            """
        ).bindparams(),
        {"n": norm_name, "cap": _MATCH_CAP},
    ).all()
    return [r[0] for r in rows]


# Hebrew final letters → base form, so a suffixed word still prefix-matches its
# root ('מגבון' vs 'מגבוני': final ן ≠ medial נ in Unicode). MySQL FULLTEXT
# normalizes these; Python str.startswith does not, so we do it ourselves.
_FINALS = str.maketrans("ךםןףץ", "כמנפצ")


def _definal(s: str) -> str:
    return s.translate(_FINALS)


def _head_ok(cand_name: str, head: str | None) -> bool:
    """Candidate keeps the query's HEAD word (its first prominent token).

    FULLTEXT containment is one-directional, so 'קוקוס טבעי' matched
    'שמן קוקוס טבעי' / 'קמח קוקוס' — different products that merely contain the
    query words. Requiring the candidate's own first prominent word to
    prefix-match the query's head rejects those (head שמן/קמח ≠ קוקוס) while
    keeping true variants ('אוכמניות' ↔ 'אוכמניות טריות', 'מגבון' ↔ 'מגבוני')."""
    if not head:
        return True
    ct = prominent_tokens(cand_name)
    if not ct:
        return True  # nothing to compare — don't over-reject
    c, h = _definal(ct[0]), _definal(head)
    return c.startswith(h) or h.startswith(c)


def _fuzzy_filter(rows: list, sizes: list[str], head: str | None) -> list[int]:
    """Keep candidates whose OWN size signature equals the query's AND whose
    head prominent word matches — the two bidirectional guards on fuzzy match.

    Size: FULLTEXT/LIKE is one-directional, so a 4-pack '4*55גרם' still matches a
    '55 גר' query; comparing full signatures both ways rejects it."""
    want = set(sizes)
    out = []
    for rid, name in rows:
        if sizes and set(size_tokens(name)) != want:
            continue
        if not _head_ok(name, head):
            continue
        out.append(rid)
    return out


def _fuzzy_ids(db: Session, brand: list[str], sizes: list[str]) -> list[int]:
    """Strict fuzzy match: require EVERY brand word (prefix) AND every size token,
    then keep only candidates with a matching size signature and head word.

    Including the size tokens is what stops a cheap single bag from matching an
    expensive multipack (their numeric signatures differ). Items with no numbers
    (e.g. produce) fall back to brand-only matching.
    """
    head = brand[0] if brand else None
    parts = [f"+{t}*" for t in brand] + [f"+{n}" for n in sizes]
    expr = " ".join(parts)
    if not expr:
        return []
    rows = db.execute(
        text(
            """
            SELECT id, name FROM products
            WHERE MATCH(name) AGAINST (:expr IN BOOLEAN MODE)
            LIMIT :cap
            """
        ),
        {"expr": expr, "cap": _MATCH_CAP},
    ).all()
    if rows:
        return _fuzzy_filter(rows, sizes, head)

    # Precise LIKE fallback: require ALL tokens as substrings (not just one).
    tokens = brand + sizes
    clauses = " AND ".join(f"name LIKE :t{i}" for i in range(len(tokens)))
    params = {f"t{i}": f"%{tokens[i]}%" for i in range(len(tokens))}
    params["cap"] = _MATCH_CAP
    rows = db.execute(
        text(f"SELECT id, name FROM products WHERE {clauses} LIMIT :cap"), params
    ).all()
    return _fuzzy_filter(rows, sizes, head)


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _select_branches(ordered: list[dict], limit: int) -> list[dict]:
    """Cap the result to `limit` branches, favouring the cheapest while keeping
    a mix of chains: take the cheapest branch of each chain first, then fill the
    remaining slots with the next-cheapest branches. Display order is preserved.
    """
    if len(ordered) <= limit:
        return ordered

    rank_of = {s["store_id"]: i for i, s in enumerate(ordered)}
    chosen: list[dict] = []
    seen: set[int] = set()

    seen_chains: set[str] = set()
    for s in ordered:  # cheapest branch per chain (ordered is already cheapest-first)
        if s["chain_name"] not in seen_chains:
            seen_chains.add(s["chain_name"])
            chosen.append(s)
            seen.add(s["store_id"])

    for s in ordered:  # fill remaining slots with the cheapest branches left
        if len(chosen) >= limit:
            break
        if s["store_id"] not in seen:
            chosen.append(s)
            seen.add(s["store_id"])

    chosen = chosen[:limit]
    chosen.sort(key=lambda s: rank_of[s["store_id"]])
    return chosen


def basket_summary(db: Session, items: list) -> dict:
    """FR-3.6 — lightweight basket summary for the sidebar: estimated cost
    (average price of each item across every branch carrying it, × quantity)
    and per-chain coverage.

    Uses the cheap same-name tier only — deliberately NOT the full tiered
    comparison — so coverage may under-count fuzzy-only matches but never
    over-promises what the comparison will find.
    """
    qty: dict[int, Decimal] = {}
    for it in items:
        qty[it.product_id] = qty.get(it.product_id, Decimal("0")) + Decimal(str(it.quantity))
    ids = list(qty)
    if not ids:
        return {"item_count": 0, "estimated_total": None, "chains": []}

    avg_rows = db.execute(
        text(
            """
            SELECT rep.id AS rid, AVG(pr.price) AS avg_price
            FROM products rep
            JOIN products p2 ON p2.name = rep.name
            JOIN prices pr ON pr.product_id = p2.id
            WHERE rep.id IN :ids
            GROUP BY rep.id
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).all()
    est = sum((Decimal(str(avg)) * qty[rid] for rid, avg in avg_rows), Decimal("0"))

    cov_rows = db.execute(
        text(
            """
            SELECT s.chain_name, COUNT(DISTINCT rep.id) AS items_covered
            FROM products rep
            JOIN products p2 ON p2.name = rep.name
            JOIN prices pr ON pr.product_id = p2.id
            JOIN stores s ON s.id = pr.store_id
            WHERE rep.id IN :ids
            GROUP BY s.chain_name
            ORDER BY s.chain_name
            """
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    ).all()

    return {
        "item_count": len(ids),
        "estimated_total": _money(est) if avg_rows else None,
        "chains": [{"chain_name": c, "items_covered": int(n)} for c, n in cov_rows],
    }


def _dedup_twin_stores(result_stores: list[dict]) -> list[dict]:
    """Collapse duplicate feed entries for the same physical branch.

    The chains' own store files sometimes publish TWO store codes for one
    physical branch (e.g. Shufersal 'BE אריאל' under codes 639 and 787 — one
    with no address and a stale, thinner price list), so the comparison showed
    the same branch twice, occasionally with outdated prices.

    Twins = same (chain_id, city, store_name). They are kept apart only when
    every entry carries its own distinct real address (genuinely two branches,
    e.g. two same-named stores along one long street). The surviving
    representative is the richest entry: most basket items found, then the one
    with an address, then the cheaper total.
    """
    groups: dict[tuple, list[dict]] = {}
    for s in result_stores:
        key = (s["chain_id"], s["city"], (s["store_name"] or "").strip())
        groups.setdefault(key, []).append(s)

    out: list[dict] = []
    for group in groups.values():
        if len(group) > 1:
            addresses = {(s["address"] or "").strip() for s in group}
            addresses.discard("")
            if len(addresses) < len(group):  # not all distinct+real → collapse
                group.sort(
                    key=lambda s: (
                        -s["found_count"],
                        not (s["address"] or "").strip(),
                        s["total"],
                    )
                )
                group = group[:1]
        out.extend(group)
    return out


def build_comparison(
    city: str,
    pids: list[int],
    qty_by_pid: dict[int, Decimal],
    products: dict[int, dict],
    price_rows: list[dict],
    limit: int = 10,
) -> dict:
    """Pure ranking logic (no DB) — unit-testable.

    `price_rows` are dict rows with: store_id, chain_id, chain_name, store_name,
    address, city, product_id, price (Decimal). Results are capped to `limit`
    branches (cheapest-first, with a mix of chains).
    """
    # group prices by store; guard against accidental duplicates (keep cheapest)
    stores: dict[int, dict] = {}
    for r in price_rows:
        sid = r["store_id"]
        acc = stores.get(sid)
        if acc is None:
            acc = stores[sid] = {
                "store_id": sid,
                "chain_id": r["chain_id"],
                "chain_name": r["chain_name"],
                "store_name": r["store_name"],
                "address": r["address"],
                "city": r["city"],
                "prices": {},
            }
        pid = r["product_id"]
        price = r["price"]
        if price is not None and (acc["prices"].get(pid) is None or price < acc["prices"][pid]):
            acc["prices"][pid] = price

    result_stores: list[dict] = []
    for acc in stores.values():
        items_out = []
        total = Decimal("0")
        missing: list[int] = []
        for pid in pids:
            qty = qty_by_pid[pid]
            price = acc["prices"].get(pid)
            if price is None:
                missing.append(pid)
                items_out.append(
                    {
                        "product_id": pid,
                        "quantity": float(qty),
                        "unit_price": None,
                        "line_total": None,
                        "found": False,
                    }
                )
            else:
                line = price * qty
                total += line
                items_out.append(
                    {
                        "product_id": pid,
                        "quantity": float(qty),
                        "unit_price": _money(price),
                        "line_total": _money(line),
                        "found": True,
                    }
                )
        result_stores.append(
            {
                "store_id": acc["store_id"],
                "chain_id": acc["chain_id"],
                "chain_name": acc["chain_name"],
                "store_name": acc["store_name"],
                "address": acc["address"],
                "city": acc["city"],
                "total": _money(total),
                "found_count": len(pids) - len(missing),
                "missing_count": len(missing),
                "missing_product_ids": missing,
                "is_complete": not missing,
                "rank": None,
                "pct_above_cheapest": None,
                "items": items_out,
            }
        )

    # collapse duplicate feed entries for the same physical branch (stale twin
    # store codes in the source files) before ranking
    result_stores = _dedup_twin_stores(result_stores)

    # complete stores compete for the winner; incomplete are shown but unranked
    complete = [s for s in result_stores if s["is_complete"]]
    incomplete = [s for s in result_stores if not s["is_complete"]]
    complete.sort(key=lambda s: s["total"])
    for i, s in enumerate(complete, start=1):
        s["rank"] = i
    # price gap (%) vs the cheapest complete basket (FR-4.2); incomplete stores
    # keep None — a partial total is not comparable.
    if complete and complete[0]["total"]:
        cheapest = complete[0]["total"]
        for s in complete:
            s["pct_above_cheapest"] = round((s["total"] - cheapest) / cheapest * 100, 1)
    # show the "closest to complete, then cheapest" incomplete stores first
    incomplete.sort(key=lambda s: (s["missing_count"], s["total"]))

    ordered = complete + incomplete
    winner_id = complete[0]["store_id"] if complete else None
    shown = _select_branches(ordered, limit)  # cap to `limit` branches

    return {
        "city": city,
        "requested_product_ids": pids,
        "products": [
            products.get(pid, {"id": pid, "name": None, "barcode": None}) for pid in pids
        ],
        "store_count": len(ordered),          # total branches found in the city
        "complete_store_count": len(complete),
        "shown_store_count": len(shown),      # branches actually returned (≤ limit)
        "winner_store_id": winner_id,
        "message": None if shown else "No stores in this city",
        "stores": shown,
    }


def compare_basket(db: Session, city: str, items: list) -> dict:
    """Compare a basket with tiered product matching (per store, per item):

      Tier 1 — the exact submitted product_id, if the store carries it.
      Tier 2 — products with the same (normalized) name (same item, different
               barcode across chains).
      Tier 3 — strict fuzzy: brand words AND size tokens (so a 10-pack never
               borrows a cheap single bag's price).

    For each (store, item) we take the BEST available tier and the cheapest price
    within it — an exact/name match is never overridden by a cheaper-but-wrong
    fuzzy match. The response shape (one representative id per item) is unchanged.
    """
    empty = {
        "city": city,
        "requested_product_ids": [],
        "products": [],
        "store_count": 0,
        "complete_store_count": 0,
        "shown_store_count": 0,
        "winner_store_id": None,
        "message": "No stores in this city",
        "stores": [],
    }

    # 1. one basket line per distinct submitted id; sum quantities for repeats
    qty_by_repr: dict[int, Decimal] = {}
    for it in items:
        qty_by_repr[it.product_id] = qty_by_repr.get(it.product_id, Decimal("0")) + Decimal(
            str(it.quantity)
        )
    repr_ids = list(qty_by_repr)
    if not repr_ids:
        return empty

    # 2. names (+ barcode for display) of the submitted products
    sub_rows = db.execute(
        text("SELECT id, name, barcode FROM products WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        ),
        {"ids": repr_ids},
    ).mappings().all()
    id_info = {r["id"]: r for r in sub_rows}

    repr_ids = [rid for rid in repr_ids if rid in id_info]
    if not repr_ids:
        return empty
    products_meta = {
        rid: {
            "id": rid,
            "name": (id_info[rid]["name"] or "").strip(),
            "barcode": id_info[rid]["barcode"],
        }
        for rid in repr_ids
    }

    # 3. candidate products per item, tagged with a match tier (1=exact id,
    #    2=same normalized name, 3=strict fuzzy with brand + size tokens).
    cand: dict[int, dict[int, int]] = {}
    for rid in repr_ids:
        name = id_info[rid]["name"]
        tiers: dict[int, int] = {rid: 1}  # exact submitted product id
        for pid in _same_name_ids(db, _norm_name(name)):
            tiers.setdefault(pid, 2)
        for pid in _fuzzy_ids(db, prominent_tokens(name), size_tokens(name)):
            tiers.setdefault(pid, 3)
        cand[rid] = tiers

    # 4. prices for every candidate product in the requested city
    all_pids = sorted({pid for tiers in cand.values() for pid in tiers})
    price_rows = db.execute(
        text(
            """
            SELECT s.id AS store_id, s.chain_id, s.chain_name, s.store_name,
                   s.address, s.city, pr.product_id, pr.price
            FROM stores s
            JOIN prices pr ON pr.store_id = s.id
            WHERE s.city = :city AND pr.product_id IN :pids
            """
        ).bindparams(bindparam("pids", expanding=True)),
        {"city": city, "pids": all_pids},
    ).mappings().all()

    price_at: dict[tuple[int, int], Decimal] = {}
    store_meta: dict[int, dict] = {}
    for r in price_rows:
        price = r["price"]
        if price is None:
            continue
        key = (r["store_id"], r["product_id"])
        if key not in price_at or price < price_at[key]:
            price_at[key] = price
        store_meta.setdefault(
            r["store_id"],
            {
                "store_id": r["store_id"],
                "chain_id": r["chain_id"],
                "chain_name": r["chain_name"],
                "store_name": r["store_name"],
                "address": r["address"],
                "city": r["city"],
            },
        )

    # 5. per (store, item): pick the best available tier, then the cheapest price
    #    within it → one representative price row per (store, item).
    chosen: list[dict] = []
    for sid, meta in store_meta.items():
        for rid in repr_ids:
            best: tuple[int, Decimal] | None = None  # (tier, price)
            for pid, tier in cand[rid].items():
                price = price_at.get((sid, pid))
                if price is None:
                    continue
                if best is None or tier < best[0] or (tier == best[0] and price < best[1]):
                    best = (tier, price)
            if best is not None:
                row = dict(meta)
                row["product_id"] = rid
                row["price"] = best[1]
                chosen.append(row)

    return build_comparison(city, repr_ids, qty_by_repr, products_meta, chosen)
