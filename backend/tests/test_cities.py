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
        ("99999", "סניף ללא שם יישוב", None),
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
        ("כפר-סבא", "כפר סבא"),
        ("כפרסבא", "כפר סבא"),
        ("כפר סבא צפון", "כפר סבא"),   # branch-area qualifier, not a city
        ("יוקנעם", "יקנעם"),
        ("יקנעם עילית", "יקנעם"),
        ("יוקנעם עילית", "יקנעם"),
        ("קריתגת", "קרית גת"),
    ],
)
def test_city_dedup_variants(raw, expected):
    assert normalize_city(raw) == expected


# ── canonical merges ────────────────────────────────────────────────────────
# These guard the split that actually happened in production: the alias
# validator only ever checked ALIAS TARGETS, so a duplicate spelling arriving
# through the CBS-code path or through the verbatim fallback sailed straight
# past it. 11 places ended up filed under two spellings, stranding 34 branches.
@pytest.mark.parametrize(
    "raw, expected",
    [
        # 1. the CBS-code path: the gazetteer calls Tel Aviv "תל אביב - יפו",
        #    this project files it as "תל אביב". Rami Levy / Osher Ad send codes.
        ("5000", "תל אביב"),
        # 2. the same spelling arriving as text
        ("תל אביב - יפו", "תל אביב"),
        # 3. the verbatim fallback: a feed typo became a city of its own
        #    (one branch, on רחוב הארבעה — the address is what identified it)
        ("תל אבית יפה", "תל אביב"),
        # already canonical: must survive untouched
        ("תל אביב", "תל אביב"),
        # קריית vs קרית, both directions of the split
        ("קריית מוצקין", "קרית מוצקין"),
        ("קריית מלאכי", "קרית מלאכי"),
        # hyphen/space variants of a compound locality
        ("פרדס חנה כרכור", "פרדס חנה-כרכור"),
        ("מעלות תרשיחא", "מעלות-תרשיחא"),
        ("קדימה צורן", "קדימה-צורן"),
        # spelled with two yods, which is why the existing alias never matched
        ("דליית אל כרמל", "דאלית אל-כרמל"),
        # renamed municipality
        ("נצרת עילית - נוף הגליל", "נוף הגליל"),
    ],
)
def test_canonical_merge_collapses_duplicate_spellings(raw, expected):
    assert normalize_city(raw) == expected


@pytest.mark.parametrize("raw", ["חצור", "יקנעם", "מיתרים"])
def test_ambiguous_names_are_never_merged(raw):
    """A wrong city is worse than none — it prices a basket against the wrong
    branches and looks fine doing it. חצור could be חצור הגלילית or חצור-אשדוד."""
    from etl.cities import CANONICAL_MERGES

    assert raw not in CANONICAL_MERGES


def test_every_merge_target_is_a_known_locality():
    """A typo here silently splits a city instead of healing one, so the loader
    drops unknown targets. If this fails, an entry was silently discarded."""
    import json
    import pathlib

    from etl.cities import CANONICAL_MERGES

    raw = json.loads(
        (pathlib.Path(__file__).parents[2] / "etl" / "city_aliases.json").read_text("utf-8")
    )
    assert len(CANONICAL_MERGES) == len(raw.get("canonical_merges") or {})


def test_canonical_city_is_idempotent_and_cycle_safe():
    from etl.cities import CANONICAL_MERGES, canonical_city

    for source, target in CANONICAL_MERGES.items():
        once = canonical_city(source)
        assert once == canonical_city(once), f"{source!r} does not settle"
        assert once == canonical_city(target)
