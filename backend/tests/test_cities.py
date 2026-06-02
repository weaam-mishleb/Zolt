"""Unit tests for ETL city normalization."""
from __future__ import annotations

import pytest

from etl.cities import normalize_city


@pytest.mark.parametrize(
    "raw, store_name, expected",
    [
        # Shufersal name variants → canonical
        ("תל אביב-יפו", None, "תל אביב"),
        ("תלאביב", None, "תל אביב"),
        ('  "תל אביב"  ', None, "תל אביב"),   # strips whitespace + quotes
        ("פתח-תקוה", None, "פתח תקווה"),
        ("פתח תקווה", None, "פתח תקווה"),
        ("ראשון-לציון", None, "ראשון לציון"),
        ("באר-שבע", None, "באר שבע"),
        # Rami Levy / Osher Ad numeric CBS codes → canonical
        ("5000", None, "תל אביב"),
        ("3000", None, "ירושלים"),
        ("9000", None, "באר שבע"),
        ("874", "מגדל העמק", "מגדל העמק"),
        # Empty / unknown code → derive city from the store name
        ("0", "אסתר המלכה תל אביב", "תל אביב"),
        ("", "רגר באר שבע", "באר שבע"),
        ("", "רמות", "ירושלים"),               # Jerusalem neighborhood
        # Unknown name passes through (cleaned), never wrongly remapped
        ("גבעתיים", None, "גבעתיים"),
        ("באר יעקב", None, "באר יעקב"),         # must NOT become "באר שבע"
        # Warehouse / no resolvable city → None
        ("99999", "בזק מחסני מזון", None),
        ("", "", None),
    ],
)
def test_normalize_city(raw, store_name, expected):
    assert normalize_city(raw, store_name) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("אוריהודה", "אור יהודה"),
        ("אור-יהודה", "אור יהודה"),
        ("בת-ים", "בת ים"),
        ("  בת   ים  ", "בת ים"),     # leading/trailing + collapsed inner spaces
        ("בית-שמש", "בית שמש"),
        (" בית  שמש ", "בית שמש"),
        ("בני-ברק", "בני ברק"),
        ("בני  ברק", "בני ברק"),
    ],
)
def test_city_dedup_variants(raw, expected):
    assert normalize_city(raw) == expected
