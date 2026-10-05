from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.config import Settings, get_settings
from ..providers.geocoding.nominatim import NominatimGeocoder
from ..services.geocode_service import BatchGeocodeRequestItem, GeocodeService

router = APIRouter()


class GeocodeItem(BaseModel):
    index: int
    original: str
    trimmed: str


class GeocodeBatchRequest(BaseModel):
    items: list[GeocodeItem] = Field(..., min_length=1, max_length=5)


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


class GeocodeBatchResponse(BaseModel):
    results: list[GeocodeBatchResponseItem]


def get_geocoder(settings: Settings = Depends(get_settings)) -> NominatimGeocoder:
    return NominatimGeocoder(settings)


def get_geocode_service(geocoder: NominatimGeocoder = Depends(get_geocoder)) -> GeocodeService:
    return GeocodeService(geocoder)


@router.post("/geocode", response_model=GeocodeBatchResponse)
async def geocode_batch(
    req: GeocodeBatchRequest,
    service: GeocodeService = Depends(get_geocode_service),
):
    if len(req.items) > 5:
        raise HTTPException(status_code=400, detail="Batch size cannot exceed 5")
    try:
        items = [
            BatchGeocodeRequestItem(index=i.index, original=i.original, trimmed=i.trimmed)
            for i in req.items
        ]
        results = await service.geocode_batch(items)
        return GeocodeBatchResponse(
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
                )
                for r in results
            ]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
