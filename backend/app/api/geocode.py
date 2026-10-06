from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.config import Settings, get_settings
from ..providers.geocoding.nominatim import NominatimGeocoder
from ..services.geocode_service import (
    BatchGeocodeRequestItem,
    GeocodeService,
    geocode_in_batches,
)

router = APIRouter()


class GeocodeItem(BaseModel):
    index: int
    original: str
    trimmed: str


class GeocodeBatchRequest(BaseModel):
    items: list[GeocodeItem] = Field(..., min_length=1, max_length=5)
    # Scope for candidate validation. Left blank, no city filter is applied.
    city: str | None = None


class GeocodeCandidate(BaseModel):
    display_name: str
    latitude: float
    longitude: float
    address: dict | None = None


class GeocodeBatchResponseItem(BaseModel):
    index: int
    original: str
    status: str
    coordinate: dict | None = None
    display_name: str | None = None
    candidates: list[GeocodeCandidate] | None = None
    error_message: str | None = None
    message: str | None = None
    error_kind: str | None = None
    searched_as: str | None = None
    house: str | None = None
    unit: str | None = None
    unit_kind: str | None = None
    unit_inferred: bool = False
    dropped_candidates: int = 0
    scope_message: str | None = None
    # The house number the geocoder actually found, when it differs from the
    # requested one. Drives the "found 40/5, you asked for 40" copy.
    found_house: str | None = None
    # City the chosen coordinate sits in, as reported by the geocoder.
    found_city: str | None = None
    # Set when the row is internally consistent but suspicious in the context of
    # the list: outside the active city, or far from every other stop.
    needs_check: bool = False
    needs_check_reason: str | None = None
    # City the user could re-run this row in, when one is known.
    retry_city: str | None = None


class GeocodeBatchResponse(BaseModel):
    results: list[GeocodeBatchResponseItem]
    # Set only when the City field was left blank and the resolved rows agree
    # on one city. A suggestion for the user to accept, never applied silently.
    city_suggestion: str | None = None
    city_suggestion_share: float | None = None


@lru_cache(maxsize=1)
def _shared_geocoder() -> NominatimGeocoder:
    """One provider instance for the whole process.

    The rate limiter and the response cache live on the instance, so building a
    new one per request would reset both.
    """
    return NominatimGeocoder(get_settings())


def get_geocoder() -> NominatimGeocoder:
    return _shared_geocoder()


def get_geocode_service(geocoder: NominatimGeocoder = Depends(get_geocoder)) -> GeocodeService:
    return GeocodeService(geocoder)


@router.post("/geocode", response_model=GeocodeBatchResponse)
async def geocode_batch(
    req: GeocodeBatchRequest,
    service: GeocodeService = Depends(get_geocode_service),
):
    if len(req.items) > 5:
        raise HTTPException(status_code=400, detail="Batch size cannot exceed 5")
    city = (req.city or "").strip() or None
    try:
        items = [
            BatchGeocodeRequestItem(index=i.index, original=i.original, trimmed=i.trimmed)
            for i in req.items
        ]
        service.reset()
        results = await geocode_in_batches(service, items, city=city)
        suggestion = service.city_suggestion
        return GeocodeBatchResponse(
            city_suggestion=suggestion.city if suggestion else None,
            city_suggestion_share=suggestion.share if suggestion else None,
            results=[
                GeocodeBatchResponseItem(
                    index=r.index,
                    original=r.original,
                    status=r.status,
                    coordinate=r.coordinate,
                    display_name=r.display_name,
                    candidates=r.candidates,
                    error_message=r.error_message,
                    message=r.message,
                    error_kind=r.error_kind,
                    searched_as=r.searched_as,
                    house=r.house,
                    unit=r.unit,
                    unit_kind=r.unit_kind,
                    unit_inferred=r.unit_inferred,
                    dropped_candidates=r.dropped_candidates,
                    scope_message=r.scope_message,
                    found_house=r.found_house,
                    found_city=r.found_city,
                    needs_check=r.needs_check,
                    needs_check_reason=r.needs_check_reason,
                    retry_city=r.retry_city,
                )
                for r in results
            ]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
