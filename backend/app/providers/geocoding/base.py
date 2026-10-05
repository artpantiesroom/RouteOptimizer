from __future__ import annotations

from abc import ABC, abstractmethod

from ...domain.geocode import GeocodeResult


class Geocoder(ABC):
    @abstractmethod
    async def geocode(self, address: str) -> GeocodeResult:
        """Resolve a free-text address. Called positionally by existing callers."""
        raise NotImplementedError

    async def geocode_structured(self, street: str, city: str) -> GeocodeResult:
        """Resolve a street + city pair using separate provider fields.

        Providers with no structured support inherit this and fall back to the
        free-text query, so ``GeocodeService`` can offer the same fallback chain
        regardless of which provider is plugged in.
        """
        return await self.geocode(f"{street}, {city}")


class GeocodeBatcher(ABC):
    pass