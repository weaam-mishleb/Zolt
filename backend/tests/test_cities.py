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
        # All three land on the CBS locality now, not the bare "יקנעם". This
        # REVERSES the earlier canonical: CITY_ALIASES still folds the variants
        # onto "יקנעם", and a canonical_merge then carries that to "יקנעם עילית"
        # — reviewed 2026-08-07, on the grounds that essentially all the retail
        # is in עילית. A branch in the מושבה is routed there by its address.
        ("יוקנעם", "יקנעם עילית"),
        ("יקנעם עילית", "יקנעם עילית"),
        ("יוקנעם עילית", "יקנעם עילית"),
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


@pytest.mark.parametrize(
    "raw, address, expected",
    [
        # One spelling, two real places — the address picks, it does not guess.
        ("חצור", "תל חי 1", "חצור הגלילית"),
        ("חצור", "קיבוץ חצור, אשדוד", "חצור-אשדוד"),
        ("יקנעם", "התמר 1", "יקנעם עילית"),
        ("יקנעם", "המושבה 3", "יקנעם (מושבה)"),
        # No address at all falls back to the reviewed default rather than None.
        ("חצור", None, "חצור הגלילית"),
    ],
)
def test_address_selects_between_two_places_sharing_a_name(raw, address, expected):
    assert normalize_city(raw, None, address) == expected


def test_regional_councils_are_allowed_targets_but_are_not_localities():
    """A council is an administrative area, not a place, so it will never be in
    the CBS list. Naming it still beats dropping the branch's city to NULL."""
    import json
    import pathlib

    from etl.cities import CANONICAL_MERGES

    raw = json.loads(
        (pathlib.Path(__file__).parents[2] / "etl" / "city_aliases.json").read_text("utf-8")
    )
    councils = set(raw["regional_councils"])
    gazetteer = set(
        json.loads(
            (pathlib.Path(__file__).parents[2] / "etl" / "localities.json").read_text("utf-8")
        )["localities"].values()
    )
    assert councils and not (councils & gazetteer)
    assert CANONICAL_MERGES["בקעת הירדן"] in councils


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


# ── chain display names ─────────────────────────────────────────────────────
def test_chain_display_name_overrides_the_feeds_own_name():
    """Some suppliers publish their registered company name rather than the brand
    a shopper recognises. The override is keyed on chain_id, not on the name it
    replaces — a key that had to match free text would stop firing silently the
    day the supplier retyped it."""
    from etl.config import CHAIN_DISPLAY_NAMES
    from etl.normalize import normalize_store

    assert CHAIN_DISPLAY_NAMES, "no display_names loaded from chains.json"
    chain_id, expected = next(iter(CHAIN_DISPLAY_NAMES.items()))
    row = {
        "chainid": chain_id,
        "storeid": "1",
        "chainname": 'משהו אחר לגמרי בע"מ',
        "storename": "x",
        "address": "y",
        "city": "תל אביב",
    }
    assert normalize_store(row, "fallback")["chain_name"] == expected


def test_chain_without_an_override_keeps_the_feed_name():
    from etl.normalize import normalize_store

    row = {
        "chainid": "7290027600007",
        "storeid": "2",
        "chainname": "שופרסל",
        "storename": "x",
        "address": "y",
        "city": "תל אביב",
    }
    assert normalize_store(row, "fallback")["chain_name"] == "שופרסל"


# ── address scrubbing ───────────────────────────────────────────────────────
# The UI prints the address under a 📍, so a placeholder does not read as missing
# data — it reads as data we believe. "{}" was on 19 branches.
@pytest.mark.parametrize(
    "raw",
    ["{}", "[]", "0", "0.0", "00", "-", "--", ".", ",", "?", "h", "   ", "", None,
     "unknown", "לא ידוע", "null", "NaN", "undefined", "5614", "123"],
)
def test_placeholder_addresses_become_none(raw):
    from etl.normalize import clean_address

    assert clean_address(raw) is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("הארבעה 6", "הארבעה 6"),
        ("  ויצמן 20  ", "ויצמן 20"),
        # The trailing lone zero is the feed's "no house number", so it is
        # stripped — but only when it stands alone. See the pair of tests below.
        ("כביש 4, בכניסה לפוריידיס 0", "כביש 4, בכניסה לפוריידיס"),
        ("שד' ירושלים", "שד' ירושלים"),
        ("Ben Gurion 1", "Ben Gurion 1"),
        ("ألقدس 3", "ألقدس 3"),
        ("ת.ד. 123", "ת.ד. 123"),
    ],
)
def test_real_addresses_survive(raw, expected):
    from etl.normalize import clean_address

    assert clean_address(raw) == expected


