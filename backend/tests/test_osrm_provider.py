import logging

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.errors import (
    RouterInvalidResponseError,
    RouterRateLimitedError,
    RouterSetupError,
    RouterTimeoutError,
    RouterTooManyPointsError,
    RouterUnavailableError,
)
from app.domain.models import Coordinate
from app.providers.routing.osrm import OsrmRouter

BASE = "https://router.project-osrm.org"


def make_settings(**overrides):
    kwargs = dict(
        NOMINATIM_USER_AGENT="RouteOptimizer-test/0.1.0 (test@test.com)",
        OSRM_BASE_URL=BASE,
        OSRM_RATE_LIMIT_DELAY_SECONDS=0.0,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def ok_body(n: int = 2) -> dict:
    return {
        "code": "Ok",
        "durations": [[0.0 if i == j else 10.0 for j in range(n)] for i in range(n)],
        "distances": [[0.0 if i == j else 1000.0 for j in range(n)] for i in range(n)],
    }


COORDS = [Coordinate(50.4501, 30.5234), Coordinate(50.4433, 30.5139)]
MATRIX_URL = (
    "https://router.project-osrm.org/table/v1/driving/"
    "30.5234,50.4501;30.5139,50.4433?annotations=duration,distance"
)


@respx.mock
@pytest.mark.asyncio
async def test_builds_the_exact_url_with_lon_lat_order():
    # respx matches the full URL including the query string and the order of
    # the coordinate pairs, so this test fails the moment the URL changes.
    route = respx.get(MATRIX_URL).respond(json=ok_body())
    result = await OsrmRouter(make_settings()).matrix(COORDS)
    assert route.called
    assert result.provider == "osrm"
    assert result.profile == "driving"
    assert result.durations_s[0][1] == 10.0
    assert result.distances_m[1][0] == 1000.0


@respx.mock
@pytest.mark.asyncio
async def test_success_response_parses_snap_distances():
    body = ok_body()
    body["sources"] = [
        {"distance": 35.1, "name": "a", "hint": "h", "location": [30.52, 50.45]},
        {"distance": 7.7, "name": "b", "hint": "h", "location": [30.51, 50.44]},
    ]
    respx.get(MATRIX_URL).respond(json=body)
    result = await OsrmRouter(make_settings()).matrix(COORDS)
    assert result.snap_distance_m == [35.1, 7.7]


@respx.mock
@pytest.mark.asyncio
async def test_null_cells_are_kept_as_none():
    body = {
        "code": "Ok",
        "durations": [[0.0, None], [None, 0.0]],
        "distances": [[0.0, None], [None, 0.0]],
    }
    respx.get(MATRIX_URL).respond(json=body)
    result = await OsrmRouter(make_settings()).matrix(COORDS)
    assert result.durations_s == [[0.0, None], [None, 0.0]]
    assert result.distances_m == [[0.0, None], [None, 0.0]]


@respx.mock
@pytest.mark.asyncio
async def test_too_big_code_maps_to_too_many_points():
    respx.get(MATRIX_URL).respond(json={"code": "TooBig"})
    with pytest.raises(RouterTooManyPointsError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_non_ok_code_maps_to_invalid_response():
    respx.get(MATRIX_URL).respond(json={"code": "NoRoute"})
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_missing_code_maps_to_invalid_response():
    single_url = BASE + "/table/v1/driving/30.5234,50.4501?annotations=duration,distance"
    respx.get(single_url).respond(json={"durations": [[0.0]], "distances": [[0.0]]})
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix([COORDS[0]])


@respx.mock
@pytest.mark.asyncio
async def test_429_raises_retryable_with_retry_after(caplog):
    respx.get(MATRIX_URL).respond(
        429, text="rate limit exceeded for this IP", headers={"Retry-After": "3"}
    )
    router = OsrmRouter(make_settings())
    with caplog.at_level(logging.WARNING, logger="app.providers.routing.osrm"):
        with pytest.raises(RouterRateLimitedError) as exc_info:
            await router.matrix(COORDS)
    assert exc_info.value.retry_after == 3.0
    assert exc_info.value.kind == "retryable"
    message = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING][0]
    assert "status=429" in message
    assert "rate limit exceeded for this IP" in message
    # Body is truncated, and no coordinates are logged.
    assert "50.4501" not in message
    assert "30.5234" not in message


@respx.mock
@pytest.mark.asyncio
async def test_429_is_a_single_request_no_retry_loop():
    route = respx.get(MATRIX_URL).respond(429, json={})
    router = OsrmRouter(make_settings())
    for _ in range(2):
        with pytest.raises(RouterRateLimitedError):
            await router.matrix(COORDS)
    assert route.call_count == 2  # one call per request, no automatic retries


@respx.mock
@pytest.mark.asyncio
async def test_5xx_maps_to_unavailable(caplog):
    respx.get(MATRIX_URL).respond(503, text="service unavailable" + "x" * 600)
    with caplog.at_level(logging.WARNING, logger="app.providers.routing.osrm"):
        with pytest.raises(RouterUnavailableError) as exc_info:
            await OsrmRouter(make_settings()).matrix(COORDS)
    assert exc_info.value.status_code == 503
    assert exc_info.value.kind == "retryable"
    message = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING][0]
    assert "status=503" in message
    # The 600-char body is cut to 500 chars (leading prefix keeps 19 chars).
    assert message.split("body=", 1)[1] == "service unavailable" + "x" * (500 - 19)


