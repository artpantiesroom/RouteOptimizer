"""Deterministic, network-free normalization of raw address text.

Real-world input for this project is Russian-language text (messenger
copies, invoices, spreadsheets) that has to be matched against Ukrainian
OpenStreetMap data. This module rewrites the text so that a geocoder has a
chance of matching, without ever guessing silently: every change is recorded
in ``applied_rules`` and reported to the user as "Searched as: ...".

Nothing here touches the network and nothing here mutates the caller's text:
``original`` is always preserved verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

# --------------------------------------------------------------------------
# Data tables (keep in one place, not scattered through the code)
# --------------------------------------------------------------------------

# Russian generic street terms -> Ukrainian equivalents. Nominatim indexes
# Ukrainian names, so mapping the generic term is usually enough: it
# tolerates Russian adjectives ("Смородинский узвіз" matches
# "Смородинський узвіз"), so no adjective table is required.
STREET_TERMS: dict[str, str] = {
    "улица": "вулиця",
    "ул": "вулиця",
    "проспект": "проспект",
    "просп": "проспект",
    "пр-т": "проспект",
    "переулок": "провулок",
    "пер": "провулок",
    "площадь": "площа",
    "пл": "площа",
    "набережная": "набережна",
    "наб": "набережна",
    "проезд": "проїзд",
    "шоссе": "шосе",
    "ш": "шосе",
    "бульвар": "бульвар",
    "бул": "бульвар",
    "спуск": "узвіз",
}

# Unit / apartment markers. Values are for readability only.
UNIT_MARKERS: dict[str, str] = {
    "кв": "apartment",
    "оф": "office",
    "под": "entrance",
    "эт": "floor",
}

# Free-text search tolerates a Russian adjective in a street name
# ("вулиця Константиновская" finds "Костянтинівська"), but the structured
# street field does not. Verified against Nominatim: the structured field wants
# "вулиця Покровська" and "вулиця Бульварно-Кудрявська", and returns nothing
# useful for the Russian spellings of those two. "-ий" maps to "-ий", so an
# already-correct name such as "московський" is left untouched.
_ADJECTIVE_RE = re.compile(r"(?<=\w)ск(ий|ая|ое|ие|ую|их|им|ими)(?!\w)", re.IGNORECASE)
_ADJECTIVE_ENDINGS = {
    "ий": "ий",
    "ая": "а",
    "ое": "е",
    "ие": "і",
    "ую": "у",
    "их": "ьких",
    "им": "ьким",
    "ими": "ькими",
}


@dataclass(frozen=True)
class CityProfile:
    """A city we can scope a search to."""

    display: str
    aliases: frozenset[str]


def _city(display: str, *aliases: str) -> CityProfile:
    return CityProfile(display=display, aliases=frozenset(aliases))


# Cities recognised in input text, used to prefill the City field and to
# scope candidate validation. Aliases are matched case-insensitively after
# apostrophe/whitespace folding.
KNOWN_CITIES: Tuple[CityProfile, ...] = (
    _city("Київ", "київ", "киев", "кииев", "kyiv", "kiev"),
    _city("Львів", "львів", "львов", "lviv", "lviv"),
    _city("Одеса", "одеса", "одесса", "odesa", "odessa"),
    _city("Харків", "харків", "харьков", "kharkiv", "kharkov"),
    _city("Дніпро", "дніпро", "днепр", "dnipro", "dnipropetrovsk"),
    _city("Вінниця", "вінниця", "винница", "vinnytsia", "vinnitsa"),
    _city("Полтава", "полтава", "poltava"),
    _city("Івано-Франківськ", "івано-франківськ", "ивано-франковск", "ivano-frankivsk"),
    _city("Тернопіль", "тернопіль", "тернополь", "ternopil"),
    _city("Луцьк", "луцьк", "луцк", "lutsk"),
    _city("Рівне", "рівне", "ривне", "rivne"),
    _city("Ужгород", "ужгород", "uzhgorod"),
    _city("Чернівці", "чернівці", "черновицы", "chernivtsi", "czernowitz"),
    _city("Суми", "суми", "сумы", "sumy"),
    _city("Чернігів", "чернігів", "чернигов", "chernihiv"),
    _city("Житомир", "житомир", "zhytomyr"),
    _city("Кропивницький", "кропивницький", "кировоград", "kropyvnytskyi"),
    _city("Миколаїв", "миколаїв", "николаев", "mykolaiv"),
    _city("Севастополь", "севастополь", "sevastopol"),
    _city("Сімферополь", "сімферополь", "симферополь", "simferopol"),
    _city("Кам'янець-Подільський", "кам'янець-подільський", "каменец-подольский"),
    _city("Біла Церква", "біла церква", "белая церковь", "bila-tsyerkva"),
    _city("Бровари", "бровари", "brovary"),
    _city("Боярка", "боярка", "boyarka"),
    _city("Гостомель", "гостомель", "gostomel"),
    _city("Вишневе", "вишневе", "vyshneve"),
    _city("Бориспіль", "бориспіль", "бориспиль", "boryspil"),
    _city("Фастів", "фастів", "фастов", "fastiv"),
)


# --------------------------------------------------------------------------
# Regexes
# --------------------------------------------------------------------------

_APOSTROPHES = re.compile(r"['ʼ’‘`´]")
_WHITESPACE = re.compile(r"\s+")
# ", ," can appear when a span is cut out of the middle of a line.
_REPEATED_SEPARATOR = re.compile(r"(?:\s*,\s*){2,}")

# Ukrainian postcodes are exactly 5 digits. Only strip a *trailing* run so we
# never mangle a house number such as "8" or a street named "5".
_POSTCODE_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)\s*$")

# "кв. 9", "кв 9", "оф.12", "под 3", "эт. 2", also "кв. № 9"
_UNIT_RE = re.compile(
    r"(?<![\w-])(кв|оф|под|эт)\.?\s*(?:№\s*)?(\d+[а-яa-z]?)(?![\w-])",
    re.IGNORECASE,
)

# "15-9": house 15 + apartment 9. Genuinely ambiguous (could be a range or a
# corps), so the interpretation is surfaced to the user rather than hidden.
# Slash is deliberately NOT treated this way: "1/2" is a real house number in
# Ukraine, and Nominatim returns it as such.
_HOUSE_UNIT_RE = re.compile(r"(?<![\w/.-])(\d+)\s*[-–—]\s*(\d+[а-яa-z]?)(?![\w/-])")

# A standalone house number: 8, 12А, 17-Б, 1/2, 5А
_HOUSE_RE = re.compile(r"(?<![\w/.\-])(\d+[/\-]?\d*[а-яa-z]?)(?![\w/\-])", re.IGNORECASE)


def house_in_text(text: str) -> Optional[str]:
    """The house number a query spells, read back off the query itself.

    The fallback chain sends different numbers in different attempts: attempt 1
    sends the line as written ("51-53"), attempt 2 drops the unit ("51"). A
    candidate has to be judged against the number *that attempt* asked for, so
    the number is read from the outgoing query rather than from ``house``,
    which only holds the parsed first half of a range.

    Purely a reader: it applies no rule the parser does not already apply.
    """
    match = _HOUSE_RE.search(text)
    return match.group(1) if match else None


def _fold(text: str) -> str:
    """Casefold and normalize apostrophes/quotes for tolerant comparison."""
    return _APOSTROPHES.sub("'", text).casefold().strip()


# Latin and Cyrillic look-alikes are mapped to one representative character so
# "6A" and "6А" (Latin vs Cyrillic A) compare equal.
_LOOK_ALIKES = {
    "а": "а", "a": "а",
    "б": "б", "b": "б",
    "в": "в",
    "г": "г", "r": "г",
    "е": "е", "e": "е",
    "є": "є", "ё": "є",
    "з": "з", "3": "з",
    "и": "и", "i": "и",
    "ї": "ї",
    "к": "к", "k": "к",
    "м": "м", "m": "м",
    "н": "н", "h": "н",
    "о": "о", "o": "о",
    "п": "п", "n": "п",
    "р": "р", "p": "р",
    "с": "с", "c": "с",
    "т": "т", "t": "т",
    "у": "у", "y": "у",
    "х": "х", "x": "х",
    "ц": "ц",
    "ш": "ш", "w": "ш",
    "щ": "щ",
}
_LOOK_ALIKE_TABLE = str.maketrans(_LOOK_ALIKES)

# "/" (and any dash, by the time it gets here) right before a letter separates
# a letter suffix from the digits: "6-А" == "6А". Between digits it is part of
# the number and is kept: "1/2" != "12".
_LETTER_AFTER_SEPARATOR = re.compile(r"/(?=[^\W\d_])")


def house_numbers_equal(requested: Optional[str], found: Optional[str]) -> bool:
    """Whether a found house number is the one the user asked for.

    A match is required before a row can be called ``resolved``: answering
    "130/1" for a request of "1", or "40/5" for "40", is a wrong address even
    though the street is right.

    Tolerant of the differences that mean the same thing to a reader:

    * letter case, and Latin letters that look like Cyrillic ones;
    * surrounding whitespace, dots and dashes used as decoration
      ("6-А", "6 - А", "6. а");
    * a dash used instead of a slash in a range ("51-53" == "51/53").

    Strict about everything else, which is the point: "1" is not "130/1", and
    "40" is not "40/5".
    """
    if requested is None or found is None:
        return False

    def canonical(value: str) -> str:
        text = _fold(value).translate(_LOOK_ALIKE_TABLE)
        text = text.replace(".", " ")
        # Every dash and slash means one separator, so "51-53", "51–53" and
        # "51/53" collapse together.
        for dash in "-–—":
            text = text.replace(dash, "/")
        # Spaces around a separator are decoration: "6 - А".
        text = text.replace(" ", "")
        # A separator in front of a letter is decoration too: "6-А" == "6А".
        text = _LETTER_AFTER_SEPARATOR.sub("", text)
        return text.strip("/")

    return canonical(requested) == canonical(found)


def _clean(text: str) -> str:
    """Collapse whitespace, merge duplicated separators, trim the ends."""
    text = _WHITESPACE.sub(" ", text)
    text = _REPEATED_SEPARATOR.sub(", ", text)
    return text.strip(" ,;")


def _remove_span(text: str, start: int, end: int, eat_separator: bool = True) -> str:
    """Cut ``text[start:end]`` out of the text.

    With ``eat_separator`` the trailing separator is removed too; the leading
    one is used as a fallback. Without it, only the span itself is cut, which
    is what the "15-9" case needs: the comma that followed the unit becomes
    the separator after the house number.
    """
    if not eat_separator:
        return _clean(text[:start] + text[end:])
    tail = end
    while tail < len(text) and text[tail] in " ,;":
        tail += 1
    if tail == end:
        # Nothing after the span: take the leading separator instead.
        while start > 0 and text[start - 1] in " ,;":
            start -= 1
    return _clean(text[:start] + " " + text[tail:])


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedAddress:
    """Normalized view of one raw address line.

    ``original`` is never modified. ``query`` is what we ask the geocoder for
    first; ``query_without_unit`` is fallback #2; ``structured_street`` plus
    ``city`` is fallback #3.
    """

    original: str
    query: str
    query_without_unit: str
    structured_street: Optional[str]
    # The same street with the house number removed, for the last-resort
    # street-only lookup used when every candidate was out of scope.
    street_only: Optional[str]
    house: Optional[str]
    unit: Optional[str]
    unit_kind: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    # True when the unit was inferred from an ambiguous "15-9"-style form.
    unit_inferred: bool = False
    applied_rules: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return self.query != self.original.strip()


def detect_city(text: str) -> Optional[CityProfile]:
    """Return the first known city whose alias appears in ``text``."""
    folded = _fold(text)
    best: Optional[CityProfile] = None
    best_pos = -1
    for profile in KNOWN_CITIES:
        for alias in profile.aliases:
            for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", folded):
                if match.start() > best_pos:
                    best_pos = match.start()
                    best = profile
    return best


def map_adjectives(text: str) -> str:
    """Rewrite Russian adjectival endings into Ukrainian ones.

    Only used for the structured street field, where the exact street name is
    required. Free-text queries keep the original spelling.
    """
    return _ADJECTIVE_RE.sub(
        lambda m: "ськ" + _ADJECTIVE_ENDINGS[m.group(1).lower()],
        text,
    )


def city_profile_for(name: str) -> Optional[CityProfile]:
    """Resolve a user-typed city name to a known profile, if we have one."""
    folded = _fold(name)
    for profile in KNOWN_CITIES:
        if folded in {_fold(a) for a in profile.aliases}:
            return profile
        if folded == _fold(profile.display):
            return profile
    return None


def locality_names(address: Optional[dict]) -> list[str]:
    """Locality fields of a provider address, most specific first.

    ``state`` is deliberately excluded: the towns this app previously picked
    instead of Kyiv (Боярка, Гостомель, Бровари) all share the state
    "Київська область", so a state match proves nothing about the city.
    """
    if not isinstance(address, dict):
        return []
    names: list[str] = []
    for key in ("city", "town", "village", "municipality", "hamlet"):
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value)
    return names


def matches_city(address: Optional[dict], city: str) -> bool:
    """Whether a candidate belongs to ``city``.

    A candidate that reports no locality at all is kept: there is nothing to
    contradict the chosen city. A candidate that does report one must match.
    """
    profile = city_profile_for(city)
    wanted = {_fold(a) for a in profile.aliases} if profile else {_fold(city)}

    localities = locality_names(address)
    if not localities:
        return True
    return any(_fold(name) in wanted for name in localities)


def _map_street_terms(text: str) -> str:
    """Replace Russian generic street terms with Ukrainian equivalents.

    Longest terms are applied first so "проспект" is not shadowed by
    "просп". The lookarounds keep us from matching inside a proper name such
    as "Бульварно-Кудрявська", where "бульвар" is only a prefix.
    """
    result = text
    for term in sorted(STREET_TERMS, key=len, reverse=True):
        replacement = STREET_TERMS[term]
        pattern = re.compile(
            r"(?<![\w-])" + re.escape(term) + r"\.?(?![\w-])",
            re.IGNORECASE,
        )
        result = pattern.sub(replacement, result)
    return result


def normalize(raw: str) -> NormalizedAddress:
    """Normalize one address line. Pure, deterministic, no network."""
    original = raw
    text = _WHITESPACE.sub(" ", raw).strip()
    rules: list[str] = []

    # --- 1. trailing postcode -------------------------------------------------
    postal_code: Optional[str] = None
    match = _POSTCODE_RE.search(text)
    if match:
        postal_code = match.group(1)
        text = _clean(text[: match.start()])
        rules.append("postcode_removed")

    # --- 2. city --------------------------------------------------------------
    city_profile = detect_city(text)
    city: Optional[str] = None
    if city_profile is not None:
        city = city_profile.display
        city_re = re.compile(
            r"(?<!\w)("
            + "|".join(re.escape(a) for a in sorted(city_profile.aliases, key=len, reverse=True))
            + r")(?!\w)",
            re.IGNORECASE,
        )
        # Replace with the canonical Ukrainian spelling.
        text = _clean(city_re.sub(city_profile.display, text))
        rules.append("city_canonicalized")

    # --- 3. unit (apartment / office / entrance / floor) ----------------------
    unit: Optional[str] = None
    unit_kind: Optional[str] = None
    unit_inferred = False
    unit_span: Optional[tuple[int, int]] = None
    eat_separator = True

    unit_match = _UNIT_RE.search(text)
    if unit_match:
        unit = unit_match.group(2)
        unit_kind = UNIT_MARKERS[unit_match.group(1).lower()]
        unit_span = unit_match.span()
        rules.append(f"unit_extracted_{unit_kind}")

    if unit is None:
        # Ambiguous "15-9" form: read as house + apartment, and say so.
        hu = _HOUSE_UNIT_RE.search(text)
        if hu:
            unit = hu.group(2)
            unit_kind = UNIT_MARKERS["кв"]
            unit_inferred = True
            # Cut from the end of the house number to the end of the unit so
            # "15 - 9" loses its separator too. The comma that followed the
            # unit then becomes the separator after the house number.
            unit_span = (hu.end(1), hu.end(2))
            eat_separator = False
            rules.append("unit_inferred_from_dash")

    query_without_unit = text
    if unit_span is not None:
        query_without_unit = _remove_span(
            text, unit_span[0], unit_span[1], eat_separator=eat_separator
        )

    # --- 4. house number ------------------------------------------------------
    house: Optional[str] = None
    street_text = query_without_unit
    if city:
        # City was already canonicalized into `text`; remove it before looking
        # for a house number so "Київ" is not mistaken for one.
        city_only = re.compile(r"(?<!\w)" + re.escape(city) + r"(?!\w)", re.IGNORECASE)
        city_match = city_only.search(street_text)
        if city_match:
            street_text = _remove_span(street_text, city_match.start(), city_match.end())

    house_match = _HOUSE_RE.search(street_text)
    if house_match:
        house = house_match.group(1)
        street_text = _remove_span(street_text, house_match.start(1), house_match.end(1))
        rules.append("house_extracted")

    structured_street = _clean(street_text)
    street_only: Optional[str] = None
    if structured_street and house:
        # Kept before the house is appended, so the street can be looked up on
        # its own when the house cannot be found anywhere in the right city.
        street_only = structured_street
        structured_street = f"{structured_street}, {house}"
    if structured_street:
        # The structured query is sent to the geocoder as-is, so it must carry
        # the same Ukrainian terms as the free-text attempts and the real
        # street name.
        structured_street = _clean(_map_street_terms(structured_street))
        structured_street = _clean(map_adjectives(structured_street))
    else:
        structured_street = None

    # --- 5. Russian -> Ukrainian generic street terms -------------------------
    mapped = _clean(_map_street_terms(text))
    mapped_without_unit = _clean(_map_street_terms(query_without_unit))
    if mapped != text:
        rules.append("street_terms_ukrainian")

    return NormalizedAddress(
        original=original,
        query=mapped or text,
        query_without_unit=mapped_without_unit or query_without_unit,
        structured_street=structured_street,
        street_only=street_only,
        house=house,
        unit=unit,
        unit_kind=unit_kind,
        city=city,
        postal_code=postal_code,
        unit_inferred=unit_inferred,
        applied_rules=tuple(rules),
    )