import pytest

from app.core.errors import ValidationError
from app.domain.models import Coordinate
from app.domain.routing import MatrixPoint, MatrixResult
from app.services.matrix_service import MatrixService


class FakeRouter:
    """Returns a fixed MatrixResult for every call, so the service can be
    tested without any network."""

    def __init__(self, result: MatrixResult | None = None) -> None:
        self.result = result
        self.calls: list = []

    async def matrix(self, coordinates):
        self.calls.append(coordinates)
        if self.result is None:
            n = len(coordinates)
            self.result = MatrixResult(
                durations_s=[
                    [0.0 if i == j else 100.0 for j in range(n)] for i in range(n)
                ],
                distances_m=[
                    [0.0 if i == j else 1500.0 for j in range(n)] for i in range(n)
                ],
                provider="fake",
            )
        return self.result


def point(point_id: str, lat: float = 50.45, lon: float = 30.52) -> MatrixPoint:
    return MatrixPoint(id=point_id, coordinate=Coordinate(lat, lon))


def two_points() -> list[MatrixPoint]:
    return [point("start", 50.45, 30.52), point("3", 50.44, 30.51)]


@pytest.mark.asyncio
async def test_at_least_two_points():
    service = MatrixService(FakeRouter())
    with pytest.raises(ValidationError, match="two points"):
        await service.compute([point("start")])


@pytest.mark.asyncio
async def test_too_many_points():
    service = MatrixService(FakeRouter(), max_points=3)
    with pytest.raises(ValidationError, match="Too many points"):
        await service.compute(
            two_points() + [point("x", 50.43, 30.50), point("y", 50.42, 30.49)]
        )


@pytest.mark.asyncio
async def test_latitude_out_of_range():
    service = MatrixService(FakeRouter())
    with pytest.raises(ValidationError, match="latitude"):
        await service.compute([point("start", 91.0, 30.52), point("3")])


@pytest.mark.asyncio
async def test_longitude_out_of_range():
    service = MatrixService(FakeRouter())
    with pytest.raises(ValidationError, match="longitude"):
        await service.compute(two_points()[:1] + [point("3", 50.44, 181.0)])


@pytest.mark.asyncio
async def test_duplicate_ids_rejected():
    service = MatrixService(FakeRouter())
    with pytest.raises(ValidationError, match="Duplicate point id"):
        await service.compute([point("start"), point("start")])


@pytest.mark.asyncio
async def test_ids_preserve_input_order():
    router = FakeRouter()
    service = MatrixService(router)
    points = [
        point("start", 50.45, 30.52),
        point("7", 50.44, 30.51),
        point("2", 50.43, 30.50),
    ]
    result = await service.compute(points)
    assert result.ids == ["start", "7", "2"]
    # Matrix rows must be indexed in the same order the requests listed them.
    assert router.calls[0][0] == points[0].coordinate
    assert router.calls[0][1] == points[1].coordinate


@pytest.mark.asyncio
async def test_row_entirely_none_is_reported_unreachable():
    n = 2
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, 100.0], [None, None]],
            distances_m=[[0.0, 1500.0], [None, None]],
            provider="fake",
        )
    )
    result = await MatrixService(router).compute(two_points())
    assert [p.id for p in result.problems] == ["3"]
    assert result.problems[0].kind == "unreachable"


@pytest.mark.asyncio
async def test_column_entirely_none_is_reported_unreachable():
    # Row 2 has routes, but column 2 is all None: the point is never a target.
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, 10.0, None], [10.0, 0.0, None], [5.0, 5.0, None]],
            distances_m=[[0.0, 100.0, None], [100.0, 0.0, None], [50.0, 50.0, None]],
            provider="fake",
        )
    )
    service = MatrixService(router)
    points = [point("start"), point("1"), point("2")]
    result = await service.compute(points)
    assert [p.id for p in result.problems] == ["2"]
    assert result.problems[0].kind == "unreachable"


@pytest.mark.asyncio
async def test_null_off_diagonal_cells_are_not_unreachable():
    # Only some pairs lack a route; the point still has a diagonal and other
    # neighbours, so it is not reported as unreachable.
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, None, 30.0], [None, 0.0, 40.0], [30.0, 40.0, 0.0]],
            distances_m=[[0.0, None, 300.0], [None, 0.0, 400.0], [300.0, 400.0, 0.0]],
            provider="fake",
        )
    )
    result = await MatrixService(router).compute([point("s"), point("1"), point("2")])
    assert result.problems == []
    assert result.matrix.durations_s[0][1] is None


@pytest.mark.asyncio
async def test_far_from_road_reported_above_threshold():
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, 100.0], [100.0, 0.0]],
            distances_m=[[0.0, 1500.0], [1500.0, 0.0]],
            provider="fake",
            snap_distance_m=[10.0, 600.0],
        )
    )
    result = await MatrixService(router).compute(two_points())
    assert [p.id for p in result.problems] == ["3"]
    assert result.problems[0].kind == "far_from_road"
    assert "600 m" in result.problems[0].message


@pytest.mark.asyncio
async def test_snap_below_threshold_is_not_a_problem():
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, 100.0], [100.0, 0.0]],
            distances_m=[[0.0, 1500.0], [1500.0, 0.0]],
            provider="fake",
            snap_distance_m=[10.0, 400.0],
        )
    )
    result = await MatrixService(router).compute(two_points())
    assert result.problems == []


@pytest.mark.asyncio
async def test_no_snap_data_means_no_far_from_road_problems():
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, 100.0], [100.0, 0.0]],
            distances_m=[[0.0, 1500.0], [1500.0, 0.0]],
            provider="fake",
        )
    )
    result = await MatrixService(router).compute(two_points())
    assert result.problems == []


@pytest.mark.asyncio
async def test_both_problems_can_apply_to_one_point():
    router = FakeRouter(
        MatrixResult(
            durations_s=[[0.0, None], [None, None]],
            distances_m=[[0.0, None], [None, None]],
            provider="fake",
            snap_distance_m=[10.0, 900.0],
        )
    )
    result = await MatrixService(router).compute(two_points())
    kinds = [p.kind for p in result.problems]
    assert kinds == ["unreachable", "far_from_road"]