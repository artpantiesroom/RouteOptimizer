import logging

import httpx
import pytest
import respx

from app.core.config import Settings
from app.domain.geocode import ErrorKind, GeocodeStatus
from app.providers.geocoding.nominatim import NominatimGeocoder

SEARCH_URL = "https://nominatim.openstreetmap.org/search"


def make_settings():
    return Settings(
        NOMINATIM_USER_AGENT="RouteOptimizer-test/0.1.0 (test@test.com)",
        NOMINATIM_RATE_LIMIT_DELAY_SECONDS=0,
    )


@respx.mock
@pytest.mark.asyncio
async def test_403_maps_to_setup_error(caplog):
    geocoder = NominatimGeocoder(make_settings())
    respx.get(SEARCH_URL).respond(403, text="Forbidden: user agent blocked")

    with caplog.at_level(logging.WARNING, logger="app.providers.geocoding.nominatim"):
        r = await geocoder.geocode("Some St 10, Moscow")

    assert r.status == GeocodeStatus.ERROR
    assert r.error_kind == ErrorKind.SETUP


@respx.mock
@pytest.mark.asyncio
async def test_401_maps_to_setup_error():
    geocoder = NominatimGeocoder(make_settings())
    respx.get(SEARCH_URL).respond(401, text="Unauthorized")

    r = await geocoder.geocode("Some St 10, Moscow")

    assert r.status == GeocodeStatus.ERROR
    assert r.error_kind == ErrorKind.SETUP


@respx.mock
@pytest.mark.asyncio
async def test_500_maps_to_retryable_error():
    geocoder = NominatimGeocoder(make_settings())
    respx.get(SEARCH_URL).respond(500, text="internal error")

    r = await geocoder.geocode("Some St 10, Moscow")

    assert r.status == GeocodeStatus.ERROR
    assert r.error_kind == ErrorKind.RETRYABLE


@respx.mock
@pytest.mark.asyncio
async def test_429_maps_to_retryable_error():
    geocoder = NominatimGeocoder(make_settings())
    respx.get(SEARCH_URL).respond(429, json={}, headers={"Retry-After": "5"})

    r = await geocoder.geocode("Some St 10, Moscow")

    assert r.status == GeocodeStatus.ERROR
    assert r.error_kind == ErrorKind.RETRYABLE


@pytest.mark.asyncio
async def test_timeout_maps_to_retryable_error():
    geocoder = NominatimGeocoder(make_settings())

    async def raise_timeout(request):
        raise httpx.TimeoutException("timed out", request=request)

    with respx.mock:
        respx.get(SEARCH_URL).mock(side_effect=raise_timeout)
        r = await geocoder.geocode("Some St 10, Moscow")

    assert r.status == GeocodeStatus.ERROR
    assert r.error_kind == ErrorKind.RETRYABLE


@pytest.mark.asyncio
async def test_network_error_maps_to_retryable_error():
    geocoder = NominatimGeocoder(make_settings())

    async def raise_network_error(request):
        raise httpx.ConnectError("connection refused", request=request)

    with respx.mock:
        respx.get(SEARCH_URL).mock(side_effect=raise_network_error)
        r = await geocoder.geocode("Some St 10, Moscow")

    assert r.status == GeocodeStatus.ERROR
    assert r.error_kind == ErrorKind.RETRYABLE


def test_setup_error_message_has_no_technical_terms():
    from app.domain.geocode import RETRYABLE_ERROR_MESSAGE, SETUP_ERROR_MESSAGE

    for text in (RETRYABLE_ERROR_MESSAGE, SETUP_ERROR_MESSAGE):
        lowered = text.lower()
        assert "geocod" not in lowered
        assert "provider" not in lowered
        assert "http" not in lowered
        assert "429" not in text
        assert "401" not in text
        assert "403" not in text


@respx.mock
@pytest.mark.asyncio
async def test_403_logs_status_and_truncated_body(caplog):
    geocoder = NominatimGeocoder(make_settings())
    long_body = "denied:" + ("x" * 900)
    respx.get(SEARCH_URL).respond(403, text=long_body)

    with caplog.at_level(logging.WARNING, logger="app.providers.geocoding.nominatim"):
        await geocoder.geocode("Some St 10, Moscow")

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "status=403" in message
    body = message.split("body=", 1)[1]
    # Body is truncated to 500 chars: the 900-char tail must be cut off.
    assert body == "denied:" + "x" * 493
    # The requested address must not be logged.
    assert "Some St 10, Moscow" not in message


@respx.mock
@pytest.mark.asyncio
async def test_500_logs_status_and_truncated_body(caplog):
    geocoder = NominatimGeocoder(make_settings())
    respx.get(SEARCH_URL).respond(503, text="y" * 900)

    with caplog.at_level(logging.WARNING, logger="app.providers.geocoding.nominatim"):
        await geocoder.geocode("Some St 10, Moscow")

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "status=503" in message
    body = message.split("body=", 1)[1]
    assert body == "y" * 500
    assert "y" * 501 not in body


@respx.mock
@pytest.mark.asyncio
async def test_setup_error_is_not_cached():
    geocoder = NominatimGeocoder(make_settings())
    route = respx.get(SEARCH_URL).respond(403, text="Forbidden")
    first = await geocoder.geocode("Some St 10, Moscow")
    second = await geocoder.geocode("Some St 10, Moscow")

    assert first.error_kind == ErrorKind.SETUP
    assert second.error_kind == ErrorKind.SETUP
    # A cached error would skip the second network call.
    assert route.call_count == 2