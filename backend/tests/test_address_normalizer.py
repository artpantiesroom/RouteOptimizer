"""Tests for the deterministic address normalizer.

Each rule from the slice-1.5 spec has at least one test here:
postcode stripping, unit extraction, street-term mapping, city detection.
"""

import pytest

from app.services.address_normalizer import (
    map_adjectives,
    KNOWN_CITIES,
    detect_city,
    normalize,
)


# --- 1. trailing postcode ---------------------------------------------------


def test_strips_trailing_five_digit_postcode():
    r = normalize("ул. Покровская, 8, Киев, 04070")
    assert r.postal_code == "04070"
    assert "04070" not in r.query


def test_keeps_house_number_that_is_not_five_digits():
    r = normalize("ул. Покровская, 8, Киев")
    assert r.postal_code is None
    assert r.house == "8"


def test_does_not_strip_five_digit_number_that_is_not_trailing():
    # A leading 5-digit token is part of the name, not a postcode.
    r = normalize("04070 Покровская, 8")
    assert r.postal_code is None


# --- 2. unit extraction -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,house,unit,kind",
    [
        ("Бульварно-Кудрявская, 15, кв. 9, Киев", "15", "9", "apartment"),
        ("Бульварно-Кудрявская, 15 кв 9, Киев", "15", "9", "apartment"),
        ("Покровская, 8, оф. 12, Киев", "8", "12", "office"),
        ("Покровская, 8, под. 3, Киев", "8", "3", "entrance"),
        ("Покровская, 8, эт. 2, Киев", "8", "2", "floor"),
    ],
)
def test_extracts_explicit_unit(raw, house, unit, kind):
    r = normalize(raw)
    assert r.house == house
    assert r.unit == unit
    assert r.unit_kind == kind
    assert r.unit_inferred is False


def test_extracts_ambiguous_dash_form_as_house_plus_apartment():
    r = normalize("ул. Бульварно-Кудрявская, 15-9, Киев, 02000")
    assert r.house == "15"
    assert r.unit == "9"
    assert r.unit_inferred is True, "15-9 is ambiguous; the UI must show the interpretation"
    assert "unit_inferred_from_dash" in r.applied_rules


def test_query_without_unit_drops_the_unit():
    r = normalize("ул. Бульварно-Кудрявская, 15-9, Киев, 02000")
    assert r.query_without_unit.endswith("15, Київ")
    assert "-9" not in r.query_without_unit


def test_query_without_unit_for_explicit_apartment():
    r = normalize("Покровская, 8, кв. 12, Киев")
    # "Покровская" is a proper name, not a generic term: it is deliberately
    # left alone (Nominatim matches the Russian adjective fine).
    assert r.query_without_unit == "Покровская, 8, Київ"


def test_keeps_full_query_with_unit():
    r = normalize("Покровская, 8, кв. 12, Киев")
    assert "12" in r.query


def test_slash_is_not_treated_as_unit():
    # "1/2" is a real house number in Ukraine, not house 1 + apartment 2.
    r = normalize("Хрещатик, 1/2, Київ")
    assert r.unit is None
    assert r.house == "1/2"


def test_letter_suffix_house_numbers_are_recognised():
    assert normalize("Смородинський узвіз, 17-Б, Київ").house == "17-Б"


# --- 3. Russian -> Ukrainian street terms -----------------------------------


@pytest.mark.parametrize(
    "raw,expected_term",
    [
        ("спуск", "узвіз"),
        ("переулок", "провулок"),
        ("площадь", "площа"),
        ("улица", "вулиця"),
        ("ул.", "вулиця"),
        ("проспект", "проспект"),
        ("просп.", "проспект"),
        ("пр-т", "проспект"),
        ("набережная", "набережна"),
        ("проезд", "проїзд"),
        ("шоссе", "шосе"),
        ("бульвар", "бульвар"),
    ],
)
def test_maps_each_generic_street_term(raw, expected_term):
    r = normalize(raw)
    assert expected_term in r.query


def test_maps_full_spusk_address():
    r = normalize("Смородинский спуск, 17, Киев")
    assert "узвіз" in r.query
    assert "спуск" not in r.query.lower()


def test_does_not_split_proper_name_containing_a_term():
    # "Бульварно-Кудрявська" starts with "бульвар" but is a proper name.
    r = normalize("вулиця Бульварно-Кудрявська, 15, Київ")
    assert "Бульварно-Кудрявська" in r.query


def test_original_is_never_modified():
    raw = "ул. Покровская, 8, Киев, 04070"
    r = normalize(raw)
    assert r.original == raw
    assert "Покровская" in r.original
    assert "Киев" in r.original


def test_structured_street_contains_street_and_house():
    r = normalize("ул. Покровская, 8, Киев, 04070")
    assert r.structured_street is not None
    assert "вулиця" in r.structured_street
    assert "8" in r.structured_street


def test_structured_street_uses_house_not_unit():
    r = normalize("ул. Бульварно-Кудрявская, 15-9, Киев, 02000")
    assert r.structured_street is not None
    assert r.structured_street.endswith(", 15")
    assert "9" not in r.structured_street


