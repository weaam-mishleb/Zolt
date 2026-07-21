"""Unit tests for money rounding and the search boolean-expression builder."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.services.comparison import _money
from backend.app.services.search import _boolean_expr


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        (Decimal("18"), 18.0),
        (Decimal("7.5"), 7.5),
        (Decimal("0"), 0.0),
        (Decimal("18.004"), 18.0),          # rounds down
        (Decimal("18.005"), 18.01),         # HALF_UP rounds up exactly at .005
        (Decimal("18.995"), 19.0),
        (Decimal("2.675"), 2.68),           # Decimal avoids the float 2.675 trap
        (Decimal("3.14159"), 3.14),
        (Decimal("100"), 100.0),
    ],
)
def test_money_rounds_half_up_to_two_places(value, expected):
    assert _money(value) == expected


@pytest.mark.parametrize(
    "query, expected",
    [
        ("חלב תנו", "+חלב* +תנו*"),          # each token required + prefix
        ("חלב", "+חלב*"),
        ("", ""),
        ("   ", ""),                          # whitespace only
        ("קוקה קולה", "+קוקה* +קולה*"),
        ("חלב  תנובה", "+חלב* +תנובה*"),     # collapse extra spaces
        ("חלב+", "+חלב*"),                    # boolean operator stripped
        ('"חלב"', "+חלב*"),                   # quotes stripped
        ("(במבה)", "+במבה*"),                 # parentheses stripped
    ],
)
def test_boolean_expr(query, expected):
    assert _boolean_expr(query) == expected
