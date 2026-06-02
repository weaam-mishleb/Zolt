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


class ProductBrief(BaseModel):
    id: int
    name: str | None = None
    barcode: str | None = None


class StoreItemPrice(BaseModel):
    product_id: int
    quantity: float
    unit_price: float | None = None  # None when the store does not carry the item
    line_total: float | None = None
    found: bool


class StoreComparison(BaseModel):
    store_id: int
    chain_id: str
    chain_name: str
    store_name: str | None = None
    address: str | None = None
    city: str | None = None
    total: float                         # sum over the items the store actually has
    found_count: int
    missing_count: int
    missing_product_ids: list[int]
    is_complete: bool                    # carries every requested product
    rank: int | None = None              # 1 = cheapest complete store; None if incomplete
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
    stores: list[StoreComparison]


# ── Admin auth ───────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until expiry
