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
import sys

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



# ── manually reviewed aliases for the high-frequency stragglers ─────────────
#
# Reviewed by hand from a frequency count of the branches still without a city.
# Several are NEIGHBOURHOODS, not localities, so they map to their parent city —
# a shopper filtering by עיר expects a Haifa branch under חיפה, not under הדר.
#
# One correction against what was proposed: יד אליהו maps to "תל אביב", not
# "תל אביב-יפו". That spelling is absent from the CBS list, and CITY_ALIASES
# already canonicalises all eleven Tel Aviv variants to "תל אביב", under which
# 114 stores are already filed. A second spelling would silently split the city
# filter in half. Conversely "סח'נין" and "יהוד-מונוסון" DO match CBS and heal
# splits that already existed in the data.



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

# ── hand-reviewed aliases, loaded from data ─────────────────────────────────
#
# etl/city_aliases.json rather than a literal here: the reviewed list runs to
# hundreds of entries, and a data file can be regenerated from a spreadsheet
# (see scripts/export_unmapped_cities.py) without anyone editing Python.
#
# Every target is VALIDATED against the gazetteer on load. That check is not
# ceremony: the comparison filter matches stores on the EXACT city string, so a
# single typo — or a plausible-looking variant like "תל אביב-יפו" when the
# canonical form is "תל אביב" — silently splits a city in half and half its
# branches stop appearing. Loud beats subtle.
_ALIASES_FILE = pathlib.Path(__file__).with_name("city_aliases.json")


