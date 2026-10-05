"""Fallback chain, city scoping and reclassification rules (slice 1.5)."""

from typing import Any, Dict, List, Optional

import pytest

from app.domain.geocode import GeocodeCandidate, GeocodeResult, GeocodeStatus
from app.services.geocode_service import (
    APPROXIMATE_MATCH_MESSAGE,
    BatchGeocodeRequestItem,
    GeocodeService,
)

KYIV = "Київ"


def item(text: str, index: int = 0) -> BatchGeocodeRequestItem:
    return BatchGeocodeRequestItem(index=index, original=text, trimmed=text)


def candidate(
    name: str,
    *,
    house: Optional[str] = None,
    city: Optional[str] = KYIV,
    road: str = "Вулиця",
    lat: float = 50.45,
    lon: float = 30.52,
    importance: Optional[float] = None,
) -> GeocodeCandidate:
    address: Dict[str, Any] = {"road": road}
    if house:
        address["house_number"] = house
    if city:
        address["city"] = city
    return GeocodeCandidate(
        display_name=name, latitude=lat, longitude=lon, address=address, importance=importance
    )


class RecordingGeocoder:
    """Records every provider call so the fallback chain can be asserted."""

    def __init__(self, responses: Dict[str, GeocodeResult]) -> None:
        self.responses = responses
        self.text_calls: List[str] = []
        self.structured_calls: List[tuple[str, str]] = []

    async def geocode(self, address: str) -> GeocodeResult:
        self.text_calls.append(address)
        return self.responses.get(address, GeocodeResult(status=GeocodeStatus.NOT_FOUND))

    async def geocode_structured(self, street: str, city: str) -> GeocodeResult:
        self.structured_calls.append((street, city))
        return self.responses.get(
            f"structured:{street}|{city}", GeocodeResult(status=GeocodeStatus.NOT_FOUND)
        )


# --- fallback chain ---------------------------------------------------------


@pytest.mark.asyncio
async def test_stops_at_first_success():
    fake = RecordingGeocoder({"Хрещатик, 1, Київ": GeocodeResult(status=GeocodeStatus.RESOLVED)})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("Хрещатик, 1, Київ")], city=KYIV)
    assert results[0].status == "resolved"
    assert len(fake.text_calls) == 1, "no further attempts after a success"


@pytest.mark.asyncio
async def test_falls_back_to_query_without_unit():
    fake = RecordingGeocoder(
        {
            "вулиця Буд, 15-9, Київ": GeocodeResult(status=GeocodeStatus.NOT_FOUND),
            "вулиця Буд, 15, Київ": GeocodeResult(status=GeocodeStatus.RESOLVED),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert fake.text_calls == ["вулиця Буд, 15-9, Київ", "вулиця Буд, 15, Київ"]


@pytest.mark.asyncio
async def test_falls_back_to_structured_query():
    fake = RecordingGeocoder({"structured:вулиця Буд, 15|Київ": GeocodeResult(status=GeocodeStatus.RESOLVED)})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert fake.structured_calls == [("вулиця Буд, 15", KYIV)]


@pytest.mark.asyncio
async def test_never_exceeds_three_attempts():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert len(fake.text_calls) + len(fake.structured_calls) == 3


@pytest.mark.asyncio
async def test_single_address_without_unit_uses_fewer_attempts():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("Смородинский спуск, 17, Киев")], city=KYIV)
    # full query, then the structured form: no unit means no second text query
    assert len(fake.text_calls) == 1
    assert len(fake.structured_calls) == 1


@pytest.mark.asyncio
async def test_error_stops_the_chain_without_burning_attempts():
    fake = RecordingGeocoder(
        {"вулиця Буд, 15-9, Київ": GeocodeResult(status=GeocodeStatus.ERROR, error_message="rate limited")}
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].status == "error"
    assert len(fake.text_calls) == 1


@pytest.mark.asyncio
async def test_reports_the_query_it_actually_searched():
    fake = RecordingGeocoder({"вулиця Буд, 15, Київ": GeocodeResult(status=GeocodeStatus.RESOLVED)})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].searched_as == "вулиця Буд, 15, Київ"


@pytest.mark.asyncio
async def test_original_is_always_returned_untouched():
    raw = "ул. Покровская, 8, Киев, 04070"
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item(raw)], city=KYIV)
    assert results[0].original == raw


# --- unit reporting ---------------------------------------------------------


@pytest.mark.asyncio
async def test_reports_inferred_unit():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].unit == "9"
    assert results[0].unit_kind == "apartment"
    assert results[0].unit_inferred is True


@pytest.mark.asyncio
async def test_reports_explicit_unit_as_not_inferred():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15, кв. 9, Киев")], city=KYIV)
    assert results[0].unit == "9"
    assert results[0].unit_inferred is False


