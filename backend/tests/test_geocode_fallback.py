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
    """Two candidates both equal to the asked house is a genuine choice."""
    fake = RecordingGeocoder(
        {
            "Хрещатик, 1, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("house 1 side wing", house="1", road="Хрещатик", lat=50.44),
                    candidate("house 1 rear", house="1", road="Провулок Хрещатицький", lat=50.45),
                    candidate("house 2", house="2", road="Хрещатик"),
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
async def test_exact_house_match_wins_over_a_nearby_other_house():
    """House 1 asked, house 1 found: that resolves even if house 2 also matched."""
    fake = RecordingGeocoder(
        {
            "Хрещатик, 1, Київ": GeocodeResult(
                status=GeocodeStatus.AMBIGUOUS,
                candidates=[
                    candidate("house 1", house="1", road="Хрещатик"),
                    candidate("house 2", house="2", road="Хрещатик"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("Хрещатик, 1, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "house 1"


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
async def test_exact_house_wins_when_a_different_house_also_matched():
    """Asking for house 3 and finding it resolves, whatever else also matched."""
    fake = RecordingGeocoder(
        {
            "вулиця Лесі, 3, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("3", house="3", road="Лесі"),
                    candidate("5", house="5", road="Лесі"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Лесі, 3, Киев")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].display_name == "3"
    assert results[0].found_house == "3"
    assert results[0].needs_check is False


@pytest.mark.asyncio
async def test_a_street_only_address_with_two_houses_stays_ambiguous():
    """With no house number in the input there is nothing to prefer."""
    fake = RecordingGeocoder(
        {
            "вулиця Лесі, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("3", house="3", road="Лесі"),
                    candidate("5", house="5", road="Лесі"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Лесі, Киев")], city=KYIV)
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


# --- slice 1.6: precision of "resolved" -------------------------------------
# Fixtures are invented addresses. The two wrong-town cases from the report are
# modelled with invented street names but the real city names that were found.


@pytest.mark.asyncio
async def test_city_in_the_address_scopes_even_without_the_city_field():
    """The Yahotyn case: "Київ" was in the line but never used as a scope."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 86, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("wrong town", house="86", city="Яготин")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=None)
    assert results[0].status == "not_found"
    assert results[0].dropped_candidates == 1
    assert "outside Київ" in results[0].scope_message


@pytest.mark.asyncio
async def test_address_city_is_used_as_the_structured_scope():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=None)
    assert fake.structured_calls == [("вулиця Тестова, 86", KYIV)]


@pytest.mark.asyncio
async def test_explicit_city_wins_over_the_city_in_the_address():
    """If the user picked a city, that is the scope, not the text.

    The free-text call still sends the line as written, so its Kyiv candidates
    are dropped as out of scope; the structured call is scoped to Lviv and wins.
    """
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 5, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("kyiv side", house="5", city="Київ")],
            ),
            "structured:вулиця Тестова, 5|Львів": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("lviv side", house="5", city="Львів")],
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 5, Київ")], city="Львів")
    assert results[0].status == "resolved"
    assert results[0].found_city == "Львів"
    assert ("вулиця Тестова, 5", "Львів") in fake.structured_calls


@pytest.mark.asyncio
async def test_house_130_slash_1_is_not_the_asked_house_1():
    """Asked 1, provider answered 130/1: not the same house."""
    fake = RecordingGeocoder(
        {
            "проспект Тестовий, 1, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("130/1", house="130/1")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("проспект Тестовий, 1, Київ")], city=KYIV)
    assert results[0].status == "ambiguous"
    assert results[0].house == "1"
    assert results[0].found_house == "130/1"


@pytest.mark.asyncio
async def test_house_40_slash_5_is_not_the_asked_house_40():
    """Asked 40, provider answered 40/5: not the same house."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 40, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("40/5", house="40/5")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 40, Київ")], city=KYIV)
    assert results[0].status == "ambiguous"
    assert results[0].house == "40"
    assert results[0].found_house == "40/5"


@pytest.mark.asyncio
async def test_mismatched_house_keeps_trying_the_structured_form():
    """A house mismatch is not a final answer: another spelling may match."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 40, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("40/5", house="40/5")],
            ),
            "structured:вулиця Тестова, 40|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("40", house="40")],
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 40, Київ")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].found_house == "40"


@pytest.mark.asyncio
async def test_house_match_accepts_a_cyrillic_letter_spelling():
    """Nominatim writes "6А", the user writes "6а". Same house."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 6а, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("6А", house="6А")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 6а, Київ")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].found_house == "6А"


@pytest.mark.asyncio
async def test_house_match_accepts_a_slash_range():
    """A range house reaches the classifier as "51/53" and matches "51/53".

    The pure function also treats "51-53" as equal (see test_address_normalizer),
    but normalize() reads a dash as the house-unit separator ("15-9"), so a dash
    range cannot survive into the pipeline.
    """
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 51/53, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("51/53", house="51/53")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 51/53, Київ")], city=KYIV)
    assert results[0].status == "resolved"


@pytest.mark.asyncio
async def test_house_mismatch_without_an_exact_match_is_not_partial_resolved():
    """Never quietly promote a wrong house to a confident answer."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 12, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("12А", house="12А")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 12, Київ")], city=KYIV)
    assert results[0].status != "resolved"
    assert results[0].status == "ambiguous"
    assert results[0].found_house == "12А"


# --- list-level checks ------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_row_reports_the_city_it_was_found_in():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 5, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("good", house="5", city=KYIV)],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 5, Київ")], city=KYIV)
    assert results[0].found_city == KYIV
    assert results[0].needs_check is False


@pytest.mark.asyncio
async def test_out_of_city_row_is_flagged_without_a_pointless_retry():
    """A row resolved in another town needs a check, but a retry cannot help.

    The street-only fallback already ran inside the active city and found no
    such street, so offering "search again in Київ" would be a dead end.
    """
    rows = [f"вулиця Тестова, {n}, Київ" for n in range(1, 5)]
    responses = {}
    for n in range(1, 5):
        responses[f"вулиця Тестова, {n}, Київ"] = GeocodeResult(
            status=GeocodeStatus.RESOLVED,
            candidates=[candidate(f"kyiv {n}", house=str(n), city=KYIV)],
        )
    odd = "вулиця Інша, 9, Київ"
    responses[odd] = GeocodeResult(
        status=GeocodeStatus.RESOLVED,
        candidates=[candidate("odd town", house="9", city="Полтава")],
    )
    fake = RecordingGeocoder(responses)
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item(t) for t in rows + [odd]], city=KYIV)
    flagged = [r for r in results if r.needs_check]
    assert len(flagged) == 1
    assert flagged[0].original == odd
    assert "Полтава" in flagged[0].needs_check_reason
    assert flagged[0].retry_city is None, "a retry in the same city cannot succeed"
    assert "No such address found in Київ" in flagged[0].message
    assert svc.city_suggestion is None, "an explicit city means no suggestion"


@pytest.mark.asyncio
async def test_dominant_city_is_inferred_when_no_city_was_given():
    rows = [f"вулиця Тестова, {n}, Київ" for n in range(1, 6)]
    responses = {}
    for n in range(1, 6):
        responses[f"вулиця Тестова, {n}, Київ"] = GeocodeResult(
            status=GeocodeStatus.RESOLVED,
            candidates=[candidate(f"kyiv {n}", house=str(n), city=KYIV)],
        )
    fake = RecordingGeocoder(responses)
    svc = GeocodeService(fake)
    await svc.geocode_batch([item(t) for t in rows], city=None)
    suggestion = svc.city_suggestion
    assert suggestion is not None
    assert suggestion.city == KYIV
    assert suggestion.resolved_count == 5
    assert suggestion.share == 1.0


@pytest.mark.asyncio
async def test_no_city_suggestion_when_the_rows_disagree():
    """One Poltava row in five is not a majority worth suggesting."""
    rows = [f"вулиця Тестова, {n}, Київ" for n in range(1, 5)]
    responses = {}
    for n in range(1, 5):
        responses[f"вулиця Тестова, {n}, Київ"] = GeocodeResult(
            status=GeocodeStatus.RESOLVED,
            candidates=[candidate(f"kyiv {n}", house=str(n), city=KYIV)],
        )
    odd = "вулиця Інша, 9"
    responses[odd] = GeocodeResult(
        status=GeocodeStatus.RESOLVED,
        candidates=[candidate("odd town", house="9", city="Полтава")],
    )
    fake = RecordingGeocoder(responses)
    svc = GeocodeService(fake)
    await svc.geocode_batch([item(t) for t in rows + [odd]], city=None)
    assert svc.city_suggestion is None


@pytest.mark.asyncio
async def test_outlier_row_is_flagged_by_distance():
    """One row far from the cluster is flagged even in the right city."""
    rows = ["вулиця Тестова, 1, Київ", "вулиця Тестова, 2, Київ", "вулиця Тестова, 3, Київ"]
    near = [(50.4501, 30.5234), (50.4510, 30.5240), (50.4505, 30.5230)]
    responses = {}
    for text, (lat, lon) in zip(rows, near):
        responses[text] = GeocodeResult(
            status=GeocodeStatus.RESOLVED,
            candidates=[
                GeocodeCandidate(
                    display_name=text, latitude=lat, longitude=lon,
                    address={"house_number": text.split(", ")[1], "city": KYIV},
                    importance=1.0,
                )
            ],
        )
    far = "вулиця Далека, 4, Київ"
    responses[far] = GeocodeResult(
        status=GeocodeStatus.RESOLVED,
        candidates=[
            GeocodeCandidate(
                display_name=far, latitude=49.5887, longitude=34.5113,
                address={"house_number": "4", "city": KYIV}, importance=1.0,
            )
        ],
    )
    fake = RecordingGeocoder(responses)
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item(t) for t in rows + [far]], city=KYIV)
    flagged = [r for r in results if r.needs_check]
    assert len(flagged) == 1
    assert flagged[0].original == far
    # The reason states how far off it is, so the user can judge it.
    assert "about 301 km" in flagged[0].needs_check_reason
    assert "limit 50 km" in flagged[0].needs_check_reason


@pytest.mark.asyncio
async def test_outlier_threshold_is_configurable_on_the_service():
    rows = ["вулиця Тестова, 1, Київ", "вулиця Тестова, 2, Київ", "вулиця Тестова, 3, Київ"]
    near = [(50.4501, 30.5234), (50.4510, 30.5240), (50.4505, 30.5230)]
    responses = {}
    for text, (lat, lon) in zip(rows, near):
        responses[text] = GeocodeResult(
            status=GeocodeStatus.RESOLVED,
            candidates=[
                GeocodeCandidate(
                    display_name=text, latitude=lat, longitude=lon,
                    address={"house_number": text.split(", ")[1], "city": KYIV},
                    importance=1.0,
                )
            ],
        )
    # ~13 km from the cluster.
    mid = "вулиця Середня, 4, Київ"
    responses[mid] = GeocodeResult(
        status=GeocodeStatus.RESOLVED,
        candidates=[
            GeocodeCandidate(
                display_name=mid, latitude=50.55, longitude=30.62,
                address={"house_number": "4", "city": KYIV}, importance=1.0,
            )
        ],
    )
    items = [item(t) for t in rows + [mid]]

    strict = GeocodeService(RecordingGeocoder(responses), max_outlier_km=10.0)
    assert any(r.needs_check for r in await strict.geocode_batch(items, city=KYIV))

    relaxed = GeocodeService(RecordingGeocoder(responses), max_outlier_km=50.0)
    assert not any(r.needs_check for r in await relaxed.geocode_batch(items, city=KYIV))


# --- trace ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_records_every_fallback_attempt():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 40, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("40/5", house="40/5")],
            )
        }
    )
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("вулиця Тестова, 40, Київ")], city=KYIV)
    steps = svc.trace_as_dicts()
    assert [s["kind"] for s in steps] == ["text", "structured"]
    assert steps[0]["query"] == "вулиця Тестова, 40, Київ"
    assert steps[1]["request_city"] == KYIV
    assert all(s["scope_city"] == KYIV for s in steps)


@pytest.mark.asyncio
async def test_trace_explains_a_house_rejection():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 1, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("130/1", house="130/1")],
            )
        }
    )
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("вулиця Тестова, 1, Київ")], city=KYIV)
    first = svc.trace_as_dicts()[0]
    assert first["asked_house"] == "1"
    assert first["candidates"][0]["house_number"] == "130/1"
    assert "house mismatch: requested 1, found 130/1" in first["notes"]


@pytest.mark.asyncio
async def test_trace_explains_an_out_of_scope_rejection():
    """The Yahotyn case: the candidate is named in the trace as rejected."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 86, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("wrong town", house="86", city="Яготин")],
            )
        }
    )
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=None)
    first = svc.trace_as_dicts()[0]
    assert first["scope_city"] == KYIV, "the city in the address is the scope"
    assert any("rejected" in note and "Яготин" in note for note in first["notes"])


@pytest.mark.asyncio
async def test_trace_marks_a_cached_row_instead_of_a_request():
    fake = RecordingGeocoder(
        {
            "Хрещатик, 1, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED, candidates=[candidate("ok", house="1")]
            )
        }
    )
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("Хрещатик, 1, Київ")], city=KYIV)
    calls = len(fake.text_calls)
    svc.reset()
    await svc.geocode_batch([item("Хрещатик, 1, Київ")], city=KYIV)
    steps = svc.trace_as_dicts()
    assert len(fake.text_calls) == calls, "the second batch used the cache"
    assert len(steps) == 1
    assert steps[0]["kind"] == "cache"
    assert steps[0]["from_cache"] is True


@pytest.mark.asyncio
async def test_trace_is_empty_unless_asked_for():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    await svc.geocode_batch([item("вулиця Тестова, 1, Київ")], city=KYIV)
    assert svc.trace == []


@pytest.mark.asyncio
async def test_trace_starts_fresh_for_each_batch():
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("вулиця Тестова, 1, Київ")], city=KYIV)
    first = len(svc.trace)
    assert first > 0
    await svc.geocode_batch([item("вулиця Інша, 2, Київ")], city=KYIV)
    assert all(step.original == "вулиця Інша, 2, Київ" for step in svc.trace)
    assert len(svc.trace) <= first, "the trace covers one batch, not all of them"


