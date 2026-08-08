"""Resolve a product image URL: DB cache → Open Food Facts → Google CSE → none.

WHY THE CACHE IS THE FEATURE
----------------------------
Measured coverage decides the shape of this. Open Food Facts has a usable image
for 7.0% of our real GTINs (14 of 200, `scripts/off_coverage.py`). We hold 212,545
real GTINs, so roughly 198,000 products would fall through to Google Custom
Search — and CSE is metered: 100 queries/day free, then $5 per 1,000 with a hard
10,000/day ceiling. Resolving the catalogue once therefore costs about $990 and
takes three weeks of continuous querying.

So this never walks the catalogue. It resolves ON DEMAND, once per product, and
writes the answer — including a miss — back to `products`. A miss has to be as
durable as a hit or the meter never stops.

Two guards make the cost bounded rather than merely discouraged:
  * a negative cache with a re-check interval (`_RECHECK_AFTER`), so an unknown
    product is retried occasionally, not per page view;
  * a per-process daily budget (`settings.image_google_daily_budget`), checked
    before every paid call. Reaching it degrades to the placeholder, which is a
    visual outcome rather than an invoice.

The hot paths — search and basket comparison — READ this cache and never trigger
a lookup, so a shopper's latency never depends on a third party. Resolution is a
separate, explicit endpoint the frontend calls for products whose URL is unknown.

ON THE GOOGLE FALLBACK, PLAINLY
-------------------------------
Image results are other people's photographs — mostly the retailers' own. Serving
them as our product imagery is the same exposure that got Shufersal scraping
dropped from this project earlier, arriving by a different route, and Google's CSE
terms separately restrict storing results the way this module does. That is a
product/legal call, not an engineering one, so the code makes it reversible rather
than invisible: the provider is off unless BOTH keys are set, every row records
which provider answered in `image_source`, and `purge_source('google')` removes
them in one statement.
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
_RECHECK_AFTER = {"off": timedelta(days=90), "google": timedelta(days=90), "none": timedelta(days=30)}

_OFF_API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
_OFF_FIELDS = "code,image_front_url,image_url"
# OFF asks automated callers to identify themselves and stay well under
# 100 req/min. We are on-demand and single-threaded, so we are far below that.
_OFF_UA = "Zolt/1.0 (grocery price comparison; +weaam_m19@icloud.com)"

_GOOGLE_API = "https://www.googleapis.com/customsearch/v1"

_SELECT = text(
    "SELECT id, barcode, name, image_url, image_source, image_checked_at "
    "FROM products WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))

_UPDATE = text(
    "UPDATE products SET image_url = :url, image_source = :src, image_checked_at = :at "
    "WHERE id = :id"
)

# Internal (non-GTIN) barcodes are namespaced '<chain_id>_<code>' by
# etl.normalize.product_key. No image provider can know those, so they skip OFF
# entirely and go straight to the name-based fallback.
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


def google_enabled() -> bool:
    """Both credentials present. Absent keys mean the provider simply does not run."""
    return bool(settings.google_api_key and settings.google_search_engine_id)


def from_google(name: str, *, budget: "_Budget") -> str | None:
    """First image result for the product name. METERED — see the module docstring."""
    if not google_enabled() or not (name or "").strip():
        return None
    if not budget.take():
        log.warning("google image budget exhausted for today — serving the placeholder")
        return None

    params = urllib.parse.urlencode(
        {
            "key": settings.google_api_key,
            "cx": settings.google_search_engine_id,
            "q": name.strip(),
            "searchType": "image",
            "num": 1,
            "safe": "active",
            # Grocery packshots are small; asking for huge originals wastes the
            # shopper's bandwidth on a 40px tile.
            "imgSize": "medium",
        }
    )
    payload = _get_json(f"{_GOOGLE_API}?{params}", {}, settings.image_provider_timeout)
    items = (payload or {}).get("items") or []
    return (items[0].get("link") if items else None) or None


class _Budget:
    """Per-process daily cap on PAID calls.

    Deliberately not a distributed counter: this is a spend guard, not a quota
    accountant. Google enforces the real ceiling; this exists so a traffic spike
    or a retry loop cannot turn into an invoice, and every worker holding its own
    conservative share is the failure mode we want.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._day: str | None = None
        self._used = 0

    def take(self) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._used = today, 0
        if self._used >= self.limit:
            return False
        self._used += 1
        return True

    @property
    def used(self) -> int:
        return self._used


_budget = _Budget(settings.image_google_daily_budget)


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

        url, source = None, "none"
        if _is_real_gtin(row["barcode"]):
            url = from_off(row["barcode"])
            if url:
                source = "off"
        if not url:
            url = from_google(row["name"], budget=_budget)
            if url:
                source = "google"

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


def budget_state() -> dict:
    """For the admin/diagnostics surface: is the paid provider on, and how hot."""
    return {
        "google_enabled": google_enabled(),
        "daily_budget": _budget.limit,
        "used_today": _budget.used,
    }
