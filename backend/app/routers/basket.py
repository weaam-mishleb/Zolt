"""Basket comparison endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import BasketCompareRequest, BasketCompareResponse
from ..services import comparison

router = APIRouter(prefix="/basket", tags=["basket"])


@router.post("/compare", response_model=BasketCompareResponse, summary="Compare a basket across the 3 chains")
def compare(req: BasketCompareRequest, db: Session = Depends(get_db)):
    """Σ(price × quantity) per branch in the requested city, ranked ascending.

    Complete stores (carry every item) compete for the win; incomplete stores
    are returned with their missing items marked but without a rank.
    """
    # Explicit input validation → HTTP 400 with a clear message.
    if not req.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Basket cannot be empty"
        )
    if any(it.quantity <= 0 for it in req.items):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity must be a positive number",
        )
    return comparison.compare_basket(db, req.city, req.items)
