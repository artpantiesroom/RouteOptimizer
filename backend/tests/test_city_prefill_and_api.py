"""City prefill and the HTTP surface for slice 1.5.

The geocode endpoint tests run against a stubbed service so the suite never
touches the live address search service.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.geocode import get_geocode_service
from app.domain.geocode import GeocodeResult, GeocodeStatus
from app.main import app
from app.services.geocode_service import BatchGeocodeResponseItem
from app.services.parse_service import ParseService


class StubService:
    """Records the city it was handed and returns a fixed result."""

    def __init__(self) -> None:
        self.seen_city: str | None = None

    async def geocode_batch(self, items, city=None):
        self.seen_city = city
        return [
            BatchGeocodeResponseItem(
                index=item.index,
                original=item.original,
                status=GeocodeStatus.NOT_FOUND.value,
                searched_as=item.trimmed,
                unit="9",
                unit_kind="apartment",
                unit_inferred=True,
                dropped_candidates=2,
            )
            for item in items
        ]


@pytest.fixture
def stub_service():
    stub = StubService()
    app.dependency_overrides[get_geocode_service] = lambda: stub
    yield stub
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


# --- parse-time city suggestion ---------------------------------------------


def test_suggests_city_from_the_first_address():
    parsed = ParseService().parse("ул. Покровская, 8, Киев\nул. Городоцька, 5, Львів")
    assert parsed.suggested_city == "Київ"


def test_suggestion_uses_the_first_recognisable_line():
    parsed = ParseService().parse("Somewhere without a city\nХрещатик, 1, Київ")
    assert parsed.suggested_city == "Київ"


def test_suggestion_is_none_without_a_known_city():
    assert ParseService().parse("Some Street 5").suggested_city is None


def test_suggestion_canonicalises_russian_spelling():
    assert ParseService().parse("Смородинский спуск, 17, Киев").suggested_city == "Київ"


def test_blank_lines_are_skipped():
    parsed = ParseService().parse("\n\nХрещатик, 1, Львів")
    assert parsed.suggested_city == "Львів"


def test_parse_endpoint_returns_suggestion(client):
    resp = client.post("/api/parse", json={"text": "ул. Покровская, 8, Киев"})
    assert resp.status_code == 200
    assert resp.json()["suggested_city"] == "Київ"


def test_parse_endpoint_suggestion_may_be_null(client):
    resp = client.post("/api/parse", json={"text": "Some Street 5"})
    assert resp.status_code == 200
    assert resp.json()["suggested_city"] is None


# --- geocode endpoint surface ------------------------------------------------


def test_city_is_passed_through_to_the_service(client, stub_service):
    resp = client.post(
        "/api/geocode",
        json={"items": [{"index": 0, "original": "x", "trimmed": "x"}], "city": "Київ"},
    )
    assert resp.status_code == 200
    assert stub_service.seen_city == "Київ"


def test_city_is_trimmed(client, stub_service):
    client.post(
        "/api/geocode",
        json={"items": [{"index": 0, "original": "x", "trimmed": "x"}], "city": "  Київ  "},
    )
    assert stub_service.seen_city == "Київ"


def test_blank_city_becomes_none(client, stub_service):
    client.post(
        "/api/geocode",
        json={"items": [{"index": 0, "original": "x", "trimmed": "x"}], "city": "   "},
    )
    assert stub_service.seen_city is None


def test_missing_city_becomes_none(client, stub_service):
    client.post("/api/geocode", json={"items": [{"index": 0, "original": "x", "trimmed": "x"}]})
    assert stub_service.seen_city is None


def test_geocode_response_exposes_new_fields(client, stub_service):
    resp = client.post(
        "/api/geocode",
        json={"items": [{"index": 0, "original": "x", "trimmed": "definitely not a place"}]},
    )
    assert resp.status_code == 200
    body = resp.json()["results"][0]
    assert body["searched_as"] == "definitely not a place"
    assert body["unit"] == "9"
    assert body["unit_kind"] == "apartment"
    assert body["unit_inferred"] is True
    assert body["dropped_candidates"] == 2


def test_geocode_still_works_without_city(client, stub_service):
    resp = client.post(
        "/api/geocode",
        json={"items": [{"index": 0, "original": "x", "trimmed": "definitely not a place"}]},
    )
    assert resp.status_code == 200


def test_geocode_rejects_more_than_five_items(client):
    items = [{"index": i, "original": "x", "trimmed": "x"} for i in range(6)]
    assert client.post("/api/geocode", json={"items": items}).status_code == 422


def test_provider_is_shared_across_requests():
    """The rate limiter and cache live on the provider instance."""
    from app.api.geocode import get_geocoder

    assert get_geocoder() is get_geocoder()