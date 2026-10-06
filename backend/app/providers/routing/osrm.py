from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Awaitable, Callable, List, Optional, Sequence, Tuple

import httpx

from ...core.config import Settings
from ...core.errors import (
    RouterInvalidResponseError,
    RouterRateLimitedError,
    RouterSetupError,
    RouterTimeoutError,
    RouterTooManyPointsError,
    RouterUnavailableError,
)
from ...core.logging import get_logger
from ...domain.models import Coordinate
from ...domain.routing import MatrixResult
from .base import Router

logger = get_logger(__name__)

# Upstream statuses where the routing service itself refused the request.
SETUP_STATUSES = frozenset({401, 403})
LOG_BODY_MAX_CHARS = 500

# A "zero" diagonal does not have to be literally 0.0, but it must be tiny.
DIAGONAL_ZERO_TOLERANCE = 1e-6

# OSRM uses dimensionless time units; the table API returns driving time in
# seconds and distance in metres.
DURATIONS_KEY = "durations"
DISTANCES_KEY = "distances"
SOURCES_KEY = "sources"
OK_CODE = "Ok"
TOO_BIG_CODE = "TooBig"

# Map the more interesting non-Ok codes to a message; everything else is a
# generic "the provider rejected the query" which is not actionable either.
_NON_OK_MESSAGES = {
    "NoRoute": "no route between the requested points",
    "NoSegment": "a point could not be snapped to the road network",
    "InvalidUrl": "provider rejected the request URL",
    "InvalidService": "provider rejected the service name",
    "InvalidVersion": "provider rejected the API version",
    "InvalidOptions": "provider rejected the request options",
    "InvalidQuery": "provider rejected the query",
    "InvalidValue": "provider rejected a coordinate value",
}


def _body_excerpt(response: Optional[httpx.Response]) -> str:
    """First LOG_BODY_MAX_CHARS of a response body, for log context only."""
    if response is None:
        return ""
    try:
        return response.text[:LOG_BODY_MAX_CHARS]
    except Exception:
        return ""


def _log_provider_failure(response: httpx.Response) -> None:
    """Log status code and a bounded body excerpt for upstream failures.

    Only the status and the body prefix are logged; coordinates are never part
    of the message.
    """
    logger.warning(
        "Matrix request failed: status=%s body=%s",
        response.status_code,
        _body_excerpt(response),
    )


def _should_log_failure(status: int) -> bool:
    # 429 is logged too: the body often explains how long to wait, and we are
    # explicitly told to keep status + body excerpt for every retryable reply.
    return status == 429 or status in SETUP_STATUSES or 500 <= status < 600


def _cache_key(coordinates: Sequence[Coordinate]) -> Tuple[Tuple[float, float], ...]:
    """Cache key: coordinates rounded to 6 decimal places (~0.1 m)."""
    return tuple(
        (round(c.latitude, 6), round(c.longitude, 6)) for c in coordinates
    )


def _snap_distances_from_body(data: Any, n: int) -> Optional[List[Optional[float]]]:
    """Per-point distance to the road network, when OSRM reports it.

    The table response carries a ``sources`` block with one ``distance`` (in
    metres, from the input coordinate to the snapped one) per point. The value
    is only trusted when it is a finite non-negative number for every point;
    any deviation means the block is not usable and we report no snap data.
    """
    sources = data.get(SOURCES_KEY) if isinstance(data, dict) else None
    if not isinstance(sources, list) or len(sources) != n:
        return None
    distances: List[Optional[float]] = []
    for source in sources:
        if not isinstance(source, dict):
            return None
        value = source.get("distance")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        value = float(value)
        if not math.isfinite(value) or value < 0:
            return None
        distances.append(value)
    return distances


