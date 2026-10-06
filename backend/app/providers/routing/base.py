from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ...domain.models import Coordinate
from ...domain.routing import MatrixResult


class Router(ABC):
    """A provider that computes a distance/duration matrix for a set of points."""

    @abstractmethod
    async def matrix(self, coordinates: Sequence[Coordinate]) -> MatrixResult:
        """Return durations and distances between every pair of coordinates.

        The returned matrices are square and indexed in input order. The
        provider may raise :class:`app.core.errors.RouterError` subclasses to
        report problems.
        """
        raise NotImplementedError