def _load_reviewed_aliases() -> tuple[dict[str, str], set[str]]:
    try:
        data = json.loads(_ALIASES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, set()          # never break an import over a data file

    known = set(LOCALITY_CODE_TO_NAME.values()) | set(CITY_ALIASES.values())
    good: dict[str, str] = {}
    for source, target in (data.get("aliases") or {}).items():
        if target is None:
            continue
        if target not in known:
            print(
                f"  ! city_aliases.json: {source!r} -> {target!r} is not a known locality "
                f"— ignoring it rather than inventing a city",
                file=sys.stderr,
            )
            continue
        good[source] = target
    blocked = {str(x).strip().lower() for x in (data.get("not_a_city") or []) if x}
    return good, blocked


REVIEWED_ALIASES, NOT_A_CITY = _load_reviewed_aliases()
CITY_ALIASES.update(REVIEWED_ALIASES)

# The index was built from the gazetteer above, before these existed — fold them
# in, or a reviewed alias is loaded and then never consulted.
for _variant, _canon in REVIEWED_ALIASES.items():
    _LOCALITY_INDEX[_norm_locality(_variant)] = _canon


# Chain branding that sits in front of the locality in a store name.
_CHAIN_PREFIXES = (
    "אקספרס", "יש חסד", "יש בשכונה", "יש בשכונה", "שופרסל דיל", "שופרסל שלי",
    "שופרסל אונליין", "שופרסל", "נטו חיסכון", "וולט מרקט", "סיטי מרקט",
    "דור אלון", "סופר יודה", "טיב טעם", "יוחננוף", "קרפור", "דיל", "שלי",
    "BE", "Be", "be",
)


def _localities_in(value) -> set[str]:
    """Every locality named by a whole segment of `value`."""
    raw = _clean(value)
    if not raw:
        return set()
    # "קרני שומרון (יוש)" must still read as קרני שומרון, or the address cannot
    # contradict a wrong match.
    raw = re.sub(r"\([^)]*\)", " ", raw)
    out = set()
    for seg in re.split(r"[-–—|,/*]+", raw):
        hit = _LOCALITY_INDEX.get(_norm_locality(seg))
        if hit:
            out.add(hit)
    return out


# A locality name directly after one of these is naming a venue, not a town:
# "קניון אורות", "חוצות אלונים", "מתחם עין חצבה".
_VENUE_WORDS = ("קניון", "חוצות", "מתחם", "פנינת", "מרכז מסחרי", "מרכז", "סי סנטר", "פארק")


def _inside_a_venue_name(locality: str, *fields) -> bool:
    """True when `locality` appears immediately after a venue word."""
    pattern = "|".join(re.escape(w) for w in _VENUE_WORDS)
    for f in fields:
        t = _norm_locality(_clean(f) or "")
        if t and re.search(rf"(?:{pattern})\s+{re.escape(locality)}", t):
            return True
    return False


def _looks_like_a_street(locality: str, address) -> bool:
    """True when `locality` appears in the address as a STREET, not a town.

    "אלונים 1, קרית טבעון" and "פארן 7 רמת אשכול" both name a real locality that
    is, here, the road the branch stands on. A house number immediately after it
    is the tell.
    """
    addr = _clean(address)
    if not addr:
        return False
    return bool(re.search(rf"{re.escape(locality)}\s+\d", _norm_locality(addr)))


def locality_from_store_name(store_name, address=None) -> str | None:
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
        if _norm_locality(cand).lower() in NOT_A_CITY:
            return None
        hit = _LOCALITY_INDEX.get(_norm_locality(cand))
        if hit:
            return _corroborate(hit, store_name, address)

    # 2. SEGMENT match. Store names often append a neighbourhood or a branch
    #    marker after a separator — "שלי חיפה- חורב", "דיל קצרין- חרמון". Each
    #    segment is still matched WHOLE, so this widens what is examined without
    #    loosening what counts as a match.
    #
    #    Note what is deliberately NOT done: matching a bare token anywhere in
    #    the name. "דלית אל כרמל" contains the token "כרמל", which is its own
    #    locality, so token matching would confidently return the wrong town.
    #    Segments keep multi-word names intact, so that trap cannot spring.
    venue = "|".join(re.escape(w) for w in _VENUE_WORDS)
    for cand in candidates:
        segments = [
            # A venue word glued to the front of a segment hides the locality:
            # "מתחם עין - חצבה" is עין חצבה, not חצבה.
            re.sub(rf"^(?:{venue})\s+", "", seg.strip())
            for seg in re.split(r"[-–—|,/*]+", cand)
            if seg.strip()
        ]
        if len(segments) < 2:
            continue

        # Contiguous RUNS of segments, longest first. Separators split names
        # that belong together ("מתחם עין - חצבה" → עין חצבה), so rejoining is
        # what recovers them — and taking the longest match means the rejoined
        # name wins over a fragment of itself.
        #
        # Runs are built only across SEPARATORS, never by splitting on spaces.
        # That is the line that keeps "דלית אל כרמל" from resolving to כרמל.
        runs = [
            " ".join(segments[i:j])
            for length in range(len(segments), 0, -1)
            for i in range(len(segments) - length + 1)
            for j in (i + length,)
        ]
        hits = [
            _LOCALITY_INDEX[key] for run in runs if (key := _norm_locality(run)) in _LOCALITY_INDEX
        ]
        if not hits:
            continue
        # `runs` is ordered longest-first, so hits[0] is the most specific match.
        # Reject only when two DIFFERENT localities match at the same length —
        # "רמלה - לוד" is a genuine coin toss.
        longest = [h for h in hits if len(h) == len(hits[0])]
        if len(set(longest)) == 1:
            return _corroborate(hits[0], store_name, address)
    return None


def _corroborate(candidate: str, store_name, address) -> str | None:
    """Keep the match only if the address does not contradict it.

    The audit of 171 matches found ~7 wrong, all one shape: the matched segment
    was a STREET, MALL or NEIGHBOURHOOD that happens to share a locality's name.
    "BE אלונים- טבעון" resolved to the kibbutz Alonim while its address reads
    "אלונים 1, קרית טבעון"; "יש מרים ירושלים- פארן" resolved to Paran, a moshav
    in the Arava, from a Jerusalem street.

    The address is independent evidence, so it gets a veto — it cannot promote a
    match, only reject one. A store whose two sources disagree is exactly the
    case where guessing is worse than NULL.
    """
    if _looks_like_a_street(candidate, address):
        return None
    if _inside_a_venue_name(candidate, store_name, address):
        return None
    in_address = _localities_in(address)
    if in_address and candidate not in in_address:
        return None                     # the two sources name different towns
    return candidate


def normalize_city(raw_city, store_name=None, address=None) -> str | None:
    """Return a canonical city name from the raw feed value (+ store name)."""
    cleaned = _clean(raw_city)
    if cleaned is None:
        return _from_store_name(store_name) or locality_from_store_name(store_name, address)

    if cleaned.isdigit():  # CBS locality code (Rami Levy / Osher Ad)
        code = str(int(cleaned))
        name = CITY_CODE_TO_NAME.get(code) or LOCALITY_CODE_TO_NAME.get(code)
        return name or _from_store_name(store_name) or locality_from_store_name(store_name, address)

    # Hebrew city name (Shufersal): map known variants, else keep cleaned name.
    return CITY_ALIASES.get(cleaned, cleaned)
