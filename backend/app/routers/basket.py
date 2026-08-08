"""Basket comparison + summary endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import (
    BasketCompareRequest,
    BasketCompareResponse,
    BasketSummaryRequest,
    BasketSummaryResponse,
)
from ..services import comparison, image_service

router = APIRouter(prefix="/basket", tags=["basket"])

_MAX_BASKET_ITEMS = 50  # SRS constraint: basket capped at 50 lines


def _validate_items(items, *, allow_empty: bool = False) -> None:
    """Shared explicit input validation → HTTP 400 with a clear message."""
    if not items and not allow_empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Basket cannot be empty"
        )
    if len(items) > _MAX_BASKET_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Basket is limited to {_MAX_BASKET_ITEMS} items",
        )
    if any(it.quantity <= 0 for it in items):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity must be a positive number",
        )


@router.post("/compare", response_model=BasketCompareResponse, summary="Compare a basket across the 3 chains")
def compare(req: BasketCompareRequest, db: Session = Depends(get_db)):
    """Σ(price × quantity) per branch in the requested city, ranked ascending.

    Complete stores (carry every item) compete for the win; incomplete stores
    are returned with their missing items marked but without a rank.
    """
    _validate_items(req.items)
    result = comparison.compare_basket(db, req.city, req.items)
    # Cache reads only — a comparison must not wait on Open Food Facts, and must
    # never trigger a metered Google call for ten branches' worth of products.
    products = result.get("products") or []
    urls = image_service.cached_urls(db, [p.get("id") for p in products if p.get("id")])
    for product in products:
        product["image_url"] = urls.get(product.get("id"))
    return result


@router.post(
    "/summary",
    response_model=BasketSummaryResponse,
    summary="Basket summary: estimated average cost + per-chain coverage (FR-3.6)",
)
def summary(req: BasketSummaryRequest, db: Session = Depends(get_db)):
    """Lightweight sidebar summary — no city needed: estimated cost (average
    price across all branches, × quantity) and how many of the basket items
    each chain carries. An empty basket yields an empty summary, not an error."""
    _validate_items(req.items, allow_empty=True)
    return comparison.basket_summary(db, req.items)
