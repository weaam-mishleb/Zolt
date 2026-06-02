"""Product search / autocomplete endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ProductOut
from ..services import search

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/search", response_model=list[ProductOut], summary="Search products (autocomplete)")
def search_products(
    q: str = Query(..., min_length=1, description="Free-text query (Hebrew product name)"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    db: Session = Depends(get_db),
):
    """Text search over product names, suitable for autocomplete.

    Uses MySQL FULLTEXT (prefix, boolean mode) with a LIKE substring fallback.
    """
    return search.search_products(db, q=q, limit=limit)
