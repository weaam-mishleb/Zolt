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

import json
import pathlib
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
    "קרית גת": ["קריית גת", "קריתגת"],
    "קרית אונו": ["קריית אונו"],
    "קרית ביאליק": ["קריית ביאליק"],
    "קרית שמונה": ["קריית שמונה", "קרית שמונא"],
    "מודיעין": ["מודיעין-מכבים-רעות", "מודיעין מכבים רעות", "מודעין"],
    # Strong de-duping for known spelling/spacing variants (dashes & no-space).
    # Extra/leading/trailing spaces are already collapsed by _clean().
    "אור יהודה": ["אוריהודה", "אור-יהודה"],
    "בת ים": ["בת-ים", "בתים"],
    "בית שמש": ["בית-שמש", "ביתשמש"],
    "בני ברק": ["בני-ברק", "בניברק"],
    # "צפון" here is a branch-area qualifier Shufersal ships as the city value
    "כפר סבא": ["כפר-סבא", "כפרסבא", "כפר סבא צפון"],
    "יקנעם": ["יוקנעם", "יקנעם עילית", "יוקנעם עילית"],
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
    # The feed's null markers, which arrive here as ordinary strings. Missing
    # "unknown" put that literal word into `stores.city` for 19 branches, where
    # it read as a real city name AND suppressed the store-name fallback that
    # would have recovered the actual one — the city is usually right there
    # ("BE טייבה", "יש חסד בית וגן", "וולט מרקט | חדרה").
    if s.lower() in {"unknown", "לא ידוע", "none", "null", "n/a", "na"}:
        return None
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




# ── the full CBS gazetteer ───────────────────────────────────────────────────
#
# The hand-built map above covers the 55 cities that happened to appear in three
# chains' files. That is ~5% of Israel's localities, and it is why 540 of 1,860
# stores had no city: "BE דלית אל כרמל" and "אקספרס כפר נטר" carry the locality
# in the store NAME, but there was nothing to recognise it against.
#
# etl/localities.json is the Central Bureau of Statistics list from data.gov.il
# (1,310 localities, with their סמל יישוב codes). Regenerate with
# `python -m scripts.fetch_localities`.
_LOCALITIES_FILE = pathlib.Path(__file__).with_name("localities.json")


def _load_localities() -> dict[str, str]:
    try:
        data = json.loads(_LOCALITIES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}                       # never break an import over a data file
    return data.get("localities", {})


LOCALITY_CODE_TO_NAME: dict[str, str] = _load_localities()
# normalized name -> canonical name
_LOCALITY_INDEX: dict[str, str] = {}


def _norm_locality(value: str) -> str:
    """Fold the spelling differences between the feed and the CBS list.

    Quotes, hyphens and doubled spaces vary freely across chains
    ("דאלית אל-כרמל" vs "דלית אל כרמל"), and none of them carry meaning.
    """
    s = re.sub(r"[\"\'`\-–—]", " ", str(value or ""))
    return re.sub(r"\s+", " ", s).strip()


for _code, _name in LOCALITY_CODE_TO_NAME.items():
    _LOCALITY_INDEX.setdefault(_norm_locality(_name), _name)
for _variant, _canon in CITY_ALIASES.items():
    _LOCALITY_INDEX.setdefault(_norm_locality(_variant), _canon)

# Chain branding that sits in front of the locality in a store name.
_CHAIN_PREFIXES = (
    "אקספרס", "יש חסד", "יש בשכונה", "יש בשכונה", "שופרסל דיל", "שופרסל שלי",
    "שופרסל אונליין", "שופרסל", "נטו חיסכון", "וולט מרקט", "סיטי מרקט",
    "דור אלון", "סופר יודה", "טיב טעם", "יוחננוף", "קרפור", "דיל", "שלי",
    "BE", "Be", "be",
)


def locality_from_store_name(store_name) -> str | None:
    """Recover a locality from a store name — or None. Never a guess.

    STRICT ON PURPOSE. The obvious implementation looks for any gazetteer entry
    inside the name and takes the longest, and that silently invents cities:
    "אקספרס גבעת עדה" resolves to גבע and "BE דלית אל כרמל" to כרמל, because
    both fragments ARE real localities. A wrong city is worse than no city —
    it filters the user's basket to branches in the wrong town and looks
    perfectly fine while doing it.

    So a match must consume the WHOLE name once the chain branding is removed.
    That trades recall for precision, which is the right way round here: an
    unmatched store shows no city, and the comparison simply does not offer it
    under a town it is not in.
    """
    raw = _clean(store_name)
    if not raw:
        return None

    candidates = [raw]
    stripped = re.sub(r"^(אונליין|online)\s*[-|]\s*", "", raw, flags=re.IGNORECASE)
    for prefix in sorted(_CHAIN_PREFIXES, key=len, reverse=True):
        if stripped.startswith(prefix):
            candidates.append(stripped[len(prefix):].strip(" -|,.*"))
            break
    candidates.append(stripped)

    # 1. whole-string match, which is the safest signal there is.
    for cand in candidates:
        hit = _LOCALITY_INDEX.get(_norm_locality(cand))
        if hit:
            return hit

    # 2. SEGMENT match. Store names often append a neighbourhood or a branch
    #    marker after a separator — "שלי חיפה- חורב", "דיל קצרין- חרמון". Each
    #    segment is still matched WHOLE, so this widens what is examined without
    #    loosening what counts as a match.
    #
    #    Note what is deliberately NOT done: matching a bare token anywhere in
    #    the name. "דלית אל כרמל" contains the token "כרמל", which is its own
    #    locality, so token matching would confidently return the wrong town.
    #    Segments keep multi-word names intact, so that trap cannot spring.
    for cand in candidates:
        segments = [seg for seg in re.split(r"[-–—|,/*]+", cand) if seg.strip()]
        if len(segments) < 2:
            continue
        hits = [
            _LOCALITY_INDEX[key]
            for seg in segments
            if (key := _norm_locality(seg)) in _LOCALITY_INDEX
        ]
        # Exactly one segment may name a locality. Two would mean the name is
        # ambiguous ("רמלה - לוד"), and picking either is a coin toss.
        if len(hits) == 1:
            return hits[0]
    return None


def normalize_city(raw_city, store_name=None) -> str | None:
    """Return a canonical city name from the raw feed value (+ store name)."""
    cleaned = _clean(raw_city)
    if cleaned is None:
        return _from_store_name(store_name) or locality_from_store_name(store_name)

    if cleaned.isdigit():  # CBS locality code (Rami Levy / Osher Ad)
        code = str(int(cleaned))
        name = CITY_CODE_TO_NAME.get(code) or LOCALITY_CODE_TO_NAME.get(code)
        return name or _from_store_name(store_name) or locality_from_store_name(store_name)

    # Hebrew city name (Shufersal): map known variants, else keep cleaned name.
    return CITY_ALIASES.get(cleaned, cleaned)
