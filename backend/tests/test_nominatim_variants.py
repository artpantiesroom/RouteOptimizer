import pytest
import respx

from app.core.config import Settings
from app.providers.geocoding.nominatim import NominatimGeocoder
from app.domain.geocode import GeocodeStatus


def make_settings():
    return Settings(
        NOMINATIM_USER_AGENT="RouteOptimizer-test/0.1.0 (test@test.com)",
        NOMINATIM_RATE_LIMIT_DELAY_SECONDS=0,
    )


@respx.mock
@pytest.mark.asyncio
async def test_house_number_slash():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(
        200,
        json=[
            {
                "lat": "1",
                "lon": "1",
                "display_name": "St, 12/2",
                "address": {"house_number": "12/2"},
                "type": "building",
            }
        ],
    )
    r = await geocoder.geocode("St 12/2")
    assert r.status == GeocodeStatus.RESOLVED


@respx.mock
@pytest.mark.asyncio
async def test_house_number_korp():
    geocoder = NominatimGeocoder(make_settings())
    respx.get("https://nominatim.openstreetmap.org/search").respond(
        200,
        json=[
            {
                "lat": "1",
                "lon": "1",
                "display_name": "St, 12 корп. 3",
                "address": {"house_number": "12 корп. 3"},
                "type": "building",
            }
        ],
    )
    r = await geocoder.geocode("St 12 корп. 3")
    assert r.status == GeocodeStatus.RESOLVED
