"""Checks that need the whole list, not a single line.

Two of them:

* ``infer_dominant_city`` — which city a list is mostly about, so the City field
  can be suggested when the user never filled it in.
* ``flag_distance_outliers`` — a stop far from every other stop is more likely a
  wrong match than a real destination.

Both are pure functions over already-geocoded rows: no network, no I/O, no
globals. Every value they return is deterministic for a given input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .address_normalizer import city_profile_for

# A stop farther than this from the middle of the route gets flagged. Route
# planning inside one city rarely needs more, and a bigger jump is usually a
# match in the wrong town rather than a long transfer.
DEFAULT_OUTLIER_KM = 50.0

# Not enough evidence to suggest a city.
MIN_RESOLVED_FOR_CITY = 5
MIN_CITY_SHARE = 0.60

_EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class CitySuggestion:
    """A city the list looks like it is about."""

    city: str
    resolved_count: int
    total_confident: int
    share: float


def infer_dominant_city(
    rows: Sequence[Any], *, min_count: int = MIN_RESOLVED_FOR_CITY, min_share: float = MIN_CITY_SHARE
) -> Optional[CitySuggestion]:
    """Suggest a city from rows that resolved confidently.

    Only ``resolved`` rows count: a row that is already asking for a check has
    not proven where it is. Rows with no reported city are ignored too, so a
    city is never invented from silence.

    Returns ``None`` unless at least ``min_count`` confident rows share a city
    and that city holds at least ``min_share`` of them, so a single accidental
    pair cannot set the scope for a whole list.
    """
    counts: Dict[str, int] = {}
    total = 0
    for row in rows:
        if getattr(row, "status", None) != "resolved":
            continue
        city = getattr(row, "found_city", None)
        if not city:
            continue
        total += 1
        counts[city] = counts.get(city, 0) + 1

    if not counts:
        return None

    city, count = max(counts.items(), key=lambda kv: kv[1])
    # The threshold is about the city, not the list: five confident rows in
    # Київ and one in Полтава is a suggestion, four and one is not.
    if count < min_count:
        return None
    share = count / total
    if share < min_share:
        return None
    return CitySuggestion(city=city, resolved_count=count, total_confident=total, share=share)


def haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _key(row: Any) -> str:
    """Stable per-row identity, so a row can be looked up in the result.

    Uses the address text rather than object identity: callers build fresh
    objects, and two identical lines are the same row as far as the user is
    concerned.
    """
    for attribute in ("original", "trimmed", "text"):
        value = getattr(row, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return str(id(row))


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def flag_distance_outliers(
    rows: Sequence[Any], *, max_km: float = DEFAULT_OUTLIER_KM
) -> Dict[str, float]:
    """Mark rows far from the middle of the rest of the route.

    Distance is measured to the median position of the *other* resolved rows, so
    one bad row cannot drag the centre towards itself. A row is only judged when
    at least three points are available to compare against.

    Returns a mapping from row key to the distance in km, containing only
    flagged rows, so the caller can tell the user how far off the match is.
    """
    points = []
    for row in rows:
        if getattr(row, "status", None) != "resolved":
            continue
        coordinate = getattr(row, "coordinate", None)
        if not coordinate:
            continue
        latitude = coordinate.get("latitude") if isinstance(coordinate, dict) else None
        longitude = coordinate.get("longitude") if isinstance(coordinate, dict) else None
        if latitude is None or longitude is None:
            continue
        points.append((_key(row), float(latitude), float(longitude)))

    if len(points) < 3:
        # Two points have no middle: either could be the odd one out.
        return {}

    flagged: Dict[str, float] = {}
    for key, latitude, longitude in points:
        others = [(la, lo) for other, la, lo in points if other != key]
        centre_lat = _median([la for la, _ in others])
        centre_lon = _median([lo for _, lo in others])
        distance = haversine_km(latitude, longitude, centre_lat, centre_lon)
        if distance > max_km:
            flagged[key] = distance
    return flagged


def describe_outlier(distance_km: float, max_km: float) -> str:
    """User-facing wording for a row flagged as far from the rest."""
    return (
        f"Found about {round(distance_km)} km from the other stops "
        f"(limit {round(max_km)} km). Please check this match."
    )