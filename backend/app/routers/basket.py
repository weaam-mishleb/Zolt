"""Basket comparison endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
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
    return comparison.compare_basket(db, req.city, req.items)
