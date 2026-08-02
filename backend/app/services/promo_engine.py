"""Promotion rule engine — pure, DB-free, unit-testable.

    price_basket(lines, promotions, now) -> priced basket

WHY PURE
--------
`build_comparison` is testable without a database because it takes rows and
returns rows. This engine follows the same contract deliberately: it never
touches the DB, never reads the clock (`now` is injected), and never parses a
Hebrew description. Given the same inputs it returns the same output — which is
the only way promotion maths can be trusted with someone's grocery bill.

WHAT IT MODELS
--------------
    FIXED_PRICE   'ב-9.90'        each unit costs `discounted_price`
    BUNDLE_PRICE  '2 ב-40'        every `min_qty` units cost `discounted_price`
    PCT_OFF       '30% הנחה'      `discount_rate` off each unit
    AMOUNT_OFF    '₪20 מעל ₪100'  flat `discount_amount` once, basket-level
    NTH_FREE      '1+1'           1 free unit per `min_qty` bought

A promotion may span SEVERAL basket lines: 'מאגדת שלגוני 2 ב-40' is valid across
flavours, so eligible units are pooled across every line the promo covers.

CONFLICTS
---------
`allow_stacking` (feed field `allowmultiplediscounts`) decides whether a promo
may combine with another on the same units. Non-stacking promos compete, and
are resolved greedily by value — see `_resolve_conflicts` for the honest
limitation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class BasketLine:
    """One basket row, already resolved to a canonical product and a price."""

    canonical_id: int
    quantity: Decimal
    unit_price: Decimal
    product_id: int | None = None
    name: str | None = None

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class Promotion:
    """A promotion as the engine sees it — rules and numbers only, no text."""

    id: int
    reward_kind: str
    canonical_ids: frozenset[int]
    min_qty: Decimal = Decimal("1")
    max_qty: Decimal | None = None
    discounted_price: Decimal | None = None
    discount_rate: Decimal | None = None
    discount_amount: Decimal | None = None
    min_basket_amount: Decimal | None = None
    allow_stacking: bool = False
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    gift_canonical_ids: frozenset[int] = field(default_factory=frozenset)

    def is_active(self, now: datetime) -> bool:
        """Open-ended bounds count as active — the feed frequently omits them."""
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


@dataclass
class _Candidate:
    """A promotion priced against this basket, before conflict resolution."""

    promo: Promotion
    savings: Decimal
    # canonical_id -> units this promo would consume (for conflict detection)
    consumed: dict[int, Decimal]


def _eligible_units(promo: Promotion, lines: list[BasketLine]) -> list[tuple[BasketLine, Decimal]]:
    """(line, usable_qty) for every basket line this promotion covers.

    `max_qty` caps the total units the promo may price, pooled across lines —
    the feed uses it for 'מוגבל ל-3'.
    """
    covered = [ln for ln in lines if ln.canonical_id in promo.canonical_ids]
    if not covered:
        return []
    # Most expensive units first: when a cap or a bundle can only take some of
    # them, discounting the dearest maximizes the customer's saving.
    covered.sort(key=lambda ln: ln.unit_price, reverse=True)

    out: list[tuple[BasketLine, Decimal]] = []
    remaining = promo.max_qty if promo.max_qty and promo.max_qty > 0 else None
    for ln in covered:
        take = ln.quantity if remaining is None else min(ln.quantity, remaining)
        if take <= 0:
            break
        out.append((ln, take))
        if remaining is not None:
            remaining -= take
            if remaining <= 0:
                break
    return out


def _evaluate(promo: Promotion, lines: list[BasketLine], basket_total: Decimal) -> _Candidate | None:
    """Compute what this promotion is worth against this basket, or None.

    `min_basket_amount` is deliberately tested against the PRE-discount total —
    'הנחה בקנייה מעל ₪100' normally means ₪100 of goods, not ₪100 after other
    promotions. The feed does not state which basis applies, so the choice is
    documented here rather than left implicit; it also keeps the result
    order-independent (a post-discount basis would make the outcome depend on
    which promotion was evaluated first).
    """
    if promo.min_basket_amount and basket_total < promo.min_basket_amount:
        return None

    # AMOUNT_OFF is basket-level: it consumes no specific units.
    if promo.reward_kind == "AMOUNT_OFF":
        if not promo.discount_amount or promo.discount_amount <= 0:
            return None
        savings = min(promo.discount_amount, basket_total)
        return _Candidate(promo, _money(savings), {})

    units = _eligible_units(promo, lines)
    if not units:
        return None

    savings = _ZERO
    consumed: dict[int, Decimal] = {}
    kind = promo.reward_kind

    if kind == "FIXED_PRICE":
        if promo.discounted_price is None:
            return None
        for ln, qty in units:
            per_unit = ln.unit_price - promo.discounted_price
            if per_unit <= 0:            # promo is not actually cheaper — ignore it
                continue
            savings += per_unit * qty
            consumed[ln.canonical_id] = consumed.get(ln.canonical_id, _ZERO) + qty

    elif kind == "PCT_OFF":
        if not promo.discount_rate or promo.discount_rate <= 0:
            return None
        for ln, qty in units:
            savings += ln.unit_price * qty * promo.discount_rate
            consumed[ln.canonical_id] = consumed.get(ln.canonical_id, _ZERO) + qty

    elif kind == "BUNDLE_PRICE":
        if promo.discounted_price is None or promo.min_qty < 1:
            return None
        # Pool units across every covered line, then form whole bundles.
        pool = [(ln, q) for ln, q in units]
        total_units = sum(q for _, q in pool)
        bundles = int(total_units // promo.min_qty)
        if bundles < 1:
            return None
        needed = Decimal(bundles) * promo.min_qty
        bundled_cost = _ZERO
        for ln, qty in pool:              # dearest first (see _eligible_units)
            if needed <= 0:
                break
            take = min(qty, needed)
            bundled_cost += ln.unit_price * take
            consumed[ln.canonical_id] = consumed.get(ln.canonical_id, _ZERO) + take
            needed -= take
        savings = bundled_cost - (Decimal(bundles) * promo.discounted_price)

    elif kind == "NTH_FREE":
        # Buy `min_qty` of the trigger items, get one unit free.
        if promo.min_qty < 1:
            return None
        sets = int(sum(q for _, q in units) // promo.min_qty)
        if sets < 1:
            return None

        # A gift may be a DIFFERENT product ('קנה X קבל Y'). When it is, the
        # free unit must come from the gift lines — and if the shopper did not
        # put the gift in the basket there is no saving to claim, only an offer.
        gift_ids = promo.gift_canonical_ids - promo.canonical_ids
        if gift_ids:
            free_pool = [
                (ln, ln.quantity) for ln in lines if ln.canonical_id in gift_ids
            ]
            if not free_pool:
                return None
        else:
            free_pool = list(units)       # same-item 1+1

        # The freed unit is the CHEAPEST eligible one — how retailers actually
        # apply it; assuming otherwise would overstate the saving.
        remaining_free = Decimal(sets)
        for ln, qty in sorted(free_pool, key=lambda t: t[0].unit_price):
            if remaining_free <= 0:
                break
            take = min(qty, remaining_free)
            savings += ln.unit_price * take
            remaining_free -= take
            consumed[ln.canonical_id] = consumed.get(ln.canonical_id, _ZERO) + take

        # The trigger units are committed to the deal too, so no other
        # promotion may also claim them.
        for ln, qty in units:
            consumed[ln.canonical_id] = consumed.get(ln.canonical_id, _ZERO) + qty

    else:                                  # UNKNOWN — never guess a discount
        return None

    if savings <= 0:
        return None
    return _Candidate(promo, _money(savings), consumed)


# Components up to this size are solved exactly (2^n subsets); larger ones fall
# back to greedy. Real conflict sets are 1–3 promotions, so the exact path is
# what actually runs — the cap only bounds the pathological case.
_EXACT_MAX = 14


def _compatible(subset: list[_Candidate]) -> bool:
    """True if these promotions may all apply together.

    Rule: for any canonical product, EITHER a single non-stacking promotion
    (`allowmultiplediscounts = 0`) OR any number of stacking ones — never both.
    """
    by_item: dict[int, list[_Candidate]] = {}
    for cand in subset:
        for cid in cand.consumed:
            by_item.setdefault(cid, []).append(cand)
    return not any(
        len(cands) > 1 and any(not c.promo.allow_stacking for c in cands)
        for cands in by_item.values()
    )


def _components(candidates: list[_Candidate]) -> list[list[_Candidate]]:
    """Split candidates into groups that can interact (share a product)."""
    owners: dict[int, list[int]] = {}
    for i, cand in enumerate(candidates):
        for cid in cand.consumed:
            owners.setdefault(cid, []).append(i)

    seen: set[int] = set()
    out: list[list[_Candidate]] = []
    for start in range(len(candidates)):
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            i = stack.pop()
            group.append(candidates[i])
            for cid in candidates[i].consumed:
                for j in owners.get(cid, ()):
                    if j not in seen:
                        seen.add(j)
                        stack.append(j)
        out.append(group)
    return out


def _best_exact(group: list[_Candidate]) -> list[_Candidate]:
    """Exhaustive search for the highest-saving compatible subset."""
    best: list[_Candidate] = []
    best_value = _ZERO
    for mask in range(1 << len(group)):
        subset = [group[i] for i in range(len(group)) if mask & (1 << i)]
        if not subset or not _compatible(subset):
            continue
        value = sum((c.savings for c in subset), _ZERO)
        if value > best_value:
            best_value, best = value, subset
    return best


def _best_greedy(group: list[_Candidate]) -> list[_Candidate]:
    """Best-value-first fallback for pathologically large conflict groups."""
    chosen: list[_Candidate] = []
    for cand in sorted(group, key=lambda c: c.savings, reverse=True):
        if _compatible(chosen + [cand]):
            chosen.append(cand)
    return chosen


def _resolve_conflicts(candidates: list[_Candidate]) -> list[_Candidate]:
    """Choose the most valuable set of promotions that may legally combine.

    Promotions that share no products cannot interact, so the problem splits
    into independent components. Each component is solved EXACTLY (it is a
    small set-packing instance); only an implausibly large one degrades to
    greedy, and that boundary is explicit rather than silent.
    """
    chosen: list[_Candidate] = []
    for group in _components(candidates):
        if len(group) == 1:
            chosen.extend(group)
        elif len(group) <= _EXACT_MAX:
            chosen.extend(_best_exact(group))
        else:
            chosen.extend(_best_greedy(group))
    return chosen


def price_basket(
    lines: list[BasketLine],
    promotions: list[Promotion],
    now: datetime,
) -> dict:
    """Price a basket at one store, applying the best compatible promotions.

    Returns the shape the API and the CartBreakdown component consume:
        base_total, final_total, total_savings, lines[], applied_promotions[]
    """
    base_total = _money(sum((ln.line_total for ln in lines), _ZERO))

    active = [p for p in promotions if p.is_active(now)]
    candidates = [c for p in active if (c := _evaluate(p, lines, base_total))]
    applied = _resolve_conflicts(candidates)

    # Attribute each promotion's saving back to the lines it consumed, so the UI
    # can strike through a per-line price instead of only showing a lump sum.
    line_savings: dict[int, Decimal] = {}
    line_promo: dict[int, Promotion] = {}
    for cand in applied:
        weights = {
            cid: qty * next(ln.unit_price for ln in lines if ln.canonical_id == cid)
            for cid, qty in cand.consumed.items()
        }
        weight_total = sum(weights.values(), _ZERO)
        for cid, weight in weights.items():
            share = cand.savings if weight_total == 0 else cand.savings * weight / weight_total
            line_savings[cid] = line_savings.get(cid, _ZERO) + share
            line_promo.setdefault(cid, cand.promo)

    out_lines = []
    for ln in lines:
        original = _money(ln.line_total)
        saved = _money(min(line_savings.get(ln.canonical_id, _ZERO), original))
        promo = line_promo.get(ln.canonical_id)
        out_lines.append({
            "canonical_id": ln.canonical_id,
            "product_id": ln.product_id,
            "name": ln.name,
            "quantity": float(ln.quantity),
            "unit_price": float(ln.unit_price),
            "original_line_total": float(original),
            "line_total": float(original - saved),
            "found": True,
            "applied_promotion": (
                {
                    "id": promo.id,
                    "reward_kind": promo.reward_kind,
                    "description": promo.description,
                    "min_qty": float(promo.min_qty),
                    "discount_rate": float(promo.discount_rate) if promo.discount_rate else None,
                    "discount_amount": float(promo.discount_amount) if promo.discount_amount else None,
                    "discounted_price": float(promo.discounted_price) if promo.discounted_price else None,
                    "min_basket_amount": (
                        float(promo.min_basket_amount) if promo.min_basket_amount else None
                    ),
                }
                if promo
                else None
            ),
        })

    total_savings = _money(sum((c.savings for c in applied), _ZERO))
    # Never let rounding or a pathological promo produce a negative bill.
    final_total = _money(max(base_total - total_savings, _ZERO))

    return {
        "base_total": float(base_total),
        "final_total": float(final_total),
        "total_savings": float(base_total - final_total),
        "lines": out_lines,
        "applied_promotions": [
            {
                "id": c.promo.id,
                "reward_kind": c.promo.reward_kind,
                "description": c.promo.description,
                "savings": float(c.savings),
                "min_qty": float(c.promo.min_qty),
                "discounted_price": (
                    float(c.promo.discounted_price) if c.promo.discounted_price else None
                ),
                "discount_rate": float(c.promo.discount_rate) if c.promo.discount_rate else None,
                "discount_amount": (
                    float(c.promo.discount_amount) if c.promo.discount_amount else None
                ),
                "min_basket_amount": (
                    float(c.promo.min_basket_amount) if c.promo.min_basket_amount else None
                ),
            }
            for c in applied
        ],
    }
