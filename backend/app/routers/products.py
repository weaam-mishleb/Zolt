"""Product search / autocomplete endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import ProductOut
from ..services import image_service, search

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/search", response_model=list[ProductOut], summary="Search products (autocomplete)")
def search_products(
    q: str = Query(..., min_length=1, description="Free-text query (Hebrew product name)"),
    limit: int = Query(10, ge=1, le=50, description="Max results"),
    db: Session = Depends(get_db),
):
    """Text search over product names, suitable for autocomplete.

    Uses MySQL FULLTEXT (prefix, boolean mode) with a LIKE substring fallback.

    Images attached here are CACHE READS only — no provider is called on a
    keystroke. Ids that come back without one are the client's cue to ask
    /products/images, which is allowed to be slow and metered.
    """
    results = search.search_products(db, q=q, limit=limit)
    # Copy before mutating: search results come from a process-local LRU, and
    # handing back the cached list once let a caller corrupt it (there is a test).
    results = [dict(r) for r in results]
    urls = image_service.cached_urls(db, [r["id"] for r in results if r.get("id")])
    for r in results:
        r["image_url"] = urls.get(r.get("id"))
    return results


@router.get("/images", summary="Resolve product images (cache → Open Food Facts)")
def product_images(
    ids: str = Query(..., description="Comma-separated product ids, max 50"),
    db: Session = Depends(get_db),
):
    """{product_id: url} for the products that HAVE an image.

    Deliberately a separate call rather than part of /products/search: reaching
    Open Food Facts does not belong in the latency budget of a keystroke. Search
    returns whatever is already cached; the client calls this only for ids that came
    back without a url, so a product is looked up once and then free forever.

    Capped at 50 ids so one request cannot turn into a burst against OFF, which is
    a volunteer-run nonprofit.
    """
    wanted: list[int] = []
    for chunk in ids.split(",")[:50]:
        chunk = chunk.strip()
        if chunk.isdigit():
            wanted.append(int(chunk))
    return image_service.resolve(db, wanted)


@router.get("/images/status", summary="Which image providers are active")
def image_status():
    return image_service.provider_state()
