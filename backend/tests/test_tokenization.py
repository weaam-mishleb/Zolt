"""Unit tests for the pure token/name helpers of the comparison engine.

These power the whole fuzzy-matching precision story (brand words, size
signatures, the head-word guard, name normalization) and run without a DB.
"""
from __future__ import annotations

import pytest

from backend.app.services.comparison import (
    _head_ok,
    _norm_name,
    prominent_tokens,
    size_tokens,
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("קוקה קולה שישיה 1.5 ליטר", ["קוקה", "קולה"]),   # size/unit/multipack dropped
        ("חלב תנובה 3% 1 ליטר", ["חלב", "תנובה"]),        # percent + unit dropped
        ("500 גרם", []),                                    # only a size + unit
        ("סוכריות על מקל", ["סוכריות", "מקל"]),           # stop-word 'על' dropped
        ("אבא אמא ילד בית גן", ["אבא", "אמא", "ילד", "בית"]),  # capped at 4
        ("", []),                                            # empty
        (None, []),                                          # None-safe
        ("א", []),                                           # single char below min length
        ("בטעם לימון", ["בטעם", "לימון"]),                # neither is a stop-word
        ("3%", []),                                          # pure percentage
        ("קוקה-קולה", ["קוקה", "קולה"]),                  # dash splits
        ("לחם/אחיד", ["לחם", "אחיד"]),                     # slash splits
        ('משקה "קולה"', ["משקה", "קולה"]),                # quotes stripped
        ("יין 750 מל", ["יין"]),                            # size + unit dropped
        ("מארז במבה 80 גרם", ["במבה"]),                   # 'מארז' is a packaging stop-word
    ],
)
def test_prominent_tokens(name, expected):
    assert prominent_tokens(name) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("מארז במבה 10*25 גרם", ["10", "25"]),   # pack pattern → both numbers
        ("במבה 80 גרם", ["80"]),
        ("אבוקדו", []),                            # produce, no numbers
        ("חלב 3% 1 ליטר", []),                     # single-digit sizes ignored
        ("מארז 10*25*50 גרם", ["10", "25"]),      # capped at 2
        ("ביסלי גריל 4*55גרם", ["4", "55"]),      # single-digit pack count kept
        ("ביסלי גריל 55 גר", ["55"]),
        ("מארז ספרייט 6*330", ["6", "330"]),
        ("מים 1.5 ליטר", []),                      # decimal splits into single digits
        ("שתיה 500 מל", ["500"]),
        ("מארז 3X250", ["3", "250"]),              # latin X separator
        ("מארז 3×250", ["3", "250"]),              # unicode × separator
        ("", []),
        (None, []),
        ("טונה 4*160 גרם מבצע 2020", ["4", "160"]),  # pack first, extra number capped out
    ],
)
def test_size_tokens(name, expected):
    assert size_tokens(name) == expected


@pytest.mark.parametrize(
    "cand, head, expected",
    [
        ("שמן קוקוס טבעי", "קוקוס", False),        # different product (head שמן)
        ("קמח קוקוס", "קוקוס", False),
        ("קוקוס רצועות טבעי", "קוקוס", True),      # same head
        ("אוכמניות טריות 125 גרם", "אוכמניות", True),
        ("מארז אוכמניות 125 גרם", "אוכמניות", True),  # 'מארז' is a stop-word, head=אוכמניות
        ("מגבוני בייבי טייגר", "מגבון", True),     # final-letter root match (ן↔נ)
        ("דיאט קולה", "קולה", False),
        ("קולה זירו", "קולה", True),
        ("תפוחים אדומים", "תפוח", True),           # prefix either direction
        ("שמן קוקוס", None, True),                  # no head → don't reject
        ("500 גרם", "במבה", True),                  # candidate has no prominent word → keep
    ],
)
def test_head_ok(cand, head, expected):
    assert _head_ok(cand, head) is expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("  חלב  תנובה ", "חלב תנובה"),
        ("חלב\tתנובה", "חלב תנובה"),
        ("חלב   3%   1   ליטר", "חלב 3% 1 ליטר"),
        ("במבה", "במבה"),
        (None, ""),
    ],
)
def test_norm_name(name, expected):
    assert _norm_name(name) == expected
