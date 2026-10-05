from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from ...core.config import Settings
from ...core.logging import get_logger
from ...domain.geocode import (
    RETRYABLE_ERROR_MESSAGE,
    SETUP_ERROR_MESSAGE,
    ErrorKind,
    GeocodeCandidate,
    GeocodeResult,
    GeocodeStatus,
)
from ...domain.models import Coordinate


logger = get_logger(__name__)

# Upstream statuses where the address search service itself refused the request.
# These are setup problems: the user's address is not at fault.
SETUP_STATUSES = frozenset({401, 403})
LOG_BODY_MAX_CHARS = 500


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

    Called for a single in-flight address only, never with a full address
    list, so customer addresses are not logged in bulk.
    """
    logger.warning(
        "Address search failed: status=%s body=%s",
        response.status_code,
        _body_excerpt(response),
    )


def _should_log_failure(status: int) -> bool:
    return status in SETUP_STATUSES or 500 <= status < 600


class NominatimGeocoder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.NOMINATIM_BASE_URL.rstrip("/")
        self._user_agent = settings.NOMINATIM_USER_AGENT
        self._country_codes = settings.NOMINATIM_COUNTRY_CODES
        self._timeout = settings.NOMINATIM_TIMEOUT_SECONDS
        self._rate_limit_delay = settings.NOMINATIM_RATE_LIMIT_DELAY_SECONDS
        self._lock = asyncio.Lock()
        self._last_request_time = 0.0
        self._cache: Dict[str, GeocodeResult] = {}

    def _cache_key(self, params: Dict[str, Any]) -> str:
        parts = [f"{name}={str(params[name]).strip().lower()}" for name in ("q", "street", "city") if params.get(name)]
        if self._country_codes:
            parts.append(f"cc={self._country_codes}")
        return "|".join(parts)

    async def geocode(self, address: str) -> GeocodeResult:
        """Free-text lookup. ``q`` is used, so no structured field is sent."""
        data, error = await self._lookup({"q": address})
        if error is not None:
            return error
        return self._classify_response(data)

    async def geocode_structured(self, street: str, city: str) -> GeocodeResult:
        """Structured lookup.

        Nominatim rejects a request that combines ``q`` with any structured
        field, so this attempt sends only ``street``/``city``. Verified live:
        that form returns Kyiv-only results where the free-text query also
        returns other oblast towns.
        """
        data, error = await self._lookup({"street": street, "city": city})
        if error is not None:
            return error
        return self._classify_response(data)

    async def _lookup(
        self, params: Dict[str, Any]
    ) -> tuple[Optional[Any], Optional[GeocodeResult]]:
        """Run one request. Returns ``(data, None)`` or ``(None, error_result)``."""
        key = self._cache_key(params)
        cached = self._cache.get(key)
        if cached is not None:
            return None, cached

        request_params: Dict[str, Any] = {
            **params,
            "format": "json",
            "addressdetails": 1,
            "limit": 5,
            "dedupe": 1,
        }
        if self._country_codes:
            request_params["countrycodes"] = self._country_codes

        headers = {"User-Agent": self._user_agent}

        async with self._lock:
            # Enforce rate limit before making real network call
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await asyncio.sleep(self._rate_limit_delay - elapsed)

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(f"{self._base_url}", params=request_params, headers=headers)
                self._last_request_time = time.monotonic()

                if resp.status_code == 429:
                    # Rate limited upstream: temporary, so the user can retry.
                    if _should_log_failure(resp.status_code):
                        _log_provider_failure(resp)
                    return None, GeocodeResult(
                        status=GeocodeStatus.ERROR,
                        error_message="rate limited",
                        error_kind=ErrorKind.RETRYABLE,
                    )

                if resp.status_code in SETUP_STATUSES:
                    _log_provider_failure(resp)
                    return None, GeocodeResult(
                        status=GeocodeStatus.ERROR,
                        error_message=f"service rejected request (status {resp.status_code})",
                        error_kind=ErrorKind.SETUP,
                    )

                if resp.status_code >= 500:
                    _log_provider_failure(resp)
                    return None, GeocodeResult(
                        status=GeocodeStatus.ERROR,
                        error_message=f"service unavailable (status {resp.status_code})",
                        error_kind=ErrorKind.RETRYABLE,
                    )

                resp.raise_for_status()
                data = resp.json()
            except httpx.TimeoutException:
                self._last_request_time = time.monotonic()
                return None, GeocodeResult(
                    status=GeocodeStatus.ERROR,
                    error_message="request timed out",
                    error_kind=ErrorKind.RETRYABLE,
                )
            except httpx.HTTPStatusError as e:
                self._last_request_time = time.monotonic()
                status_code = e.response.status_code if e.response is not None else None
                if status_code is not None and _should_log_failure(status_code):
                    _log_provider_failure(e.response)
                return None, GeocodeResult(
                    status=GeocodeStatus.ERROR,
                    error_message=f"unexpected response (status {status_code})",
                    error_kind=ErrorKind.SETUP,
                )
            except Exception:
                self._last_request_time = time.monotonic()
                return None, GeocodeResult(
                    status=GeocodeStatus.ERROR,
                    error_message="network failure",
                    error_kind=ErrorKind.RETRYABLE,
                )

        # Classify outside the lock so the next request is not blocked by us.
        result = self._classify_response(data)
        # Cache only non-error statuses
        if result.status != GeocodeStatus.ERROR:
            self._cache[key] = result
        return data, None

    def _has_house_number(self, addr: Optional[Dict[str, Any]]) -> bool:
        if not addr:
            return False
        hn = addr.get("house_number")
        if not hn:
            return False
        # Non-empty string/number
        s = str(hn).strip()
        if s == "":
            return False
        return True

    def _classify_response(self, data: Any) -> GeocodeResult:
        if not isinstance(data, list) or len(data) == 0:
            return GeocodeResult(
                status=GeocodeStatus.NOT_FOUND,
                error_message="No results found",
            )

        candidates: List[GeocodeCandidate] = []
        for item in data:
            try:
                lat = float(item.get("lat"))
                lon = float(item.get("lon"))
            except (TypeError, ValueError):
                continue
            cand = GeocodeCandidate(
                display_name=str(item.get("display_name", "")),
                latitude=lat,
                longitude=lon,
                place_id=item.get("place_id"),
                osm_type=item.get("osm_type"),
                osm_id=item.get("osm_id"),
                importance=item.get("importance"),
                address=item.get("address"),
            )
            candidates.append(cand)

        if len(candidates) == 0:
            return GeocodeResult(
                status=GeocodeStatus.NOT_FOUND,
                error_message="No valid results found",
            )

        candidates = candidates[:5]

        # A candidate counts as house-level when the provider resolved an actual
        # house number, or returned the building itself.
        precise = [
            c
            for c in candidates
            if self._has_house_number(c.address if isinstance(c.address, dict) else None)
        ]
        if precise:
            return GeocodeResult(
                status=GeocodeStatus.RESOLVED if len(precise) == 1 else GeocodeStatus.AMBIGUOUS,
                coordinate=(
                    Coordinate(precise[0].latitude, precise[0].longitude) if len(precise) == 1 else None
                ),
                display_name=precise[0].display_name if len(precise) == 1 else None,
                candidates=precise if len(precise) > 1 else [],
            )

        # Nothing reached house level: keep every street-level hit so the service
        # layer can group them and report one approximate representative.
        return GeocodeResult(
            status=GeocodeStatus.PARTIAL,
            candidates=candidates,
            error_message="Found street/locality but not a specific house/building number",
            message="Partial match: house/building number missing",
        )
