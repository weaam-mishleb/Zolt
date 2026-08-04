"""Tests for pruning canonical products nothing maps to.

WHY THIS EXISTS
---------------
`etl.canonical` only upserts; nothing ever deletes a canonical row whose last
member product went away — and members go away routinely, because a reload that
rekeys barcodes replaces `products`, which cascades `product_map` away and
strands the canonical behind it.

On production that had reached 299,922 of 487,891 canonical rows (61.5%) with no
member at all, plus 928,197 promotion_items pointing at them. On an instance
whose 1,619 MB working set already does not fit its 1,024 MB buffer pool, that
is ~237 MB of cache spent on rows nothing can read — which is why upserts were
running at ~300 rows/s.
"""
from __future__ import annotations

from scripts.prune_orphans import CHUNK, _COUNT_DEAD_LINKS, _COUNT_ORPHANS, _DELETE, survey


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Conn:
    """Answers survey()'s three counts in the order it asks for them."""

    def __init__(self, orphans, dead_links, total):
        self._answers = [orphans, dead_links, total]

    def execute(self, *_a, **_kw):
        return _Result(self._answers.pop(0))


def test_survey_reports_orphans_dead_links_and_total():
    assert survey(_Conn(299_922, 928_197, 487_891)) == (299_922, 928_197, 487_891)


def test_a_clean_database_surveys_as_zero():
    assert survey(_Conn(0, 0, 45_562)) == (0, 0, 45_562)


# ── the predicate ───────────────────────────────────────────────────────────
def test_orphan_means_no_product_maps_to_it():
    """Not 'member_count = 0' — that column is a denormalised cache and is stale
    exactly when rows have been deleted underneath it, which is this situation."""
    sql = str(_COUNT_ORPHANS)
    assert "NOT EXISTS" in sql
    assert "product_map" in sql
    assert "member_count" not in sql


def test_dead_links_are_counted_through_product_map_too():
    assert "product_map" in str(_COUNT_DEAD_LINKS)
    assert "promotion_items" in str(_COUNT_DEAD_LINKS)


def test_the_delete_targets_canonical_products_by_id():
    """promotion_items and product_map both cascade from canonical_products, so
    deleting the parent is enough and nothing is left dangling."""
    sql = str(_DELETE)
    assert "DELETE FROM canonical_products" in sql
    assert "WHERE id IN" in sql


def test_deletion_is_chunked_not_one_giant_transaction():
    """A single 300k-row DELETE is the same long-transaction mistake that made
    the writes slow in the first place."""
    assert 0 < CHUNK <= 20_000
    assert "LIMIT :chunk" in str(__import__("scripts.prune_orphans", fromlist=["_ORPHAN_IDS"])._ORPHAN_IDS)
