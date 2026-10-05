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

# Heuristic: result types that typically indicate building/house level precision
BUILDING_LIKE_TYPES = {
    "building",
    "house",
    "residential",
    "apartments",
    "yes",  # sometimes used for buildings
    "office",
    "commercial",
    "retail",
    "industrial",
    "warehouse",
    "hotel",
    "school",
    "hospital",
    "place_of_worship",
    "government",
    "public_building",
    "civic",
    "entrance",
    "room",
}


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

    def _canonical_key(self, address: str) -> str:
        return address.strip().lower()

    async def geocode(self, address: str) -> GeocodeResult:
        key = self._canonical_key(address)
        if key in self._cache:
            cached = self._cache[key]
            # Never cache error results per requirements
            if cached.status != GeocodeStatus.ERROR:
                return cached
            # If somehow cached error, don't return it; proceed to fetch fresh

        async with self._lock:
            # Enforce rate limit before making real network call
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await asyncio.sleep(self._rate_limit_delay - elapsed)

            params: Dict[str, Any] = {
                "q": address,
                "format": "json",
                "addressdetails": 1,
                "limit": 5,
                "dedupe": 1,
            }
            if self._country_codes:
                params["countrycodes"] = self._country_codes

            headers = {"User-Agent": self._user_agent}

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(f"{self._base_url}", params=params, headers=headers)
                self._last_request_time = time.monotonic()

                if resp.status_code == 429:
                    # Rate limited upstream: temporary, so the user can retry.
                    if _should_log_failure(resp.status_code):
                        _log_provider_failure(resp)
                    return GeocodeResult(
                        status=GeocodeStatus.ERROR,
                        error_message="rate limited",
                        error_kind=ErrorKind.RETRYABLE,
                    )

                if resp.status_code in SETUP_STATUSES:
                    _log_provider_failure(resp)
                    return GeocodeResult(
                        status=GeocodeStatus.ERROR,
                        error_message=f"service rejected request (status {resp.status_code})",
                        error_kind=ErrorKind.SETUP,
                    )

                if resp.status_code >= 500:
                    _log_provider_failure(resp)
                    return GeocodeResult(
                        status=GeocodeStatus.ERROR,
                        error_message=f"service unavailable (status {resp.status_code})",
                        error_kind=ErrorKind.RETRYABLE,
                    )

                resp.raise_for_status()
                data = resp.json()
            except httpx.TimeoutException:
                self._last_request_time = time.monotonic()
                return GeocodeResult(
                    status=GeocodeStatus.ERROR,
                    error_message="request timed out",
                    error_kind=ErrorKind.RETRYABLE,
                )
            except httpx.HTTPStatusError as e:
                self._last_request_time = time.monotonic()
                status_code = e.response.status_code if e.response is not None else None
                if status_code is not None and _should_log_failure(status_code):
                    _log_provider_failure(e.response)
                return GeocodeResult(
                    status=GeocodeStatus.ERROR,
                    error_message=f"unexpected response (status {status_code})",
                    error_kind=ErrorKind.SETUP,
                )
            except Exception:
                self._last_request_time = time.monotonic()
                return GeocodeResult(
                    status=GeocodeStatus.ERROR,
                    error_message="network failure",
                    error_kind=ErrorKind.RETRYABLE,
                )

        # Classify and validate response (lock released after request)
        classification = self._classify_response(data, address)
        # Cache only non-error statuses
        if classification.status != GeocodeStatus.ERROR:
            self._cache[key] = classification
        return classification

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

    def _is_building_like_type(self, item: Dict[str, Any]) -> bool:
        rtype = (item.get("type") or "").lower()
        rclass = (item.get("class") or "").lower()
        if rtype in BUILDING_LIKE_TYPES or rclass in BUILDING_LIKE_TYPES:
            return True
        return False

    def _is_street_or_locality_level(self, item: Dict[str, Any]) -> bool:
        rtype = (item.get("type") or "").lower()
        # street-like or locality-like
        street_like = {
            "road",
            "street",
            "residential",
            "pedestrian",
            "footway",
            "path",
            "hamlet",
            "village",
            "town",
            "city",
            "county",
            "state",
            "country",
            "suburb",
            "neighbourhood",
            "quarter",
            "borough",
            "municipality",
        }
        if rtype in street_like:
            return True
        return False

    def _classify_response(self, data: Any, address: str) -> GeocodeResult:
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
            display_name = str(item.get("display_name", ""))
            cand = GeocodeCandidate(
                display_name=display_name,
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

        # Only up to 5 requested
        candidates = candidates[:5]

        if len(candidates) == 1:
            c = candidates[0]
            addr = c.address if isinstance(c.address, dict) else None
            has_hn = self._has_house_number(addr)
            is_bldg = self._is_building_like_type(candidates[0]) if False else self._is_building_like_type(data[0])  # data item
            # better check from original data item
            pass

        # Recheck using original data items
        orig_items = data[: len(candidates)]
        if len(orig_items) == 1:
            item = orig_items[0]
            addr = item.get("address") if isinstance(item.get("address"), dict) else None
            has_hn = self._has_house_number(addr)
            is_bldg_like = self._is_building_like_type(item)
            # Resolved only if has house number OR building-like type at building/house level
            # Also accept if result type indicates precise point
            precise = has_hn or is_bldg_like
            if precise:
                c = candidates[0]
                return GeocodeResult(
                    status=GeocodeStatus.RESOLVED,
                    coordinate=Coordinate(c.latitude, c.longitude),
                    display_name=c.display_name,
                )
            # Otherwise partial - street/locality level
            c = candidates[0]
            return GeocodeResult(
                status=GeocodeStatus.PARTIAL,
                coordinate=None,
                display_name=c.display_name,
                candidates=[c],
                error_message="Found street/locality but not a specific house/building number",
                message="Partial match: house/building number missing",
            )

        # Multiple candidates - ambiguous
        # If clearly one is stronger AND has house number/building-like, still ambiguous list is better for user choice
        # Return all as ambiguous
        return GeocodeResult(
            status=GeocodeStatus.AMBIGUOUS,
            candidates=candidates,
            error_message="Multiple comparable candidates found",
        )
