from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from .models import Coordinate


class GeocodeStatus(str, Enum):
    RESOLVED = "resolved"
    PARTIAL = "partial"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    ERROR = "error"


class ErrorKind(str, Enum):
    """How an error should be presented to the user.

    RETRYABLE: temporary condition, retrying the same address can succeed.
    SETUP: the address search service rejected the request itself, so the
    address is not at fault and retrying will not help.
    """

    RETRYABLE = "retryable"
    SETUP = "setup"


# User-facing copy. Must not contain technical terms (geocoding, provider, HTTP).
RETRYABLE_ERROR_MESSAGE = "Could not check this address right now. Try again."
SETUP_ERROR_MESSAGE = (
    "The address search service rejected the request. This is a setup problem, not your address."
)


@dataclass(frozen=True)
class GeocodeCandidate:
    display_name: str
    latitude: float
    longitude: float
    place_id: Optional[int] = None
    osm_type: Optional[str] = None
    osm_id: Optional[int] = None
    importance: Optional[float] = None
    address: Optional[dict] = None


@dataclass(frozen=True)
class GeocodeResult:
    status: GeocodeStatus
    coordinate: Optional[Coordinate] = None
    display_name: Optional[str] = None
    candidates: List[GeocodeCandidate] = field(default_factory=list)
    error_message: Optional[str] = None
    message: Optional[str] = None
    error_kind: Optional[ErrorKind] = None
    # The exact text we asked the provider for, so the UI can show it.
    searched_as: Optional[str] = None
    # House-unit interpretation, when the input contained one.
    house: Optional[str] = None
    unit: Optional[str] = None
    unit_kind: Optional[str] = None
    unit_inferred: bool = False
    # Candidates discarded because they fell outside the chosen city.
    dropped_candidates: int = 0
    # Plain-language note about the discarded candidates.
    scope_message: Optional[str] = None
    # House number the geocoder actually found, when it differs from the one
    # that was asked for. Drives the "found 40/5, you asked for 40" copy.
    found_house: Optional[str] = None
    # City the chosen coordinate sits in, when it is known.
    found_city: Optional[str] = None
    # True when the row needs a human look: outside the active city, or so far
    # from the rest of the route that it is probably a mistake.
    needs_check: bool = False
    # Why the row needs a look, in user-facing words.
    needs_check_reason: Optional[str] = None
    # City the user could re-run this row in, when one is known.
    retry_city: Optional[str] = None
    # True when the provider returned candidates but every one of them fell
    # outside the active city. Triggers the street-only last-resort lookup.
    all_out_of_scope: bool = False