@respx.mock
@pytest.mark.asyncio
async def test_401_and_403_map_to_setup_error():
    for status in (401, 403):
        respx.get(MATRIX_URL).respond(status, text="denied")
        with pytest.raises(RouterSetupError) as exc_info:
            await OsrmRouter(make_settings()).matrix(COORDS)
        assert exc_info.value.kind == "setup"
        respx.reset()


@pytest.mark.asyncio
async def test_timeout_maps_to_timeout_error():
    router = OsrmRouter(make_settings())

    async def raise_timeout(request):
        raise httpx.TimeoutException("timed out", request=request)

    with respx.mock:
        respx.get(MATRIX_URL).mock(side_effect=raise_timeout)
        with pytest.raises(RouterTimeoutError):
            await router.matrix(COORDS)


@pytest.mark.asyncio
async def test_network_error_maps_to_unavailable():
    router = OsrmRouter(make_settings())

    async def raise_connect_error(request):
        raise httpx.ConnectError("connection refused", request=request)

    with respx.mock:
        respx.get(MATRIX_URL).mock(side_effect=raise_connect_error)
        with pytest.raises(RouterUnavailableError):
            await router.matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_malformed_json_maps_to_invalid_response():
    respx.get(MATRIX_URL).respond(200, text="<html>this is not json</html>")
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_non_square_matrix_maps_to_invalid_response():
    body = {"code": "Ok", "durations": [[0.0, 10.0]], "distances": [[0.0, 10.0]]}
    respx.get(MATRIX_URL).respond(json=body)
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_negative_value_maps_to_invalid_response():
    body = {
        "code": "Ok",
        "durations": [[0.0, -1.0], [1.0, 0.0]],
        "distances": [[0.0, 10.0], [10.0, 0.0]],
    }
    respx.get(MATRIX_URL).respond(json=body)
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_non_numeric_value_maps_to_invalid_response():
    body = {
        "code": "Ok",
        "durations": [[0.0, "10.0"], [10.0, 0.0]],
        "distances": [[0.0, 10.0], [10.0, 0.0]],
    }
    respx.get(MATRIX_URL).respond(json=body)
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_boolean_cell_maps_to_invalid_response():
    body = {
        "code": "Ok",
        "durations": [[0.0, True], [False, 0.0]],
        "distances": [[0.0, 10.0], [10.0, 0.0]],
    }
    respx.get(MATRIX_URL).respond(json=body)
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_non_finite_value_maps_to_invalid_response():
    body = '{"code":"Ok","durations":[[0.0,NaN],[1.0,0.0]],"distances":[[0.0,10.0],[10.0,0.0]]}'
    respx.get(MATRIX_URL).respond(200, text=body)
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_non_zero_diagonal_maps_to_invalid_response():
    body = {
        "code": "Ok",
        "durations": [[1.0, 10.0], [10.0, 0.0]],
        "distances": [[0.0, 10.0], [10.0, 0.0]],
    }
    respx.get(MATRIX_URL).respond(json=body)
    with pytest.raises(RouterInvalidResponseError):
        await OsrmRouter(make_settings()).matrix(COORDS)


@respx.mock
@pytest.mark.asyncio
async def test_cache_reuses_result_for_rounded_coordinates():
    route = respx.get(MATRIX_URL).respond(json=ok_body())
    router = OsrmRouter(make_settings())
    first = await router.matrix(COORDS)
    # Coordinates differing only past the 6th decimal round to the same key.
    shifted = [Coordinate(c.latitude + 0.0000002, c.longitude - 0.0000003) for c in COORDS]
    second = await router.matrix(shifted)
    assert route.call_count == 1
    assert first is second


@respx.mock
@pytest.mark.asyncio
async def test_errors_are_never_cached():
    route = respx.get(MATRIX_URL).respond(429, json={})
    router = OsrmRouter(make_settings())
    for _ in range(2):
        with pytest.raises(RouterRateLimitedError):
            await router.matrix(COORDS)
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_rate_limiter_spaces_requests():
    # A clock that returns a fixed sequence, mirroring the real one: `now` when
    # the request starts, `now` again after it finishes. The second request
    # starts 0.3 s after the first one finished, so it must wait 0.7 s.
    def make_clock():
        values = [100.0, 100.3, 100.3, 101.3]
        state = {"i": 0}

        def clock():
            value = values[min(state["i"], len(values) - 1)]
            state["i"] += 1
            return value

        return clock

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    router = OsrmRouter(
        make_settings(OSRM_RATE_LIMIT_DELAY_SECONDS=1.0),
        monotonic=make_clock(),
        sleep=fake_sleep,
    )
    other_coords = [Coordinate(50.4502, 30.5235), Coordinate(50.4434, 30.5138)]
    other_url = (
        "https://router.project-osrm.org/table/v1/driving/"
        "30.5235,50.4502;30.5138,50.4434?annotations=duration,distance"
    )
    with respx.mock:
        route_a = respx.get(MATRIX_URL).respond(json=ok_body())
        route_b = respx.get(other_url).respond(json=ok_body())
        await router.matrix(COORDS)
        # Slightly different coordinates so the second call is a cache miss.
        await router.matrix(other_coords)
        assert route_a.call_count == 1
        assert route_b.call_count == 1
    assert sleeps == [1.0]


@respx.mock
@pytest.mark.asyncio
async def test_unusable_sources_block_does_not_break_matrix():
    body = ok_body()
    body["sources"] = "nonsense"
    respx.get(MATRIX_URL).respond(json=body)
    result = await OsrmRouter(make_settings()).matrix(COORDS)
    assert result.snap_distance_m is None
    assert result.durations_s[0][1] == 10.0