# --- per-attempt house validation -------------------------------------------


@pytest.mark.asyncio
async def test_a_dash_range_is_judged_as_the_range_the_first_attempt_sent():
    """Attempt 1 sends "51-53" literally, so "51/53" is the answer it wanted.

    The normalizer still parses "51-53" as house 51 + unit 53 and sends "51" as
    a later fallback, but the number a candidate is judged against is the one
    the attempt that found it actually asked for.
    """
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 51-53, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("range", house="51/53")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 51-53, Київ")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].found_house == "51/53"
    assert results[0].unit == "53", "the unit is still reported to the user"
    assert results[0].unit_inferred is True


@pytest.mark.asyncio
async def test_a_literal_dash_form_matches_the_slash_the_provider_returns():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 15-9, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("flat", house="15/9")],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 15-9, Київ")], city=KYIV)
    assert results[0].status == "resolved"
    assert results[0].found_house == "15/9"


@pytest.mark.asyncio
async def test_a_candidate_matching_only_the_second_attempt_does_not_satisfy_the_first():
    """A candidate for "51" must not answer a first attempt that asked "51-53"."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 51-53, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("single", house="51")],
            ),
            "вулиця Тестова, 51, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("single", house="51")],
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 51-53, Київ")], city=KYIV)
    # The first attempt cannot claim "51", so it falls through to the second,
    # which did ask for "51" and therefore may.
    assert results[0].status == "resolved"
    assert results[0].searched_as == "вулиця Тестова, 51, Київ"
    assert fake.text_calls == ["вулиця Тестова, 51-53, Київ", "вулиця Тестова, 51, Київ"]


@pytest.mark.asyncio
async def test_each_attempt_is_traced_with_the_number_it_sent():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 51-53, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("range", house="51/53")],
            )
        }
    )
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("вулиця Тестова, 51-53, Київ")], city=KYIV)
    asked = [step["asked_house"] for step in svc.trace_as_dicts()]
    assert asked == ["51-53"]


# --- a single mismatched candidate ------------------------------------------


@pytest.mark.asyncio
async def test_a_range_holding_the_requested_number_offers_one_candidate_to_confirm():
    """Asked 30, OSM has 28-30: strict, but one tap to accept it."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 30, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[
                    candidate("range", house="28-30"),
                    candidate("lettered", house="30-А"),
                ],
            )
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 30, Київ")], city=KYIV)
    row = results[0]
    assert row.status == "ambiguous"
    assert row.house == "30"
    assert row.found_house == "28-30"
    assert row.message == "Found 28-30, you asked for 30."
    assert len(row.candidates) == 1, "one wrong address is not a choice"