class OsrmRouter(Router):
    def __init__(
        self,
        settings: Settings,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._base_url = settings.OSRM_BASE_URL.rstrip("/")
        self._user_agent = settings.NOMINATIM_USER_AGENT
        self._timeout = settings.OSRM_TIMEOUT_SECONDS
        self._rate_limit_delay = settings.OSRM_RATE_LIMIT_DELAY_SECONDS
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_request_time = 0.0
        self._cache: dict[Tuple[Tuple[float, float], ...], MatrixResult] = {}

    def _profile(self) -> str:
        # Only driving is used for now; the profile is fixed in the URL.
        return "driving"

    def _build_url(self, coordinates: Sequence[Coordinate]) -> str:
        # OSRM expects longitude,latitude pairs in the URL path.
        locs = ";".join(f"{c.longitude},{c.latitude}" for c in coordinates)
        return (
            f"{self._base_url}/table/v1/{self._profile()}/{locs}"
            f"?annotations=duration,distance"
        )

    async def matrix(self, coordinates: Sequence[Coordinate]) -> MatrixResult:
        key = _cache_key(coordinates)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        data = await self._request(coordinates)

        result = self._parse_and_validate(data, len(coordinates))
        # Only successful responses are cached; errors are never cached.
        self._cache[key] = result
        return result

    async def _request(self, coordinates: Sequence[Coordinate]) -> Any:
        url = self._build_url(coordinates)
        headers = {"User-Agent": self._user_agent}

        async with self._lock:
            # Shared lock: the delay and the request happen under one lock, so
            # concurrent callers cannot line up and fire requests back-to-back.
            now = self._monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await self._sleep(self._rate_limit_delay - elapsed)

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, headers=headers)
                self._last_request_time = self._monotonic()
            except httpx.TimeoutException:
                self._last_request_time = self._monotonic()
                raise RouterTimeoutError("routing request timed out")
            except Exception:
                self._last_request_time = self._monotonic()
                raise RouterUnavailableError("routing service unreachable (network error)")

        if resp.status_code == 429:
            if _should_log_failure(resp.status_code):
                _log_provider_failure(resp)
            retry_after = _parse_retry_after(resp)
            raise RouterRateLimitedError(
                "routing service rate limited (status 429)",
                status_code=resp.status_code,
                retry_after=retry_after,
            )

        if resp.status_code in SETUP_STATUSES:
            _log_provider_failure(resp)
            raise RouterSetupError(
                f"routing service rejected the request (status {resp.status_code})",
                status_code=resp.status_code,
            )

        if resp.status_code >= 500:
            _log_provider_failure(resp)
            raise RouterUnavailableError(
                f"routing service unavailable (status {resp.status_code})",
                status_code=resp.status_code,
            )

        if resp.status_code != 200:
            if _should_log_failure(resp.status_code):
                _log_provider_failure(resp)
            raise RouterInvalidResponseError(
                f"unexpected response (status {resp.status_code})",
                status_code=resp.status_code,
            )

        try:
            return resp.json()
        except Exception:
            raise RouterInvalidResponseError("routing response is not valid JSON")

    def _parse_and_validate(self, data: Any, n: int) -> MatrixResult:
        """Turn raw JSON into a trustable MatrixResult.

        Nothing from the wire is used without a check: the code field, the shape
        of both matrices, cell types, non-negative values and a zero diagonal.
        """
        if not isinstance(data, dict):
            raise RouterInvalidResponseError("routing response is not a JSON object")

        code = data.get("code")
        if code != OK_CODE:
            if code == TOO_BIG_CODE:
                raise RouterTooManyPointsError(
                    "routing service refused the request: too many points"
                )
            message = _NON_OK_MESSAGES.get(code)
            if message:
                raise RouterInvalidResponseError(
                    f"routing service rejected the request ({message})"
                )
            raise RouterInvalidResponseError(
                f"routing service returned code {code!r}"
            )

        durations = _validated_matrix(data, DURATIONS_KEY, n)
        distances = _validated_matrix(data, DISTANCES_KEY, n)
        snap = _snap_distances_from_body(data, n)

        return MatrixResult(
            durations_s=durations,
            distances_m=distances,
            provider="osrm",
            profile=self._profile(),
            snap_distance_m=snap,
        )


def _validated_matrix(data: dict, key: str, n: int) -> List[List[Optional[float]]]:
    raw = data.get(key)
    if not isinstance(raw, list) or len(raw) != n:
        raise RouterInvalidResponseError(
            f"{key} is missing or has the wrong number of rows"
        )

    validated: List[List[Optional[float]]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, list) or len(row) != n:
            raise RouterInvalidResponseError(
                f"{key} is not a square {n}x{n} matrix"
            )
        values: List[Optional[float]] = []
        for j, value in enumerate(row):
            if value is None:
                # No route between the pair.
                values.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RouterInvalidResponseError(
                    f"{key} contains a non-numeric cell"
                )
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise RouterInvalidResponseError(
                    f"{key} contains a negative or non-finite value"
                )
            if i == j and abs(number) > DIAGONAL_ZERO_TOLERANCE:
                raise RouterInvalidResponseError(
                    f"{key} has a non-zero diagonal at row {i}"
                )
            values.append(number)
        validated.append(values)
    return validated


def _parse_retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None