# --- city scoping -----------------------------------------------------------


@pytest.mark.asyncio
async def test_drops_candidates_outside_the_city():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("Kyiv", house="8"),
                    candidate("Boyarka", house="8", city="Боярка"),
                    candidate("Gostomel", house="8", city="Гостомель"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "Kyiv"
    assert results[0].dropped_candidates == 2


@pytest.mark.asyncio
async def test_all_candidates_outside_the_city_is_not_found_with_a_notice():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("Boyarka", house="8", city="Боярка"),
                    candidate("Gostomel", house="8", city="Гостомель"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "not_found"
    assert results[0].dropped_candidates == 2
    assert results[0].scope_message == "2 matches were ignored (outside Київ)."


@pytest.mark.asyncio
async def test_scope_notice_is_singular_for_one_dropped_match():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("Kyiv", house="8"),
                    candidate("Boyarka", house="8", city="Боярка"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].scope_message == "1 match was ignored (outside Київ)."


@pytest.mark.asyncio
async def test_no_scope_notice_when_nothing_was_dropped():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED, candidates=[candidate("Kyiv", house="8")]
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].scope_message is None


@pytest.mark.asyncio
async def test_house_number_is_reported_for_the_interpretation_line():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].house == "15"
    assert results[0].unit == "9"


@pytest.mark.asyncio
@pytest.mark.parametrize("locality_field", ["city", "town", "village", "municipality"])
async def test_any_locality_field_can_confirm_the_city(locality_field):
    """The city may be reported under any of these keys."""

    def with_locality(name: str, locality_field: str, value: str) -> GeocodeCandidate:
        return GeocodeCandidate(
            display_name=name,
            latitude=50.45,
            longitude=30.52,
            address={locality_field: value, "road": "Покровська"},
        )

    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    fake.responses["вулиця Покровская, 8, Київ"] = GeocodeResult(
        status=GeocodeStatus.RESOLVED,
        candidates=[
            GeocodeCandidate(
                display_name="in scope",
                latitude=50.45,
                longitude=30.52,
                address={locality_field: KYIV, "road": "Покровська", "house_number": "8"},
            ),
            with_locality("out of scope", locality_field, "Бровари"),
        ],
    )
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "in scope"
    assert results[0].dropped_candidates == 1


@pytest.mark.asyncio
async def test_a_village_in_another_region_is_dropped():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    fake.responses["вулиця Покровская, 8, Київ"] = GeocodeResult(
        status=GeocodeStatus.RESOLVED,
        candidates=[
            candidate("Velyka Dyminka", house="8", city="Велика Диминка"),
            candidate("Boyarka", house="8", city="Боярка"),
        ],
    )
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "not_found"
    assert results[0].dropped_candidates == 2


@pytest.mark.asyncio
async def test_state_alone_does_not_prove_a_city_match():
    """Боярка and Гостомель share Київська область, so state cannot be used."""
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("Боярка", house="8", city="Боярка")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "not_found"


@pytest.mark.asyncio
async def test_candidate_without_any_locality_is_kept():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("No locality", house="8", city=None)],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].dropped_candidates == 0


@pytest.mark.asyncio
async def test_no_city_means_no_filtering():
    fake = RecordingGeocoder(
        {
"вулиця Покровская, 8": GeocodeResult(
                    status=GeocodeStatus.RESOLVED,
                    candidates=[
                        candidate("A", house="8", city="Боярка", road="Вулиця А"),
                        candidate("B", house="8", road="Вулиця Б"),
                    ],
                )
            }
        )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8")])
    assert results[0].status == "ambiguous"
    assert results[0].dropped_candidates == 0


# --- reclassification -------------------------------------------------------


