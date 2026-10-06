from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .models import Coordinate


@dataclass(frozen=True)
class MatrixResult:
    """A distance/duration matrix for one set of points.

    Both matrices are square, size ``n x n`` with ``n = len(coordinates)``, and
    indexed in the same order the coordinates were given. A ``None`` cell means
    no route exists between that pair (including the whole row or column of an
    unsnappable point). The diagonal is zero for every point that snapped to the
    road network.
    """

    durations_s: List[List[Optional[float]]]
    distances_m: List[List[Optional[float]]]
    provider: str
    profile: str = "driving"
    # Per-point distance in metres from the input coordinate to the road
    # network, when the provider reports it. ``None`` means the provider did
    # not report snap distances for this request.
    snap_distance_m: Optional[List[Optional[float]]] = None


@dataclass(frozen=True)
class MatrixPoint:
    """One coordinate in the matrix, identified by the client's own id."""

    id: str
    coordinate: Coordinate


Coordinates = Sequence[Coordinate]