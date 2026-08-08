"""Resolve a product image URL: DB cache → Open Food Facts → none.

WHY THE CACHE IS THE FEATURE
----------------------------
Measured coverage decides the shape of this. Open Food Facts has a usable image
for 7.0% of our real GTINs (14 of 200, `scripts/off_coverage.py`). We hold 212,545
real GTINs, so ~198,000 of them have no image and the placeholder is the normal
case for a product tile, not an edge case.

This never walks the catalogue. It resolves ON DEMAND, once per product, and writes
the answer — including a miss — back to `products`. A miss has to be as durable as
a hit, or every unknown product is re-queried on every page view and OFF absorbs
traffic it gets nothing for.

A miss re-checks on an interval (`_RECHECK_AFTER`) rather than per page view,
because OFF is volunteer-curated and genuinely gains products: the same 200-GTIN
sample measured 2.5% in June and 7.0% in August.

WHY THERE IS ONLY ONE PROVIDER
------------------------------
A Google Custom Search fallback was built here and removed. Two reasons, in order
of weight:

  * the images it returned were inconsistent in crop, background and subject —
    it made the product list look worse than the generated placeholder does;
  * they are third parties' photographs, and republishing them is the same
    exposure that got chain scraping dropped from this project earlier.

`image_source` is still recorded per row, and `purge_source()` still exists, so
adding a LICENSED provider later is a contained change and withdrawing one stays a
single statement.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import settings

log = logging.getLogger("zolt.images")

# How long a resolved answer is trusted. Hits are stable (a URL rarely changes);
# misses deserve another look eventually because OFF is volunteer-curated and
# genuinely gains products over time — 2.5% coverage in June measured 7.0% in
# August.
_RECHECK_AFTER = {"off": timedelta(days=90), "none": timedelta(days=30)}

_OFF_API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
_OFF_FIELDS = "code,image_front_url,image_url"
# OFF asks automated callers to identify themselves and stay well under
# 100 req/min. We are on-demand and single-threaded, so we are far below that.
_OFF_UA = "Zolt/1.0 (grocery price comparison; +weaam_m19@icloud.com)"


_SELECT = text(
    "SELECT id, barcode, name, image_url, image_source, image_checked_at "
    "FROM products WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))

_UPDATE = text(
    "UPDATE products SET image_url = :url, image_source = :src, image_checked_at = :at "
    "WHERE id = :id"
)

# Internal (non-GTIN) barcodes are namespaced '<chain_id>_<code>' by
# etl.normalize.product_key — a code this project invented. No external provider
# can know one, so those products are never looked up at all.
def _is_real_gtin(barcode: str | None) -> bool:
    bc = (barcode or "").strip()
    return bc.isdigit() and 8 <= len(bc) <= 14


def _fresh(source: str | None, checked_at: datetime | None, now: datetime) -> bool:
    """True when a stored answer — hit or miss — is still trusted."""
    if checked_at is None:
        return False
    return now - checked_at < _RECHECK_AFTER.get(source or "none", timedelta(days=30))


def _get_json(url: str, headers: dict[str, str], timeout: int) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
        # An image is decoration. A provider being slow or down must degrade to the
        # placeholder, never surface as a failed request for a price comparison.
        log.info("image provider call failed: %s", exc)
        return None


def from_off(barcode: str) -> str | None:
    """Open Food Facts front image, or None. Free, unmetered, no key."""
    payload = _get_json(
        f"{_OFF_API.format(barcode=barcode)}?fields={_OFF_FIELDS}",
        {"User-Agent": _OFF_UA},
        settings.image_provider_timeout,
    )
    if not payload or payload.get("status") == 0:
        return None
    product = payload.get("product") or {}
    url = product.get("image_front_url") or product.get("image_url")
    return url or None


def cached_urls(db: Session, product_ids: list[int]) -> dict[int, str]:
    """{product_id: url} for what is ALREADY stored. Never calls a provider.

    This is what search and basket comparison use. A shopper's latency must not
    depend on Open Food Facts being up.
    """
    if not product_ids:
        return {}
    try:
        rows = db.execute(_SELECT, {"ids": product_ids}).mappings().all()
    except SQLAlchemyError:
        # The columns may not be migrated yet. Images are decoration; a missing
        # column must not break search.
        log.warning("image columns unavailable — serving placeholders", exc_info=True)
        return {}
    return {r["id"]: r["image_url"] for r in rows if r["image_url"]}


def resolve(db: Session, product_ids: list[int], *, now: datetime | None = None) -> dict[int, str]:
    """Resolve images for these products, querying providers only where needed.

    Returns only the products that ended up WITH a url. Writes every outcome back,
    misses included, so the next call is free.
    """
    if not product_ids:
        return {}
    now = now or datetime.now()

    try:
        rows = db.execute(_SELECT, {"ids": product_ids}).mappings().all()
    except SQLAlchemyError:
        log.warning("image columns unavailable — serving placeholders", exc_info=True)
        return {}

    out: dict[int, str] = {}
    for row in rows:
        if _fresh(row["image_source"], row["image_checked_at"], now):
            if row["image_url"]:
                out[row["id"]] = row["image_url"]
            continue

        # Internal (non-GTIN) codes are skipped entirely: no image provider can
        # know a barcode we invented, so asking is a guaranteed miss that still
        # costs a round trip.
        url, source = None, "none"
        if _is_real_gtin(row["barcode"]):
            url = from_off(row["barcode"])
            if url:
                source = "off"

        try:
            db.execute(_UPDATE, {"url": url, "src": source, "at": now, "id": row["id"]})
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            log.warning("could not cache image result for product %s", row["id"], exc_info=True)
        if url:
            out[row["id"]] = url
    return out


def purge_source(db: Session, source: str) -> int:
    """Forget everything one provider gave us, so the decision stays reversible.

    Clears the URL and the timestamp together — leaving the timestamp would mark
    the rows fresh-and-empty and stop them ever being looked up again.
    """
    result = db.execute(
        text(
            "UPDATE products SET image_url = NULL, image_source = NULL, image_checked_at = NULL "
            "WHERE image_source = :src"
        ),
        {"src": source},
    )
    db.commit()
    return result.rowcount


def provider_state() -> dict:
    """For the diagnostics surface: which providers are active, and coverage so far."""
    return {
        "providers": ["off"],
        "timeout_s": settings.image_provider_timeout,
    }
