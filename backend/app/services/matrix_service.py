from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..core.errors import ValidationError
from ..domain.models import Coordinate
from ..domain.routing import MatrixPoint, MatrixResult
from ..providers.routing.base import Router


@dataclass(frozen=True)
class MatrixProblem:
    """One thing the user should look at before relying on the matrix.

    ``kind`` is one of ``"unreachable"`` (no route to or from this point) or
    ``"far_from_road"`` (snap distance above the configured threshold).
    """

    id: str
    kind: str
    message: str


@dataclass
class MatrixServiceResult:
    matrix: MatrixResult
    # Point ids in the same order as the matrix rows.
    ids: List[str]
    problems: List[MatrixProblem] = field(default_factory=list)


class MatrixService:
    """Validates the request, calls the Router and explains the matrix."""

    def __init__(
        self,
        router: Router,
        *,
        max_points: int = 50,
        max_snap_distance_m: float = 500.0,
    ) -> None:
        self._router = router
        self._max_points = max_points
        self._max_snap_distance_m = max_snap_distance_m

    def validate(self, points: Sequence[MatrixPoint]) -> None:
        if len(points) < 2:
            raise ValidationError(
                "At least two points are needed (the start point plus one stop)."
            )
        if len(points) > self._max_points:
            raise ValidationError(
                f"Too many points (max {self._max_points}). Reduce the list and try again."
            )
        seen_ids = set()
        for point in points:
            lat, lon = point.coordinate.latitude, point.coordinate.longitude
            if not (-90.0 <= lat <= 90.0):
                raise ValidationError(f"Point '{point.id}' has an invalid latitude.")
            if not (-180.0 <= lon <= 180.0):
                raise ValidationError(f"Point '{point.id}' has an invalid longitude.")
            if point.id in seen_ids:
                raise ValidationError(f"Duplicate point id '{point.id}'.")
            seen_ids.add(point.id)

    async def compute(self, points: Sequence[MatrixPoint]) -> MatrixServiceResult:
        self.validate(points)
        matrix = await self._router.matrix(
            [point.coordinate for point in points]
        )
        ids = [point.id for point in points]
        return MatrixServiceResult(
            matrix=matrix,
            ids=ids,
            problems=self._explain(matrix, ids),
        )

    def _explain(self, matrix: MatrixResult, ids: List[str]) -> List[MatrixProblem]:
        problems: List[MatrixProblem] = []
        n = len(ids)
        for i, point_id in enumerate(ids):
            row_or_column_void = _row_or_column_entirely_none(
                matrix.durations_s, i
            ) or _row_or_column_entirely_none(matrix.distances_m, i)
            if row_or_column_void:
                problems.append(
                    MatrixProblem(
                        id=point_id,
                        kind="unreachable",
                        message="No route to or from this point. Check the address.",
                    )
                )
            snap = matrix.snap_distance_m
            if snap is not None and snap[i] is not None and snap[i] > self._max_snap_distance_m:
                problems.append(
                    MatrixProblem(
                        id=point_id,
                        kind="far_from_road",
                        message=(
                            f"This point is about {snap[i]:.0f} m from the "
                            "road network. Check the address."
                        ),
                    )
                )
        return problems


def _row_or_column_entirely_none(matrix: List[List[Optional[float]]], index: int) -> bool:
    """A point whose row or column is all None cannot be reached at all."""
    if all(value is None for value in matrix[index]):
        return True
    return all(row[index] is None for row in matrix)