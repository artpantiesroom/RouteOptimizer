import pytest
import httpx
import respx

from app.core.config import Settings
from app.providers.geocoding.nominatim import NominatimGeocoder
from app.domain.geocode import GeocodeStatus


def make_settings():
    s = Settings(
        NOMINATIM_USER_AGENT="RouteOptimizer-test/0.1.0 (test@test.com)",
        NOMINATIM_RATE_LIMIT_DELAY_SECONDS=0,
    )
    return s


@respx.mock
@pytest.mark.asyncio
async def test_resolved_with_house_number():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(
        200,
        json=[
            {
                "lat": "55.7558",
                "lon": "37.6176",
                "display_name": "Some St, 10, Moscow, Russia",
                "address": {"house_number": "10", "road": "Some St"},
                "type": "house",
            }
        ],
    )
    r = await geocoder.geocode("Some St 10, Moscow")
    assert r.status == GeocodeStatus.RESOLVED


@respx.mock
@pytest.mark.asyncio
async def test_partial_missing_house_number():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(
        200,
        json=[
            {
                "lat": "55.7558",
                "lon": "37.6176",
                "display_name": "Some St, Moscow, Russia",
                "address": {"road": "Some St"},
                "type": "street",
            }
        ],
    )
    r = await geocoder.geocode("Some St, Moscow")
    assert r.status == GeocodeStatus.PARTIAL


@respx.mock
@pytest.mark.asyncio
async def test_ambiguous_multiple_candidates():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(
        200,
        json=[
            {
                "lat": "1",
                "lon": "1",
                "display_name": "A",
                "address": {"house_number": "1"},
                "type": "house",
            },
            {
                "lat": "2",
                "lon": "2",
                "display_name": "B",
                "address": {"house_number": "2"},
                "type": "house",
            },
        ],
    )
    r = await geocoder.geocode("Some")
    assert r.status == GeocodeStatus.AMBIGUOUS


@respx.mock
@pytest.mark.asyncio
async def test_not_found_empty():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(200, json=[])
    r = await geocoder.geocode("nowhere")
    assert r.status == GeocodeStatus.NOT_FOUND


@respx.mock
@pytest.mark.asyncio
async def test_error_429_no_retry_loop():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(429, json={}, headers={"Retry-After": "5"})
    r = await geocoder.geocode("bad")
    assert r.status == GeocodeStatus.ERROR


@respx.mock
@pytest.mark.asyncio
async def test_house_number_variants():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(
        200,
        json=[
            {
                "lat": "1",
                "lon": "2",
                "display_name": "St, 12A",
                "address": {"house_number": "12A"},
                "type": "building",
            }
        ],
    )
    r = await geocoder.geocode("St 12A")
    assert r.status == GeocodeStatus.RESOLVED
