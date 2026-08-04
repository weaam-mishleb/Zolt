"""Tests for merging repeated sightings of the same product.

WHY THIS EXISTS
---------------
A product is published once per branch that sells it. Shufersal sent the same
33,217 products 1,151,672 times (34.7x) and Rami Levy its 21,932 products
1,607,073 times (73.3x) — every repeat a row lock on `products`, which is where
the deadlocks concentrated, and the reason those two chains ran for over an hour.

The branches do not agree with each other, so the repeats cannot simply be
dropped: measured on Rami Levy, `unit_of_measure` differs between sightings of
one barcode in 35% of cases and `manufacturer` in 6%, almost always because one
branch omits what another supplies. Merging keeps the richest view and sends a
write only when it actually changes.
"""
from __future__ import annotations

from decimal import Decimal

from etl.run import _merge_product


def _p(**kw):
    base = {"barcode": "729", "name": "חלב", "manufacturer": None, "unit_qty": None,
            "quantity": None, "unit_of_measure": None, "is_weighted": False}
    return {**base, **kw}


def test_the_first_sighting_is_taken_whole():
    incoming = _p(manufacturer="תנובה")
    assert _merge_product(None, incoming) == incoming


def test_a_later_blank_never_erases_a_known_value():
    """THE data bug: `manufacturer = VALUES(manufacturer)` let whichever branch
    came last overwrite a real manufacturer with NULL."""
    known = _p(manufacturer="תנובה")
    merged = _merge_product(known, _p(manufacturer=None))
    assert merged["manufacturer"] == "תנובה"


def test_a_later_value_fills_a_gap():
    known = _p(manufacturer=None)
    merged = _merge_product(known, _p(manufacturer="פילסברי"))
    assert merged["manufacturer"] == "פילסברי"


def test_a_later_value_wins_over_an_earlier_one():
    known = _p(unit_of_measure="100 גרם")
    merged = _merge_product(known, _p(unit_of_measure="1 ליטר"))
    assert merged["unit_of_measure"] == "1 ליטר"


def test_empty_strings_count_as_blank_not_as_data():
    known = _p(unit_qty="גרם")
    assert _merge_product(known, _p(unit_qty=""))["unit_qty"] == "גרם"


def test_false_is_a_real_value_not_a_blank():
    """is_weighted=False must be able to correct an earlier True — bool(False)
    is falsy, so a naive truthiness check would silently ignore it."""
    known = _p(is_weighted=True)
    assert _merge_product(known, _p(is_weighted=False))["is_weighted"] is False


def test_zero_quantity_is_a_real_value():
    known = _p(quantity=Decimal("5"))
    assert _merge_product(known, _p(quantity=Decimal("0")))["quantity"] == Decimal("0")


def test_an_unchanged_repeat_compares_equal_so_no_write_is_sent():
    """This equality is what suppresses the redundant writes — the caller only
    upserts when the merged view differs from what it last sent."""
    known = _p(manufacturer="תנובה", unit_of_measure="1 ליטר")
    assert _merge_product(known, _p(manufacturer="תנובה", unit_of_measure="1 ליטר")) == known
    assert _merge_product(known, _p()) == known          # all-blank repeat


def test_merging_does_not_mutate_the_previous_view():
    known = _p(manufacturer="תנובה")
    _merge_product(known, _p(manufacturer="אחר"))
    assert known["manufacturer"] == "תנובה"
