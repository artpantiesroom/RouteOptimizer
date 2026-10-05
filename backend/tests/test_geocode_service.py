import pytest

from app.domain.geocode import GeocodeCandidate, GeocodeResult, GeocodeStatus, Coordinate
from app.services.geocode_service import (
    BatchGeocodeRequestItem,
    GeocodeService,
)


class FakeGeocoder:
    def __init__(self):
        self.calls = []

    async def geocode(self, address: str) -> GeocodeResult:
        self.calls.append(address)
        addr = address.lower()
        if addr == "exact":
            return GeocodeResult(
                status=GeocodeStatus.RESOLVED,
                coordinate=Coordinate(55.0, 37.0),
                display_name="Exact address",
            )
        if addr == "partial":
            c = GeocodeCandidate(display_name="Partial street", latitude=1.0, longitude=2.0)
            return GeocodeResult(
                status=GeocodeStatus.PARTIAL,
                candidates=[c],
                error_message="Partial",
                message="Partial",
            )
        if addr == "ambig":
            c1 = GeocodeCandidate(display_name="A", latitude=1.0, longitude=1.0)
            c2 = GeocodeCandidate(display_name="B", latitude=2.0, longitude=2.0)
            return GeocodeResult(status=GeocodeStatus.AMBIGUOUS, candidates=[c1, c2])
        if addr == "nf":
            return GeocodeResult(status=GeocodeStatus.NOT_FOUND)
        if addr == "err":
            return GeocodeResult(status=GeocodeStatus.ERROR, error_message="boom")
        return GeocodeResult(status=GeocodeStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_batch_geocode_basic():
    svc = GeocodeService(FakeGeocoder())
    items = [
        BatchGeocodeRequestItem(index=0, original="Exact", trimmed="Exact"),
        BatchGeocodeRequestItem(index=1, original="nf", trimmed="nf"),
    ]
    results = await svc.geocode_batch(items)
    assert results[0].status == "resolved"
    assert results[1].status == "not_found"


@pytest.mark.asyncio
async def test_duplicates_geocode_once():
    fake = FakeGeocoder()
    svc = GeocodeService(fake)
    items = [
        BatchGeocodeRequestItem(index=0, original="Exact", trimmed="Exact"),
        BatchGeocodeRequestItem(index=1, original="exact", trimmed="exact"),
    ]
    await svc.geocode_batch(items)
    # same canonical key -> one call
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_dont_cache_errors():
    fake = FakeGeocoder()
    svc = GeocodeService(fake)
    items = [BatchGeocodeRequestItem(index=0, original="err", trimmed="err")]
    await svc.geocode_batch(items)
    await svc.geocode_batch(items)
    # error not cached -> called twice
    assert len(fake.calls) == 2