@pytest.mark.asyncio
async def test_a_mismatch_is_watched_over_the_rest_of_the_chain():
    """The chain keeps going, and only reports the mismatch if nothing fits."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 40, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other", house="40/5")],
            ),
            "structured:вулиця Тестова, 40|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other", house="40/5")],
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 40, Київ")], city=KYIV)
    assert results[0].status == "ambiguous"
    assert results[0].message == "Found 40/5, you asked for 40."


# --- street-only last resort -------------------------------------------------


@pytest.mark.asyncio
async def test_a_street_that_exists_gives_an_approximate_pin_instead_of_a_dead_retry():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 86, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="86", city="Полтава")],
            ),
            "structured:вулиця Тестова, 86|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="86", city="Полтава")],
            ),
            "structured:вулиця Тестова|Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL,
                candidates=[candidate("the street", house=None)],
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=KYIV)
    row = results[0]
    assert row.status == "partial"
    assert row.coordinate is not None, "an approximate pin is still useful"
    assert row.needs_check is True
    assert "Street found, house not found" in row.message
    assert "not the house 86" in row.message
    assert row.retry_city is None, "retrying the same city cannot do better"
    assert ("вулиця Тестова", KYIV) in fake.structured_calls


@pytest.mark.asyncio
async def test_a_street_absent_from_the_city_says_so_and_offers_no_retry():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 86, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="86", city="Полтава")],
            ),
            "structured:вулиця Тестова, 86|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="86", city="Полтава")],
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=KYIV)
    row = results[0]
    assert row.status == "not_found"
    assert row.message == "No such address found in Київ in the map data"
    assert row.retry_city is None
    assert row.needs_check is True
    # The drop notice survives, so the user can see where the matches were.
    assert row.dropped_candidates == 1
    assert "Полтава" in row.needs_check_reason


@pytest.mark.asyncio
async def test_the_street_only_lookup_is_skipped_when_nothing_was_out_of_scope():
    """A plain miss must not spend a request on a street-level lookup."""
    fake = RecordingGeocoder({})
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=KYIV)
    assert results[0].status == "not_found"
    assert ("вулиця Тестова", KYIV) not in fake.structured_calls


@pytest.mark.asyncio
async def test_the_street_only_lookup_is_traced():
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 86, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="86", city="Полтава")],
            ),
            "structured:вулиця Тестова|Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL, candidates=[candidate("the street")]
            ),
        }
    )
    svc = GeocodeService(fake, trace=True)
    await svc.geocode_batch([item("вулиця Тестова, 86, Київ")], city=KYIV)
    last = svc.trace_as_dicts()[-1]
    assert last["kind"] == "street-only"
    assert last["structured_street"] == "вулиця Тестова"
    assert last["asked_house"] is None


@pytest.mark.asyncio
async def test_a_later_in_scope_answer_is_not_replaced_by_the_street_only_check():
    """An out-of-scope first attempt must not cost us a good in-scope answer."""
    fake = RecordingGeocoder(
        {
            "вулиця Тестова, 9, Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="9", city="Полтава")],
            ),
            "structured:вулиця Тестова, 9|Київ": GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                candidates=[candidate("other town", house="9", city="Полтава")],
            ),
            "structured:вулиця Тестова|Київ": GeocodeResult(
                status=GeocodeStatus.PARTIAL, candidates=[candidate("the street")]
            ),
        }
    )
    svc = GeocodeService(fake)
    results = await svc.geocode_batch([item("вулиця Тестова, 9, Київ")], city=KYIV)
    assert results[0].status == "partial"
    # The pin kept is the one the city agreed with, not the street-only probe.
    assert results[0].found_city == KYIV
