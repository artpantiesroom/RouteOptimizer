"""HTTP surface for POST /api/matrix.

The router/service is stubbed out, so the suite never touches the live routing
service. The tests cover the thin endpoint contract: request parsing, rounding,
error mapping (validation vs retryable vs setup) and the response shape.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.matrix import get_matrix_service
from app.core.errors import RouterSetupError, RouterTooManyPointsError, RouterUnavailableError
from app.domain.routing import MatrixResult
from app.main import app


class StubMatrixService:
    def __init__(self) -> None:
        self.calls: list = []
        self.result = None
        self.error: Exception | None = None

    async def compute(self, points):
        self.calls.append(points)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        n = len(points)
        return type("R", (), {
            "matrix": MatrixResult(
                durations_s=[
                    [0.0 if i == j else 100.8 for j in range(n)] for i in range(n)
                ],
                distances_m=[
                    [0.0 if i == j else 1234.7 for j in range(n)] for i in range(n)
                ],
                provider="osrm",
                profile="driving",
                snap_distance_m=None,
            ),
            "ids": [p.id for p in points],
            "problems": [],
        })()


@pytest.fixture
def stub_service():
    stub = StubMatrixService()
    app.dependency_overrides[get_matrix_service] = lambda: stub
    yield stub
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def make_points(n: int = 2) -> list[dict]:
    return [
        {"id": "start", "lat": 50.45, "lon": 30.52},
        {"id": "0", "lat": 50.44, "lon": 30.51},
    ][:n]


def test_matrix_returns_rounded_matrices_and_ids(client, stub_service):
    resp = client.post("/api/matrix", json=make_points())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ids"] == ["start", "0"]
    assert body["durations_s"] == [[0, 101], [101, 0]]
    assert body["distances_m"] == [[0, 1235], [1235, 0]]
    assert body["problems"] == []
    assert body["provider"] == "osrm"
    assert body["profile"] == "driving"
    assert "without live traffic" in body["note"]


def test_matrix_rounds_none_cells_to_null(client, stub_service):
    stub_service.result = _service_result([[0.0, None]], [[0.0, None]], ["start", "0"])
    resp = client.post("/api/matrix", json=make_points())
    body = resp.json()
    assert body["durations_s"] == [[0, None]]
    assert body["distances_m"] == [[0, None]]


def test_matrix_exposes_problems(client, stub_service):
    problem = type("P", (), {"id": "0", "kind": "far_from_road", "message": "about 600 m"})()
    stub_service.result = _service_result(
        [[0.0, 100.0], [100.0, 0.0]],
        [[0.0, 1000.0], [1000.0, 0.0]],
        ["start", "0"],
        problems=[problem],
    )
    body = client.post("/api/matrix", json=make_points()).json()
    assert body["problems"] == [
        {"id": "0", "kind": "far_from_road", "message": "about 600 m"}
    ]


def test_point_ids_are_echoed_in_input_order(client, stub_service):
    resp = client.post(
        "/api/matrix",
        json=[
            {"id": "start", "lat": 50.45, "lon": 30.52},
            {"id": "9", "lat": 50.44, "lon": 30.51},
            {"id": "7", "lat": 50.43, "lon": 30.50},
        ],
    )
    assert resp.json()["ids"] == ["start", "9", "7"]
    sent_ids = [p.id for p in stub_service.calls[0]]
    assert sent_ids == ["start", "9", "7"]


def test_retryable_router_error_maps_to_502(client, stub_service):
    stub_service.error = RouterUnavailableError("routing service unavailable (status 503)")
    resp = client.post("/api/matrix", json=make_points())
    assert resp.status_code == 502
    assert resp.json()["detail"] == {
        "message": "routing service unavailable (status 503)",
        "error_kind": "retryable",
    }


def test_setup_router_error_maps_to_502_setup(client, stub_service):
    stub_service.error = RouterSetupError("routing service rejected the request (status 403)")
    resp = client.post("/api/matrix", json=make_points())
    assert resp.status_code == 502
    assert resp.json()["detail"]["error_kind"] == "setup"


def test_too_many_points_maps_to_400(client, stub_service):
    stub_service.error = RouterTooManyPointsError("too many points")
    resp = client.post("/api/matrix", json=make_points())
    assert resp.status_code == 400
    assert "Reduce the list" in resp.json()["detail"]


def _service_result(durations, distances, ids, problems=None):
    return type("R", (), {
        "matrix": MatrixResult(
            durations_s=durations,
            distances_m=distances,
            provider="osrm",
            profile="driving",
            snap_distance_m=None,
        ),
        "ids": ids,
        "problems": problems or [],
    })()