@pytest.mark.asyncio
async def test_same_street_without_house_numbers_becomes_one_partial():
    fake = RecordingGeocoder(
        {
            "вулиця Константиновская, 13, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("A", road="Константинівська", lat=50.44, lon=30.51, importance=0.4),
                    candidate("B", road="Константинівська", lat=50.45, lon=30.52, importance=0.6),
                    candidate("C", road="Константинівська", lat=50.46, lon=30.53, importance=0.5),
                    candidate("D", road="Константинівська", lat=50.47, lon=30.54, importance=0.3),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Константиновская, 13, Киев")], city=KYIV)
    assert results[0].status == "partial"
    assert len(results[0].candidates or []) == 1
    assert results[0].message == APPROXIMATE_MATCH_MESSAGE


@pytest.mark.asyncio
async def test_partial_picks_the_most_important_representative():
    fake = RecordingGeocoder(
        {
            "вулиця Константиновская, 13, Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL,
                candidates=[
                    candidate("low", road="С", lat=1.0, lon=1.0, importance=0.1),
                    candidate("high", road="С", lat=2.0, lon=2.0, importance=0.9),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Константиновская, 13, Киев")], city=KYIV)
    assert results[0].display_name == "high"
    assert results[0].coordinate == {"latitude": 2.0, "longitude": 2.0}


@pytest.mark.asyncio
async def test_ambiguous_only_when_several_house_level_candidates():
    fake = RecordingGeocoder(
        {
            "Хрещатик, 1, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("house 1", house="1"),
                    candidate("house 2", house="2"),
                    candidate("street", house=None, road="Хрещатик"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("Хрещатик, 1, Киев")], city=KYIV)
    assert results[0].status == "ambiguous"
    assert len(results[0].candidates or []) == 2
    assert all(c["address"].get("house_number") for c in results[0].candidates)


@pytest.mark.asyncio
async def test_single_house_level_candidate_resolves_despite_others():
    fake = RecordingGeocoder(
        {
            "Хрещатик, 1, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("house 1", house="1"),
                    candidate("street", house=None, road="Хрещатик"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("Хрещатик, 1, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "house 1"


@pytest.mark.asyncio
async def test_out_of_scope_candidates_cannot_make_it_ambiguous():
    fake = RecordingGeocoder(
        {
            "Хрещатик, 1, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("Kyiv", house="1"),
                    candidate("Boyarka", house="1", city="Боярка"),
                    candidate("Gostomel", house="1", city="Гостомель"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("Хрещатик, 1, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].dropped_candidates == 2


@pytest.mark.asyncio
async def test_scope_is_applied_before_ambiguity_for_partial_too():
    fake = RecordingGeocoder(
        {
            "вулиця Константиновская, 13, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("Kyiv road", road="Константинівська"),
                    candidate("Boyarka road", road="Константинівська", city="Боярка"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Константиновская, 13, Киев")], city=KYIV)
    assert results[0].status == "partial"
    assert results[0].display_name == "Kyiv road"
    assert results[0].dropped_candidates == 1


# --- caching ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_key_includes_the_city():
    fake = RecordingGeocoder({"вулиця Покровская, 8, Київ": GeocodeResult(status=GeocodeStatus.RESOLVED)})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city="Львів")
    assert len(fake.text_calls) == 2, "a different city must not reuse the cache"


@pytest.mark.asyncio
async def test_successful_result_is_cached_for_the_same_city():
    fake = RecordingGeocoder({"вулиця Покровская, 8, Київ": GeocodeResult(status=GeocodeStatus.RESOLVED)})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert len(fake.text_calls) == 1


@pytest.mark.asyncio
async def test_errors_are_not_cached():
    fake = RecordingGeocoder(
        {"вулиця Покровская, 8, Київ": GeocodeResult(status=GeocodeStatus.ERROR, error_message="boom")}
    )
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert len(fake.text_calls) == 2


@pytest.mark.asyncio
async def test_duplicates_in_one_batch_are_resolved_once():
    fake = RecordingGeocoder({"вулиця Покровская, 8, Київ": GeocodeResult(status=GeocodeStatus.RESOLVED)})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch(
        [item("ул. Покровская, 8, Киев", 0), item("ул. Покровская, 8, киев", 1)], city=KYIV
    )
    assert len(fake.text_calls) == 1
    assert [r.index for r in results] == [0, 1]


@pytest.mark.asyncio
async def test_batch_still_limited_to_five():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    with pytest.raises(ValueError):
        await svc.geocode_batch([item(f"line {i}", i) for i in range(6)], city=KYIV)


@pytest.mark.asyncio
async def test_structured_attempt_still_runs_after_everything_was_out_of_scope():
    """Free text can return only other-cities matches; structured still helps."""
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("Boyarka", house="8", city="Боярка")],
            ),
            "structured:вулиця Покровська, 8|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED, candidates=[candidate("Kyiv", house="8")]
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "Kyiv"
    assert fake.structured_calls == [("вулиця Покровська, 8", KYIV)]


@pytest.mark.asyncio
async def test_partial_is_kept_while_a_later_attempt_resolves():
    """An explicit house number means partial is not the end of the chain."""
    fake = RecordingGeocoder(
        {
            "вулиця Буд, 15-9, Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL, candidates=[candidate("street only")]
            ),
            "вулиця Буд, 15, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED, candidates=[candidate("house 15", house="15")]
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Буд, 15-9, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "house 15"


@pytest.mark.asyncio
async def test_partial_is_final_when_the_input_has_no_house_number():
    fake = RecordingGeocoder(
        {
            "вулиця Константиновская, Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL, candidates=[candidate("street only")]
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Константиновская, Киев")], city=KYIV)
    assert results[0].status == "partial"
    assert len(fake.text_calls) == 1, "no point retrying a street-only request"


@pytest.mark.asyncio
async def test_best_result_survives_when_later_attempts_are_worse():
    fake = RecordingGeocoder(
        {
            "вулиця Константиновская, 13, Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL, candidates=[candidate("street only")]
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Константиновская, 13, Киев")], city=KYIV)
    assert results[0].status == "partial"
    assert results[0].display_name == "street only"


@pytest.mark.asyncio
async def test_drop_notice_survives_when_no_attempt_resolves():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("Boyarka", house="8", city="Боярка")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "not_found"
    assert results[0].dropped_candidates == 1
    assert results[0].scope_message == "1 match was ignored (outside Київ)."


# --- reported cases ---------------------------------------------------------


@pytest.mark.asyncio
async def test_reported_case_1_pokrovskaya_resolves_inside_kyiv():
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("Покровська 8, Київ", house="8"),
                    candidate("Покровська 8, Боярка", house="8", city="Боярка"),
                    candidate("Покровська 8, Гостомель", house="8", city="Гостомель"),
                    candidate("Покровська 8, Бровари", house="8", city="Бровари"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев, 04070")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].dropped_candidates == 3


@pytest.mark.asyncio
async def test_reported_case_2_kostiantynivska_is_one_approximate_result():
    fake = RecordingGeocoder(
        {
            "вулиця Константиновская, 13, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("Константинівська", road="Константинівська", lat=50.4 + i / 100, lon=30.5)
                    for i in range(4)
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Константиновская, 13, Киев")], city=KYIV)
    assert results[0].status == "partial"
    assert results[0].message == "Street found, house not found. The pin is approximate."


@pytest.mark.asyncio
async def test_reported_case_3_spusk_is_searched_as_uzviz():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("Смородинский спуск, 17, Киев")], city=KYIV)
    assert fake.text_calls[0] == "Смородинский узвіз, 17, Київ"

# --- places at the same house are one location ------------------------------


@pytest.mark.asyncio
async def test_a_place_at_the_same_house_is_not_a_choice():
    """Nominatim lists the address and a POI there as separate rows."""
    fake = RecordingGeocoder(
        {
            "вулиця Покровская, 8, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("8, Покровська вулиця", house="8", road="Покровська вулиця",
                              importance=0.4),
                    candidate("Ліцей №100 «Поділ», 8, Покровська вулиця", house="8",
                              road="Покровська вулиця", importance=0.6),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("ул. Покровская, 8, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    # The more important row represents the location.
    assert results[0].display_name.startswith("Ліцей")


@pytest.mark.asyncio
async def test_different_houses_on_one_street_stay_ambiguous():
    fake = RecordingGeocoder(
        {
            "вулиця Лесі, 3, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("3", house="3", road="Лесі Ukrainian"),
                    candidate("5", house="5", road="Лесі Ukrainian"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Лесі, 3, Киев")], city=KYIV)
    assert results[0].status == "ambiguous"
    assert len(results[0].candidates) == 2


@pytest.mark.asyncio
async def test_collapsing_keeps_coordinates_in_sync_with_the_kept_row():
    """The coordinate must come from the row we kept, not the one we dropped."""
    fake = RecordingGeocoder(
        {
            "вулиця Буд, 15, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("house", house="15", road="Буд", lat=1.0, lon=2.0,
                              importance=0.9),
                    candidate("poi", house="15", road="Буд", lat=9.9, lon=8.8,
                              importance=0.1),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Буд, 15, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].coordinate["latitude"] == 1.0
    assert results[0].coordinate["longitude"] == 2.0


@pytest.mark.asyncio
async def test_searched_as_shows_the_city_for_a_structured_attempt():
    """The city is part of what was asked for, so the user should see it."""
    fake = RecordingGeocoder(
        {
            "вулиця Буряківська, 12, Київ": GeocodeResult(status=GeocodeStatus.NOT_FOUND),
            "structured:вулиця Буряківська, 12|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED, candidates=[candidate("Kyiv", house="12")]
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Буряківська, 12, Киев")], city=KYIV)
    assert results[0].searched_as == "вулиця Буряківська, 12, Київ"


@pytest.mark.asyncio
async def test_structured_attempt_uses_the_chosen_city_when_the_address_has_none():
    """A city from the City field is enough to build the structured lookup."""
    fake = RecordingGeocoder(
        {"structured:вулиця Буряківська, 12|Київ": GeocodeResult(
            status=GeocodeStatus.RESOLVED, candidates=[candidate("Kyiv", house="12")]
        )}
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Буряківська, 12")], city=KYIV)
    assert results[0].status == "resolved"
    assert fake.structured_calls == [("вулиця Буряківська, 12", KYIV)]