# --- 4. city detection ------------------------------------------------------


def test_detects_city_and_canonicalizes_to_ukrainian():
    r = normalize("ул. Покровская, 8, Киев")
    assert r.city == "Київ"
    assert "Київ" in r.query
    assert "Киев" not in r.query


def test_detects_city_in_ukrainian_spelling():
    assert normalize("вулиця Хрещатик, 1, Київ").city == "Київ"


def test_city_removed_from_structured_street():
    r = normalize("ул. Покровская, 8, Киев")
    assert "Київ" not in r.structured_street


def test_detect_city_helper():
    assert detect_city("ул. Покровская, 8, Киев").display == "Київ"
    assert detect_city("Хрещатик, 1, Львів").display == "Львів"
    assert detect_city("вулиця Хрещатик, 1, Київ").display == "Київ"
    assert detect_city("Somewhere in Nowhere") is None


def test_no_city_detected():
    r = normalize("Some Street 5")
    assert r.city is None


def test_city_not_confused_with_similar_word():
    # "Київ" must match as a whole word only.
    assert detect_city("Київського") is None


def test_every_city_profile_has_at_least_two_aliases():
    for profile in KNOWN_CITIES:
        assert len(profile.aliases) >= 2


# --- determinism -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "ул. Покровская, 8, Киев, 04070",
        "ул. Константиновская, 13, Киев",
        "Смородинский спуск, 17, Киев",
        "ул. Бульварно-Кудрявская, 15-9, Киев, 02000",
    ],
)
def test_normalization_is_deterministic(raw):
    first = normalize(raw)
    for _ in range(3):
        assert normalize(raw) == first


def test_reported_case_1_pokrovskaya():
    r = normalize("ул. Покровская, 8, Киев, 04070")
    assert r.postal_code == "04070"
    assert r.house == "8"
    assert r.city == "Київ"
    assert "вулиця" in r.query
    assert "04070" not in r.query


def test_reported_case_2_kostiantynivska():
    r = normalize("ул. Константиновская, 13, Киев")
    assert r.house == "13"
    assert r.city == "Київ"
    assert "вулиця" in r.query


def test_reported_case_3_smorodynsky_spusk():
    r = normalize("Смородинский спуск, 17, Киев")
    assert "узвіз" in r.query
    assert r.house == "17"
    assert r.city == "Київ"


def test_reported_case_4_budvar_kudryavska():
    r = normalize("ул. Бульварно-Кудрявская, 15-9, Киев, 02000")
    assert r.house == "15"
    assert r.unit == "9"
    assert r.unit_inferred is True
    assert r.postal_code == "02000"
    assert r.city == "Київ"


# --- edge cases ------------------------------------------------------------


def test_empty_input():
    r = normalize("")
    assert r.query == ""
    assert r.house is None
    assert r.city is None


def test_whitespace_only_input():
    r = normalize("   ")
    assert r.query == ""


def test_text_without_any_structure():
    r = normalize("Hello")
    assert r.query == "Hello"
    assert r.house is None


def test_collapses_internal_whitespace():
    r = normalize("ул.   Покровская,   8,  Киев")
    assert "  " not in r.query


def test_changing_flag_reflects_normalization():
    assert normalize("ул. Покровская, 8, Киев").changed is True
    assert normalize("Some Street 5").changed is False

# --- 5. adjective rewriting for the structured street field -----------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("вулиця Покровская, 8", "вулиця Покровська, 8"),
        ("Бульварно-Кудрявська, 15", "Бульварно-Кудрявська, 15"),
        ("Смородинский узвіз, 17", "Смородинський узвіз, 17"),
        ("Покровское, 1", "Покровське, 1"),
        ("Покровские, 1", "Покровські, 1"),
        ("Московский проспект, 1", "Московський проспект, 1"),
        ("московський проспект, 1", "московський проспект, 1"),
        # The mechanical form is wrong for this street - Nominatim spells it
        # "Костянтинівська" - so the structured attempt finds nothing and the
        # free-text attempt is what answers this address.
        ("вулиця Константиновская, 13", "вулиця Константиновська, 13"),
        ("Константиновское, 1", "Константиновське, 1"),
    ],
)
def test_adjective_rewriting(raw, expected):
    assert map_adjectives(raw) == expected


def test_adjectives_are_untouched_in_the_free_text_query():
    """Free text already matches Russian adjectives, so leave the query alone."""
    r = normalize("ул. Покровская, 8, Киев")
    assert r.query == "вулиця Покровская, 8, Київ"
    assert r.structured_street == "вулиця Покровська, 8"


def test_structured_street_uses_the_real_name_for_case_1():
    r = normalize("ул. Покровская, 8, Киев, 04070")
    assert r.structured_street == "вулиця Покровська, 8"


def test_structured_street_uses_the_real_name_for_case_4():
    r = normalize("ул. Бульварно-Кудрявская, 15-9, Киев, 02000")
    assert r.structured_street == "вулиця Бульварно-Кудрявська, 15"