def test_address_rules_are_not_applied_to_numbers():
    """clean_str also feeds to_decimal / norm_code / to_bool / parse_dt. A bare
    "0" must stay a zero there — treating it as absent would turn a price of zero
    into NULL and a store code of "0" into None."""
    from decimal import Decimal

    from etl.normalize import clean_str, norm_code, to_decimal

    assert to_decimal("0") == Decimal("0")
    assert norm_code("0") == "0"
    assert clean_str("0") == "0"
    # ...while the structural markers are rejected everywhere, numbers included.
    assert clean_str("{}") is None
    assert to_decimal("{}") is None


def test_normalize_store_nulls_a_placeholder_address():
    from etl.normalize import normalize_store

    row = {"chainid": "7290027600007", "storeid": "9", "chainname": "שופרסל",
           "storename": "רמת החייל", "address": "{}", "city": "תל אביב"}
    assert normalize_store(row, "fallback")["address"] is None


# ── trailing house-number zero ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("כביש ראשי 5614 0", "כביש ראשי 5614"),
        ("אלקודס 0", "אלקודס"),
        ("הדקל ,אזור תעשייה צפוני 0", "הדקל ,אזור תעשייה צפוני"),
        ("ויצמן 0 0", "ויצמן"),
        # A REAL house number must survive. The rule needs whitespace or a comma
        # before the zero, so the 0 inside "20"/"100" is never touched — that is
        # the whole difference between cleanup and data loss.
        ("הרצל 20", "הרצל 20"),
        ("הרצל 100", "הרצל 100"),
        ("שדרות בן גוריון 10", "שדרות בן גוריון 10"),
        ("דרך חיפה 502", "דרך חיפה 502"),
    ],
)
def test_trailing_lone_zero_is_stripped_but_real_numbers_are_not(raw, expected):
    from etl.normalize import clean_address

    assert clean_address(raw) == expected


def test_address_that_is_only_zeros_becomes_none_not_empty():
    """Stripping runs BEFORE the validity checks, so a value that strips down to
    nothing returns None rather than an empty string the UI would still render."""
    from etl.normalize import clean_address

    assert clean_address("0 0 0") is None
    assert clean_address("א 0") is None      # one letter left over is not an address


def test_every_display_name_matches_a_configured_chain_name():
    """The override exists to stop the feed's company name winning — not to
    invent a third spelling. Two spellings of one chain read as two chains."""
    import json
    import pathlib

    from etl.config import CHAIN_DISPLAY_NAMES

    data = json.loads(
        (pathlib.Path(__file__).parents[2] / "etl" / "chains.json").read_text("utf-8")
    )
    configured = {c.get("name") for c in data.get("chains", [])}
    # Two deliberate exceptions, both listed rather than pattern-matched so that
    # adding a third is a decision someone makes on purpose:
    #   פז-yellow   — has no `chains` entry at all; it arrives inside another
    #                 chain's file, so there is no configured name to match.
    #   משנת יוסף   — `chains` splits it as "משנת יוסף 1"/"משנת יוסף 2" because
    #                 upstream publishes two files. The digit is a file-splitting
    #                 artifact, not part of the brand, and must not reach a price
    #                 card.
    allowed = {"פז-yellow", "משנת יוסף"}
    unknown = {v for v in CHAIN_DISPLAY_NAMES.values() if v not in configured} - allowed
    assert not unknown, f"display names with no matching chain: {unknown}"
