from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ..domain.geocode import GeocodeResult, GeocodeStatus
from ..providers.geocoding.base import Geocoder


@dataclass
class BatchGeocodeRequestItem:
    index: int
    original: str
    trimmed: str


@dataclass
class BatchGeocodeResponseItem:
    index: int
    original: str
    status: str
    coordinate: Dict[str, float] | None = None
    display_name: str | None = None
    candidates: List[Dict[str, any]] | None = None
    error_message: str | None = None
    message: str | None = None
    error_kind: str | None = None


class GeocodeService:
    def __init__(self, geocoder: Geocoder) -> None:
        self._geocoder = geocoder
        self._batch_cache: Dict[str, GeocodeResult] = {}

    async def geocode_batch(self, items: List[BatchGeocodeRequestItem]) -> List[BatchGeocodeResponseItem]:
        if len(items) > 5:
            raise ValueError("Batch size cannot exceed 5")
        results: List[BatchGeocodeResponseItem] = []
        # Resolve each item; cache by canonical key for non-error
        # Duplicates may appear as separate items with same trimmed; geocode once
        key_to_result: Dict[str, GeocodeResult] = {}
        # first pass: get cached or prepare
        for item in items:
            key = item.trimmed.strip().lower()
            if key in key_to_result:
                continue
            if key in self._batch_cache:
                res = self._batch_cache[key]
                if res.status != GeocodeStatus.ERROR:
                    key_to_result[key] = res
                    continue
            # need to fetch
            res = await self._geocoder.geocode(item.trimmed)
            if res.status != GeocodeStatus.ERROR:
                self._batch_cache[key] = res
            key_to_result[key] = res

        for item in items:
            key = item.trimmed.strip().lower()
            res = key_to_result.get(key)
            coord = None
            if res and res.coordinate is not None:
                coord = {"latitude": res.coordinate.latitude, "longitude": res.coordinate.longitude}
            candidates = None
            if res and res.candidates:
                candidates = [
                    {
                        "display_name": c.display_name,
                        "latitude": c.latitude,
                        "longitude": c.longitude,
                        "address": c.address,
                    }
                    for c in res.candidates
                ]
            results.append(
                BatchGeocodeResponseItem(
                    index=item.index,
                    original=item.original,
                    status=res.status.value if res else GeocodeStatus.ERROR.value,
                    coordinate=coord,
                    display_name=res.display_name if res else None,
                    candidates=candidates,
                    error_message=res.error_message if res else "No result",
                    message=res.message if res else None,
                    error_kind=res.error_kind.value if res and res.error_kind else None,
                )
            )
        return results
