"""Store listing endpoints (filter by city)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import StoreOut
from ..services import search

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("", response_model=list[StoreOut], summary="List stores (filter by city)")
def list_stores(
    city: str | None = Query(None, description="Filter by city (partial match)"),
    chain: str | None = Query(None, description="Filter by chain name or chain id"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return search.list_stores(db, city=city, chain=chain, limit=limit, offset=offset)


@router.get("/cities", response_model=list[str], summary="Distinct cities with stores")
def list_cities(db: Session = Depends(get_db)):
    return search.list_cities(db)
