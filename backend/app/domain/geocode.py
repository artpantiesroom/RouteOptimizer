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
