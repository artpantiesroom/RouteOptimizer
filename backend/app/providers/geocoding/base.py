from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ...domain.geocode import GeocodeResult


class Geocoder(ABC):
    @abstractmethod
    async def geocode(self, address: str) -> GeocodeResult:
        raise NotImplementedError


class GeocodeBatcher(ABC):
    pass
