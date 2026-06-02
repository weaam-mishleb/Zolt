"""City-name normalization for the Israeli supermarket feeds.

The three chains encode the store's city very differently:

  * Shufersal  — a Hebrew city NAME in `city`, with spelling variants
                 ("תל אביב" / "תל אביב-יפו" / "תלאביב", "פתח תקוה" / "פתח תקווה" …).
  * Rami Levy  — a numeric CBS locality code (סמל יישוב) in `city`
                 (3000 = ירושלים, 5000 = תל אביב …); the locality is in `store_name`.
  * Osher Ad   — same numeric-code convention as Rami Levy.

`normalize_city()` turns all of these into one canonical Hebrew city name so the
basket comparison (which matches stores by exact city) sees every chain together.
"""
from __future__ import annotations

import re

# ── CBS locality codes (סמל יישוב) → canonical name ──────────────────────────
# Covers the codes that appear in the Rami Levy / Osher Ad store files.
CITY_CODE_TO_NAME: dict[str, str] = {
    "31": "אופקים",
    "70": "אשדוד",
    "171": "פרדסיה",
    "195": "קדימה צורן",
    "246": "נתיבות",
    "681": "גבעת שמואל",
    "874": "מגדל העמק",
    "1015": "מבשרת ציון",
    "1031": "שדרות",
    "1139": "כרמיאל",
    "1200": "מודיעין",
    "2400": "אור יהודה",
    "2500": "נשר",
    "2600": "אילת",
    "2610": "בית שמש",
    "2620": "קרית אונו",
    "2630": "קרית גת",
    "2640": "ראש העין",
    "2660": "יבנה",
    "2800": "קרית שמונה",
    "3000": "ירושלים",
    "3570": "אריאל",
    "3616": "מעלה אדומים",
    "3780": "ביתר עילית",
    "4000": "חיפה",
    "5000": "תל אביב",
    "6100": "בני ברק",
    "6200": "בת ים",
    "6300": "גבעתיים",
    "6400": "הרצליה",
    "6500": "חדרה",
    "6600": "חולון",
    "6700": "טבריה",
    "6900": "כפר סבא",
    "7000": "לוד",
    "7100": "אשקלון",
    "7400": "נתניה",
    "7600": "עכו",
    "7700": "עפולה",
    "7800": "פרדס חנה כרכור",
    "7900": "פתח תקווה",
    "8300": "ראשון לציון",
    "8400": "רחובות",
    "8500": "רמלה",
    "8600": "רמת גן",
    "8700": "רעננה",
    "9000": "באר שבע",
    "9100": "נהריה",
    "9200": "בית שאן",
    "9300": "זכרון יעקב",
    "9500": "קרית ביאליק",
    "9600": "קרית ים",
    "9700": "הוד השרון",
}

# ── Name variants / abbreviations → canonical name (for the Shufersal feed) ──
_ALIASES_BY_CANON: dict[str, list[str]] = {
    "תל אביב": ["תל אביב-יפו", "תל אביב יפו", "תל-אביב", "תלאביב", "ת.א", 'ת"א', "תא",
                "רמת אביב", "רמת אביב א", "רמת החייל"],
    "ירושלים": ["ירושלם"],
    "פתח תקווה": ["פתח תקוה", "פתח-תקוה", "פתח-תקווה", "פ.ת"],
    "באר שבע": ["באר-שבע", 'ב"ש'],
    "רמת גן": ["רמת-גן"],
    "רמת השרון": ["רמת-השרון"],
    "ראשון לציון": ["ראשון-לציון", 'ראשל"צ'],
    "הרצליה": ["הרצלייה"],
    "נהריה": ["נהרייה"],
    "קרית ים": ["קריית ים"],
    "קרית גת": ["קריית גת"],
    "קרית אונו": ["קריית אונו"],
    "קרית ביאליק": ["קריית ביאליק"],
    "קרית שמונה": ["קריית שמונה", "קרית שמונא"],
    "מודיעין": ["מודיעין-מכבים-רעות", "מודיעין מכבים רעות", "מודעין"],
}

# ── Neighborhood / branch locality → city (for storename fallback) ──────────
_NEIGHBORHOODS_BY_CITY: dict[str, list[str]] = {
    "ירושלים": ["תלפיות", "רמות", "גבעת שאול", "פסגת זאב", "עטרות",
                "כנפי נשרים", "ארמון הנציב", "קרית יובל", "קריית יובל"],
}


def _clean(value) -> str | None:
    """Strip surrounding whitespace / quotes / dashes and collapse inner spaces."""
    if value is None:
        return None
    s = str(value).strip().strip("'\"").strip()
    s = s.strip("-").strip()
    s = re.sub(r"\s+", " ", s)
    return s or None


# Build flat lookup tables (keys pre-cleaned so lookups match cleaned input).
CITY_ALIASES: dict[str, str] = {}
for _canon, _variants in _ALIASES_BY_CANON.items():
    CITY_ALIASES[_clean(_canon)] = _canon
    for _v in _variants:
        CITY_ALIASES[_clean(_v)] = _canon

NEIGHBORHOOD_TO_CITY: dict[str, str] = {}
for _city, _hoods in _NEIGHBORHOODS_BY_CITY.items():
    for _h in _hoods:
        NEIGHBORHOOD_TO_CITY[_clean(_h)] = _city

# Canonical city names, longest first (greedy token/substring matching).
CANONICAL_CITIES: list[str] = sorted(
    set(CITY_CODE_TO_NAME.values())
    | set(CITY_ALIASES.values())
    | set(NEIGHBORHOOD_TO_CITY.values()),
    key=len,
    reverse=True,
)


def _from_store_name(store_name) -> str | None:
    """Best-effort city from a branch name, e.g. 'רגר באר שבע' → 'באר שבע'."""
    sn = _clean(store_name)
    if not sn:
        return None
    if sn in NEIGHBORHOOD_TO_CITY:
        return NEIGHBORHOOD_TO_CITY[sn]
    if sn in CITY_ALIASES:
        return CITY_ALIASES[sn]
    tokens = sn.split()
    for city in CANONICAL_CITIES:
        # multi-word city → substring; single-word → whole-token match
        if (" " in city and city in sn) or (city in tokens):
            return city
    return None


def normalize_city(raw_city, store_name=None) -> str | None:
    """Return a canonical city name from the raw feed value (+ store name)."""
    cleaned = _clean(raw_city)
    if cleaned is None:
        return _from_store_name(store_name)

    if cleaned.isdigit():  # CBS locality code (Rami Levy / Osher Ad)
        name = CITY_CODE_TO_NAME.get(str(int(cleaned)))
        return name or _from_store_name(store_name)

    # Hebrew city name (Shufersal): map known variants, else keep cleaned name.
    return CITY_ALIASES.get(cleaned, cleaned)
