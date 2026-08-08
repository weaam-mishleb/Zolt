"""Pydantic request/response models for the public API."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    barcode: str
    name: str
    manufacturer: str | None = None
    unit_qty: str | None = None
    unit_of_measure: str | None = None
    quantity: float | None = None        # package size, e.g. 55 (גרם)
    is_weighted: bool | None = None      # sold by weight (butcher/produce counter)
    availability: int | None = None      # branches carrying it (search ranking + UI)


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chain_id: str
    chain_name: str
    sub_chain_id: str | None = None
    store_code: str
    store_name: str | None = None
    address: str | None = None
    city: str | None = None
    zip_code: str | None = None


# ── Basket comparison ────────────────────────────────────────────
class BasketItemIn(BaseModel):
    product_id: int
    # No gt=0 here — the basket endpoint validates manually and returns HTTP 400
    # with a clear message (instead of FastAPI's default 422).
    quantity: float = Field(1, description="Units of this product (weighted items may be fractional)")


class BasketCompareRequest(BaseModel):
    city: str = Field(..., min_length=1, description="City whose branches are compared")
    # min_length intentionally omitted — empty baskets are rejected in the router
    # with HTTP 400 "Basket cannot be empty".
    items: list[BasketItemIn] = Field(default_factory=list, description="Basket lines")


class BasketSummaryRequest(BaseModel):
    items: list[BasketItemIn] = Field(default_factory=list, description="Basket lines")


class BasketSummaryChain(BaseModel):
    chain_name: str
    items_covered: int                   # basket items this chain carries (same-name tier)


class BasketSummaryResponse(BaseModel):
    item_count: int
    estimated_total: float | None = None  # Σ(avg price across branches × qty) — FR-3.6
    chains: list[BasketSummaryChain] = []


class ProductBrief(BaseModel):
    id: int
    name: str | None = None
    barcode: str | None = None


class AppliedPromotion(BaseModel):
    """A promotion the engine actually applied — what the UI explains to the user."""

    id: int
    reward_kind: str                      # FIXED_PRICE | BUNDLE_PRICE | PCT_OFF | ...
    description: str | None = None        # feed text, display only
    savings: float | None = None          # ILS this promotion took off the basket
    min_qty: float | None = None
    discounted_price: float | None = None
    discount_rate: float | None = None
    discount_amount: float | None = None
    min_basket_amount: float | None = None


class AvailablePromotion(BaseModel):
    """A promotion that EXISTS on this line but has NOT been applied — almost
    always because the basket quantity has not reached `min_qty`.

    Separate from AppliedPromotion on purpose: that model carries `savings`, and
    there are no savings here yet. Reporting a number the shopper has not earned
    is exactly the kind of thing this response should not do.
    """

    id: int
    reward_kind: str
    description: str | None = None
    min_qty: float | None = None          # how many units unlock it
    discounted_price: float | None = None
    discount_rate: float | None = None
    discount_amount: float | None = None
    min_basket_amount: float | None = None
    units_needed: float | None = None     # min_qty − quantity already in the basket


class StoreItemPrice(BaseModel):
    product_id: int
    quantity: float
    unit_price: float | None = None  # None when the store does not carry the item
    line_total: float | None = None  # after promotions
    original_line_total: float | None = None  # before promotions (struck through in the UI)
    applied_promotion: AppliedPromotion | None = None
    # Set only when `applied_promotion` is None — the two are mutually exclusive,
    # so the UI never has to decide which of them to believe.
    available_promotion: AvailablePromotion | None = None
    # The offer that LOST. Set only alongside `applied_promotion`: a shopper who
    # knows the branch runs "3 ב-17" and sees us charge ₪5 a unit has no way to
    # tell we compared them — this is that receipt. Only ever an offer that is
    # genuinely not cheaper than what we charged.
    alternative_promotion: AvailablePromotion | None = None
    found: bool


class StoreComparison(BaseModel):
    store_id: int
    chain_id: str
    chain_name: str
    store_name: str | None = None
    address: str | None = None
    city: str | None = None
    total: float                         # FINAL price — after promotions; ranking uses this
    base_total: float | None = None      # before promotions
    total_savings: float | None = None   # base_total − total
    applied_promotions: list[AppliedPromotion] = []
    found_count: int
    missing_count: int
    missing_product_ids: list[int]
    is_complete: bool                    # carries every requested product
    rank: int | None = None              # 1 = cheapest complete store; None if incomplete
    pct_above_cheapest: float | None = None  # % gap vs the cheapest complete basket (FR-4.2)
    items: list[StoreItemPrice]


class BasketCompareResponse(BaseModel):
    city: str
    requested_product_ids: list[int]
    products: list[ProductBrief]
    store_count: int                 # total branches found in the city
    complete_store_count: int
    shown_store_count: int = 0        # branches returned after the 10-branch cap
    winner_store_id: int | None = None
    message: str | None = None        # e.g. "No stores in this city" when empty
    promotions_applied: bool = False   # did any branch get a discount?
    stores: list[StoreComparison]


# ── Admin auth ───────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until expiry
